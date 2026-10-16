"""Per-account API keys for /api/v1.

Keys are random tokens shown once at creation; only a SHA-256 hash is
stored. Verification is a hash lookup, so a leaked database doesn't leak
access. The old single global key (the `mcp_api_key` setting) is still
accepted as a fallback — see routes/api.py.
"""

import hashlib
import secrets
from typing import Dict, List, Optional

KEY_PREFIX = "gx_"


def generate_key() -> str:
    """A new random API key in the same gx_ format as the CLI."""
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    """SHA-256 hex of a raw key — what's stored and compared."""
    return hashlib.sha256(raw.encode()).hexdigest()


def create_key(db, user_id: int, name: str) -> Dict:
    """Create a key for a user. Returns the row plus the raw key (show once)."""
    from services.mutations import AgentError
    name = (name or "").strip() or "default"
    if not db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise AgentError("not_found", "User not found.")
    raw = generate_key()
    cur = db.execute(
        "INSERT INTO api_keys (user_id, name, key_hash, prefix) VALUES (?, ?, ?, ?)",
        (user_id, name, hash_key(raw), raw[:12]),
    )
    db.commit()
    return {"id": cur.lastrowid, "name": name, "prefix": raw[:12],
            "key": raw}


def list_keys(db, user_id: int) -> List[Dict]:
    """A user's keys, newest first — hashes never leave the database."""
    return [dict(r) for r in db.execute(
        "SELECT id, name, prefix, created_at, last_used_at FROM api_keys"
        " WHERE user_id = ? ORDER BY id DESC",
        (user_id,),
    )]


def revoke_key(db, user_id: int, key_id: int) -> Dict:
    """Delete one of a user's keys."""
    from services.mutations import AgentError
    cur = db.execute(
        "DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
    db.commit()
    if not cur.rowcount:
        raise AgentError("not_found", "API key not found.")
    return {"revoked": key_id}


def verify_key(db, raw: str) -> Optional[int]:
    """The user id for a valid raw key, else None. Touches last_used_at."""
    if not raw:
        return None
    row = db.execute(
        "SELECT id, user_id FROM api_keys WHERE key_hash = ?",
        (hash_key(raw),),
    ).fetchone()
    if not row:
        return None
    db.execute("UPDATE api_keys SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
               (row["id"],))
    db.commit()
    return row["user_id"]
