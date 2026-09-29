"""Security behaviour: setup lockout, safe redirects, CSRF, escaping."""

from conftest import create_user, login


def test_setup_redirects_when_no_account(client):
    resp = client.get("/ingredients")
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]


def test_setup_cannot_reset_existing_admin(client, db):
    create_user(client, "admin", "original-pass")
    resp = client.post("/setup", data={
        "username": "admin", "password": "hijacked-pass", "confirm_password": "hijacked-pass",
    })
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    # Original password still works; the attacker's doesn't.
    bad = client.post("/login", data={"username": "admin", "password": "hijacked-pass"})
    assert "/login" in bad.headers["Location"]
    good = client.post("/login", data={"username": "admin", "password": "original-pass"})
    assert good.headers["Location"].endswith("/")


def test_setup_cannot_add_second_user(client, db):
    create_user(client, "admin", "original-pass")
    client.post("/setup", data={"username": "intruder", "password": "12345678", "confirm_password": "12345678"})
    assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_login_ignores_external_next(client):
    create_user(client, "admin", "password123")
    for target in ("https://evil.example/", "//evil.example/", "javascript:alert(1)"):
        resp = client.post(f"/login?next={target}", data={"username": "admin", "password": "password123"})
        assert resp.headers["Location"] == "/", target


def test_login_follows_local_next(client):
    create_user(client, "admin", "password123")
    resp = client.post("/login?next=/recipes", data={"username": "admin", "password": "password123"})
    assert resp.headers["Location"] == "/recipes"


def test_env_admin_created_on_first_request(client, db, monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "envadmin")
    monkeypatch.setenv("ADMIN_PASSWORD", "envpass123")
    resp = client.get("/login")
    assert resp.status_code == 200
    assert db.execute("SELECT username FROM users").fetchone()["username"] == "envadmin"


def test_csrf_enabled_outside_tests():
    from app import create_app
    production_like = create_app(testing=False)
    assert production_like.config["WTF_CSRF_ENABLED"] is True
    c = production_like.test_client()
    resp = c.post("/setup", data={"username": "x", "password": "12345678", "confirm_password": "12345678"})
    assert resp.status_code == 400  # rejected: no CSRF token


def test_ingredient_options_are_json_not_html(client, db):
    login(client)
    db.execute("INSERT INTO ingredients (name, category, unit) VALUES ('<script>x</script>', 'other', 'each')")
    db.commit()
    resp = client.get("/ingredients/options.json")
    assert resp.is_json
    assert resp.get_json()[0]["name"] == "<script>x</script>"


def test_state_changes_reject_get(client, db):
    login(client)
    for path in ("/recipes/delete/1", "/ingredients/delete/1", "/meal-plan/generate",
                 "/shopping-list/toggle/1", "/pantry/delete/1", "/meal-rules/delete/1"):
        assert client.get(path).status_code == 405, path


def test_staging_banner_only_when_app_env_set(client, monkeypatch):
    login(client)
    assert b"env-banner" not in client.get("/").data
    monkeypatch.setenv("APP_ENV", "staging")
    assert b"STAGING" in client.get("/").data
