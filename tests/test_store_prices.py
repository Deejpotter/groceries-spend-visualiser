"""Shelf prices: store parsers, history and summary, tracking and the Prices pages."""

from datetime import date

import pytest

from conftest import login
from services import store_prices as sp
from services.price_tracking import (
    FakeStoreClient, auto_watch_woolworths, check_messages, check_prices, ingredient_prices, load_watches,
    prices_overview, watch_product,
)
from services.store_clients import StoreError

TODAY = date(2026, 11, 7)

WOOLWORTHS_ITEM = {"Stockcode": 888137, "Name": "Woolworths Full Cream Milk", "DisplayName": "Woolworths Full Cream Milk 2L",
                   "Price": 3.4, "WasPrice": 3.4, "CupPrice": 1.7, "CupMeasure": "1L", "HasCupPrice": True,
                   "IsAvailable": True, "UrlFriendlyName": "woolworths-full-cream-milk"}
COLES_ITEM = {"_type": "PRODUCT", "id": 1913790, "name": "Full Cream Milk", "brand": "Devondale", "size": "2L",
              "availability": True, "pricing": {"now": 4.2, "was": 4.7, "unit": {
                  "price": 2.1, "ofMeasureQuantity": 1, "ofMeasureUnits": "l"}}}
ALDI_TILE = (
    '<div class="product-tile"><a href="/product/farmdale-full-cream-milk-2l-000000000000398689" '
    'class="base-link product-tile__link">'
    '<div class="product-tile__brandname" data-test="x"><p aria-label="FARMDALE,">FARMDALE</p></div>'
    '<div class="product-tile__name" data-test="x"><p aria-label="Full Cream Milk 2L,">Full Cream Milk 2L</p></div>'
    '<div data-test="product-tile__comparison-price" class="product-tile__comparison-price"><p>($1.70 per 1 L)</p></div>'
    '<div class="base-price base-price--product-tile"><span class="base-price__regular"><span>$3.39</span></span></div>'
    '</a></div>')
ALDI_PAGE = (
    '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
    '<script type="application/ld+json">{"@type":"Product","offers":{"@type":"Offer","price":"3.39",'
    '"availability":"https://schema.org/InStock"}}</script>'
    '<a href="/brands/Farmdale" class="base-link product-details__brand-name">FARMDALE</a>'
    '<h1 class="product-details__title">Full Cream Milk 2L</h1>'
    '<span class="product-details--initializing product-details__comparison-price"> ($1.70 per 1 L) </span>')
ALDI_URL = "https://www.aldi.com.au/product/farmdale-full-cream-milk-2l-000000000000398689"


# --- parsers -----------------------------------------------------------------

def test_cup_string_normalises_units():
    assert sp.cup_string(1.65, 1, "l") == "$1.65 / 1L"
    assert sp.cup_string("2.38", "100", "g") == "$2.38 / 100G"
    assert sp.cup_string(0.5, 1, "each") == "$0.50 / 1EA"
    assert sp.cup_string(None, 1, "l") is None and sp.cup_string(1, 1, "furlong") is None


def test_woolworths_product_and_search():
    product = sp.parse_woolworths_product({"Product": WOOLWORTHS_ITEM})
    assert product == {"store": "woolworths", "code": "888137", "name": "Woolworths Full Cream Milk 2L",
                       "price": 3.4, "was_price": None, "cup_price": "$1.70 / 1L", "available": True,
                       "url": "https://www.woolworths.com.au/shop/productdetails/888137/woolworths-full-cream-milk"}
    special = dict(WOOLWORTHS_ITEM, Stockcode=88436, Price=4.2, WasPrice=4.7)
    found = sp.parse_woolworths_search({"Products": [{"Products": [WOOLWORTHS_ITEM]}, {"Products": [special]}]})
    assert [(p["code"], p["was_price"]) for p in found] == [("888137", None), ("88436", 4.7)]
    assert sp.parse_woolworths_product({}) is None


