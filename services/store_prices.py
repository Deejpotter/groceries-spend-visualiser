"""Shelf prices from Woolworths, Coles and Aldi, and an ingredient's price history.

Pure functions only: the store responses are parsed here into one shape, and the
history and summary are built from plain rows. Fetching lives in
``services/store_clients.py`` and saving in ``services/price_tracking.py``.

A parsed product is::

    {"store": "coles", "code": "8150288", "name": "Coles Full Cream Milk 3L",
     "price": 4.95, "was_price": None, "cup_price": "$1.65 / 1L",
     "url": "https://www.coles.com.au/product/...", "available": True}

Cup prices are written in the ``$1.65 / 1L`` form that ``parse_cup_price``
already reads, so shelf and paid prices convert to an ingredient's unit the same way.
"""

import html
import json
import re
from datetime import date, timedelta
from typing import Dict, Iterable, List, Optional

STORES = {
    "woolworths": {"label": "Woolworths", "base": "https://www.woolworths.com.au"},
    "coles": {"label": "Coles", "base": "https://www.coles.com.au"},
    "aldi": {"label": "Aldi", "base": "https://www.aldi.com.au"},
}
STORE_KEYS = list(STORES)
SEARCH_LIMIT = 12
_CUP_UNITS = {"G": "G", "KG": "KG", "ML": "ML", "L": "L", "EA": "EA", "EACH": "EA"}


def store_key(store: Optional[str]) -> Optional[str]:
    """'Woolworths' / 'woolworths' -> 'woolworths'; None for an unknown store."""
    key = (store or "").strip().lower()
    return key if key in STORES else None


def store_label(store: Optional[str]) -> str:
    key = store_key(store)
    return STORES[key]["label"] if key else (store or "")


def cup_string(price, amount, unit) -> Optional[str]:
    """(1.65, 1, 'l') -> '$1.65 / 1L'. None when any part is missing or unknown."""
    try:
        price, amount = float(price), float(amount or 1)
    except (TypeError, ValueError):
        return None
    code = _CUP_UNITS.get(str(unit or "").strip().upper())
    if not code or price < 0 or amount <= 0:
        return None
    return f"${price:.2f} / {amount:g}{code}"


def _money(value) -> Optional[float]:
    try:
        number = float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number > 0 else None


def _product(store, code, name, price, was_price=None, cup_price=None, url=None, available=True) -> Dict:
    price = _money(price)
    was = _money(was_price)
    return {
        "store": store, "code": str(code), "name": " ".join(str(name or "").split()),
        "price": price, "was_price": was if was and price and was > price else None,
        "cup_price": cup_price, "url": url, "available": bool(available) and price is not None,
    }


# --- Woolworths -------------------------------------------------------------

def _woolworths_item(item: Dict) -> Optional[Dict]:
    code = item.get("Stockcode")
    if not code:
        return None
    measure = re.match(r"\s*([\d.]*)\s*([A-Za-z]+)", str(item.get("CupMeasure") or ""))
    cup = cup_string(item.get("CupPrice"), measure.group(1) or 1, measure.group(2)) if (
        measure and item.get("HasCupPrice", True)) else None
    slug = item.get("UrlFriendlyName") or ""
    return _product(
        "woolworths", code, item.get("DisplayName") or item.get("Name"),
        item.get("Price"), item.get("WasPrice"), cup,
        f"{STORES['woolworths']['base']}/shop/productdetails/{code}/{slug}".rstrip("/"),
        item.get("IsAvailable", True),
    )


def parse_woolworths_product(payload: Dict) -> Optional[Dict]:
    """The product detail API: {"Product": {...}}."""
    item = (payload or {}).get("Product")
    return _woolworths_item(item) if isinstance(item, dict) else None


