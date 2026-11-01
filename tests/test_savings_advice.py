"""Suggested savings per payday from bank history."""

from datetime import date, timedelta

from conftest import login
from services.savings_advice import (
    cash_flow,
    is_move,
    make_txn,
    parse_accounts,
    suggest_savings,
    transfer_account,
)

PAY = {"merchant_key": "acme payroll", "frequency": "fortnightly", "amount": 3000.0}


def _history(days=84, groceries=300.0, spending=700.0, pay=3000.0, extra=()):
    """Fortnightly pay, groceries and spending over `days` days ending 2026-10-30."""
    end = date(2026, 10, 30)
    rows = []
    for n in range(0, days, 14):
        day = (end - timedelta(days=n)).isoformat()
        rows.append(make_txn(day, pay, "ACME PAYROLL", "credit", merchant_key="acme payroll"))
        rows.append(make_txn(day, groceries, "WOOLWORTHS 1234", status="grocery"))
        rows.append(make_txn(day, spending, "BUNNINGS 552"))
    first = (end - timedelta(days=days - 1)).isoformat()
    rows.append(make_txn(first, 1, "BANK FEE", status="other"))
    return rows + list(extra)


def test_transfer_account_reads_commbank_wording():
    assert transfer_account("Transfer to xx9910 CommBank app") == {"way": "to", "account": "9910"}
    assert transfer_account("Transfer From xx1139 NetBank") == {"way": "from", "account": "1139"}
    assert transfer_account("Fast Transfer From J Smith rent") is None


def test_is_move_covers_own_accounts_and_known_patterns():
    assert is_move("Transfer to xx9781 CommBank app")
    assert is_move("Transfer to savings")
    assert not is_move("BUNNINGS 552")


def test_parse_accounts():
    assert parse_accounts("9910, xx1139") == ["9910", "1139"]
    assert parse_accounts("") == []


def test_cash_flow_splits_rows():
    rows = _history(extra=[
        make_txn("2026-10-20", 500, "Transfer to xx9910 CommBank app"),
        make_txn("2026-10-25", 200, "Transfer from xx9910 CommBank app", "credit"),
        make_txn("2026-10-26", 50, "Transfer from xx4444 NetBank", "credit"),
    ])
    flow = cash_flow(rows, "acme payroll", ["9910"])
    assert flow["days"] == 84
    assert flow["pay"] == 18000
    assert flow["groceries"] == 1800
    assert flow["spending"] == 4201
    assert flow["moved_out"] == 500
    assert flow["from_savings"] == 200
    assert flow["other_in"] == 0
    assert flow["top_spending"][0] == {"name": "bunnings", "amount": 4200, "example": "BUNNINGS 552"}


def test_cash_flow_ignores_rows_outside_window():
    rows = _history() + [make_txn("2025-01-01", 9999, "OLD THING")]
    assert cash_flow(rows, "acme payroll")["spending"] == 4201


def test_cash_flow_empty():
    assert cash_flow([], None) is None


def test_suggest_savings_from_surplus():
    flow = cash_flow(_history(), "acme payroll")
    advice = suggest_savings(flow, PAY, monthly_bills=0)
    assert advice["income"] == 3000
    assert advice["groceries"] == 300
    assert round(advice["costs"]) == 1000
    assert round(advice["surplus"]) == 2000
    # 80% of the $2,000 surplus, split in half and rounded down to $10.
    assert advice["long_term"] == 790
    assert advice["savings"] == 800
    assert advice["save_share"] == 53
    assert advice["benchmark"] == 600
    assert advice["benchmark_gap"] == 0
    assert advice["enough_history"]
    assert not advice["overspending"]


def test_bills_count_once_when_also_in_bank_rows():
    flow = cash_flow(_history(), "acme payroll")
    monthly_bills = 400 * 365.25 / 12 / 14  # $400 a fortnight
    advice = suggest_savings(flow, PAY, monthly_bills=monthly_bills)
    assert advice["bills"] == 400
    assert round(advice["everyday"]) == 300
    assert round(advice["costs"]) == 1000


def test_bills_paid_elsewhere_still_count():
    flow = cash_flow(_history(spending=100), "acme payroll")
    monthly_bills = 400 * 365.25 / 12 / 14
    advice = suggest_savings(flow, PAY, monthly_bills=monthly_bills)
    assert advice["everyday"] == 0
    assert round(advice["costs"]) == 700


def test_emergency_target_is_three_months_of_essentials():
    flow = cash_flow(_history(), "acme payroll")
    advice = suggest_savings(flow, PAY, monthly_bills=0)
    # $300 groceries a fortnight is about $652 a month.
    assert advice["emergency_target"] == 1950
    assert advice["pays_to_target"] == 3


def test_overspending_suggests_nothing():
    flow = cash_flow(_history(spending=2900), "acme payroll")
    advice = suggest_savings(flow, PAY)
    assert advice["overspending"]
    assert advice["savings"] == 0
    assert advice["long_term"] == 0
    assert advice["benchmark_gap"] == 600
    assert advice["pays_to_target"] is None


def test_income_override_wins():
    flow = cash_flow(_history(), "acme payroll")
    assert suggest_savings(flow, PAY, income_override=2500)["income"] == 2500


def test_short_history_is_flagged():
    flow = cash_flow(_history(days=14), "acme payroll")
    assert not suggest_savings(flow, PAY)["enough_history"]


