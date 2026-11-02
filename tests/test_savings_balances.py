"""Savings balances from BankSync and progress to the emergency fund goal."""

import json

from conftest import login
from services.banksync_client import account_snapshot, sync_accounts
from services.savings_advice import savings_accounts, savings_progress

ACCOUNTS = [
    {"ending": "1139", "name": "Smart Access", "type": "TRANS_AND_SAVINGS_ACCOUNTS", "balance": 812.5},
    {"ending": "9910", "name": "NetBank Saver", "type": "SAVINGS", "balance": 1500.0},
    {"ending": "4321", "name": "GoalSaver", "type": "SAVINGS", "balance": 250.0},
]


def test_account_snapshot_reads_banksync_fields():
    snap = account_snapshot({"id": "a1", "accountNumber": "06 2000 1234 9910", "accountName": "NetBank Saver",
                             "accountType": "SAVINGS", "balance": "1500.00", "availableBalance": 1490})
    assert snap == {"id": "a1", "ending": "9910", "name": "NetBank Saver", "type": "SAVINGS",
                    "balance": 1500.0, "available": 1490.0}
    assert account_snapshot({"accountName": "no id"}) is None
    assert account_snapshot({"id": "a2", "balance": None})["balance"] is None


def test_savings_accounts_from_endings_or_names():
    assert [a["ending"] for a in savings_accounts(ACCOUNTS, ["9910"])] == ["9910"]
    assert [a["ending"] for a in savings_accounts(ACCOUNTS)] == ["9910", "4321"]
    assert savings_accounts([], ["9910"]) == []


def test_savings_progress():
    p = savings_progress(savings_accounts(ACCOUNTS), target=3500, long_term_per_pay=200)
    assert p["saved"] == 1750
    assert p["remaining"] == 1750
    assert p["percent"] == 50
    assert p["pays_to_go"] == 9
    assert not p["reached"]
    done = savings_progress(savings_accounts(ACCOUNTS), target=1000)
    assert done["reached"] and done["percent"] == 100 and done["pays_to_go"] is None
    assert savings_progress([{"ending": "1", "balance": None}], target=1000) is None


class _Fake:
    def list_banks(self):
        return [{"id": "b1"}]

    def list_accounts(self, bank_id):
        return [{"id": "a1", "accountNumber": "xxxx9910", "accountName": "NetBank Saver",
                 "accountType": "SAVINGS", "balance": 1500, "availableBalance": 1500}]

    def list_transactions(self, bank_id, account_id, from_date=None, to_date=None):
        return []


def test_sync_saves_account_balances(db):
    sync_accounts(db, _Fake(), [])
    saved = json.loads(db.execute("SELECT value FROM settings WHERE key = 'banksync_accounts'").fetchone()["value"])
    assert saved["accounts"][0]["ending"] == "9910"
    assert saved["accounts"][0]["balance"] == 1500
    assert saved["as_of"]


def _with_history_and_accounts(db):
    from test_savings_advice import _history, _store
    _store(db, _history())
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('banksync_accounts', ?)",
               (json.dumps({"as_of": "2026-11-01T09:00+00:00", "accounts": ACCOUNTS}),))
    db.commit()


def test_fortnight_page_shows_progress(client, db):
    login(client)
    _with_history_and_accounts(db)
    html = client.get("/subscriptions/fortnight").get_data(as_text=True)
    assert "Emergency fund:" in html
    assert "$1,750.00 of $1,950.00" in html
    assert "NetBank Saver xx9910" in html


def test_settings_lists_detected_accounts(client, db):
    login(client)
    _with_history_and_accounts(db)
    html = client.get("/settings").get_data(as_text=True)
    assert "GoalSaver xx4321: $250.00" in html
