import argparse
import contextlib
import sys
import urllib.parse
from collections.abc import Sequence
from datetime import UTC, datetime

from agent_context.chats import ChatError, Chats
from agent_context.llm import load_llm_config

from price_hunt.aliexpress import AliExpressClient, Product, item_url, product_id_in, search_url
from price_hunt.checking import LOWEST_PRICE_WINDOW, Finding, FindingKind, check_watch, check_watches
from price_hunt.errors import InvalidInputError, PriceHuntError, VerifierError
from price_hunt.llm import ListingVerifier
from price_hunt.money import format_euros, format_min_drop, parse_euros, parse_min_drop
from price_hunt.settings import load_settings
from price_hunt.watches import WatchStore

SEARCH_FOOTNOTE = (
    "AliExpress prices are read from the public site for delivery to NL, in EUR; shipping and VAT "
    "may not be included, so confirm on the product page."
)
AMAZON_SEARCHES = (
    ("Amazon.nl", "https://www.amazon.nl/s?k="),
    ("Amazon.de", "https://www.amazon.de/s?k="),
    ("Amazon.es", "https://www.amazon.es/s?k="),
    ("Amazon.fr", "https://www.amazon.fr/s?k="),
    ("Amazon.it", "https://www.amazon.it/s?k="),
)
EU_COMPARISON_SEARCHES = (
    ("Tweakers Pricewatch", "https://tweakers.net/pricewatch/zoeken/?keyword="),
    ("Idealo.nl", "https://www.idealo.nl/zoeken.html?q="),
    ("Idealo.de", "https://www.idealo.de/preisvergleich/MainSearchProductCategory.html?q="),
    ("Idealo.es", "https://www.idealo.es/resultados.html?q="),
)
CHAT_HELP = "Telegram chat id or name for the alerts (default: the chat you are in)"


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
    products = sorted(
        client.search(keywords, max_cents, sort=args.sort),
        key=lambda product: product.price_cents,
    )
    if not products:
        print(f"AliExpress: nothing found for {keywords!r} delivering to NL.")
    for number, product in enumerate(products[: args.limit], start=1):
        print(
            f"{number}. [{product.product_id}] {_product_line(product)}\n   {product.title}\n   {product.url}"
        )
    print(f"\n{SEARCH_FOOTNOTE}")
    encoded = urllib.parse.quote_plus(keywords)
    for name, base in AMAZON_SEARCHES + EU_COMPARISON_SEARCHES:
        print(f"{name} search: {base}{encoded}")
    return 0


def _watch_add(args: argparse.Namespace) -> int:
    target = parse_euros(args.target)
    min_drop_cents, min_drop_bps = parse_min_drop(args.min_drop) if args.min_drop else (None, None)
    chat = Chats.load().resolve(args.chat)
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
        watch = store.add(
            product_id,
            query or page_title,
            item_url(product_id),
            target,
            now,
            chat.id,
            min_drop_cents,
            min_drop_bps,
        )
        store.save_verdicts(watch.id, {product_id: True})
    else:
        watch = store.add(
            "", reference, search_url(reference), target, now, chat.id, min_drop_cents, min_drop_bps
        )

    drop_label = format_min_drop(min_drop_cents, min_drop_bps)
    drop_suffix = f", min drop {drop_label}" if drop_label else ""
    print(f"Alerts go to {chat.label}.")

    try:
        outcome = check_watch(store, client, verifier, watch, now)
    except PriceHuntError as error:
        # The watch stays: the hourly job retries the failed first check.
        print(
            f"Watching #{watch.id}: {watch.title}\n"
            f"now unknown, target {format_euros(target)}{drop_suffix}, checked hourly\n"
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
        f"now {now_text}, target {format_euros(target)}{drop_suffix}, checked hourly\n"
        f"{watch.url}"
    )
    if outcome.verifier_error is not None:
        print(f"First check had a warning ({outcome.verifier_error}); the hourly job will retry.")
    if outcome.cheapest_cents is not None and outcome.cheapest_cents <= target:
        print("It is already at or below the target.")
    return 0


