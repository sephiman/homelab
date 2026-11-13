import pytest
from conftest import FAMILY_CHAT, OWNER_CHAT, FakeWeb, concert_page

from page_watch.cli import main


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def watch_concert(web: FakeWeb, capsys: pytest.CaptureFixture[str], *options: str) -> str:
    url = web.page("/agenda", concert_page())
    code, out, err = run(capsys, "add", url, "tickets for The Band on 14 March are on sale", *options)
    assert code == 0, err
    return out


def check(web: FakeWeb, capsys: pytest.CaptureFixture[str], chat: str = OWNER_CHAT, **later: float) -> str:
    web.advance(**later)
    code, out, err = run(capsys, "check", "--chat", chat)
    assert code == 0, err
    return out


def test_add_reads_the_page_once_and_says_what_it_watches(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    out = watch_concert(web, capsys)

    assert out.startswith("Watching #1: tickets for The Band on 14 March are on sale\n")
    assert "now: Tickets are not on sale yet." in out
    assert 'checked every 1h, looking at "The Band - 14 March 2027"' in out
    assert f"Alerts go to {OWNER_CHAT}." in out
    assert len(web.model_calls) == 1
    assert "Buy tickets" not in web.model_calls[0], "script contents must not reach the model"


def test_check_stays_silent_and_skips_the_model_while_the_page_is_unchanged(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)

    outputs = [check(web, capsys, hours=1) for _ in range(3)]

    assert outputs == ["", "", ""]
    assert len(web.model_calls) == 1


def test_check_reports_ready_once_when_the_page_shows_the_condition(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)
    web.page("/agenda", concert_page(status="Buy tickets"))

    ready = check(web, capsys, hours=1)
    after = check(web, capsys, hours=1)
    _, listing, _ = run(capsys, "list")

    assert ready == (
        f"READY #1: tickets for The Band on 14 March are on sale\nTickets are on sale.\n{web.url}/agenda\n"
    )
    assert after == ""
    assert "met 2026-11-13 11:00" in listing


def test_check_ignores_changes_away_from_the_watched_part(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)
    web.page("/agenda", concert_page(footer="© 2027 Venue · 1,234 visitors today"))

    assert check(web, capsys, hours=1) == ""
    assert len(web.model_calls) == 1


def test_check_skips_the_model_while_the_waiting_wording_is_still_there(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)
    web.page("/agenda", concert_page(extra="<p>Doors 20:00</p>"))

    assert check(web, capsys, hours=1) == ""
    assert len(web.model_calls) == 1


def test_check_asks_the_model_once_per_version_of_the_page(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)
    web.page("/agenda", concert_page(status="Presale for members only"))
    check(web, capsys, hours=1)
    web.page("/agenda", concert_page())
    check(web, capsys, hours=1)
    web.page("/agenda", concert_page(status="Presale for members only"))

    assert check(web, capsys, hours=1) == ""
    assert len(web.model_calls) == 2


def test_check_waits_for_the_interval_and_the_start_time(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys, "--every", "30m", "--from", "2026-11-14 10:00")
    fetches_after_add = len(web.fetches)

    check(web, capsys, hours=23, minutes=58)  # 09:58 Madrid: two minutes early
    early = len(web.fetches)
    check(web, capsys, minutes=2)
    first = len(web.fetches)
    check(web, capsys, minutes=10)
    too_soon = len(web.fetches)
    check(web, capsys, minutes=20)

    assert [early, first, too_soon, len(web.fetches)] == [
        fetches_after_add,
        fetches_after_add + 1,
        fetches_after_add + 1,
        fetches_after_add + 2,
    ]


def test_check_reports_a_failing_page_once_after_three_failures(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys)
    web.page("/agenda", "<html>Forbidden</html>", status=403)

    outputs = [check(web, capsys, hours=1) for _ in range(4)]

    assert outputs[:2] == ["", ""]
    assert outputs[2].startswith("FAILING #1: tickets for The Band on 14 March are on sale\n")
    assert "the last 3 checks failed: HTTP 403 from 127.0.0.1: the site blocks scripts" in outputs[2]
    assert outputs[3] == ""


def test_check_reports_a_page_that_changes_on_every_check_once(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    watch_concert(web, capsys, "--every", "5m")
    outputs = []
    for viewers in range(8):
        web.page("/agenda", concert_page(status=f"Presale: {viewers} people in the queue"))
        outputs.append(check(web, capsys, minutes=5))

    noisy = [out for out in outputs if out]
    assert len(noisy) == 1 and noisy[0].startswith("NOISY #1:")
    assert "each costing a model call" in noisy[0]


def test_add_refuses_a_site_that_blocks_scripts(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    url = web.page("/tickets", "<html><title>Just a moment...</title><p>Verify you are human</p></html>")

    code, _, err = run(capsys, "add", url, "tickets on sale")

    assert code == 1
    assert "anti-bot page" in err
    assert run(capsys, "list", "--all")[1] == "No page watches or reminders.\n"


def test_add_refuses_a_page_that_needs_javascript(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    url = web.page("/app", "<html><body><div id='root'></div><script>render()</script></body></html>")

    code, _, err = run(capsys, "add", url, "tickets on sale")

    assert code == 1
    assert "almost no text without JavaScript" in err
    assert web.model_calls == []


def test_add_does_not_watch_what_is_already_met(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    url = web.page("/agenda", concert_page(status="Buy tickets"))

    code, out, _ = run(capsys, "add", url, "tickets on sale")

    assert code == 0
    assert out.startswith("ALREADY MET: Tickets are on sale.")
    assert run(capsys, "list")[1] == f"No page watches or reminders for {OWNER_CHAT}.\n"


def test_add_fails_cleanly_when_the_model_reply_is_not_json(
    web: FakeWeb, capsys: pytest.CaptureFixture[str]
) -> None:
    url = web.page("/agenda", concert_page())
    web.raw_reply = "I think so"

    code, _, err = run(capsys, "add", url, "tickets on sale")

    assert code == 1
    assert "no JSON object" in err


def test_watches_alert_only_the_chat_that_asked(
    web: FakeWeb, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", FAMILY_CHAT)
    out = watch_concert(web, capsys)
    monkeypatch.delenv("HERMES_SESSION_CHAT_ID")
    web.page("/agenda", concert_page(status="Buy tickets"))

    owner = check(web, capsys, OWNER_CHAT, hours=1)
    family = check(web, capsys, FAMILY_CHAT)
    _, owner_list, _ = run(capsys, "list")

    assert f"Alerts go to Family ({FAMILY_CHAT})." in out
    assert owner == ""
    assert family.startswith("READY #1")
    assert owner_list.endswith("1 more in other chats: list --all\n")


def test_add_refuses_a_chat_name_it_does_not_know(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    url = web.page("/agenda", concert_page())

    code, _, err = run(capsys, "add", url, "tickets on sale", "--chat", "cousins")

    assert code == 1
    assert "no chat is named 'cousins'" in err


def test_reminder_is_sent_once_at_its_local_time(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "remind", "2026-11-13 12:00", "book", "the", "tickets", "--chat", "Family")

    early = check(web, capsys, FAMILY_CHAT, minutes=50)  # 10:50 Madrid
    due = check(web, capsys, FAMILY_CHAT, hours=1, minutes=10)
    again = check(web, capsys, FAMILY_CHAT, minutes=5)

    assert code == 0
    assert out == f"Reminder R1 at 2026-11-13 12:00: book the tickets\nIt goes to Family ({FAMILY_CHAT}).\n"
    assert (early, due, again) == ("", "REMINDER: book the tickets\n", "")


def test_remind_refuses_a_time_in_the_past(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "remind", "2026-11-13 09:00", "too late")

    assert code == 1
    assert "already past" in err


def test_rm_stops_a_watch_and_cancels_a_reminder(web: FakeWeb, capsys: pytest.CaptureFixture[str]) -> None:
    watch_concert(web, capsys)
    run(capsys, "remind", "2026-12-01", "check the presale")

    removed = [run(capsys, "rm", ref)[1] for ref in ("#1", "r1")]
    code, _, err = run(capsys, "rm", "1")

    assert removed == ["Removed #1.\n", "Removed R1.\n"]
    assert (code, err) == (1, "page-watch: no watch #1\n")
    assert run(capsys, "list")[1] == f"No page watches or reminders for {OWNER_CHAT}.\n"
