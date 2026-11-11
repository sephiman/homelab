from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeAliExpress

import price_hunt.cli as cli_module
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
    assert "no listing 999" in err


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


def test_watch_add_takes_the_id_from_www_and_http_item_links(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("1005006789", "15.20")

    for reference in (
        "www.aliexpress.com/item/1005006789.html",
        "http://www.aliexpress.com/item/1005006789.html",
    ):
        code, out, _ = run(capsys, "watch", "add", reference, "14")

        assert code == 0
        assert "Watching #1: Shelly Plus 1PM\nnow €15.20, target €14.00" in out
        run(capsys, "watch", "rm", "#1")


def test_watch_add_keeps_the_watch_when_the_first_check_fails(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "15.20")
    aliexpress.search_error = "blocked"

    code, out, _ = run(capsys, "watch", "add", "111", "14")

    assert code == 0
    assert "Watching #1: Shelly Plus 1PM" in out
    assert "First check failed (blocked by AliExpress anti-bot protection)" in out
    _, listing, _ = run(capsys, "watch", "list")
    assert "#1 Shelly Plus 1PM" in listing


def test_watch_add_by_name_tracks_the_cheapest_verified_offer(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")
    aliexpress.offer("222", "2.10", "Wall bracket mount for Shelly")
    aliexpress.verifier.verdicts["222"] = False

    code, out, _ = run(capsys, "watch", "add", "shelly plus 1pm", "14")

    assert code == 0
    assert "Watching #1: shelly plus 1pm\nnow €13.49 (cheapest verified), target €14.00" in out
    _, listing, _ = run(capsys, "watch", "list")
    assert "now €13.49" in listing and "lowest 30 days €13.49" in listing
    assert len(aliexpress.verifier.calls) == 1


def test_check_messages_once_when_a_cheaper_offer_appears(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")
    run(capsys, "watch", "add", "shelly plus 1pm", "14")

    aliexpress.offer("333", "11.90", "Shelly Plus 1PM EU warehouse stock")
    _, second, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")

    assert second.startswith("TARGET REACHED #1: shelly plus 1pm is €11.90 (target €14.00")
    assert "cheapest offer: Shelly Plus 1PM EU warehouse stock" in second
    assert "/item/333.html" in second
    assert repeated == ""


def test_check_verifies_each_new_cheap_listing_once(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")
    aliexpress.verifier.verdicts["222"] = False
    run(capsys, "watch", "add", "shelly plus 1pm", "14")
    assert len(aliexpress.verifier.calls) == 1

    aliexpress.offer("444", "1.99", "Shelly Plus 1PM screwdriver bit")
    aliexpress.verifier.verdicts["444"] = False
    _, first, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")

    assert first == ""
    assert repeated == ""
    assert len(aliexpress.verifier.calls) == 2


def test_watch_add_by_link_asks_the_model_for_the_product_name(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    title = "SONOFF ZBMINIR2 Extreme Zigbee Switch Detach Relay Mode"
    aliexpress.offer("555", "12.90", title)
    aliexpress.verifier.renames[title] = "SONOFF ZBMINIR2"

    code, out, _ = run(capsys, "watch", "add", "https://www.aliexpress.com/item/555.html", "14")

    assert code == 0
    assert "Watching #1: SONOFF ZBMINIR2" in out
    assert aliexpress.verifier.name_calls == [title]


def test_check_reports_verifier_failure_once(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")
    run(capsys, "watch", "add", "shelly plus 1pm", "14")
    aliexpress.offer("666", "4.99", "USB cable for Shelly")
    aliexpress.verifier.error = "llm down"

    _, first, _ = run(capsys, "check")
    _, repeated, _ = run(capsys, "check")

    assert first == "PRICE CHECK FAILED: llm down\n"
    assert repeated == ""


def test_check_records_own_price_and_checks_all_watches_when_verifier_fails(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str]
) -> None:
    aliexpress.offer("111", "447.46", "Gaming Console")
    run(capsys, "watch", "add", "111", "360")

    # A cheap accessory appears below target, tripping the verifier.
    aliexpress.offer("222", "15.00", "Controller grips for console")
    aliexpress.verifier.error = "llm 403"

    # Add a second watch that will reach its target during check.
    aliexpress.offer("333", "15.00", "Shelly Plus 1PM")
    run(capsys, "watch", "add", "333", "14")

    # Update 111's price, and drop 333 below its target.
    aliexpress.offer("111", "445.00", "Gaming Console")
    aliexpress.offer("333", "12.00")
    code, out, _ = run(capsys, "check")

    assert code == 0
    assert "TARGET REACHED #2: Shelly Plus 1PM is €12.00" in out
    assert "PRICE CHECK FAILED: llm 403" in out

    # Watch #1 must have recorded the updated price despite the verifier error.
    _, listing, _ = run(capsys, "watch", "list")
    assert "#1 Gaming Console\n   now €445.00" in listing


def test_check_rereports_persistent_error_after_24_hours(
    aliexpress: FakeAliExpress, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")
    run(capsys, "watch", "add", "shelly plus 1pm", "14")
    aliexpress.offer("666", "4.99", "USB cable for Shelly")
    aliexpress.verifier.error = "llm down"

    base_time = datetime(2026, 11, 11, 10, 0, tzinfo=UTC)
    monkeypatch.setattr(cli_module, "now_utc", lambda: base_time)
    _, first, _ = run(capsys, "check")
    assert first == "PRICE CHECK FAILED: llm down\n"

    # 1 hour later: silenced to avoid spam
    monkeypatch.setattr(cli_module, "now_utc", lambda: base_time + timedelta(hours=1))
    _, second, _ = run(capsys, "check")
    assert second == ""

    # 25 hours later: re-reported so persistent failure is not invisible
    monkeypatch.setattr(cli_module, "now_utc", lambda: base_time + timedelta(hours=25))
    _, third, _ = run(capsys, "check")
    assert third == "PRICE CHECK FAILED: llm down\n"


def test_check_without_verifier_still_watches_the_exact_listing(
    aliexpress: FakeAliExpress,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "build_verifier", lambda: None)
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")

    code, out, _ = run(capsys, "watch", "add", "111", "14")
    assert code == 0
    assert "now €13.49, target €14.00" in out

    aliexpress.offer("111", "12.20")
    _, finding, _ = run(capsys, "check")
    assert finding.startswith("TARGET REACHED #1: Shelly Plus 1PM is €12.20")


def test_check_without_verifier_skips_unverified_name_watches(
    aliexpress: FakeAliExpress,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli_module, "build_verifier", lambda: None)
    aliexpress.offer("111", "13.49", "Shelly Plus 1PM")

    run(capsys, "watch", "add", "shelly plus 1pm", "14")
    _, out, _ = run(capsys, "check")

    assert out == ""
