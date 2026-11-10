from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from price_hunt.aliexpress import AliExpressClient
from price_hunt.watches import Watch, WatchStore

LOWEST_PRICE_WINDOW = timedelta(days=30)


class FindingKind(Enum):
    TARGET_REACHED = "target reached"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class WatchState:
    notified: bool
    available: bool


@dataclass(frozen=True)
class Finding:
    kind: FindingKind
    watch: Watch
    price_cents: int | None = None
    lowest_cents: int | None = None


def next_state(watch: Watch, price_cents: int | None) -> tuple[WatchState, FindingKind | None]:
    """Each watch notifies once per drop: rising back above the target re-arms it."""
    if price_cents is None:
        finding = FindingKind.UNAVAILABLE if watch.available else None
        return WatchState(notified=watch.notified, available=False), finding
    if price_cents > watch.target_cents:
        return WatchState(notified=False, available=True), None
    if watch.notified:
        return WatchState(notified=True, available=True), None
    return WatchState(notified=True, available=True), FindingKind.TARGET_REACHED


def check_watches(store: WatchStore, client: AliExpressClient, now: datetime) -> list[Finding]:
    watches = store.all()
    if not watches:
        return []
    known = {watch.product_id: watch.title for watch in watches}
    prices = {product.product_id: product.price_cents for product in client.products(known)}
    findings: list[Finding] = []
    for watch in watches:
        price = prices.get(watch.product_id)
        if price is not None:
            store.record_price(watch.id, price, now)
        state, kind = next_state(watch, price)
        store.update_state(watch.id, state.notified, state.available)
        if kind is not None:
            lowest = store.price_summary(watch.id, now - LOWEST_PRICE_WINDOW).lowest_cents
            findings.append(Finding(kind, watch, price, lowest))
    return findings
