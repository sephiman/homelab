import argparse
import sys
import urllib.parse
from collections.abc import Sequence
from datetime import UTC, datetime

from price_hunt.aliexpress import AliExpressClient, Product
from price_hunt.checking import LOWEST_PRICE_WINDOW, Finding, FindingKind, check_watches
from price_hunt.errors import AliExpressError, InvalidInputError, PriceHuntError
from price_hunt.money import format_euros, parse_euros
from price_hunt.settings import load_settings
from price_hunt.watches import WatchStore

SEARCH_FOOTNOTE = (
    "AliExpress prices are read from the public site for delivery to NL, in EUR; shipping and VAT "
    "may not be included, so confirm on the product page."
)
AMAZON_SEARCHES = (("Amazon.nl", "https://www.amazon.nl/s?k="), ("Amazon.de", "https://www.amazon.de/s?k="))


def _product_line(product: Product) -> str:
    parts = [format_euros(product.price_cents)]
    if product.original_price_cents and product.original_price_cents > product.price_cents:
        parts[0] += f" (was {format_euros(product.original_price_cents)})"
    if product.rating:
        parts.append(f"★ {product.rating}")
    if product.sold is not None:
        parts.append(f"{product.sold:,} sold")
    return " · ".join(parts)


def _search(args: argparse.Namespace) -> int:
    keywords = " ".join(args.keywords)
    max_cents = parse_euros(args.max) if args.max else None
    client = AliExpressClient(load_settings())
    products = sorted(client.search(keywords, max_cents), key=lambda product: product.price_cents)
    if not products:
        print(f"AliExpress: nothing found for {keywords!r} delivering to NL.")
    for number, product in enumerate(products[: args.limit], start=1):
        print(
            f"{number}. [{product.product_id}] {_product_line(product)}\n   {product.title}\n   {product.url}"
        )
    print(f"\n{SEARCH_FOOTNOTE}")
    for name, base in AMAZON_SEARCHES:
        print(f"{name} search: {base}{urllib.parse.quote_plus(keywords)}")
    return 0


def _watch_add(args: argparse.Namespace) -> int:
    target = parse_euros(args.target)
    settings = load_settings()
    client = AliExpressClient(settings)
    product_id = client.resolve_product_id(args.product)
    product = client.lookup(product_id)
    if product is None:
        raise InvalidInputError(f"AliExpress returns no product {product_id} for delivery to NL")
    store = WatchStore(settings.db_path)
    now = datetime.now(UTC)
    watch = store.add(product, target, now)
    store.record_price(watch.id, product.price_cents, now)
    print(
        f"Watching #{watch.id}: {product.title}\n"
        f"now {format_euros(product.price_cents)}, target {format_euros(target)}, checked hourly\n"
        f"{product.url}"
    )
    if product.price_cents <= target:
        print("It is already at or below the target.")
    return 0


def _watch_list(_: argparse.Namespace) -> int:
    store = WatchStore(load_settings().db_path)
    watches = store.all()
    if not watches:
        print("No AliExpress price watches.")
    since = datetime.now(UTC) - LOWEST_PRICE_WINDOW
    for watch in watches:
        summary = store.price_summary(watch.id, since)
        now = format_euros(summary.latest_cents) if summary.latest_cents is not None else "unknown"
        lowest = format_euros(summary.lowest_cents) if summary.lowest_cents is not None else "unknown"
        flags = "".join(
            [
                " [target reached, notified]" if watch.notified else "",
                "" if watch.available else " [unavailable]",
            ]
        )
        print(
            f"#{watch.id} {watch.title}\n   now {now}, target {format_euros(watch.target_cents)}, "
            f"lowest 30 days {lowest}{flags}\n   {watch.url}"
        )
    return 0


def _watch_remove(args: argparse.Namespace) -> int:
    store = WatchStore(load_settings().db_path)
    if not store.remove(args.id):
        raise InvalidInputError(f"no watch #{args.id}")
    print(f"Removed watch #{args.id}.")
    return 0


def _finding_text(finding: Finding) -> str:
    watch = finding.watch
    if finding.kind is FindingKind.UNAVAILABLE:
        return (
            f"UNAVAILABLE #{watch.id}: {watch.title} is no longer offered for delivery to NL "
            f"(removed, sold out or not shipping here)\n{watch.url}"
        )
    assert finding.price_cents is not None
    lowest = f"; lowest in 30 days {format_euros(finding.lowest_cents)}" if finding.lowest_cents else ""
    return (
        f"TARGET REACHED #{watch.id}: {watch.title} is {format_euros(finding.price_cents)} "
        f"(target {format_euros(watch.target_cents)}{lowest})\n{watch.url}"
    )


def _check(_: argparse.Namespace) -> int:
    """Prints only what changed, so the cron job stays silent (and skips the model) on a quiet run."""
    settings = load_settings()
    store = WatchStore(settings.db_path)
    try:
        findings = check_watches(store, AliExpressClient(settings), datetime.now(UTC))
    except AliExpressError as error:
        # Reported once per distinct error, not every run while the API stays down.
        if store.last_error() != str(error):
            print(f"PRICE CHECK FAILED: {error}")
            store.set_last_error(str(error))
        return 0
    store.set_last_error(None)
    for finding in findings:
        print(_finding_text(finding))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price-hunt", description="AliExpress prices delivered to NL, in EUR"
    )
    commands = parser.add_subparsers(required=True)

    search = commands.add_parser("search", help="search AliExpress, cheapest first")
    search.add_argument("keywords", nargs="+")
    search.add_argument("--max", help="maximum price in euros")
    search.add_argument("--limit", type=int, default=10)
    search.set_defaults(run=_search)

    watch = commands.add_parser("watch", help="price-drop watches").add_subparsers(required=True)
    add = watch.add_parser("add", help="watch a product until it costs the target or less")
    add.add_argument("product", help="AliExpress product link or id")
    add.add_argument("target", help="target price in euros")
    add.set_defaults(run=_watch_add)
    watch.add_parser("list").set_defaults(run=_watch_list)
    remove = watch.add_parser("rm", help="stop watching")
    remove.add_argument("id", type=lambda text: int(text.lstrip("#")))
    remove.set_defaults(run=_watch_remove)

    commands.add_parser("check", help="check every watch (run by the cron job)").set_defaults(run=_check)
    return parser


def main(argv: Sequence[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        result: int = args.run(args)
    except PriceHuntError as error:
        print(f"price-hunt: {error}", file=sys.stderr)
        return 1
    return result
