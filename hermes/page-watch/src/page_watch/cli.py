import argparse
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from agent_context.chats import ChatError, Chats
from agent_context.llm import ChatModel, load_llm_config
from agent_context.managed_config import load_timezone

from page_watch.checking import MAX_PAGE_CHARS, PageChecker, learn
from page_watch.errors import InvalidInputError, PageWatchError
from page_watch.judge import PageJudge
from page_watch.page import PageFetcher, normalize_url
from page_watch.settings import db_path
from page_watch.snapshot import excerpt
from page_watch.timing import format_every, format_local, parse_every, parse_local_time
from page_watch.watches import PageWatch, Verdict, WatchStore

CHAT_HELP = "Telegram chat id or name for the alerts (default: the chat you are in)"
# Below this much text the page needs JavaScript, and its content never reaches page-watch.
MIN_PAGE_CHARS = 200


def build_judge() -> PageJudge | None:
    """Hermes' own model reads the pages; None when it isn't an OpenAI-compatible provider."""
    config = load_llm_config()
    return PageJudge(ChatModel(config, session_prefix="page-watch")) if config is not None else None


def build_fetcher() -> PageFetcher:
    return PageFetcher()


def local_zone() -> ZoneInfo:
    return load_timezone()


def now_utc() -> datetime:
    return datetime.now(UTC)


def _add(args: argparse.Namespace) -> int:
    chat = Chats.load().resolve(args.chat)
    url = normalize_url(args.url)
    condition = " ".join(args.condition.split())
    if not condition:
        raise InvalidInputError("say what to wait for, e.g. 'tickets for the 14 March show are on sale'")
    every = parse_every(args.every)
    zone = local_zone()
    starts_at = parse_local_time(args.starting, zone).astimezone(UTC) if args.starting else None
    judge = build_judge()
    if judge is None:
        raise PageWatchError("no model configured to read pages (Hermes' model must be OpenAI-compatible)")

    lines = build_fetcher().visible_lines(url)
    if len("".join(lines)) < MIN_PAGE_CHARS:
        raise InvalidInputError(
            f"{url} shows almost no text without JavaScript, so changes would never be seen; "
            "watch another page that will show it, such as the original source or an announcement page"
        )
    assessment = judge.assess(condition, url, excerpt(lines, MAX_PAGE_CHARS))
    if assessment.met and not args.even_if_met:
        print(
            f"ALREADY MET: {assessment.summary}\n{url}\n"
            "Not watching. Add --even-if-met to watch for the next change anyway."
        )
        return 0

    now = now_utc()
    store = WatchStore(db_path())
    blank = PageWatch(0, chat.id, url, condition, every, starts_at, (), (), (), None, "", checked_at=now)
    watch = store.add(learn(blank, lines, assessment), now)
    store.record_model_call(watch.id, now)
    assert watch.fingerprint is not None
    store.save_verdict(watch.id, watch.fingerprint, Verdict(assessment.met, assessment.summary))

    start_text = f" from {format_local(starts_at, zone)}" if starts_at else ""
    anchor_text = ", ".join(f'"{anchor}"' for anchor in watch.anchors) or "the whole page"
    print(
        f"Watching #{watch.id}: {condition}\n{url}\n"
        f"now: {assessment.summary}\n"
        f"checked every {format_every(every)}{start_text}, looking at {anchor_text}\n"
        f"Alerts go to {chat.label}."
    )
    return 0


def _remind(args: argparse.Namespace) -> int:
    chat = Chats.load().resolve(args.chat)
    zone = local_zone()
    due_at = parse_local_time(args.when, zone).astimezone(UTC)
    if due_at <= now_utc():
        raise InvalidInputError(f"{format_local(due_at, zone)} is already past")
    text = " ".join(" ".join(args.text).split())
    if not text:
        raise InvalidInputError("say what to remind about")
    reminder = WatchStore(db_path()).add_reminder(chat.id, text, due_at)
    print(f"Reminder R{reminder.id} at {format_local(due_at, zone)}: {text}\nIt goes to {chat.label}.")
    return 0


