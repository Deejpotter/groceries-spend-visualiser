"""Tests for database persistence helpers: fresh-DB detection, backups, stats."""

import os
import sqlite3

import pytest

import database


@pytest.fixture
def temp_db_path(tmp_path, monkeypatch):
    """Point DATABASE_PATH at a scratch file and restore it afterwards."""
    path = str(tmp_path / "scratch.db")
    monkeypatch.setenv("DATABASE_PATH", path)
    return path


def _add_user(db):
    db.execute(
        "INSERT INTO users (username, password_hash) VALUES ('keeper', 'hash')"
    )
    db.commit()


def test_init_db_reports_fresh_database_then_existing(temp_db_path):
    assert database.init_db() is False
    assert os.path.exists(temp_db_path)
    assert database.init_db() is True


def test_database_stats_on_missing_file(temp_db_path):
    stats = database.database_stats()
    assert stats["exists"] is False
    assert stats["path"] == temp_db_path
    assert stats["counts"] == {}


def test_database_stats_reports_row_counts(temp_db_path):
    database.init_db()
    db = database._connect()
    try:
        _add_user(db)
    finally:
        db.close()

    stats = database.database_stats()
    assert stats["exists"] is True
    assert stats["size_bytes"] > 0
    assert stats["counts"]["users"] == 1
    assert stats["counts"]["ingredients"] == 0


def test_backup_database_copies_existing_rows(temp_db_path, tmp_path):
    database.init_db()
    db = database._connect()
    try:
        _add_user(db)
    finally:
        db.close()

    dest = database.backup_database(dest_dir=str(tmp_path / "backups"))

    assert os.path.exists(dest)
    assert dest.endswith(".db")

    backup = sqlite3.connect(dest)
    try:
        rows = backup.execute("SELECT username FROM users").fetchall()
    finally:
        backup.close()
    assert [("keeper",)] == rows


def test_backup_database_defaults_next_to_the_source(temp_db_path):
    database.init_db()
    dest = database.backup_database()
    assert os.path.dirname(dest) == os.path.dirname(temp_db_path)
    assert os.path.exists(dest)