def test_money_pulled_back_from_savings_per_pay():
    rows = _history(extra=[make_txn("2026-10-25", 840, "Transfer from xx9910 CommBank app", "credit")])
    advice = suggest_savings(cash_flow(rows, "acme payroll", ["9910"]), PAY)
    assert advice["from_savings"] == 140


def test_needs_pay_and_flow():
    assert suggest_savings(None, PAY) is None
    assert suggest_savings(cash_flow(_history(), "acme payroll"), None) is None


def _store(db, rows):
    for n, row in enumerate(rows):
        db.execute(
            "INSERT INTO bank_transactions (external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(n), row["txn_date"], row["description"], row["merchant_key"],
             row["amount"], row["direction"], row["status"]))
    db.commit()


def test_fortnight_page_shows_suggestion(client, db):
    login(client)
    _store(db, _history())
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Suggested savings" in html
    assert "$800.00" in html
    assert "$790.00" in html


def test_fortnight_page_without_bank_rows(client):
    login(client)
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Suggested savings" in html
    assert "Import bank transactions" in html


def test_apply_suggestion_saves_settings(client, db):
    login(client)
    _store(db, _history())
    response = client.post("/subscriptions/fortnight/apply-savings")
    assert response.status_code == 302
    from database import get_setting
    assert get_setting("savings_per_pay") == "800.00"
    assert get_setting("long_term_per_pay") == "790.00"
    assert get_setting("fortnightly_income") == "3000.00"


def test_apply_keeps_income_already_set(client, db):
    login(client)
    _store(db, _history())
    from database import get_setting, set_setting
    set_setting("fortnightly_income", "2800")
    db.commit()
    client.post("/subscriptions/fortnight/apply-savings")
    assert get_setting("fortnightly_income") == "2800"


def test_apply_without_history_changes_nothing(client, db):
    login(client)
    client.post("/subscriptions/fortnight/apply-savings")
    from database import get_setting
    assert not get_setting("savings_per_pay")


def test_apply_requires_post(client):
    login(client)
    assert client.get("/subscriptions/fortnight/apply-savings").status_code == 405


def test_settings_saves_savings_accounts(client, db):
    login(client)
    client.post("/settings/save", data={"action": "save_budget", "savings_accounts": "xx9910, 4321 "})
    from database import get_setting
    assert get_setting("savings_accounts") == "9910, 4321"
    assert 'value="9910, 4321"' in client.get("/settings").get_data(as_text=True)


def test_api_savings_needs_key(client):
    assert client.get("/api/v1/budget/savings").status_code == 401


def test_api_savings_null_without_history(app):
    from database import get_db, set_setting
    with app.app_context():
        set_setting("mcp_api_key", "k-123")
        get_db().commit()
    client = app.test_client()
    r = client.get("/api/v1/budget/savings", headers={"Authorization": "Bearer k-123"})
    assert r.status_code == 200
    assert r.get_json() == {"savings": None}


def test_api_savings_matches_page(app):
    from database import get_db, set_setting
    with app.app_context():
        set_setting("mcp_api_key", "k-123")
        _store(get_db(), _history())
    client = app.test_client()
    body = client.get("/api/v1/budget/savings", headers={"Authorization": "Bearer k-123"}).get_json()
    assert body["savings"]["savings"] == 800
    assert body["savings"]["long_term"] == 790
    assert body["savings"]["enough_history"] is True


def _banksync(rows):
    """Rows as BankSync stores them: money out is 'debit', money in is 'unknown'."""
    out = []
    for row in rows:
        row = dict(row, source="banksync")
        if row["direction"] == "credit":
            row["direction"] = "unknown"
        out.append(row)
    return out


def test_resolve_directions_trusts_sources_that_sign_debits():
    from services.bank_import import resolve_directions
    rows = [{"source": "banksync", "direction": "debit"}, {"source": "banksync", "direction": "unknown"},
            {"source": "other", "direction": "unknown"}, {"source": "csv", "direction": "credit"}]
    resolved = resolve_directions(rows)
    assert [r["direction"] for r in resolved] == ["debit", "credit", "unknown", "credit"]
    assert rows[1]["direction"] == "unknown"


def test_unknown_pay_rows_count_as_pay_not_spending():
    rows = [dict(r, direction="unknown") if r["direction"] == "credit" else r for r in _history()]
    flow = cash_flow(rows, "acme payroll")
    assert flow["pay"] == 18000
    assert flow["spending"] == 4201


def _store_with_source(db, rows):
    for n, row in enumerate(rows):
        db.execute(
            "INSERT INTO bank_transactions (source, external_id, txn_date, description, merchant_key,"
            " amount, direction, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (row.get("source", "csv"), str(n), row["txn_date"], row["description"], row["merchant_key"],
             row["amount"], row["direction"], row["status"]))
    db.commit()


def test_fortnight_page_suggests_savings_from_banksync_rows(client, db):
    login(client)
    _store_with_source(db, _banksync(_history()))
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "$800.00" in html
    assert "$790.00" in html


def test_settings_detects_payday_from_banksync_rows(client, db):
    login(client)
    _store_with_source(db, _banksync(_history()))
    html = client.get("/settings").get_data(as_text=True)
    assert "Your bank data looks like a fortnightly pay" in html
