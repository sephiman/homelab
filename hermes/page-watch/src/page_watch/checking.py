from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import Enum

from page_watch.errors import PageWatchError
from page_watch.judge import Assessment, PageJudge
from page_watch.page import PageFetcher
from page_watch.snapshot import excerpt, fingerprint, focus, phrases_on
from page_watch.watches import PageWatch, Verdict, WatchStore

# The job runs every 5 minutes: a watch due a few seconds after a run still counts as due then.
_SCHEDULE_SLACK = timedelta(seconds=60)
FAILURES_BEFORE_REPORT = 3
NOISY_CALLS_PER_DAY = 6
MAX_PAGE_CHARS = 15_000


class FindingKind(Enum):
    READY = "READY"
    FAILING = "FAILING"
    NOISY = "NOISY"
    REMINDER = "REMINDER"


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    text: str


def _finding(kind: FindingKind, watch: PageWatch, detail: str) -> Finding:
    return Finding(kind, f"{kind.value} #{watch.id}: {watch.condition}\n{detail}\n{watch.url}")


def is_due(watch: PageWatch, now: datetime) -> bool:
    if watch.met_at is not None or (watch.starts_at is not None and watch.starts_at > now):
        return False
    return watch.checked_at is None or watch.checked_at + watch.every <= now + _SCHEDULE_SLACK


def learn(watch: PageWatch, lines: list[str], assessment: Assessment) -> PageWatch:
    """Keeps the model's phrases that really are on the page; the waiting ones gate later calls."""
    _, anchored = focus(lines, watch.anchors)
    anchors = watch.anchors if anchored else phrases_on(lines, assessment.anchors)
    focused, _ = focus(lines, anchors)
    return replace(
        watch,
        anchors=anchors,
        waiting=phrases_on(focused, assessment.waiting),
        ready=assessment.ready,
        fingerprint=fingerprint(focused),
        summary=assessment.summary,
    )


class PageChecker:
    def __init__(self, store: WatchStore, fetcher: PageFetcher, judge: PageJudge | None) -> None:
        self._store = store
        self._fetcher = fetcher
        self._judge = judge

    def check_chat(self, chat_id: str, now: datetime) -> list[Finding]:
        """Checks the due watches and reminders of one chat: each chat's job delivers its own."""
        findings: list[Finding] = []
        for watch in self._store.watches(chat_id):
            if is_due(watch, now):
                findings.extend(self.check(watch, now))
        for reminder in self._store.reminders(chat_id):
            if reminder.due_at <= now + _SCHEDULE_SLACK:
                findings.append(Finding(FindingKind.REMINDER, f"REMINDER: {reminder.text}"))
                self._store.remove_reminder(reminder.id)
        self._store.prune_model_calls(now - timedelta(days=2))
        return findings

    def check(self, watch: PageWatch, now: datetime) -> list[Finding]:
        try:
            checked = self._look(watch, now)
        except PageWatchError as error:
            return self._failed(watch, str(error), now)
        self._store.save(checked)
        if checked.met_at is not None:
            return [_finding(FindingKind.READY, watch, checked.summary)]
        return self._noise(checked, now)

    def _look(self, watch: PageWatch, now: datetime) -> PageWatch:
        lines = self._fetcher.visible_lines(watch.url)
        focused, _ = focus(lines, watch.anchors)
        current = fingerprint(focused)
        seen = replace(watch, checked_at=now, failures=0, last_error=None)
        if current == watch.fingerprint:
            return seen
        cached = self._store.verdict(watch.id, current)
        if cached is not None:
            return replace(
                seen, fingerprint=current, summary=cached.summary, met_at=now if cached.met else None
            )
        # The page changed elsewhere in the focused part, but its "not yet" wording is all still there.
        if (
            watch.waiting
            and phrases_on(focused, watch.waiting) == watch.waiting
            and not phrases_on(focused, watch.ready)
        ):
            return replace(seen, fingerprint=current)
        if self._judge is None:
            raise PageWatchError(
                "no model configured to read the page (Hermes' model must be OpenAI-compatible)"
            )
        assessment = self._judge.assess(watch.condition, watch.url, excerpt(focused, MAX_PAGE_CHARS))
        self._store.record_model_call(watch.id, now)
        learned = learn(seen, lines, assessment)
        assert learned.fingerprint is not None
        self._store.save_verdict(watch.id, learned.fingerprint, Verdict(assessment.met, assessment.summary))
        return replace(learned, met_at=now if assessment.met else None)

    def _failed(self, watch: PageWatch, error: str, now: datetime) -> list[Finding]:
        failures = watch.failures + 1
        self._store.save(replace(watch, checked_at=now, failures=failures, last_error=error))
        if failures != FAILURES_BEFORE_REPORT:
            return []
        return [_finding(FindingKind.FAILING, watch, f"the last {failures} checks failed: {error}")]

    def _noise(self, watch: PageWatch, now: datetime) -> list[Finding]:
        if (
            watch.noisy_reported
            or self._store.model_calls_since(watch.id, now - timedelta(days=1)) < NOISY_CALLS_PER_DAY
        ):
            return []
        self._store.save(replace(watch, noisy_reported=True))
        detail = (
            f"the page changed on {NOISY_CALLS_PER_DAY}+ checks in a day, each costing a model call;"
            " check less often or watch a simpler page"
        )
        return [_finding(FindingKind.NOISY, watch, detail)]