def parse_woolworths_search(payload: Dict) -> List[Dict]:
    """The search API: {"Products": [{"Products": [{...}]}, ...]} (each group is one tile)."""
    found = []
    for group in (payload or {}).get("Products") or []:
        for item in (group or {}).get("Products") or []:
            product = _woolworths_item(item)
            if product:
                found.append(product)
    return found[:SEARCH_LIMIT]


# --- Coles ------------------------------------------------------------------

def coles_build_id(page_html: str) -> Optional[str]:
    """The Next.js build id, needed for the JSON data routes."""
    match = re.search(r'"buildId"\s*:\s*"([^"]+)"', page_html or "")
    return match.group(1) if match else None


def _coles_item(item: Dict) -> Optional[Dict]:
    if not isinstance(item, dict) or not item.get("id") or item.get("_type") not in (None, "PRODUCT"):
        return None
    pricing = item.get("pricing") or {}
    unit = pricing.get("unit") or {}
    cup = cup_string(unit.get("price"), unit.get("ofMeasureQuantity"), unit.get("ofMeasureUnits"))
    name = " ".join(p for p in (item.get("brand"), item.get("name"), item.get("size")) if p)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return _product("coles", item["id"], name, pricing.get("now"), pricing.get("was"), cup,
                    f"{STORES['coles']['base']}/product/{slug}-{item['id']}", item.get("availability", True))


def parse_coles_search(payload: Dict) -> List[Dict]:
    results = (((payload or {}).get("pageProps") or {}).get("searchResults") or {}).get("results") or []
    return [p for p in (_coles_item(r) for r in results) if p][:SEARCH_LIMIT]


def parse_coles_product(payload: Dict) -> Optional[Dict]:
    return _coles_item(((payload or {}).get("pageProps") or {}).get("product"))


def coles_slug(url: Optional[str]) -> Optional[str]:
    match = re.search(r"/product/([a-z0-9-]+)", url or "")
    return match.group(1) if match else None


# --- Aldi -------------------------------------------------------------------

_ALDI_CUP_RE = re.compile(r"\$\s*([\d.]+)\s*per\s*([\d.]*)\s*([A-Za-z]+)", re.I)
_ALDI_CODE_RE = re.compile(r"/product/([a-z0-9-]*?-(\d{6,}))")


def _text(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment or "")).split())


def _aldi_cup(fragment: str) -> Optional[str]:
    match = _ALDI_CUP_RE.search(_text(fragment))
    return cup_string(match.group(1), match.group(2) or 1, match.group(3)) if match else None


def _aldi_price(fragment: str) -> Optional[float]:
    match = re.search(r'base-price__regular"[^>]*>\s*<span>\s*\$?([\d.,]+)', fragment or "")
    return _money(match.group(1)) if match else None


def parse_aldi_search(page_html: str, limit: int = SEARCH_LIMIT) -> List[Dict]:
    """Product tiles on https://www.aldi.com.au/results?q=... (or any category page)."""
    found = []
    for tile in (page_html or "").split('class="product-tile"')[1:]:
        link = _ALDI_CODE_RE.search(tile)
        if not link:
            continue
        brand = re.search(r'product-tile__brandname"[^>]*>(.*?)</div>', tile, re.S)
        name = re.search(r'product-tile__name"[^>]*>(.*?)</div>', tile, re.S)
        full = " ".join(p for p in (_text(brand.group(1)) if brand else "", _text(name.group(1)) if name else "") if p)
        cup = re.search(r'product-tile__comparison-price"[^>]*>(.*?)</div>', tile, re.S)
        found.append(_product("aldi", link.group(2).lstrip("0") or link.group(2), full, _aldi_price(tile), None,
                              _aldi_cup(cup.group(1)) if cup else None,
                              f"{STORES['aldi']['base']}/product/{link.group(1)}"))
    return found[:limit]


