"""
User diary CRUD and personal baseline materialisation.

Design
──────
Every time the user submits a diary entry we:
  1. Embed the text
  2. Insert into user_diaries with full score breakdown
  3. Recompute and upsert user_baseline (centroid of all embeddings so far)

SeekDB specific
───────────────
  Baseline embedding is stored as VECTOR(384) — we read it back to compute
  cosine distance in Python without an extra round-trip.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from dataclasses import dataclass

from .db import get_connection
from .embedder import embed, vec_sql


@dataclass
class UserDiary:
    id: int
    diary_date: str
    diary_text: str
    glucose_level: float | None
    blood_pressure: int | None
    risk_score: float
    risk_level: str
    trajectory_score: float
    trend_score: float
    baseline_score: float
    created_at: str


# ── Writes ─────────────────────────────────────────────────────────────────

def save_diary(
    diary_text: str,
    glucose_level: float | None,
    blood_pressure: int | None,
    risk_score: float,
    risk_level: str,
    trajectory_score: float,
    trend_score: float,
    baseline_score: float,
    embedding: list[float],
) -> int:
    """Insert a new diary entry and refresh the personal baseline."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO user_diaries
            (diary_date, diary_text, glucose_level, blood_pressure,
             risk_score, risk_level, trajectory_score, trend_score,
             baseline_score, diary_embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            date.today().isoformat(),
            diary_text,
            glucose_level,
            blood_pressure,
            round(risk_score, 2),
            risk_level,
            round(trajectory_score, 2),
            round(trend_score, 2),
            round(baseline_score, 2),
            vec_sql(embedding),
        ),
    )
    conn.commit()
    new_id = cursor.lastrowid

    # Refresh baseline after every write
    _refresh_baseline(conn, cursor)

    cursor.close()
    conn.close()
    return new_id


def _refresh_baseline(conn, cursor) -> None:
    """
    Recompute the baseline as the centroid of ALL user diary embeddings.
    Stored as a single row (overwritten each time) in user_baseline.

    SeekDB stores VECTOR as JSON array text, so we decode it in Python and
    average the vectors — no server-side aggregation needed for this demo.
    """
    cursor.execute(
        "SELECT diary_embedding, glucose_level FROM user_diaries ORDER BY id"
    )
    rows = cursor.fetchall()
    if not rows:
        return

    embeddings = []
    glucose_vals = []
    for raw_vec, gl in rows:
        if raw_vec:
            embeddings.append(json.loads(raw_vec))
        if gl is not None:
            glucose_vals.append(float(gl))

    if not embeddings:
        return

    # Centroid
    n = len(embeddings)
    dim = len(embeddings[0])
    centroid = [sum(embeddings[i][d] for i in range(n)) / n for d in range(dim)]
    avg_glucose = sum(glucose_vals) / len(glucose_vals) if glucose_vals else None

    cursor.execute("DELETE FROM user_baseline")
    cursor.execute(
        """
        INSERT INTO user_baseline (entry_count, avg_glucose, baseline_embedding)
        VALUES (%s, %s, %s)
        """,
        (n, avg_glucose, vec_sql(centroid)),
    )
    conn.commit()


# ── Reads ──────────────────────────────────────────────────────────────────

def get_diary_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_diaries")
    count = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return count


def get_recent_diaries(n: int = 30) -> list[UserDiary]:
    """Return the N most recent diaries, newest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, diary_date, diary_text, glucose_level, blood_pressure,
               risk_score, risk_level, trajectory_score, trend_score,
               baseline_score, created_at
        FROM user_diaries
        ORDER BY id DESC
        LIMIT %s
        """,
        (n,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    return [
        UserDiary(
            id=r[0],
            diary_date=str(r[1]),
            diary_text=r[2],
            glucose_level=r[3],
            blood_pressure=r[4],
            risk_score=float(r[5]) if r[5] is not None else 0.0,
            risk_level=r[6] or "low",
            trajectory_score=float(r[7]) if r[7] is not None else 0.0,
            trend_score=float(r[8]) if r[8] is not None else 0.0,
            baseline_score=float(r[9]) if r[9] is not None else 0.0,
            created_at=str(r[10]),
        )
        for r in rows
    ]


def get_glucose_trend(days: int = 14) -> list[tuple[str, float]]:
    """Return (date, glucose_level) pairs for the last N days, oldest first."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT diary_date, glucose_level
        FROM user_diaries
        WHERE glucose_level IS NOT NULL
        ORDER BY id DESC
        LIMIT %s
        """,
        (days,),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    # reverse so oldest first
    return [(str(r[0]), float(r[1])) for r in reversed(rows)]


def get_baseline() -> tuple[list[float], float, int] | None:
    """
    Return (centroid_embedding, avg_glucose, entry_count) or None if no baseline.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT baseline_embedding, avg_glucose, entry_count FROM user_baseline LIMIT 1"
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row or not row[0]:
        return None
    centroid = json.loads(row[0])
    avg_glucose = float(row[1]) if row[1] is not None else 0.0
    entry_count = int(row[2])
    return centroid, avg_glucose, entry_count
