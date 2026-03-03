"""
Database connection management for SeekDB (MySQL-compatible protocol).
"""

from __future__ import annotations

import os
import time
import mysql.connector
from dotenv import load_dotenv

load_dotenv()


_UNSET = object()


_SESSION_TIMEOUT = 600   # seconds — long enough for index rebuilds


def get_connection(database: str | None = _UNSET):
    """Return a MySQL connection to SeekDB with generous session timeouts.

    Pass database=None to connect without selecting any database
    (needed for CREATE DATABASE).  Omit the argument to use the
    SEEKDB_DATABASE env-var (default: 'health_demo').
    """
    if database is _UNSET:
        database = os.getenv("SEEKDB_DATABASE", "health_demo")

    kwargs = dict(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", 2881)),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        connection_timeout=30,
        use_pure=True,   # avoid C-extension charset lookup on macOS
    )
    if database is not None:
        kwargs["database"] = database

    conn = mysql.connector.connect(**kwargs)
    # Apply long session timeouts on every connection so DDL and bulk
    # operations are never dropped mid-execution.
    # ob_query_timeout / ob_trx_timeout are OceanBase-specific (in microseconds).
    # net_read/write_timeout are MySQL-compat variables.
    cur = conn.cursor()
    for stmt in (
        f"SET SESSION ob_query_timeout    = {_SESSION_TIMEOUT * 1_000_000}",
        f"SET SESSION ob_trx_timeout      = {_SESSION_TIMEOUT * 1_000_000}",
        f"SET SESSION net_read_timeout    = {_SESSION_TIMEOUT}",
        f"SET SESSION net_write_timeout   = {_SESSION_TIMEOUT}",
        f"SET SESSION wait_timeout        = {_SESSION_TIMEOUT}",
        f"SET SESSION interactive_timeout = {_SESSION_TIMEOUT}",
    ):
        try:
            cur.execute(stmt)
        except Exception:
            pass  # skip variables not supported by this SeekDB build
    cur.close()
    return conn


def wait_for_seekdb(max_retries: int = 30, interval: int = 5) -> bool:
    """Poll SeekDB until it can actually execute a query (not just accept TCP).

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
                connection_timeout=5,
                use_pure=True,
            )
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchall()
            cur.close()
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
