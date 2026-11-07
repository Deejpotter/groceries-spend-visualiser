"""Network access to the stores' public product pages. The only module that fetches.

None of these are documented APIs: they are what each store's own website calls. They
can change or start blocking without notice, so every failure is a StoreError and the
caller carries on with the prices it already has.
"""

import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional

from services import store_prices as sp

TIMEOUT = 15
HTML_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
COLES_PAGE_TRIES = 3

# Coles' JSON routes need the site's current build id. Its HTML pages sometimes answer
# with a bot check instead, but the JSON routes don't, so the id is kept for the life of
# the process and only fetched again when Coles deploys (the data route then 404s).
_coles_build_cache = {"id": None}
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


class StoreError(Exception):
    """A store could not be reached or its reply could not be read."""


class StoreClient:
    """One session across stores: keeps cookies (Woolworths) and the Coles build id."""

    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self._woolworths_ready = False

    def _get(self, url: str, accept: str = "application/json") -> str:
        if accept == "text/html":
            accept = HTML_ACCEPT
        request = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-AU,en;q=0.9"})
        try:
            with self.opener.open(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise StoreError(f"{urllib.parse.urlparse(url).netloc} returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StoreError(f"Couldn't reach {urllib.parse.urlparse(url).netloc}: {exc}") from exc

    def _json(self, url: str) -> Dict:
        text = self._get(url)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise StoreError(f"{urllib.parse.urlparse(url).netloc} didn't send product data "
                             "(it may be blocking automated requests).") from exc

    def _woolworths(self):
        if not self._woolworths_ready:
            self._get(sp.STORES["woolworths"]["base"] + "/", accept="text/html")
            self._woolworths_ready = True

    def _coles_build_id(self, page_url: Optional[str] = None) -> str:
        """Read from a product page (allowed by robots.txt) or the home page."""
        if not _coles_build_cache["id"]:
            pages = [page_url] if page_url else []
            pages += [sp.STORES["coles"]["base"] + "/"] * COLES_PAGE_TRIES
            for attempt, page in enumerate(pages[:COLES_PAGE_TRIES]):
                try:
                    build = sp.coles_build_id(self._get(page, accept="text/html"))
                except StoreError:
                    build = None
                if build:
                    _coles_build_cache["id"] = build
                    break
                if attempt + 1 < COLES_PAGE_TRIES:
                    time.sleep(1 + attempt)
            else:
                raise StoreError("Coles answered with a bot check instead of its page. Try again later.")
        return _coles_build_cache["id"]

    def _coles_data(self, path: str, page_url: Optional[str] = None) -> Dict:
        url = f"{sp.STORES['coles']['base']}/_next/data/{{}}/en/{path}"
        try:
            return self._json(url.format(self._coles_build_id(page_url)))
        except StoreError as exc:
            if "HTTP 404" not in str(exc):
                raise
            _coles_build_cache["id"] = None  # Coles deployed a new build
            return self._json(url.format(self._coles_build_id(page_url)))

    def search(self, store: str, query: str) -> List[Dict]:
        key = sp.store_key(store)
        query = (query or "").strip()
        if not key or not query:
            return []
        q = urllib.parse.quote(query)
        if key == "woolworths":
            self._woolworths()
            return sp.parse_woolworths_search(self._json(
                f"{sp.STORES[key]['base']}/apis/ui/Search/products?searchTerm={q}&pageSize={sp.SEARCH_LIMIT}"))
        if key == "coles":
            return sp.parse_coles_search(self._coles_data(f"search/products.json?q={q}"))
        return sp.parse_aldi_search(self._get(f"{sp.STORES[key]['base']}/results?q={q}", accept="text/html"))

    def product(self, store: str, code: str, url: Optional[str] = None) -> Optional[Dict]:
        """Today's price for one product, or None when the store no longer lists it."""
        key = sp.store_key(store)
        if key == "woolworths":
            self._woolworths()
            return sp.parse_woolworths_product(self._json(
                f"{sp.STORES[key]['base']}/apis/ui/product/detail/{urllib.parse.quote(str(code))}"))
        if key == "coles":
            slug = sp.coles_slug(url) or f"product-{code}"
            payload = self._coles_data(f"product/{slug}.json", url if sp.coles_slug(url) else None)
            redirect = ((payload.get("pageProps") or {}).get("__N_REDIRECT") or "")
            if redirect and sp.coles_slug(redirect):
                payload = self._coles_data(f"product/{sp.coles_slug(redirect)}.json")
            return sp.parse_coles_product(payload)
        if key == "aldi":
            if not url:
                raise StoreError("An Aldi product needs its page address.")
            return sp.parse_aldi_product(self._get(url, accept="text/html"), url)
        raise StoreError(f"Unknown store: {store}")
