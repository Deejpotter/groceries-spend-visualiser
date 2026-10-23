"""BankSync integration: transaction mapping, client, sync orchestration."""

import email.message
import io
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pytest

from conftest import login
from services.banksync_client import (
    BankSyncClient, BankSyncError, describe_credential,
    map_banksync_transaction, sync_accounts,
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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _http_error(url, code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError(url, code, "Error", headers, io.BytesIO(b"detail"))


def test_client_retries_after_rate_limit(monkeypatch):
    delays = []
    calls = {"n": 0}
    monkeypatch.setattr(
        "services.banksync_client.time.sleep", lambda seconds: delays.append(seconds))

    def fake(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(request.full_url, 429, retry_after=7)
        return _FakeResponse(b'{"ok": 1}')

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    assert BankSyncClient("k")._get("/v1/whoami") == {"ok": 1}
    assert delays == [7.0]


def test_client_gives_up_after_repeated_rate_limits(monkeypatch):
    monkeypatch.setattr("services.banksync_client.time.sleep", lambda seconds: None)

    def fake(request, timeout=None):
        raise _http_error(request.full_url, 429, retry_after=1)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    with pytest.raises(BankSyncError, match="rate limit"):
        BankSyncClient("k", max_retries=2)._get("/v1/whoami")


def test_client_caps_absurd_retry_after(monkeypatch):
    delays = []
    calls = {"n": 0}
    monkeypatch.setattr(
        "services.banksync_client.time.sleep", lambda seconds: delays.append(seconds))

    def fake(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(request.full_url, 429, retry_after=99999)
        return _FakeResponse(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    BankSyncClient("k")._get("/v1/whoami")
    assert delays == [65.0]


def test_client_explains_consent_and_scope_errors(monkeypatch):
    _fake_urlopen(monkeypatch, b"no", status=503)
    with pytest.raises(BankSyncError, match="reconnect"):
        BankSyncClient("k")._get("/v1/banks")
    _fake_urlopen(monkeypatch, b"no", status=403)
    with pytest.raises(BankSyncError, match="banks:read"):
        BankSyncClient("k")._get("/v1/banks")
    _fake_urlopen(monkeypatch, b"no", status=401)
    with pytest.raises(BankSyncError, match="rejected the API key"):
        BankSyncClient("k")._get("/v1/banks")


def test_list_transactions_follows_the_cursor(monkeypatch):
    pages = [
        {"success": True, "data": [TXN_WOOLWORTHS],
         "meta": {"hasMore": True, "cursor": "abc"}},
        {"success": True, "data": [TXN_COLES], "meta": {"hasMore": False}},
    ]
    urls = []

    def fake(request, timeout=None):
        urls.append(request.full_url)
        return _FakeResponse(json.dumps(pages.pop(0)).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rows = BankSyncClient("k").list_transactions(
        "b1", "a1", from_date="2026-09-01")
    assert [row.get("id") or row.get("transactionId") for row in rows] == [
        "txn-1", "txn-2"]
    assert "from=2026-09-01" in urls[0]
    assert "cursor=abc" in urls[1]


def test_list_transactions_stops_without_a_cursor(monkeypatch):
    payload = {"success": True, "data": [TXN_WOOLWORTHS], "meta": {"hasMore": False}}
    calls = {"n": 0}

    def fake(request, timeout=None):
        calls["n"] += 1
        return _FakeResponse(json.dumps(payload).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    rows = BankSyncClient("k").list_transactions("b1", "a1")
    assert len(rows) == 1
    assert calls["n"] == 1


def test_describe_credential_reads_the_workspace():
    info = describe_credential({
        "workspace": {"name": "Frankston Home"},
        "plan": "Standard",
        "scopes": ["banks:read", "transactions:read"],
    })
    assert info["name"] == "Frankston Home"
    assert info["plan"] == "Standard"
    assert info["scopes"] == ["banks:read", "transactions:read"]
    assert info["plan_has_api"] is True


def test_describe_credential_flags_plans_without_api_access():
    assert describe_credential({"plan": "Free"})["plan_has_api"] is False
    assert describe_credential({"plan": "starter"})["plan_has_api"] is False
    assert describe_credential({"plan": ""})["plan_has_api"] is True
    assert describe_credential({})["name"] == "workspace"


def test_describe_credential_accepts_comma_separated_scopes():
    info = describe_credential({"scopes": "banks:read, transactions:read"})
    assert info["scopes"] == ["banks:read", "transactions:read"]


def test_test_key_route_warns_about_plans_without_api(client, db, monkeypatch):
    login(client)

    class FreePlan:
        def __init__(self, api_key):
            pass

        def whoami(self):
            return {"workspace": {"name": "Home"}, "plan": "Free", "scopes": []}

        def list_banks(self):
            return []

    monkeypatch.setattr("services.banksync_client.BankSyncClient", FreePlan)
    resp = client.post("/settings/save", data={
        "action": "test_banksync", "banksync_api_key": "bsk_test"},
        headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b"no API access" in resp.data
    assert b"alert-warning" in resp.data


def test_test_key_route_reports_connection_health(client, db, monkeypatch):
    login(client)

    class Healthy:
        def __init__(self, api_key):
            pass

        def whoami(self):
            return {"workspace": {"name": "Home"}, "plan": "Standard", "scopes": []}

        def list_banks(self):
            return [{"id": "b1"}, {"id": "b2"}]

    monkeypatch.setattr("services.banksync_client.BankSyncClient", Healthy)
    resp = client.post("/settings/save", data={
        "action": "test_banksync", "banksync_api_key": "bsk_test"},
        headers={"HX-Request": "true"})
    assert resp.status_code == 200
    assert b"alert-success" in resp.data
    assert b"2 bank connections" in resp.data


def test_test_key_route_escapes_error_html(client, db, monkeypatch):
    login(client)

    class Exploding:
        def __init__(self, api_key):
            pass

        def whoami(self):
            raise BankSyncError("<script>alert(1)</script>")

    monkeypatch.setattr("services.banksync_client.BankSyncClient", Exploding)
    resp = client.post("/settings/save", data={
        "action": "test_banksync", "banksync_api_key": "bsk_test"},
        headers={"HX-Request": "true"})
    assert b"<script>alert(1)</script>" not in resp.data
    assert b"&lt;script&gt;" in resp.data


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


def test_auto_sync_due():
    from routes.spend import _auto_sync_due
    now = datetime(2026, 10, 11, 12, 0)
    assert _auto_sync_due(None, now) is True
    assert _auto_sync_due("garbage", now) is True
    assert _auto_sync_due("2026-10-11T11:00:00", now) is False
    assert _auto_sync_due("2026-10-10T11:00:00", now) is True


def _set_key(db):
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("banksync_api_key", "bsk_test"))
    db.commit()


def test_dashboard_auto_syncs_in_background(client, db, monkeypatch):
    login(client)
    _set_key(db)
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS]))
    client.get("/spend")
    for _ in range(50):  # the sync runs on a background thread
        if db.execute(
            "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
        ).fetchone()[0]:
            break
        time.sleep(0.1)
    assert db.execute(
        "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
    ).fetchone()[0] == 1
    for thread in threading.enumerate():
        if thread.name == "banksync-auto-sync":
            thread.join(timeout=10)  # don't unlink the DB file mid-sync


def test_dashboard_skips_auto_sync_when_fresh(client, db, monkeypatch):
    login(client)
    _set_key(db)
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("banksync_last_sync",
         datetime.now(timezone.utc).replace(tzinfo=None).isoformat()))
    db.commit()
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS]))
    client.get("/spend")
    time.sleep(0.5)
    assert db.execute(
        "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
    ).fetchone()[0] == 0


def test_dashboard_skips_auto_sync_without_key(client, db, monkeypatch):
    login(client)
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS]))
    client.get("/spend")
    time.sleep(0.5)
    assert db.execute(
        "SELECT COUNT(*) FROM purchases WHERE origin = 'banksync'"
    ).fetchone()[0] == 0


def test_sync_stamps_last_sync(client, db, monkeypatch):
    login(client)
    _set_key(db)
    monkeypatch.setattr(
        "services.banksync_client.BankSyncClient",
        lambda api_key: FakeBankSync([TXN_WOOLWORTHS]))
    client.post("/spend/banksync/sync")
    assert db.execute(
        "SELECT value FROM settings WHERE key = 'banksync_last_sync'"
    ).fetchone() is not None
