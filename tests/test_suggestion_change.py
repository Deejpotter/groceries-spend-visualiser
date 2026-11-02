"""Flag when the savings suggestion moved since the last payday."""

from conftest import login
from services.savings_advice import make_txn, suggestion_change
from test_savings_advice import _history, _store


def _advice(costs, savings, long_term):
    return {"costs": costs, "savings": savings, "long_term": long_term}


def test_small_moves_are_not_flagged():
    assert suggestion_change(_advice(1000, 400, 400), _advice(1050, 380, 380)) is None


def test_big_moves_are_flagged():
    change = suggestion_change(_advice(1000, 400, 400), _advice(1300, 160, 160))
    labels = {c["label"]: c for c in change["items"]}
    assert labels["Usual costs"]["delta"] == 300 and labels["Usual costs"]["percent"] == 30
    assert labels["Suggested savings"]["delta"] == -480 and labels["Suggested savings"]["percent"] == -60


def test_from_zero_savings():
    change = suggestion_change(_advice(3000, 0, 0), _advice(2000, 400, 390))
    saved = [c for c in change["items"] if c["label"] == "Suggested savings"][0]
    assert saved["percent"] is None and saved["delta"] == 790


def test_missing_side():
    assert suggestion_change(None, _advice(1, 1, 1)) is None
    assert suggestion_change(_advice(1, 1, 1), None) is None


def test_fortnight_page_shows_the_change(client, db):
    login(client)
    rows = _history() + [make_txn("2026-10-30", 2500, "HARVEY NORMAN FRANKSTON")]
    _store(db, rows)
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Changed since your last payday" in html
    assert "Usual costs up" in html


def test_fortnight_page_quiet_when_steady(client, db):
    login(client)
    _store(db, _history())
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Changed since your last payday" not in html