def _watch_line(watch: PageWatch, zone: ZoneInfo, now: datetime) -> str:
    if watch.met_at is not None:
        state = f"met {format_local(watch.met_at, zone)}"
    elif watch.starts_at is not None and watch.starts_at > now:
        state = f"every {format_every(watch.every)} from {format_local(watch.starts_at, zone)}"
    else:
        state = f"every {format_every(watch.every)}"
    if watch.last_error:
        state += f", {watch.failures} failed checks: {watch.last_error}"
    return f"#{watch.id} {watch.condition}\n   {state}; now: {watch.summary}\n   {watch.url}"


def _list(args: argparse.Namespace) -> int:
    chats = Chats.load()
    chat = None if args.all else chats.resolve(args.chat)
    chat_id = chat.id if chat else None
    store = WatchStore(db_path())
    zone, now = local_zone(), now_utc()
    watches, reminders = store.watches(chat_id), store.reminders(chat_id)
    if not watches and not reminders:
        print(f"No page watches or reminders{f' for {chat.label}' if chat else ''}.")
    for watch in watches:
        owner = f"\n   alerts to {chats.chat(watch.chat_id).label}" if chat is None else ""
        print(_watch_line(watch, zone, now) + owner)
    for reminder in reminders:
        owner = f" (to {chats.chat(reminder.chat_id).label})" if chat is None else ""
        print(f"R{reminder.id} {format_local(reminder.due_at, zone)}: {reminder.text}{owner}")
    elsewhere = len(store.watches()) + len(store.reminders()) - len(watches) - len(reminders)
    if elsewhere:
        print(f"{elsewhere} more in other chats: list --all")
    return 0


def _remove(args: argparse.Namespace) -> int:
    reference = args.id.strip().lstrip("#").upper()
    store = WatchStore(db_path())
    is_reminder = reference.startswith("R")
    number = reference.removeprefix("R")
    if not number.isdigit():
        raise InvalidInputError(f"not a watch (#3) or reminder (R3) id: {args.id!r}")
    removed = store.remove_reminder(int(number)) if is_reminder else store.remove(int(number))
    label = f"R{number}" if is_reminder else f"#{number}"
    if not removed:
        raise InvalidInputError(f"no {'reminder' if is_reminder else 'watch'} {label}")
    print(f"Removed {label}.")
    return 0


def _check(args: argparse.Namespace) -> int:
    """Prints only what the chat must hear, so its job stays silent (and skips the model) otherwise."""
    chat = Chats.load().resolve(args.chat)
    checker = PageChecker(WatchStore(db_path()), build_fetcher(), build_judge())
    for finding in checker.check_chat(chat.id, now_utc()):
        print(finding.text)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="page-watch", description="Tell a chat when a web page shows something, or remind it"
    )
    commands = parser.add_subparsers(required=True)

    add = commands.add_parser("add", help="watch a page until it shows the condition")
    add.add_argument("url", help="the page to watch")
    add.add_argument("condition", help="what to wait for, e.g. 'tickets for 14 March are on sale'")
    add.add_argument("--every", default="1h", help="how often to check: 5m to 7d (default 1h)")
    add.add_argument("--from", dest="starting", help="first check, e.g. '2026-12-01 09:50' (local time)")
    add.add_argument("--even-if-met", action="store_true", help="watch even if the page already shows it")
    add.add_argument("--chat", help=CHAT_HELP)
    add.set_defaults(run=_add)

    remind = commands.add_parser("remind", help="a one-off message at a local time")
    remind.add_argument("when", help="'2026-12-12 09:55' (local time)")
    remind.add_argument("text", nargs="+")
    remind.add_argument("--chat", help=CHAT_HELP)
    remind.set_defaults(run=_remind)

    listing = commands.add_parser("list", help="this chat's watches and reminders")
    listing.add_argument("--chat", help=CHAT_HELP)
    listing.add_argument("--all", action="store_true", help="those of every chat")
    listing.set_defaults(run=_list)

    remove = commands.add_parser("rm", help="stop a watch (#3) or cancel a reminder (R3)")
    remove.add_argument("id")
    remove.set_defaults(run=_remove)

    check = commands.add_parser("check", help="check a chat's due watches (run by its scheduled job)")
    check.add_argument("--chat", help=CHAT_HELP)
    check.set_defaults(run=_check)
    return parser


def main(argv: Sequence[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        result: int = args.run(args)
    except (PageWatchError, ChatError) as error:
        print(f"page-watch: {error}", file=sys.stderr)
        return 1
    return result