def _watch_list(args: argparse.Namespace) -> int:
    chats = Chats.load()
    chat = None if args.all else chats.resolve(args.chat)
    store = WatchStore(load_settings().db_path)
    watches = store.all(chat.id if chat else None)
    if not watches:
        print(f"No AliExpress price watches{f' for {chat.label}' if chat else ''}.")
    since = datetime.now(UTC) - LOWEST_PRICE_WINDOW
    for watch in watches:
        summary = store.price_summary(watch.id, since)
        now = format_euros(summary.latest_cents) if summary.latest_cents is not None else "unknown"
        lowest = format_euros(summary.lowest_cents) if summary.lowest_cents is not None else "unknown"
        drop_label = format_min_drop(watch.min_drop_cents, watch.min_drop_bps)
        drop_text = f", min drop {drop_label}" if drop_label else ""
        flags = "".join(
            [
                " [target reached, notified]" if watch.notified else "",
                "" if watch.available else " [unavailable]",
            ]
        )
        owner = f"\n   alerts to {chats.chat(watch.chat_id).label}" if chat is None and watch.chat_id else ""
        print(
            f"#{watch.id} {watch.title}\n   now {now}, target {format_euros(watch.target_cents)}{drop_text}, "
            f"lowest 30 days {lowest}{flags}\n   {watch.url}{owner}"
        )
    elsewhere = len(store.all()) - len(watches)
    if elsewhere:
        print(f"{elsewhere} more in other chats: watch list --all")
    return 0


def _watch_target(args: argparse.Namespace) -> int:
    store = WatchStore(load_settings().db_path)
    existing = store.get(args.id)
    if existing is None:
        raise InvalidInputError(f"no watch #{args.id}")
    new_target = parse_euros(args.target)
    update_min_drop = args.min_drop is not None
    min_drop_cents, min_drop_bps = (
        parse_min_drop(args.min_drop) if args.min_drop is not None else (None, None)
    )
    updated = store.update_target(args.id, new_target, min_drop_cents, min_drop_bps, update_min_drop)
    assert updated is not None
    drop_label = format_min_drop(updated.min_drop_cents, updated.min_drop_bps)
    drop_text = f", min drop {drop_label}" if drop_label else ""
    print(
        f"Updated watch #{updated.id}: {updated.title}\n"
        f"target {format_euros(updated.target_cents)} (was {format_euros(existing.target_cents)})"
        f"{drop_text}"
    )
    return 0


def _watch_history(args: argparse.Namespace) -> int:
    store = WatchStore(load_settings().db_path)
    watch = store.get(args.id)
    if watch is None:
        raise InvalidInputError(f"no watch #{args.id}")
    stats = store.price_stats(watch.id, now_utc() - LOWEST_PRICE_WINDOW)
    latest = format_euros(stats.latest_cents) if stats.latest_cents is not None else "unknown"
    lowest = format_euros(stats.lowest_cents) if stats.lowest_cents is not None else "unknown"
    highest = format_euros(stats.highest_cents) if stats.highest_cents is not None else "unknown"
    drop_label = format_min_drop(watch.min_drop_cents, watch.min_drop_bps)
    drop_text = f", min drop {drop_label}" if drop_label else ""
    print(
        f"#{watch.id} {watch.title}\n"
        f"   now {latest}, target {format_euros(watch.target_cents)}{drop_text}\n"
        f"   30 days: lowest {lowest}, highest {highest} ({stats.count} checks)\n"
        f"   {watch.url}"
    )
    points = store.price_history(watch.id, limit=args.limit)
    if points:
        print("Recent prices:")
        for checked_at, price_cents in points:
            print(f"   {checked_at[:16].replace('T', ' ')}  {format_euros(price_cents)}")
    verdicts = store.verdicts(watch.id)
    if verdicts:
        matched_ids = sorted(pid for pid, ok in verdicts.items() if ok)
        rejected_ids = sorted(pid for pid, ok in verdicts.items() if not ok)
        if matched_ids:
            print(f"Matched listings ({len(matched_ids)}): {', '.join(matched_ids)}")
        if rejected_ids:
            print(f"Rejected listings ({len(rejected_ids)}): {', '.join(rejected_ids)}")
    return 0


