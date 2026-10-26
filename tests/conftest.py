"""Shared pytest fixtures: a Flask app on a throwaway SQLite database."""

import os
import shutil
import sys
import tempfile

import pytest
import werkzeug.security

# Production hashes with scrypt, which takes about 0.25s per call and roughly
# doubled the suite's runtime. Tests only need a valid hash, so use a single
# round of PBKDF2. Patched before app modules import it.
_real_generate_password_hash = werkzeug.security.generate_password_hash


def _fast_password_hash(password, method="pbkdf2:sha256:1", salt_length=16):
    return _real_generate_password_hash(password, method=method,
                                        salt_length=salt_length)


werkzeug.security.generate_password_hash = _fast_password_hash
generate_password_hash = _fast_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the module-level app (created on import of app.py) at a scratch DB, never ./data.
_import_db = os.path.join(tempfile.mkdtemp(), "import.db")
os.environ["DATABASE_PATH"] = _import_db
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-pytest")
for var in ("ADMIN_USERNAME", "ADMIN_PASSWORD"):
    os.environ.pop(var, None)

# An ambient AI key would make tests call a real API. Tests that need one set
# it explicitly and mock the network.
for var in ("OPENROUTER_API_KEY", "OPENROUTER_MODEL"):
    os.environ.pop(var, None)


_template_db = None
_template_cache = None


def _fresh_database(db_path):
    """Copy a schema-built database instead of running the schema per test.

    Creating the schema on disk took about 0.17s per test; copying the file
    takes a millisecond. ``init_db`` still runs on the copy, so migrations are
    exercised exactly as in production.
    """
    global _template_db
    if _template_db is None:
        _template_db = os.path.join(tempfile.mkdtemp(), "template.db")
        os.environ["DATABASE_PATH"] = _template_db
        from database import init_db
        init_db()
    shutil.copyfile(_template_db, db_path)


@pytest.fixture
def app():
    """Flask app (CSRF off) with a fresh temporary SQLite database."""
    global _template_cache
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    _fresh_database(db_path)
    os.environ["DATABASE_PATH"] = db_path

    from app import create_app
    from jinja2 import FileSystemBytecodeCache
    application = create_app(testing=True)
    # Each test builds a new app and Jinja environment; share compiled
    # templates between them instead of recompiling every page per test.
    if _template_cache is None:
        _template_cache = FileSystemBytecodeCache(tempfile.mkdtemp())
    application.jinja_env.bytecode_cache = _template_cache
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
