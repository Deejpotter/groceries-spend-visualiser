"""App icon, manifest and page metadata."""

import re

from tests.conftest import create_user, login


def test_favicon_needs_no_login(client):
    create_user(client, "tester", "test12345")
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.mimetype == "image/vnd.microsoft.icon"
    assert response.data[:4] == b"\x00\x00\x01\x00"


def test_manifest_lists_icons_that_exist(client):
    create_user(client, "tester", "test12345")
    response = client.get("/manifest.webmanifest")
    assert response.status_code == 200
    assert response.mimetype == "application/manifest+json"
    data = response.get_json()
    assert data["name"] == "Larder"
    assert data["start_url"] == "/"
    assert {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
    assert any(i["purpose"] == "maskable" for i in data["icons"])
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_pages_carry_icon_and_metadata(client):
    login(client)
    page = client.get("/").get_data(as_text=True)
    for needle in ('rel="icon" href="/static/icons/icon.svg', 'rel="apple-touch-icon"',
                   'rel="manifest" href="/manifest.webmanifest"', 'name="description"',
                   'name="theme-color"', 'name="robots" content="noindex, nofollow"', 'property="og:title"'):
        assert needle in page
    for path in re.findall(r'href="(/static/icons/[^"?]+)', page):
        assert client.get(path).status_code == 200


def test_login_page_has_icon_too(client):
    create_user(client, "tester", "test12345")
    page = client.get("/login").get_data(as_text=True)
    assert 'rel="icon"' in page and 'rel="manifest"' in page


def test_share_image_is_https_on_a_real_host(client):
    create_user(client, "tester", "test12345")
    page = client.get("/login", base_url="http://groceries.example.com").get_data(as_text=True)
    assert 'property="og:image" content="https://groceries.example.com/static/icons/icon-512.png' in page
