from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from price_hunt.aliexpress import AliExpressClient
from price_hunt.llm import ListingVerifier
from price_hunt.watches import Watch, WatchStore

LOWEST_PRICE_WINDOW = timedelta(days=30)
_MAX_QUERY_WORDS = 12


class FindingKind(Enum):
    TARGET_REACHED = "target reached"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    watch: Watch
    price_cents: int | None = None
    lowest_cents: int | None = None
    offer_url: str | None = None
    offer_title: str | None = None


@dataclass(frozen=True)
class WatchOutcome:
    findings: tuple[Finding, ...]
    cheapest_cents: int | None
    own_cents: int | None


def check_watches(
    store: WatchStore, client: AliExpressClient, verifier: ListingVerifier | None, now: datetime
) -> list[Finding]:
    findings: list[Finding] = []
    for index, watch in enumerate(store.all()):
        if index:
            client.pace()
        findings.extend(check_watch(store, client, verifier, watch, now).findings)
    return findings


def check_watch(
    store: WatchStore, client: AliExpressClient, verifier: ListingVerifier | None, watch: Watch, now: datetime
) -> WatchOutcome:
    """One search per watch; the model classifies each new listing at or below the target once."""
    words = watch.title.split()
    results = client.search(" ".join(words[:_MAX_QUERY_WORDS]), page_size=60) if words else []
    if not results:
        finding = Finding(FindingKind.UNAVAILABLE, watch) if watch.available else None
        store.update_state(watch.id, watch.notified, False)
        return WatchOutcome((finding,) if finding else (), None, None)

    own = next((p for p in results if p.product_id == watch.product_id), None) if watch.product_id else None
    relevant = [p for p in results if p.price_cents <= watch.target_cents]
    known = store.verdicts(watch.id)
    uncached = [p for p in relevant if p.product_id not in known]
    if uncached and verifier is not None:
        matches = verifier.matching_ids(watch.title, uncached)
        fresh = {p.product_id: p.product_id in matches for p in uncached}
        store.save_verdicts(watch.id, fresh)
        known.update(fresh)
    matched = [p for p in relevant if known.get(p.product_id) is True]

    cheapest = min(matched, key=lambda p: p.price_cents) if matched else None
    recorded = own or cheapest
    if recorded is not None:
        store.record_price(watch.id, recorded.price_cents, now)

    if cheapest is None:
        # Nothing verified at or below the target: re-arm until a matching offer returns.
        store.update_state(watch.id, False, True)
        return WatchOutcome((), None, own.price_cents if own else None)

    store.update_state(watch.id, True, True)
    if (
        watch.notified
        and watch.last_notified_cents is not None
        and cheapest.price_cents >= watch.last_notified_cents
    ):
        return WatchOutcome((), cheapest.price_cents, own.price_cents if own else None)
    store.set_last_notified(watch.id, cheapest.price_cents)
    lowest = store.price_summary(watch.id, now - LOWEST_PRICE_WINDOW).lowest_cents
    finding = Finding(
        FindingKind.TARGET_REACHED, watch, cheapest.price_cents, lowest, cheapest.url, cheapest.title
    )
    return WatchOutcome((finding,), cheapest.price_cents, own.price_cents if own else None)