def _watch_reject(args: argparse.Namespace) -> int:
    store = WatchStore(load_settings().db_path)
    watch = store.get(args.id)
    if watch is None:
        raise InvalidInputError(f"no watch #{args.id}")
    product_id = product_id_in(args.product)
    if product_id is None:
        raise InvalidInputError(f"not an AliExpress product id or link: {args.product!r}")
    store.save_verdicts(watch.id, {product_id: False})
    print(f"Rejected listing {product_id} for watch #{watch.id}.")
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
    if finding.kind is FindingKind.LOWER_PRICE:
        previous = f"previous low {format_euros(finding.lowest_cents)}, " if finding.lowest_cents else ""
        text = (
            f"LOWER PRICE #{watch.id}: {watch.title} is {format_euros(finding.price_cents)} "
            f"({previous}target {format_euros(watch.target_cents)})"
        )
    else:
        lowest = f"; lowest in 30 days {format_euros(finding.lowest_cents)}" if finding.lowest_cents else ""
        text = (
            f"TARGET REACHED #{watch.id}: {watch.title} is {format_euros(finding.price_cents)} "
            f"(target {format_euros(watch.target_cents)}{lowest})"
        )
    if finding.offer_url and finding.offer_url != watch.url:
        return f"{text}\ncheapest offer: {finding.offer_title}\n{finding.offer_url}"
    return f"{text}\n{watch.url}"


def _check(args: argparse.Namespace) -> int:
    """Prints only what changed, so the cron job stays silent (and skips the model) on a quiet run."""
    chats = Chats.load()
    chat = chats.resolve(args.chat)
    settings = load_settings()
    store = WatchStore(settings.db_path)
    if chat == chats.default:
        store.adopt_unowned(chat.id)
    now = now_utc()
    findings, check_error = check_watches(store, AliExpressClient(settings), build_verifier(), now, chat.id)

    for finding in findings:
        print(_finding_text(finding))

    if check_error is not None:
        # Re-reported once every 24h if it stays broken; silent in between so it doesn't spam.
        if store.should_report_error(chat.id, str(check_error), now):
            print(f"PRICE CHECK FAILED: {check_error}")
            store.set_last_error(chat.id, str(check_error), now)
        return 0

    store.clear_last_error(chat.id)
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
    search.add_argument("--sort", choices=("best", "price"), default="best", help="AliExpress sort order")
    search.set_defaults(run=_search)

    watch = commands.add_parser("watch", help="price-drop watches").add_subparsers(required=True)
    add = watch.add_parser(
        "add", help="watch the cheapest offer of a product until it costs the target or less"
    )
    add.add_argument("product", help="product name, AliExpress product link or id")
    add.add_argument("target", help="target price in euros")
    add.add_argument(
        "--min-drop",
        dest="min_drop",
        help="minimum drop for new-low alerts above target (e.g. '5', '2.50', or '5%%')",
    )
    add.add_argument("--chat", help=CHAT_HELP)
    add.set_defaults(run=_watch_add)
    listing = watch.add_parser("list", help="this chat's watches")
    listing.add_argument("--chat", help=CHAT_HELP)
    listing.add_argument("--all", action="store_true", help="the watches of every chat")
    listing.set_defaults(run=_watch_list)
    target_cmd = watch.add_parser("target", help="update target price without losing history")
    target_cmd.add_argument("id", type=lambda text: int(text.lstrip("#")))
    target_cmd.add_argument("target", help="new target price in euros")
    target_cmd.add_argument(
        "--min-drop",
        dest="min_drop",
        help="update minimum drop for new-low alerts ('5', '5%%', or 'none')",
    )
    target_cmd.set_defaults(run=_watch_target)
    history = watch.add_parser("history", help="show 30-day price history and verified listings")
    history.add_argument("id", type=lambda text: int(text.lstrip("#")))
    history.add_argument("--limit", type=int, default=15)
    history.set_defaults(run=_watch_history)
    reject = watch.add_parser("reject", help="mark a listing as not matching this watch")
    reject.add_argument("id", type=lambda text: int(text.lstrip("#")))
    reject.add_argument("product", help="AliExpress product id or link")
    reject.set_defaults(run=_watch_reject)
    remove = watch.add_parser("rm", help="stop watching")
    remove.add_argument("id", type=lambda text: int(text.lstrip("#")))
    remove.set_defaults(run=_watch_remove)

    check = commands.add_parser("check", help="check a chat's watches (run by its scheduled job)")
    check.add_argument("--chat", help=CHAT_HELP)
    check.set_defaults(run=_check)
    return parser


def main(argv: Sequence[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        result: int = args.run(args)
    except (PriceHuntError, ChatError) as error:
        print(f"price-hunt: {error}", file=sys.stderr)
        return 1
    return result
