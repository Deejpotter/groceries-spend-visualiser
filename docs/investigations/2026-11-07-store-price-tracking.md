# Live and historical shelf prices: Woolworths, Coles and Aldi

- **Date:** 2026-11-07
- **Investigator:** Claude Code (for Deej)
- **Status:** Done for Woolworths and Aldi. Coles is built but blocked from this network for now.
- **Plan:** `docs/plans/2026-11-07-store-price-tracking.md`

## 1. What was asked

"How's the live/historical price tracking for Woolworths, Coles and Aldi?" Plan it, implement it, and
merge it through `dev` into `main`.

## 2. What existed before

- **Paid prices:** each linked Woolworths purchase keeps its price and cup price. Each ingredient
  holds one price, refreshed on import. There was no price-over-time view.
- **Coles and Aldi:** only bank totals. No item prices.
- **Shelf prices:** none at any store.

## 3. What was checked

| Store | Search | One product | Result from this PC |
|---|---|---|---|
| Woolworths | `/apis/ui/Search/products` | `/apis/ui/product/detail/{stockcode}` | Works. Needs the home page cookie first. |
| Coles | `/_next/data/{build}/en/search/products.json` | `/_next/data/{build}/en/product/{slug}.json` | JSON routes work. HTML pages started returning a bot check ("Pardon Our Interruption") after about 20 probes. |
| Aldi | `/results?q=` HTML | `/product/{slug}` HTML (JSON-LD offer) | Works. The `api.aldi.com.au` JSON API returns Akamai "Access Denied". |

- **robots.txt:** all three stores disallow their search pages (`/shop/search`, `/search/`,
  `/results`) and allow product pages.
- **Coles build id:** the JSON routes need it, and it is only in the HTML. The id captured at the start
  still worked hours later. The data route returns 404 when it is stale.

## 4. What was changed

- **#142 (feature):**
  - Tables `price_watches` and `shelf_prices`.
  - `services/store_prices.py`: parsers, pasted links, history and summary (pure).
  - `services/store_clients.py`: the only network code.
  - `services/price_tracking.py`: watches, auto-watch Woolworths from invoice stockcodes, the daily check.
  - Prices pages: the list, and a per-ingredient page with a chart, search, link paste and check now.
  - CLI `check-prices`.
  - API `GET /api/v1/prices` and `/prices/search`.
  - Design choices made while building:
    - The automatic check fetches only product pages, which robots.txt allows. Search runs only when
      the user clicks Search.
    - Coles build id is read from a product page or the home page (not search), cached in memory and
      in the `coles_build_id` setting, and refreshed on a 404.
    - A store that fails once is skipped for the rest of that run.
- **#143:** the store-error API status changed from 502 to 424. Cloudflare replaced the 502 body with
  its own "error code: 502" page, which hid the reason.
- **#145:** found on the production Prices page:
  - Salami named "... per 100g" (priced per kg) showed last paid as $360/kg.
  - "Apple Royal Gala each" was priced per kg using the ingredient's pack size.
  - For kg/L ingredients these names now give no per-unit price unless the cup price converts. The
    import price refresh uses the same helper, so it is protected too.
- Releases: #144 and #146 (dev → main).

## 5. What was deliberately not changed

- **Shopping-list estimates** still use the paid price, not the cheapest shelf price. The Prices page
  shows the saving first; switching estimates is a separate decision.
- **Coles workarounds:** no attempt to get past Coles' bot check (no headless browser, no
  rotating IPs). It is built to work when Coles lets requests through.
- **Scheduled task:** none was added in Coolify. The check runs when /prices is opened, at most once a
  day. `flask --app app check-prices` is ready for a scheduled task if wanted.
- **Watched products:** no Coles or Aldi products were added beyond one Aldi test (below). The rest
  are Deej's choice.

## 6. Verification

- **Local:**
  - 1064 tests pass.
  - Live end-to-end on a scratch database: Woolworths and Aldi searched, tracked and checked.
- **Staging (1bd43d7):**
  - `/api/v1/prices/search` returned 12 Woolworths and 9 Aldi results with live prices.
  - Coles returned 424 "Coles answered with a bot check".
  - The staging check passed.
- **Production (94db827, then 9bc63c7):**
  - Opening /prices ran the first check: 52 ingredients auto-watched at Woolworths, 49 prices saved.
  - After #145, salami reads paid $36/kg against $29.50/kg on special, and apples no longer show a
    false per-kg price.
  - Aldi search for lactose-free milk returned live results. Tracking FARMDALE Full Cream Lactose
    Free Long Life Milk 1L saved $1.99, and the page marks Aldi cheapest against Woolworths' $2.00.
  - Coles search shows the bot-check message.

## 7. Open items

- **Coles:** retry in a few days. Once one HTML request gets through, the build id is saved and the
  JSON routes do the rest.
- **13 ingredients** show Woolworths cheaper than last paid. Most are specials (salami, Nuttelex,
  coleslaw dressing).
- **Optional:** a Coolify scheduled task running `flask --app app check-prices` daily, so prices are
  checked without opening the page.

## 8. Commands to reproduce

```bash
python -m pytest tests/test_store_prices.py tests/test_price_refresh.py -q
flask --app app check-prices
curl -H "Authorization: Bearer $KEY" "https://groceries-staging.deejpotter.com/api/v1/prices/search?store=aldi&q=milk"
```
