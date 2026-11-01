"""The post-deploy staging check script, run against the Flask test client."""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import staging_check as sc  # noqa: E402
from test_savings_advice import _history, _store  # noqa: E402

KEY = "staging-key-1"


def _fetcher(client):
    def fetch(url, key=None, timeout=20):
        path = url.split("http://staging", 1)[1]
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        r = client.get(path, headers=headers, follow_redirects=True)
        return r.status_code, r.get_data(as_text=True)
    return fetch


def _setup(app, history=True):
    from database import get_db, set_setting
    with app.app_context():
        set_setting("mcp_api_key", KEY)
        if history:
            _store(get_db(), _history())
        get_db().commit()


def test_commit_matches_short_and_full():
    full = "4de13a747eece9b0457dd61b0165bce0b0179790"
    assert sc.commit_matches(full, "4de13a7")
    assert sc.commit_matches("4de13a7", full)
    assert not sc.commit_matches("4de13a", full)
    assert not sc.commit_matches(None, full)
    assert not sc.commit_matches("fd802c9", full)


def test_check_savings_accepts_null_and_good_payload():
    assert sc.check_savings({"savings": None}) == []
    good = {"income": 3000, "costs": 1000, "surplus": 2000, "savings": 800, "long_term": 790,
            "emergency_target": 1950, "enough_history": True, "overspending": False}
    assert sc.check_savings({"savings": good}) == []


def test_check_savings_flags_bad_payloads():
    bad = {"income": 3000, "costs": 1000, "surplus": 500, "savings": 800, "long_term": 790,
           "emergency_target": 1950, "enough_history": True, "overspending": False}
    assert sc.check_savings({"savings": bad}) == ["suggested savings are more than the surplus"]
    assert sc.check_savings({"savings": {"income": 1}})[0].startswith("savings is missing")


def test_run_passes_on_a_healthy_site(app, monkeypatch):
    monkeypatch.setenv("SOURCE_COMMIT", "fd802c99e0806e2599a32af5b11f12f414495b0f")
    _setup(app)
    problems = sc.run("http://staging", "fd802c9", KEY, fetcher=_fetcher(app.test_client()),
                      sleep=lambda s: None)
    assert problems == []


def test_run_waits_then_reports_the_wrong_commit(app, monkeypatch):
    monkeypatch.setenv("SOURCE_COMMIT", "aaaaaaaaaaaa")
    _setup(app)
    naps = []
    problems = sc.run("http://staging", "bbbbbbbbbbbb", KEY, timeout=0,
                      fetcher=_fetcher(app.test_client()), sleep=naps.append)
    assert problems == ["staging runs aaaaaaaaaaaa, expected bbbbbbbbbbbb after 0s."]


def test_run_explains_a_missing_commit(app, monkeypatch):
    monkeypatch.delenv("SOURCE_COMMIT", raising=False)
    _setup(app)
    problems = sc.run("http://staging", "bbbbbbb", KEY, timeout=0,
                      fetcher=_fetcher(app.test_client()), sleep=lambda s: None)
    assert "Include Source Commit in Build" in problems[0]


def test_run_flags_a_wrong_api_key(app):
    _setup(app, history=False)
    problems = sc.run("http://staging", None, "wrong", fetcher=_fetcher(app.test_client()))
    assert "/api/v1/budget/savings returned 401" in problems


def test_run_without_key_says_so(app):
    _setup(app, history=False)
    problems = sc.run("http://staging", None, None, fetcher=_fetcher(app.test_client()))
    assert problems == ["STAGING_API_KEY is not set, so the API checks did not run"]


def test_wait_for_commit_polls_until_it_appears():
    answers = iter([(502, ""), (200, json.dumps({"commit": "old1234"})),
                    (200, json.dumps({"commit": "new1234"}))])
    naps = []
    ok, status, body = sc.wait_for_commit("http://s", "new1234", 60, 5,
                                          fetcher=lambda url: next(answers), sleep=naps.append)
    assert ok and sc.deployed_commit(body) == "new1234"
    assert naps == [5, 5]


def test_main_needs_a_url(monkeypatch, capsys):
    monkeypatch.delenv("STAGING_URL", raising=False)
    assert sc.main([]) == 2


def test_health_problem_names_the_cause():
    assert "down or unreachable" in sc.health_problem(0, "", "abc1234", 60)
    assert "down or unreachable" in sc.health_problem(502, "", "abc1234", 60)
    assert "from before the staging check" in sc.health_problem(200, '{"status": "ok"}', "abc1234", 60)
    assert "Include Source Commit" in sc.health_problem(200, '{"status": "ok", "commit": null}', "abc1234", 60)
    assert "did not return JSON" in sc.health_problem(200, "<html>", "abc1234", 60)
