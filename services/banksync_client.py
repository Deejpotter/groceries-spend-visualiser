"""BankSync REST client: read-only bank transaction access via API key.

BankSync connects to Australian banks (including CommBank) through the
Consumer Data Right via Fiskil. The API key is created in BankSync's
Developer Settings and sent as the X-API-Key header.
"""

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

BASE_URL = "https://api.banksync.io"
FIRST_SYNC_DAYS = 180  # CDR exposes roughly six months of history


class BankSyncError(Exception):
    """A BankSync API call failed (bad key, rate limit, network)."""


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

    def __init__(self, api_key: str, timeout: int = 15):
        self.api_key = api_key
        self.timeout = timeout

    def _get(self, path: str, params: Optional[Dict] = None) -> Any:
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
            raise BankSyncError(
                f"BankSync API {exc.code}: {detail or exc.reason}")
        except urllib.error.URLError as exc:
            raise BankSyncError(f"Couldn't reach BankSync: {exc.reason}")

    def whoami(self) -> Dict:
        return self._get("/v1/whoami")

    def list_banks(self) -> List[Dict]:
        return _as_list(self._get("/v1/banks"))

    def list_accounts(self, bank_id: str) -> List[Dict]:
        return _as_list(self._get(f"/v1/banks/{bank_id}/accounts"))

    def list_transactions(self, bank_id: str, account_id: str,
                          from_date: Optional[str] = None,
                          to_date: Optional[str] = None) -> List[Dict]:
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        return _as_list(self._get(
            f"/v1/banks/{bank_id}/accounts/{account_id}/transactions",
            params))


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