def test_coles_search_skips_non_products_and_builds_url():
    found = sp.parse_coles_search({"pageProps": {"searchResults": {"results": [
        {"_type": "SINGLE_TILE"}, COLES_ITEM]}}})
    assert found == [{"store": "coles", "code": "1913790", "name": "Devondale Full Cream Milk 2L", "price": 4.2,
                      "was_price": 4.7, "cup_price": "$2.10 / 1L", "available": True,
                      "url": "https://www.coles.com.au/product/devondale-full-cream-milk-2l-1913790"}]
    assert sp.parse_coles_product({"pageProps": {"product": COLES_ITEM}})["price"] == 4.2
    assert sp.coles_slug(found[0]["url"]) == "devondale-full-cream-milk-2l-1913790"
    assert sp.coles_build_id('..."buildId":"20261030.5-abc"...') == "20261030.5-abc"


def test_aldi_search_tiles_and_product_page():
    tile, = sp.parse_aldi_search("<html>" + ALDI_TILE + "</html>")
    assert (tile["code"], tile["name"], tile["price"], tile["cup_price"], tile["url"]) == (
        "398689", "FARMDALE Full Cream Milk 2L", 3.39, "$1.70 / 1L", ALDI_URL)
    page = sp.parse_aldi_product(ALDI_PAGE, ALDI_URL)
    assert (page["code"], page["name"], page["price"], page["cup_price"], page["available"]) == (
        "398689", "FARMDALE Full Cream Milk 2L", 3.39, "$1.70 / 1L", True)
    gone = sp.parse_aldi_product(ALDI_PAGE.replace("InStock", "OutOfStock"), ALDI_URL)
    assert gone["available"] is False


# --- history and summary -----------------------------------------------------

MILK = {"id": 1, "name": "Milk", "unit": "L", "pack_size": None}


def test_history_puts_every_source_in_the_ingredient_unit():
    paid = [{"order_date": "2026-10-01", "store": "Woolworths", "product_name": "Woolworths Full Cream Milk 2L",
             "unit_price": 3.1, "cup_price": None}]
    shelf = [sp.make_shelf_row("coles", "2026-11-07", 3.4, "$1.70 / 1L"),
             sp.make_shelf_row("aldi", "2026-11-07", 3.39, None, name="Farmdale Full Cream Milk 2L"),
             sp.make_shelf_row("woolworths", "2026-11-06", None)]
    series = sp.price_history(MILK, paid, shelf)
    assert series["paid"][0]["unit_price"] == 1.55
    assert series["coles"][0]["unit_price"] == 1.7
    assert series["aldi"][0]["unit_price"] == 1.695  # from the size in the name
    assert "woolworths" not in series


def test_summary_picks_cheapest_fresh_store_and_changes():
    series = {
        "paid": [{"date": "2026-10-20", "unit_price": 2.0, "price": 4.0}],
        "coles": [{"date": "2026-10-01", "unit_price": 2.1}, {"date": "2026-11-07", "unit_price": 1.8, "was_price": 4.2}],
        "aldi": [{"date": "2026-10-20", "unit_price": 1.5}],  # older than a week: shown, not picked
        "woolworths": [{"date": "2026-11-07", "unit_price": 1.9}],
    }
    summary = sp.price_summary(series, TODAY)
    stores = {s["store"]: s for s in summary["stores"]}
    assert summary["cheapest"]["store"] == "coles" and summary["saving_per_unit"] == 0.2
    assert stores["coles"]["change_30d"] == -0.3 and stores["coles"]["on_special"]
    assert stores["coles"]["low_6m"] == 1.8 and not stores["aldi"]["fresh"]
    assert stores["woolworths"]["change_30d"] is None


# --- tracking ----------------------------------------------------------------

def _milk(db, purchases=True):
    db.execute("INSERT INTO ingredients (id, name, category, unit) VALUES (1, 'Milk', 'dairy', 'L')")
    if purchases:
        for day, code in (("2026-09-01", "111"), ("2026-10-01", "888137")):
            db.execute("INSERT INTO purchases (order_date, basket_id, store, product_name, quantity, unit_price,"
                       " line_total, cup_price, stockcode, ingredient_id) VALUES (?, ?, 'Woolworths',"
                       " 'Woolworths full cream milk 2l', 1, 3.1, 3.1, NULL, ?, 1)", (day, day, code))
    db.commit()


def _product(store, code="1", price=3.4, **extra):
    return dict({"store": store, "code": code, "name": f"{store} milk 2L", "price": price, "was_price": None,
                 "cup_price": f"${price / 2:.2f} / 1L", "url": f"https://example.test/{code}", "available": True}, **extra)


