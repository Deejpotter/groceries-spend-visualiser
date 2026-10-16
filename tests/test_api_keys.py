"""Per-account API keys: service, settings UI, and /api/v1 auth."""

import pytest

from conftest import login
from services.api_keys import (
    create_key, hash_key, list_keys, revoke_key, verify_key,
)
from services.mutations import AgentError


def _user_id(db):
    db.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, 'x')",
        ("keyuser",))
    db.commit()
    return db.execute(
        "SELECT id FROM users WHERE username = 'keyuser'").fetchone()["id"]


def test_create_verify_revoke_round_trip(db):
    uid = _user_id(db)
    key = create_key(db, uid, "MCP server")
    assert key["key"].startswith("gx_")
    assert key["prefix"] == key["key"][:12]
    assert verify_key(db, key["key"]) == uid
    listed = list_keys(db, uid)
    assert len(listed) == 1
    assert listed[0]["name"] == "MCP server"
    assert listed[0]["last_used_at"] is not None  # verify touched it
    assert "key_hash" not in listed[0] and "key" not in listed[0]
    assert revoke_key(db, uid, key["id"]) == {"revoked": key["id"]}
    assert verify_key(db, key["key"]) is None
    assert list_keys(db, uid) == []


def test_verify_rejects_unknown(db):
    _user_id(db)
    assert verify_key(db, "gx_nope") is None
    assert verify_key(db, "") is None
    assert verify_key(db, None) is None


def test_create_blank_name_defaults_and_unknown_user_fails(db):
    uid = _user_id(db)
    assert create_key(db, uid, "   ")["name"] == "default"
    with pytest.raises(AgentError, match="User not found"):
        create_key(db, 9999, "x")


def test_revoke_missing_key_fails(db):
    uid = _user_id(db)
    with pytest.raises(AgentError, match="not found"):
        revoke_key(db, uid, 9999)


def test_keys_are_scoped_to_the_user(db):
    uid = _user_id(db)
    db.execute(
        "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, 'x')",
        ("other",))
    db.commit()
    other = db.execute(
        "SELECT id FROM users WHERE username = 'other'").fetchone()["id"]
    key = create_key(db, uid, "mine")
    with pytest.raises(AgentError, match="not found"):
        revoke_key(db, other, key["id"])  # someone else's key: not found
    assert list_keys(db, other) == []


def test_hash_is_stable_and_hashed_at_rest(db):
    assert hash_key("gx_abc") == hash_key("gx_abc")
    uid = _user_id(db)
    key = create_key(db, uid, "x")
    stored = db.execute(
        "SELECT key_hash FROM api_keys WHERE id = ?", (key["id"],)).fetchone()
    assert stored["key_hash"] != key["key"]
    assert stored["key_hash"] == hash_key(key["key"])


def test_existing_database_gains_api_keys_table(db):
    """init_db on a pre-table database creates api_keys (stale deploys)."""
    from database import init_db
    db.execute("DROP TABLE api_keys")
    db.commit()
    init_db()
    assert db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'api_keys'"
    ).fetchone()


def test_settings_page_lists_keys(client, db):
    login(client)
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert b"API Keys" in resp.data
    assert b"No API keys yet." in resp.data


def test_create_shows_key_once_and_key_works(client, db):
    login(client)
    resp = client.post("/settings/api-keys", data={"key_name": "agent"})
    assert resp.status_code == 200
    assert b"won&#39;t be shown again" in resp.data or b"won't be shown again" in resp.data
    import re
    match = re.search(rb"<code>(gx_[A-Za-z0-9_-]+)</code>", resp.data)
    assert match, "raw key should be rendered once"
    raw = match.group(1).decode()
    # the key authenticates /api/v1 without any global key set
    r = client.get("/api/v1/server-info",
                   headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.get_json()["server"] == "groceries"
    # ...but a second look at settings no longer shows it
    assert raw.encode() not in client.get("/settings").data


def test_revoke_kills_the_key(client, db):
    login(client)
    client.post("/settings/api-keys", data={"key_name": "doomed"})
    row = db.execute("SELECT id FROM api_keys").fetchone()
    resp = client.post(f"/settings/api-keys/{row['id']}/revoke")
    assert resp.status_code == 302
    r = client.get("/api/v1/server-info",
                   headers={"Authorization": "Bearer gx_anything"})
    assert r.status_code == 401


def test_revoke_missing_key_redirects_with_error(client, db):
    login(client)
    resp = client.post("/settings/api-keys/9999/revoke")
    assert resp.status_code == 302


def test_create_requires_login(client):
    assert client.post("/settings/api-keys").status_code == 302


def test_legacy_global_key_still_works(client, db):
    """The CLI-set mcp_api_key keeps working as a fallback."""
    from database import set_setting
    login(client)
    set_setting("mcp_api_key", "test-legacy-key")
    db.commit()
    r = client.get("/api/v1/server-info",
                   headers={"Authorization": "Bearer test-legacy-key"})
    assert r.status_code == 200
