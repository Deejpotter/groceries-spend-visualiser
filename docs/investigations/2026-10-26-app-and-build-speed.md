# App, test and build speed

- **Date:** 2026-10-26
- **Investigator:** Claude Code (Opus 5.5)
- **Status:** Pushed to `dev`; staging deploy to confirm

## 1. What was asked

"Can we make this faster? The whole app and build and everything."

## 2. What was checked

- Test suite: `pytest --durations`, then cProfile on a batch of integration tests.
- Pages: every GET page, timed with the Flask test client against a copy of the
  local database seeded with 7,971 bank transactions and 8,000 purchase lines
  (the local database had no purchases or bank data). Median of 7 runs, because
  single runs on this PC varied by up to 3x.
- `Dockerfile`, `.dockerignore`, `gunicorn.conf.py`, `.github/workflows/ci.yml`.

## 3. What the evidence showed

### Tests (4m 56s)
1. No single slow test: about 0.45s per test, all of it setup.
2. **Password hashing:** scrypt costs about 0.25s, and most tests hash twice
   (create the user, then log in).
3. **Schema creation:** `executescript(SCHEMA)` on a new file cost 0.165s per test.
4. **Template compiling:** each test builds a new app, which recompiles every
   Jinja template it renders.
5. One process on a 12-core machine.

### Pages
| Page | Cause |
|---|---|
| `/subscriptions/suggest` (2.45s, plus up to 60s for AI) | Streams built twice and transactions filtered 3x; `strptime` and merchant normalisation repeated for every row; the automatic AI review ran inside the page request |
| `/spend/products` (1.41s) | Two correlated subqueries per product, with no index on `purchases.product_name` |
| `/` (458ms), `/spend` (387ms), `/subscriptions` (226ms) | `datetime.strptime` on every row (about 30µs each) |

A first attempt at the suggestions page got slower (about 3s) because the new
fuzzy merchant match compared every pair of merchants with `SequenceMatcher`
(10,122 calls, 1.2s). It now runs cheap length and quick-ratio checks first,
with caching.

### Build
- `pip install --no-cache-dir` downloads every package on each rebuild of that layer.
- The CI Docker job builds with no layer cache.

## 4. What was changed

- `tests/conftest.py`: one-round PBKDF2 hash in tests only; the schema is built
  once per process and copied for each test (`init_db` still runs on the copy);
  a shared Jinja bytecode cache.
- `pytest.ini` + `pytest-xdist`: tests run on all cores by default.
- `models.parse_ymd`: `date.fromisoformat` with a `strptime` fallback for
  unpadded legacy dates. Used by every service `_as_date` helper.
- `services/recurring.py`: `lru_cache` on `normalise_merchant`,
  `is_ignored_description` and `_similar_keys`; cheap pre-checks before
  `SequenceMatcher`.
- `routes/subscriptions.py`: streams built once and passed into
  `build_candidates`. The automatic AI review moved to
  `POST /subscriptions/suggest/review-new`, which the page triggers through
  HTMX after it renders. It runs once per load; on failure the error shows in
  the panel and it doesn't loop.
- `database.py`: index `idx_purchases_product (product_name, order_date)`,
  added through `SCHEMA` so existing databases get it on start.
- `Dockerfile`: BuildKit pip cache mount. `ci.yml`: buildx with the GitHub
  Actions layer cache.

## 5. Results

| | Before | After |
|---|---|---|
| Full test suite | 4m 56s | 39s |
| `/subscriptions/suggest` (no AI) | 2,451 ms | 364 ms |
| `/spend/products` | 1,406 ms | 77 ms |
| `/` dashboard | 458 ms | 106 ms |
| `/spend` | 387 ms | 157 ms |
| `/subscriptions` | 226 ms | 55 ms |
| Suggestions page with unreviewed merchants | waited on OpenRouter (up to 60s) | renders immediately; the AI result swaps in |

## 6. Deliberately not changed

- Production password hashing stays scrypt.
- `curl` stays in the image: Coolify's health check may rely on it.
- Gunicorn worker and thread counts: no evidence they are the bottleneck.

## 7. Verification

- 673 tests pass (`python -m pytest`).
- Docker isn't running on this PC, so the Dockerfile change is verified by the
  staging deploy. If Coolify builds without BuildKit, `RUN --mount` fails the
  build; the fix is to revert that line.
- The CI change can't be checked while GitHub Actions billing is cancelled.

## 8. Commands to reproduce

```
python -m pytest --durations=15 -p no:xdist
python -m cProfile -s tottime -m pytest -q -p no:xdist tests/test_integration.py -k TestPantry
```