def test_woolworths_is_watched_from_the_latest_stockcode(db):
    _milk(db)
    assert auto_watch_woolworths(db) == 1
    watch, = load_watches(db, 1)
    assert (watch["store"], watch["store_code"], watch["origin"]) == ("woolworths", "888137", "auto")
    assert auto_watch_woolworths(db) == 0


def test_check_saves_once_a_day_and_skips_a_failing_store(db):
    _milk(db)
    watch_product(db, 1, _product("coles", "c1"), "2026-11-01")
    watch_product(db, 1, _product("aldi", "a1"), "2026-11-01")
    client = FakeStoreClient({("woolworths", "888137"): _product("woolworths", "888137", 3.3),
                              ("coles", "c1"): StoreError("Coles returned HTTP 403."),
                              ("aldi", "a1"): None})
    result = check_prices(db, TODAY, client, pause=0)
    assert (result["checked"], result["unavailable"], result["auto_watched"]) == (1, 1, 1)
    assert result["failed"] == {"coles": "Coles returned HTTP 403."}
    rows = db.execute("SELECT store, price, available FROM shelf_prices WHERE checked_on = '2026-11-07'"
                      " ORDER BY store").fetchall()
    assert [tuple(r) for r in rows] == [("aldi", None, 0), ("woolworths", 3.3, 1)]
    calls = len(client.calls)
    check_prices(db, TODAY, client, pause=0)
    assert client.calls[calls:] == [("coles", "c1")]  # only the store that failed is tried again
    assert check_messages(result)[1] == ("warning", "Coles prices weren't checked: Coles returned HTTP 403.")


def test_watching_replaces_the_store_product_and_overview_ranks_savings(db):
    _milk(db)
    watch_product(db, 1, _product("coles", "c1", 3.6), "2026-11-07")
    watch_product(db, 1, _product("coles", "c2", 2.8), "2026-11-07")
    assert [w["store_code"] for w in load_watches(db, 1)] == ["c2"]
    data = ingredient_prices(db, {"id": 1, "unit": "L", "pack_size": None}, TODAY)
    assert data["summary"]["cheapest"]["latest"]["unit_price"] == 1.4
    row, = prices_overview(db, TODAY)
    assert row["summary"]["saving_per_unit"] == pytest.approx(0.15)


# --- routes ------------------------------------------------------------------

@pytest.fixture
def fake_client(app):
    client = FakeStoreClient(
        products={("coles", "1913790"): _product("coles", "1913790", 4.2, was_price=4.7),
                  ("woolworths", "888137"): _product("woolworths", "888137", 3.4)},
        results={"coles": [_product("coles", "1913790", 4.2)], "aldi": StoreError("Aldi returned HTTP 403.")})
    app.config["STORE_CLIENT_FACTORY"] = lambda: client
    yield client
    app.config.pop("STORE_CLIENT_FACTORY", None)


def test_search_track_check_and_stop(client, db, fake_client, monkeypatch):
    monkeypatch.setattr("routes.prices.local_today", lambda: TODAY)
    login(client)
    _milk(db)
    page = client.get("/prices/1?store=coles&q=milk").data
    assert b"coles milk 2L" in page and b"Track" in page and b"$2.10" in page
    assert b"Aldi returned HTTP 403." in client.get("/prices/1?store=aldi&q=milk").data

    resp = client.post("/prices/1/watch", data={"store": "coles", "code": "1913790", "name": "x", "url": ""},
                       follow_redirects=True)
    assert b"Tracking coles milk 2L at Coles." in resp.data and b"special" in resp.data
    assert db.execute("SELECT price FROM shelf_prices WHERE store = 'coles'").fetchone()[0] == 4.2

    resp = client.post("/prices/check", follow_redirects=True)
    assert b"Checked 1 shelf price." in resp.data  # Woolworths, auto-watched; Coles already checked today
    overview = client.get("/prices").data
    assert b"Milk" in overview and b"Woolworths" in overview

    resp = client.post("/prices/1/unwatch/coles", follow_redirects=True)
    assert b"Stopped tracking Coles" in resp.data
    assert [w["store"] for w in load_watches(db, 1)] == ["woolworths"]


