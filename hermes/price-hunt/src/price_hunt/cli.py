import argparse
import contextlib
import sys
import urllib.parse
from collections.abc import Sequence
from datetime import UTC, datetime

from price_hunt.aliexpress import AliExpressClient, Product, item_url, search_url
from price_hunt.checking import LOWEST_PRICE_WINDOW, Finding, FindingKind, check_watch, check_watches
from price_hunt.errors import InvalidInputError, PriceHuntError, VerifierError
from price_hunt.llm import ListingVerifier, load_llm_config
from price_hunt.money import format_euros, parse_euros
from price_hunt.settings import load_settings
from price_hunt.watches import WatchStore

SEARCH_FOOTNOTE = (
    "AliExpress prices are read from the public site for delivery to NL, in EUR; shipping and VAT "
    "may not be included, so confirm on the product page."
)
AMAZON_SEARCHES = (("Amazon.nl", "https://www.amazon.nl/s?k="), ("Amazon.de", "https://www.amazon.de/s?k="))


def build_verifier() -> ListingVerifier | None:
    """Hermes' own model as the listing verifier; None when unconfigured (watches degrade)."""
    config = load_llm_config()
    return ListingVerifier(config) if config is not None else None


def now_utc() -> datetime:
    return datetime.now(UTC)


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
    verifier = build_verifier()
    store = WatchStore(settings.db_path)
    now = datetime.now(UTC)

    reference = args.product.strip()
    if not reference:
        raise InvalidInputError("name a product or paste an AliExpress link")
    if reference.isdigit() or reference.startswith(("https://", "http://", "www.")):
        product_id = client.resolve_product_id(reference)
        page_title = client.page_title(product_id)
        if page_title is None:
            raise InvalidInputError(
                f"AliExpress returns no listing {product_id}: paste the /item/<id>.html link"
            )
        query = None
        if verifier is not None:
            with contextlib.suppress(VerifierError):
                query = verifier.product_name(page_title)
        watch = store.add(product_id, query or page_title, item_url(product_id), target, now)
        store.save_verdicts(watch.id, {product_id: True})
    else:
        watch = store.add("", reference, search_url(reference), target, now)

    try:
        outcome = check_watch(store, client, verifier, watch, now)
    except PriceHuntError as error:
        # The watch stays: the hourly job retries the failed first check.
        print(
            f"Watching #{watch.id}: {watch.title}\n"
            f"now unknown, target {format_euros(target)}, checked hourly\n"
            f"{watch.url}\n"
            f"First check failed ({error}); the hourly job will retry."
        )
        return 0
    if watch.product_id and outcome.own_cents is not None:
        now_text = format_euros(outcome.own_cents)
    elif outcome.cheapest_cents is not None:
        now_text = f"{format_euros(outcome.cheapest_cents)} (cheapest verified)"
    else:
        now_text = "unknown"
    print(
        f"Watching #{watch.id}: {watch.title}\n"
        f"now {now_text}, target {format_euros(target)}, checked hourly\n"
        f"{watch.url}"
    )
    if outcome.verifier_error is not None:
        print(f"First check had a warning ({outcome.verifier_error}); the hourly job will retry.")
    if outcome.cheapest_cents is not None:
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
    text = (
        f"TARGET REACHED #{watch.id}: {watch.title} is {format_euros(finding.price_cents)} "
        f"(target {format_euros(watch.target_cents)}{lowest})"
    )
    if finding.offer_url and finding.offer_url != watch.url:
        return f"{text}\ncheapest offer: {finding.offer_title}\n{finding.offer_url}"
    return f"{text}\n{watch.url}"


def _check(_: argparse.Namespace) -> int:
    """Prints only what changed, so the cron job stays silent (and skips the model) on a quiet run."""
    settings = load_settings()
    store = WatchStore(settings.db_path)
    now = now_utc()
    try:
        findings, verifier_error = check_watches(store, AliExpressClient(settings), build_verifier(), now)
    except PriceHuntError as error:
        # Re-reported once every 24h if it stays broken; silent in between so it doesn't spam.
        if store.should_report_error(str(error), now):
            print(f"PRICE CHECK FAILED: {error}")
            store.set_last_error(str(error), now)
        return 0

    for finding in findings:
        print(_finding_text(finding))

    if verifier_error is not None:
        if store.should_report_error(str(verifier_error), now):
            print(f"PRICE CHECK FAILED: {verifier_error}")
            store.set_last_error(str(verifier_error), now)
        return 0

    store.clear_last_error()
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
    add = watch.add_parser(
        "add", help="watch the cheapest offer of a product until it costs the target or less"
    )
    add.add_argument("product", help="product name, AliExpress product link or id")
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
