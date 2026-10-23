"""BankSync REST client: read-only bank transaction access via API key.

BankSync connects to Australian banks (including CommBank) through the
Consumer Data Right via Fiskil. The API key is created in BankSync's
Developer Settings and sent as the X-API-Key header.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.banksync.io"
FIRST_SYNC_DAYS = 180  # CDR exposes roughly six months of history
MAX_RETRIES = 3
RETRY_CAP_SECONDS = 65  # one rate-limit window; never sleep longer than that
DEFAULT_RETRY_SECONDS = 5
MAX_PAGES = 50  # safety stop so a repeating cursor can't loop forever
API_PLANS = {"standard", "professional", "business", "enterprise"}


class BankSyncError(Exception):
    """A BankSync API call failed (bad key, rate limit, network)."""


def _error_message(code: int, detail: str) -> str:
    """Turn an HTTP status into something a person can act on."""
    if code == 401:
        return ("BankSync rejected the API key (401). Create a new key in "
                "BankSync > Settings > Developers and save it here.")
    if code == 403:
        return ("BankSync refused the request (403): the key is missing a "
                "permission. Recreate it with banks:read and "
                "transactions:read scopes.")
    if code == 429:
        return ("BankSync rate limit reached (429). It allows about 10-20 "
                "requests a minute; wait a minute and try again.")
    if code == 503:
        return ("BankSync says the bank needs reconnecting (503). Open "
                "BankSync, reconnect the bank and tick the accounts to share.")
    return f"BankSync API {code}: {detail or 'no detail'}"


def describe_credential(who: Dict) -> Dict:
    """Summarise a /v1/whoami payload: {name, plan, scopes, plan_has_api}."""
    workspace = who.get("workspace") if isinstance(who, dict) else None
    workspace = workspace if isinstance(workspace, dict) else {}
    name = workspace.get("name") or (who or {}).get("name") or "workspace"
    plan = str(who.get("plan") or workspace.get("plan") or "").strip()
    scopes = who.get("scopes") or []
    if isinstance(scopes, str):
        scopes = [part.strip() for part in scopes.split(",") if part.strip()]
    elif isinstance(scopes, list):
        scopes = [str(scope) for scope in scopes]
    else:
        scopes = []
    return {
        "name": name,
        "plan": plan,
        "scopes": scopes,
        "plan_has_api": (plan.lower() in API_PLANS) if plan else True,
    }


def _as_list(response: Any) -> List[Dict]:
    """BankSync returns bare lists or {items|data|banks|accounts: [...]}."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        for key in ("items", "data", "banks", "accounts", "transactions"):
            value = response.get(key)
            if isinstance(value, list):
                return value
    return []