def test_empty_prices_page_explains_tracking(client, db):
    login(client)
    assert b"Nothing tracked yet" in client.get("/prices").data


def test_api_prices_and_search(client, db, fake_client, monkeypatch):
    from database import set_setting
    set_setting("mcp_api_key", "prices-key")
    _milk(db)
    watch_product(db, 1, _product("coles", "1913790", 4.2), TODAY.isoformat())
    monkeypatch.setattr("models.today", lambda: TODAY)
    headers = {"Authorization": "Bearer prices-key"}
    item, = client.get("/api/v1/prices", headers=headers).get_json()["items"]
    assert item["stores"]["coles"]["unit_price"] == 2.1 and item["cheapest"] == "coles"
    found = client.get("/api/v1/prices/search?store=coles&q=milk", headers=headers).get_json()
    assert found["results"][0]["code"] == "1913790"
    assert client.get("/api/v1/prices/search?store=aldi&q=milk", headers=headers).status_code == 424
    assert client.get("/api/v1/prices/search?store=iga&q=milk", headers=headers).status_code == 400


def test_pasted_links_name_the_store_and_code():
    assert sp.parse_product_link("https://www.woolworths.com.au/shop/productdetails/888137/woolworths-milk?x=1") == {
        "store": "woolworths", "code": "888137",
        "url": "https://www.woolworths.com.au/shop/productdetails/888137/woolworths-milk"}
    assert sp.parse_product_link("https://www.coles.com.au/product/coles-full-cream-milk-2l-439693")["code"] == "439693"
    assert sp.parse_product_link(ALDI_URL)["code"] == "398689"
    assert sp.parse_product_link("https://www.iga.com.au/product/1") is None
    assert sp.parse_product_link("https://www.coles.com.au/browse/dairy") is None


def test_track_by_pasted_link(client, db, fake_client):
    login(client)
    _milk(db, purchases=False)
    resp = client.post("/prices/1/watch", data={"link": "https://www.coles.com.au/product/x-1913790"},
                       follow_redirects=True)
    assert b"Tracking coles milk 2L at Coles." in resp.data
    assert load_watches(db, 1)[0]["store_code"] == "1913790"
    resp = client.post("/prices/1/watch", data={"link": "https://example.com/a"}, follow_redirects=True)
    assert b"isn&#39;t a Woolworths, Coles or Aldi product page" in resp.data


class _Reply:
    def __init__(self, body):
        self.body = body.encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Opener:
    """Answers by URL; a bot check first, then the page; the old build id 404s."""

    def __init__(self):
        self.urls = []
        self.page_answers = ["<title>Pardon Our Interruption</title>", '"buildId":"new"']

    def open(self, request, timeout):
        import urllib.error
        url = request.full_url
        self.urls.append(url)
        if "/_next/data/old/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if "/_next/data/new/" in url:
            import json
            return _Reply(json.dumps({"pageProps": {"product": COLES_ITEM}}))
        return _Reply(self.page_answers.pop(0))


def test_coles_client_retries_the_bot_check_and_refreshes_a_stale_build(monkeypatch):
    from services import store_clients
    monkeypatch.setattr(store_clients.time, "sleep", lambda s: None)
    monkeypatch.setitem(store_clients._coles_build_cache, "id", "old")
    opener = _Opener()
    product = store_clients.StoreClient(opener).product(
        "coles", "1913790", "https://www.coles.com.au/product/devondale-full-cream-milk-2l-1913790")
    assert product["price"] == 4.2 and store_clients._coles_build_cache["id"] == "new"
    assert [u for u in opener.urls if "/search" in u] == []  # never the search page robots.txt disallows


def test_check_remembers_the_coles_build_id(db, monkeypatch):
    from services import store_clients
    monkeypatch.setitem(store_clients._coles_build_cache, "id", None)
    db.execute("INSERT INTO settings (key, value) VALUES ('coles_build_id', 'saved')")
    db.commit()
    check_prices(db, TODAY, FakeStoreClient(), pause=0)
    assert store_clients._coles_build_cache["id"] == "saved"
    store_clients._coles_build_cache["id"] = "newer"
    check_prices(db, TODAY, FakeStoreClient(), pause=0)
    assert db.execute("SELECT value FROM settings WHERE key = 'coles_build_id'").fetchone()[0] == "newer"