def parse_aldi_product(page_html: str, url: str) -> Optional[Dict]:
    """A product page: the price from its JSON-LD offer, name and unit price from the page."""
    link = _ALDI_CODE_RE.search(url or "")
    if not link:
        return None
    price, available = None, True
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', page_html or "", re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        offers = data.get("offers") if isinstance(data, dict) else None
        if isinstance(offers, dict) and offers.get("price"):
            price = offers.get("price")
            available = "OutOfStock" not in str(offers.get("availability") or "")
            break
    if price is None:
        price = _aldi_price(page_html)
    title = re.search(r'<h1[^>]*product-details__title[^>]*>(.*?)</h1>', page_html or "", re.S)
    brand = re.search(r'product-details__brand-name[^>]*>(.*?)</a>', page_html or "", re.S)
    cup = re.search(r'product-details__comparison-price"[^>]*>(.*?)</span>', page_html or "", re.S)
    name = " ".join(p for p in (_text(brand.group(1)) if brand else "", _text(title.group(1)) if title else "") if p)
    return _product("aldi", link.group(2).lstrip("0") or link.group(2), name, price, None,
                    _aldi_cup(cup.group(1)) if cup else None, f"{STORES['aldi']['base']}/product/{link.group(1)}",
                    available)


def parse_product_link(link: Optional[str]) -> Optional[Dict]:
    """A pasted product page address -> {store, code, url}, or None when it isn't one.

    Woolworths: /shop/productdetails/888137/...; Coles and Aldi: /product/<name>-<code>.
    """
    match = re.match(r"\s*https?://(?:www\.)?(woolworths|coles|aldi)\.com\.au(/[^\s?#]*)", link or "", re.I)
    if not match:
        return None
    store, path = match.group(1).lower(), match.group(2)
    if store == "woolworths":
        code = re.search(r"/productdetails/(\d+)", path)
        return {"store": store, "code": code.group(1), "url": STORES[store]["base"] + path} if code else None
    if store == "coles":
        slug = coles_slug(path)
        code = re.search(r"-(\d+)$", slug or "")
        return {"store": store, "code": code.group(1), "url": f"{STORES[store]['base']}/product/{slug}"} if code else None
    code = _ALDI_CODE_RE.search(path)
    return {"store": store, "code": code.group(2).lstrip("0"),
            "url": f"{STORES[store]['base']}/product/{code.group(1)}"} if code else None


# --- Captured in the user's browser -----------------------------------------

CAPTURE_MAX_ITEMS = 200
_BUILD_ID_RE = re.compile(r"^[A-Za-z0-9._-]{6,120}$")


def parse_capture(payload) -> Dict:
    """What the Prices bookmarklet sent from a store page -> {store, url, build_id, products}.

    The bookmarklet runs in the user's own browser, so it sees what a person sees, including
    Coles pages that answer the server with a bot check. It sends the store's own product data,
    trimmed: Coles and Woolworths items as their pages hold them, Aldi as product tile or product
    page HTML. It is parsed like any store reply; a URL off the store's site rejects the lot.
    """
    empty = {"store": None, "url": None, "build_id": None, "products": []}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return empty
    if not isinstance(payload, dict):
        return empty
    store = store_key(payload.get("store"))
    url = str(payload.get("url") or "")
    if not store or not re.match(rf"https://(?:www\.)?{store}\.com\.au/", url):
        return empty
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    items = items[:CAPTURE_MAX_ITEMS]
    if store == "coles":
        products = [_coles_item(i) for i in items]
    elif store == "woolworths":
        products = [_woolworths_item(i) if isinstance(i, dict) else None for i in items]
    else:
        page = str(payload.get("html") or "")
        if 'class="product-tile"' in page:
            products = parse_aldi_search(page, limit=CAPTURE_MAX_ITEMS)
        else:
            products = [parse_aldi_product(page, url)]
    found, seen = [], set()
    for product in products:
        if product and product["price"] and product["code"] not in seen:
            seen.add(product["code"])
            found.append(product)
    build = str(payload.get("build") or "")
    return {"store": store, "url": url, "products": found,
            "build_id": build if store == "coles" and _BUILD_ID_RE.match(build) else None}