class BankSyncClient:
    """Thin read-only client for the BankSync REST API."""

    def __init__(self, api_key: str, timeout: int = 15, max_retries: int = MAX_RETRIES):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def _get(self, path: str, params: Optional[Dict] = None,
             _attempt: int = 0) -> Any:
        url = BASE_URL + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url, headers={"X-API-Key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode()[:200]
            except Exception:
                pass
            if exc.code == 429 and _attempt < self.max_retries:
                time.sleep(self._retry_delay(exc))
                return self._get(path, params, _attempt + 1)
            raise BankSyncError(_error_message(exc.code, detail))
        except urllib.error.URLError as exc:
            raise BankSyncError(f"Couldn't reach BankSync: {exc.reason}")

    @staticmethod
    def _retry_delay(exc: urllib.error.HTTPError) -> float:
        """Honour Retry-After, capped so a bad value can't stall a sync."""
        raw = ""
        try:
            raw = (exc.headers or {}).get("Retry-After") or ""
        except Exception:
            raw = ""
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_SECONDS
        return max(0.0, min(seconds, RETRY_CAP_SECONDS))

    def _get_page(self, path: str, params: Optional[Dict] = None):
        """Return (items, next_cursor) for a cursor-paginated list endpoint."""
        body = self._get(path, params)
        items = _as_list(body)
        cursor = None
        if isinstance(body, dict):
            meta = body.get("meta")
            meta = meta if isinstance(meta, dict) else {}
            if meta.get("hasMore"):
                cursor = meta.get("cursor") or meta.get("nextCursor")
            if cursor is None:
                cursor = body.get("cursor")
        return items, cursor

    def whoami(self) -> Dict:
        return self._get("/v1/whoami")

    def list_banks(self) -> List[Dict]:
        return _as_list(self._get("/v1/banks"))

    def list_accounts(self, bank_id: str) -> List[Dict]:
        return _as_list(self._get(f"/v1/banks/{bank_id}/accounts"))

    def list_transactions(self, bank_id: str, account_id: str,
                          from_date: Optional[str] = None,
                          to_date: Optional[str] = None) -> List[Dict]:
        """All transactions for an account, following the response cursor."""
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        path = f"/v1/banks/{bank_id}/accounts/{account_id}/transactions"
        rows: List[Dict] = []
        cursor = None
        for _ in range(MAX_PAGES):
            page_params = dict(params)
            if cursor:
                page_params["cursor"] = cursor
            page, cursor = self._get_page(path, page_params)
            rows.extend(page)
            if not cursor:
                break
        return rows


def map_banksync_transaction(txn: Dict) -> Optional[Dict]:
    """A BankSync transaction dict to {txn_id, order_date, description, amount}.

    Returns None when the transaction can't be read. Providers disagree on
    field names and on whether money out is signed, so every field has
    aliases and the amount's magnitude is what's imported.
    """
    def first(*keys):
        for key in keys:
            value = txn.get(key)
            if value not in (None, ""):
                return value
        return None

    txn_id = first("id", "transactionId", "transaction_id", "externalId")
    raw_date = first("date", "transactionDate", "authorisedDate", "postedDate")
    description = first("name", "description", "merchantName", "memo")
    amount = first("amount")
    if txn_id is None or raw_date is None or amount is None or not description:
        return None
    from services.bank_import import _date, _num
    order_date = _date(str(raw_date))
    value = _num(amount)
    if not order_date or value is None or value == 0:
        return None
    return {"txn_id": str(txn_id), "order_date": order_date,
            "description": str(description).strip(),
            "amount": round(abs(value), 2)}


def sync_accounts(db, client, patterns) -> Dict:
    """Fetch new transactions for every connected account and import them.

    Returns {added, duplicates, unclassified, errors}. Each account's cursor
    (the date of its newest seen transaction) is kept in the
    banksync_cursors setting, so the next sync only reads fresh rows.
    """
    from database import get_setting, set_setting
    from services.bank_import import (
        bank_txn_to_purchase, import_bank_transactions,
    )
    try:
        cursors = json.loads(get_setting("banksync_cursors") or "{}")
    except ValueError:
        cursors = {}
    added = 0
    duplicates: List[Dict] = []
    unclassified: List[Dict] = []
    errors: List[str] = []
    today = date.today()
    for bank in client.list_banks():
        bank_id = str(bank.get("id") or bank.get("bankId") or "")
        if not bank_id:
            continue
        for account in client.list_accounts(bank_id):
            account_id = str(account.get("id") or account.get("accountId") or "")
            if not account_id:
                continue
            bookmark = cursors.get(account_id) or {}
            from_date = bookmark.get("date") or (
                today - timedelta(days=FIRST_SYNC_DAYS)).isoformat()
            try:
                txns = client.list_transactions(
                    bank_id, account_id, from_date=from_date)
            except BankSyncError as exc:
                errors.append(str(exc))
                continue
            purchases = []
            latest = bookmark.get("date")
            for txn in txns:
                row = map_banksync_transaction(txn)
                if row is None:
                    continue
                if latest is None or row["order_date"] > latest:
                    latest = row["order_date"]
                purchase = bank_txn_to_purchase(row, patterns)
                if purchase:
                    purchases.append(purchase)
                else:
                    unclassified.append(row)
            batch_added, batch_duplicates = import_bank_transactions(
                db, purchases)
            added += batch_added
            duplicates.extend(batch_duplicates)
            if latest:
                cursors[account_id] = {"date": latest}
    set_setting("banksync_cursors", json.dumps(cursors))
    db.commit()
    from services.bank_import import remember_unclassified
    remember_unclassified(db, unclassified)
    return {"added": added, "duplicates": duplicates,
            "unclassified": unclassified, "errors": errors}


def run_sync(db) -> Dict:
    """Run one sync and stamp the last-sync bookmark."""
    from database import get_setting, set_setting
    from services.bank_import import load_merchant_patterns
    result = sync_accounts(
        db, BankSyncClient(get_setting("banksync_api_key")),
        load_merchant_patterns(db))
    set_setting("banksync_last_sync",
                datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
    db.commit()
    return result
