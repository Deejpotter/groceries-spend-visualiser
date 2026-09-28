"""Session-based authentication for Grocery Visualiser."""

import functools
from flask import session, redirect, url_for, request, current_app
from werkzeug.security import generate_password_hash, check_password_hash


def login_user(username: str):
    """Store username in session to log in."""
    session["username"] = username
    session.permanent = False


def logout_user():
    """Clear session to log out."""
    session.pop("username", None)


def get_current_user():
    """Return the username of the logged-in user, or None."""
    return session.get("username")


def is_logged_in():
    """Check if a user is logged in."""
    return "username" in session


def login_required(f):
    """Decorator: redirect to login if user is not authenticated."""
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def hash_password(password: str) -> str:
    """Hash a password for storage."""
    return generate_password_hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its stored hash."""
    return check_password_hash(password_hash, password)
