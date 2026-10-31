"""Check a deployed Larder: right commit, pages up, API answers, savings sane.

Standard library only, so CI can run it without installing the app.

    python scripts/staging_check.py --url https://staging.example --commit <sha>

STAGING_API_KEY (the site's mcp_api_key) enables the read-only API checks.
Exits non-zero with a list of problems when anything is wrong.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API_PATHS = ("/api/v1/server-info", "/api/v1/pay-cycle", "/api/v1/spend/summary",
             "/api/v1/plan", "/api/v1/budget/savings")
SAVINGS_KEYS = ("income", "costs", "surplus", "savings", "long_term", "emergency_target",
                "enough_history", "overspending")


def fetch(url, key=None, timeout=20):
    """(status, body text) for a GET; status 0 when the site can't be reached."""
    request = urllib.request.Request(url, headers={"User-Agent": "larder-staging-check"})
    if key:
        request.add_header("Authorization", f"Bearer {key}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        return 0, str(exc)


def deployed_commit(body):
    """The commit /health reports, or None."""
    try:
        return json.loads(body).get("commit") or None
    except (ValueError, AttributeError):
        return None


def commit_matches(deployed, expected):
    """True when the deployed commit is the expected one (short or full SHAs)."""
    if not deployed or not expected:
        return False
    n = min(len(deployed), len(expected))
    return n >= 7 and deployed[:n].lower() == expected[:n].lower()


def check_savings(payload):
    """Problems with a /budget/savings payload; empty when it looks right."""
    advice = payload.get("savings") if isinstance(payload, dict) else None
    if advice is None:
        return []
    problems = [f"savings is missing {key}" for key in SAVINGS_KEYS if key not in advice]
    if problems:
        return problems
    if advice["income"] <= 0:
        problems.append("savings income is not above zero")
    if min(advice["savings"], advice["long_term"]) < 0:
        problems.append("a suggested savings amount is negative")
    if advice["savings"] + advice["long_term"] > max(advice["surplus"], 0) + 0.01:
        problems.append("suggested savings are more than the surplus")
    if advice["overspending"] and advice["savings"] + advice["long_term"] > 0:
        problems.append("savings suggested while overspending")
    return problems


def wait_for_commit(base, expected, timeout, interval, fetcher=fetch, sleep=time.sleep):
    """Poll /health until it reports `expected`. Returns (ok, last commit seen)."""
    deadline = time.monotonic() + timeout
    seen = None
    while True:
        status, body = fetcher(base + "/health")
        seen = deployed_commit(body) if status == 200 else None
        if commit_matches(seen, expected):
            return True, seen
        if time.monotonic() >= deadline:
            return False, seen
        sleep(interval)


def run(base, expected, key, timeout=1200, interval=20, fetcher=fetch, sleep=time.sleep):
    """All checks; returns a list of problems."""
    base = base.rstrip("/")
    problems = []
    if expected:
        ok, seen = wait_for_commit(base, expected, timeout, interval, fetcher, sleep)
        if not ok:
            if seen is None:
                problems.append("/health reports no commit. Turn on 'Include Source Commit in "
                                "Build' in Coolify (Advanced) and redeploy.")
            else:
                problems.append(f"staging runs {seen[:12]}, expected {expected[:12]} "
                                f"after {timeout}s")
            return problems
    status, body = fetcher(base + "/login")
    if status != 200 or "Larder" not in body:
        problems.append(f"/login returned {status}")
    if not key:
        problems.append("STAGING_API_KEY is not set, so the API checks did not run")
        return problems
    for path in API_PATHS:
        status, body = fetcher(base + path, key)
        if status != 200:
            problems.append(f"{path} returned {status}")
            continue
        try:
            payload = json.loads(body)
        except ValueError:
            problems.append(f"{path} did not return JSON")
            continue
        if path.endswith("/budget/savings"):
            problems.extend(check_savings(payload))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.getenv("STAGING_URL"))
    parser.add_argument("--commit", default=os.getenv("EXPECTED_COMMIT"))
    parser.add_argument("--timeout", type=int, default=1200)
    args = parser.parse_args(argv)
    if not args.url:
        print("Set STAGING_URL (a repository variable) or pass --url.")
        return 2
    problems = run(args.url, args.commit, os.getenv("STAGING_API_KEY"), args.timeout)
    if problems:
        print("Staging check failed:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print(f"Staging check passed for {args.url}" + (f" at {args.commit[:12]}" if args.commit else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
