"""Shared pytest fixtures: a Flask app on a throwaway SQLite database."""

import os
import sys
import tempfile

import pytest
from werkzeug.security import generate_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the module-level app (created on import of app.py) at a scratch DB, never ./data.
_import_db = os.path.join(tempfile.mkdtemp(), "import.db")
os.environ["DATABASE_PATH"] = _import_db
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
for var in ("ADMIN_USERNAME", "ADMIN_PASSWORD"):
    os.environ.pop(var, None)


@pytest.fixture
def app():
    """Flask app (CSRF off) with a fresh temporary SQLite database."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    os.environ["DATABASE_PATH"] = db_path

    from app import create_app
    application = create_app(testing=True)
    yield application

    os.environ["DATABASE_PATH"] = _import_db
    os.unlink(db_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    """Database connection for direct SQL setup/assertions."""
    from database import get_db
    with app.app_context():
        yield get_db()


def create_user(client, username, password):
    from database import get_db
    with client.application.app_context():
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash) VALUES (?, ?)",
            (username, generate_password_hash(password)),
        )
        conn.commit()


def login(client, username="tester", password="test12345"):
    """Create a user (if needed) and log the test client in."""
    create_user(client, username, password)
    client.post("/login", data={"username": username, "password": password})