# --- History and summary ----------------------------------------------------

def _unit_price(name, cup, price, unit, pack_size):
    from services.spend_import import ingredient_unit_price
    return ingredient_unit_price(name, cup, price, unit, pack_size)


def price_history(ingredient: Dict, paid: Iterable[Dict], shelf: Iterable[Dict]) -> Dict[str, List[Dict]]:
    """Dated per-unit prices, oldest first, one series per source.

    ``paid`` rows are linked purchases (order_date, product_name, unit_price, cup_price, store);
    ``shelf`` rows are saved shelf prices (checked_on, store, name, price, was_price, cup_price).
    Series keys are 'paid' and each store key. Points whose price can't be put in the
    ingredient's unit are left out rather than guessed.
    """
    unit, pack = ingredient.get("unit") or "each", ingredient.get("pack_size")
    series: Dict[str, List[Dict]] = {}
    for row in paid:
        value = _unit_price(row.get("product_name"), row.get("cup_price"), row.get("unit_price"), unit, pack)
        if value:
            series.setdefault("paid", []).append({
                "date": row["order_date"], "unit_price": round(value, 4), "price": row.get("unit_price"),
                "name": row.get("product_name"), "store": store_label(row.get("store"))})
    for row in shelf:
        if not row.get("price"):
            continue
        value = _unit_price(row.get("name"), row.get("cup_price"), row["price"], unit, pack)
        if value:
            series.setdefault(row["store"], []).append({
                "date": row["checked_on"], "unit_price": round(value, 4), "price": row["price"],
                "was_price": row.get("was_price"), "name": row.get("name")})
    for points in series.values():
        points.sort(key=lambda p: p["date"])
    return series


def _on_or_before(points: List[Dict], day: str) -> Optional[Dict]:
    earlier = [p for p in points if p["date"] <= day]
    return earlier[-1] if earlier else None


def price_summary(series: Dict[str, List[Dict]], today: date) -> Dict:
    """Latest shelf price per store, the cheapest, the change over 30 days and the 6-month low.

    A shelf price older than a week is shown but not used to pick the cheapest store.
    """
    fresh_from = (today - timedelta(days=7)).isoformat()
    month_ago = (today - timedelta(days=30)).isoformat()
    half_year = (today - timedelta(days=182)).isoformat()
    stores = []
    for key in STORE_KEYS:
        points = series.get(key) or []
        if not points:
            continue
        latest = points[-1]
        before = _on_or_before(points, month_ago)
        recent = [p["unit_price"] for p in points if p["date"] >= half_year]
        stores.append({
            "store": key, "label": STORES[key]["label"], "latest": latest,
            "fresh": latest["date"] >= fresh_from,
            "on_special": bool(latest.get("was_price")),
            "change_30d": round(latest["unit_price"] - before["unit_price"], 4) if before else None,
            "low_6m": min(recent) if recent else None,
        })
    fresh = [s for s in stores if s["fresh"]]
    cheapest = min(fresh, key=lambda s: s["latest"]["unit_price"]) if fresh else None
    paid = (series.get("paid") or [None])[-1]
    saving = None
    if cheapest and paid and paid["unit_price"] > cheapest["latest"]["unit_price"]:
        saving = round(paid["unit_price"] - cheapest["latest"]["unit_price"], 4)
    return {"stores": stores, "cheapest": cheapest, "last_paid": paid, "saving_per_unit": saving}


def make_shelf_row(store: str = "coles", checked_on: str = "2026-11-07", price: float = 4.95,
                   cup_price: Optional[str] = "$1.65 / 1L", was_price: Optional[float] = None,
                   name: str = "Coles Full Cream Milk 3L", code: str = "8150288") -> Dict:
    """A saved shelf price row for tests."""
    return {"store": store, "store_code": code, "checked_on": checked_on, "name": name, "price": price,
            "was_price": was_price, "cup_price": cup_price, "available": 1}
