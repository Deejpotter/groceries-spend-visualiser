"""Initialise or upgrade the database schema: `python init_db.py` (same as `flask --app app init-db`)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, get_database_path


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {get_database_path()}")
