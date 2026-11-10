import pytest
from conftest import FakeAliExpress

from price_hunt.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_search_lists_offers_cheapest_first_with_amazon_links(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20", "Shelly Plus 1PM original")
    aliexpress.offer("222", "9.84", "Shelly Plus 1PM Mini")

    code, out, _ = run(capsys, "search", "shelly", "plus", "1pm")

    assert code == 0
    assert out.index("€9.84") < out.index("€15.20")
    assert "1. [222] €9.84 (was €19.99) · ★ 4.9 · 1,234 sold" in out
    assert "https://www.amazon.nl/s?k=shelly+plus+1pm" in out
    request = aliexpress.requests[0]
    assert request["kind"] == "search"
    assert request["slug"] == "shelly-plus-1pm"


def test_search_sends_max_price_in_euros(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "search", "relay", "--max", "12,50")

    assert aliexpress.requests[0]["maxPrice"] == "12.50"


def test_search_reports_nothing_found(aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "search", "unobtainium")

    assert code == 0
    assert "nothing found for 'unobtainium'" in out


def test_search_without_item_list_reports_nothing_found(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.no_item_list = True

    code, out, _ = run(capsys, "search", "obscure")

    assert code == 0
    assert "nothing found for 'obscure'" in out


def test_watch_add_takes_the_id_from_an_item_link(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("1005006789", "15.20")

    code, out, _ = run(capsys, "watch", "add", "https://www.aliexpress.com/item/1005006789.html?spm=x", "14")

    assert code == 0
    assert "Watching #1: Shelly Plus 1PM\nnow €15.20, target €14.00" in out
    _, listing, _ = run(capsys, "watch", "list")
    assert "#1 Shelly Plus 1PM\n   now €15.20, target €14.00, lowest 30 days €15.20" in listing


def test_watch_add_rejects_a_product_not_offered_to_nl(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, err = run(capsys, "watch", "add", "999", "14")

    assert code == 1
    assert "no product 999 for delivery to NL" in err


def test_check_is_silent_while_the_price_stays_above_target(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20")
    run(capsys, "watch", "add", "111", "14")

    code, out, _ = run(capsys, "check")

    assert code == 0
    assert out == ""


def test_check_reports_a_drop_once_and_again_after_the_price_rises(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20")
    run(capsys, "watch", "add", "111", "14")

    aliexpress.offer("111", "13.49")
    _, first, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")
    aliexpress.offer("111", "16.00")
    run(capsys, "check")
    aliexpress.offer("111", "13.90")
    _, again, _ = run(capsys, "check")

    assert first.startswith(
        "TARGET REACHED #1: Shelly Plus 1PM is €13.49 (target €14.00; lowest in 30 days €13.49)"
    )
    assert repeated == ""
    assert again.startswith(
        "TARGET REACHED #1: Shelly Plus 1PM is €13.90 (target €14.00; lowest in 30 days €13.49)"
    )


def test_check_reports_a_product_that_disappears_once(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20")
    run(capsys, "watch", "add", "111", "14")
    del aliexpress.products["111"]

    _, first, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")

    assert first.startswith("UNAVAILABLE #1: Shelly Plus 1PM is no longer offered for delivery to NL")
    assert repeated == ""


def test_check_reports_the_same_anti_bot_error_once(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20")
    run(capsys, "watch", "add", "111", "14")
    aliexpress.error = "blocked"

    _, first, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")

    assert first == "PRICE CHECK FAILED: blocked by AliExpress anti-bot protection\n"
    assert repeated == ""


def test_watch_rm_stops_the_watch(aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]) -> None:
    aliexpress.offer("111", "15.20")
    run(capsys, "watch", "add", "111", "14")

    code, out, _ = run(capsys, "watch", "rm", "#1")

    assert code == 0
    assert out == "Removed watch #1.\n"
    _, listing, _ = run(capsys, "watch", "list")
    assert listing == "No AliExpress price watches.\n"
