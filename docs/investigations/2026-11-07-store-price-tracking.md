# Live and historical shelf prices: Woolworths, Coles and Aldi

- **Date:** 2026-11-07
- **Investigator:** Claude Code (for Deej)
- **Status:** Done. Coles was blocked from this network; the capture bookmarklet (section 9) reconnects it.
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

- **Coles:** superseded by the capture bookmarklet (section 9). One capture on any Coles page
  saves the build id; the JSON routes do the rest.
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

## 9. Follow-up: where the data comes from, and the capture bookmarklet

**Asked:** where does the data come from, find reliable sources, then implement a better option.

**Sources checked (2026-11-07):**
- No store publishes a price API or dataset. The ACCC supermarkets inquiry final report (21 Mar 2025)
  recommended Coles and Woolworths provide price APIs to third parties; the government accepted the
  recommendations in principle, but no law requiring an API was found. The 1 July 2026 excessive
  pricing prohibition is about prices, not data access.
- Open source: hotprices-au (MIT, a draft with no published Australian history) and Chook Check
  (a Firefox extension that records prices as you browse). Paid: Apify scrapers and Actowiz data. All of
  these scrape the same store pages this app reads.

**Evidence:**
- In a real browser on this network, Coles pages load normally (no bot check). `__NEXT_DATA__` holds the
  product, search results and `buildId`; after in-site navigation the current page's data is in
  `next.router.components[route]`, not `__NEXT_DATA__`.
- With that build id set, the server's own Coles product and search requests worked from this network.
  The bot check only blocks the HTML page the build id was read from.
- Woolworths product pages hold the same item fields the API returns in `__NEXT_DATA__`; Woolworths
  search pages don't hold prices in the page. Aldi tiles and product pages carry the data as HTML/JSON-LD.

**Changed:** a "Capture prices" bookmarklet. It reads the page in the user's browser and opens
`/prices/capture#<data>`. The page posts the data to a review screen (products, prices, already-tracked
ingredients, a suggested ingredient for a single product). Save stores today's price for tracked and
picked products and, for Coles, the build id.

**Not changed:** no browser extension (a bookmarklet needs no install or store review); no paid feed;
untracked products on a list page are not saved (they'd have no ingredient to show under).

**Verified:** 1075 tests pass. End to end on a local server with a throwaway database: captured Coles
salted butter in the built-in browser, the review suggested Butter, Save tracked it at $14.00/kg and
saved the build id; the server's own "Check now" and a Coles search (12 results) then worked. The local
server, database and test login were removed afterwards.
