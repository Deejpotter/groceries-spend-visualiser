# Plan: live and historical prices for Woolworths, Coles and Aldi

- **Date:** 2026-11-07
- **Status:** In progress

## Where it stands

- **Paid prices (history):** every imported Woolworths purchase keeps its price and cup price.
  Each ingredient holds one price, refreshed on import from its latest linked purchase. There is
  no price-over-time view per ingredient.
- **Coles and Aldi:** only bank payments (shop totals). No item prices.
- **Live shelf prices:** none.

## What the stores allow (checked 2026-11-07 from this PC)

| Store | Search | One product | Notes |
|---|---|---|---|
| Woolworths | `/apis/ui/Search/products` JSON | `/apis/ui/product/detail/{stockcode}` JSON | Needs the session cookie from the home page. Gives price, was price, cup price. |
| Coles | `/_next/data/{buildId}/en/search/products.json` | `/_next/data/{buildId}/en/product/{slug}.json` | The build id is read from any page. Gives now, was and unit price. |
| Aldi | `/results?q=` HTML tiles | `/product/{slug}` HTML (JSON-LD) | The JSON API is blocked (Akamai). The HTML pages work. |

Found while building it: robots.txt at all three stores disallows the search pages and allows product
pages. The automatic daily check fetches product pages only; search runs only when the user clicks
Search, and a pasted product link works without any search. After a dozen probes, Coles started
answering this PC's HTML requests with a bot check ("Pardon Our Interruption"); its JSON data routes
kept working, so the build id they need is cached in memory and in the `coles_build_id` setting.

None of these is a documented public API. They can change or block us, so every fetch fails
softly and the history already saved stays.

## Plan

1. **Schema.** `price_watches` (ingredient + store → one store product) and `shelf_prices`
   (one row per store product per day: price, was price, cup price, available).
2. **Pure service `services/store_prices.py`:** parse each store's search and product
   responses into one shape; normalise cup prices to the `$X / 100G` form the app already
   parses; build an ingredient's price history (paid + shelf per store) and a summary in the
   ingredient's unit (latest per store, cheapest now, change over 30 days, lowest in 6 months,
   on special).
3. **Fetch layer `services/store_clients.py`:** urllib with timeouts and one cookie jar;
   raises `StoreError`; nothing else touches the network.
4. **Tracking `services/price_tracking.py`:** watch/unwatch, auto-watch Woolworths from the
   stockcode of an ingredient's latest linked purchase, and `check_prices()` that fetches each
   watched product once a day and saves a `shelf_prices` row.
5. **Routes and pages (`routes/prices.py`, nav "Prices" under Track):**
   - `/prices`: every tracked ingredient, shelf price per store in its unit, cheapest store,
     30-day change, specials.
   - `/prices/<ingredient_id>`: chart of paid and shelf prices over time, search each store,
     track or untrack a product, "Check now".
   - Daily check runs in the background when `/prices` is opened (same pattern as the
     BankSync auto-sync), plus `flask --app app check-prices` for a scheduled task.
6. **API:** `GET /api/v1/prices` (summary) and `GET /api/v1/prices/search?store=&q=`
   (read-only), so agents and the MCP server can use it.
7. **Tests** for parsers (trimmed fixtures), history and summary maths, tracking with a fake
   fetcher, and the routes.
8. **Ship:** PR to `dev`, check staging (the search endpoint proves the server can reach the
   stores), PR `dev` → `main`, check production, write the investigation doc.

## Not in scope

- Changing shopping-list estimates to the cheapest store. The summary shows the saving first;
  switching estimates is a later decision.
- Importing Coles or Aldi receipts item by item (CSV import with a store name already works).
