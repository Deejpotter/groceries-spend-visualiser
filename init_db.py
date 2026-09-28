"""Database initialization CLI script."""

import os
import sys

# Ensure the app root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import init_db, DATABASE_PATH


def main():
    print(f"Initializing database at: {DATABASE_PATH}")

    # Ensure parent directory exists
    parent = os.path.dirname(DATABASE_PATH)
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)

    if not os.path.exists(DATABASE_PATH):
        init_db()
        print("Database created successfully.")
    else:
        # Re-run to ensure all tables exist (idempotent)
        init_db()
        print("Database already exists. Schema verified/updated.")

    print("Done.")


if __name__ == "__main__":
    main()
