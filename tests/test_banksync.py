"""BankSync integration: transaction mapping, client, sync orchestration."""

import io
import json
import urllib.error
import urllib.request

import pytest

from conftest import login
from services.banksync_client import (
    BankSyncClient, BankSyncError, map_banksync_transaction, sync_accounts,
)

PATTERNS = [("woolworths", "Woolworths"), ("coles", "Coles"),
            ("aldi", "ALDI"), ("iga", "IGA")]

TXN_WOOLWORTHS = {
    "id": "txn-1", "date": "2026-10-01T00:00:00Z",
    "name": "WOOLWORTHS MT WAVERLEY 3456", "amount": -82.40,
}
TXN_COLES = {
    "transactionId": "txn-2", "transactionDate": "2026-10-02",
    "merchantName": "COLES EXPRESS SOUTH YARRA", "amount": "23.10",
}
TXN_UNKNOWN = {
    "id": "txn-3", "date": "2026-10-03",
    "description": "UNKNOWN SHOPPE", "amount": 10.00,
}


def test_map_transaction_aliases():
    row = map_banksync_transaction(TXN_WOOLWORTHS)
    assert row == {"txn_id": "txn-1", "order_date": "2026-10-01",
                   "description": "WOOLWORTHS MT WAVERLEY 3456",
                   "amount": 82.40}
    row = map_banksync_transaction(TXN_COLES)
    assert (row["txn_id"], row["order_date"], row["amount"]) == \
        ("txn-2", "2026-10-02", 23.10)


def test_map_transaction_rejects_unreadable():
    assert map_banksync_transaction({"id": "x", "date": "2026-10-01"}) is None
    assert map_banksync_transaction({
        "id": "x", "date": "2026-10-01", "name": "Woolworths"}) is None
    assert map_banksync_transaction({
        "id": "x", "date": "nonsense", "name": "Woolworths",
        "amount": 1}) is None
    assert map_banksync_transaction({
        "id": "x", "date": "2026-10-01", "name": "Woolworths",
        "amount": 0}) is None


def _fake_urlopen(monkeypatch, payload, status=200):
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake(request, timeout=None):
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "Error", None,
                io.BytesIO(b"bad key"))
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def test_client_get_success(monkeypatch):
    _fake_urlopen(monkeypatch, b'{"ok": 1}')
    assert BankSyncClient("k")._get("/v1/whoami") == {"ok": 1}


def test_client_get_http_error(monkeypatch):
    _fake_urlopen(monkeypatch, b"bad key", status=401)
    with pytest.raises(BankSyncError, match="401"):
        BankSyncClient("k")._get("/v1/whoami")


def test_client_get_network_error(monkeypatch):
    def fake(request, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(BankSyncError, match="Couldn't reach"):
        BankSyncClient("k")._get("/v1/whoami")


class FakeBankSync:
    def __init__(self, txns):
        self.txns = txns
        self.calls = []

    def list_banks(self):
        return [{"id": "b1", "name": "Commonwealth Bank"}]

    def list_accounts(self, bank_id):
        return [{"id": "a1", "name": "Everyday"}]

    def list_transactions(self, bank_id, account_id, from_date=None,
                          to_date=None):
        self.calls.append((bank_id, account_id, from_date))
        return self.txns


def test_sync_accounts_imports_and_bookmarks(db):
    result = sync_accounts(
        db, FakeBankSync([TXN_WOOLWORTHS, TXN_COLES, TXN_UNKNOWN]), PATTERNS)
    assert result["added"] == 2
    assert len(result["unclassified"]) == 1
    rows = db.execute(
        "SELECT basket_id, store, origin FROM purchases").fetchall()
    assert {(r["basket_id"], r["store"], r["origin"]) for r in rows} == {
        ("bs:txn-1", "Woolworths", "banksync"),
        ("bs:txn-2", "Coles", "banksync"),
    }
    cursors = json.loads(db.execute(
        "SELECT value FROM settings WHERE key = 'banksync_cursors'"
    ).fetchone()["value"])
    # cursor advances past unclassified rows too: they were seen
    assert cursors == {"a1": {"date": "2026-10-03"}}


def test_sync_accounts_is_idempotent(db):
    client = FakeBankSync([TXN_WOOLWORTHS, TXN_COLES])
    assert sync_accounts(db, client, PATTERNS)["added"] == 2
    second = sync_accounts(db, client, PATTERNS)
    assert second["added"] == 0
    assert len(second["duplicates"]) == 2
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 2


def test_sync_accounts_resumes_from_cursor(db):
    client = FakeBankSync([TXN_WOOLWORTHS])
    sync_accounts(db, client, PATTERNS)
    resumed = FakeBankSync([TXN_COLES])
    sync_accounts(db, resumed, PATTERNS)
    assert resumed.calls[0][2] == "2026-10-01"  # from_date = cursor date


def test_sync_accounts_surfaces_account_errors(db, monkeypatch):
    class FailingBankSync(FakeBankSync):
        def list_transactions(self, *args, **kwargs):
            raise BankSyncError("BankSync API 401: bad key")

    result = sync_accounts(db, FailingBankSync([]), PATTERNS)
    assert result["errors"] == ["BankSync API 401: bad key"]
    assert result["added"] == 0


def test_banksync_sync_route(client, db, monkeypatch):
    login(client)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("banksync_api_key", "bsk_test"))
    db.commit()
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS, TXN_COLES]))
    resp = client.post("/spend/banksync/sync")
    assert resp.status_code == 200
    assert db.execute(
        "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
    ).fetchone()[0] == 2


def test_banksync_sync_route_requires_key(client, db):
    login(client)
    resp = client.post("/spend/banksync/sync")
    assert resp.status_code == 302  # redirected with an error flash
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0


def test_banksync_sync_route_api_error(client, db, monkeypatch):
    login(client)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("banksync_api_key", "bsk_test"))
    db.commit()

    class Broken:
        def __init__(self, api_key):
            pass

        def list_banks(self):
            raise BankSyncError("BankSync API 401: bad key")

    monkeypatch.setattr("services.banksync_client.BankSyncClient", Broken)
    resp = client.post("/spend/banksync/sync")
    assert resp.status_code == 302  # error flash, nothing imported
    assert db.execute("SELECT COUNT(*) FROM purchases").fetchone()[0] == 0
