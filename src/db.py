"""
Database connection management for SeekDB (MySQL-compatible protocol).
"""

import os
import time
import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def get_connection(database: str | None = None):
    """Return a MySQL connection to SeekDB."""
    return mysql.connector.connect(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", 2881)),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        database=database or os.getenv("SEEKDB_DATABASE", "health_demo"),
        connection_timeout=10,
    )


def wait_for_seekdb(max_retries: int = 30, interval: int = 5) -> bool:
    """
    Poll SeekDB until it accepts connections.
    Returns True when ready, raises RuntimeError on timeout.
    """
    print("⏳ Waiting for SeekDB to be ready...")
    for attempt in range(1, max_retries + 1):
        try:
            conn = mysql.connector.connect(
                host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
                port=int(os.getenv("SEEKDB_PORT", 2881)),
                user=os.getenv("SEEKDB_USER", "root"),
                password=os.getenv("SEEKDB_PASSWORD", ""),
                connection_timeout=3,
            )
            conn.close()
            print("✅ SeekDB is ready.")
            return True
        except Exception:
            print(f"   attempt {attempt}/{max_retries} — not ready yet, retrying in {interval}s…")
            time.sleep(interval)
    raise RuntimeError(
        "SeekDB did not become ready in time.\n"
        "Run: docker-compose up -d  and wait ~30 s before retrying."
    )
