"""
Embed synthetic population records and bulk-insert into SeekDB.
"""

import time

from tqdm import tqdm

from .db import get_connection
from .embedder import get_embedder, vec_sql
from .schema import (
    CREATE_DATABASE, CREATE_TABLE, CREATE_USER_DIARIES, CREATE_USER_BASELINE,
    CREATE_EXPERIMENTS, CREATE_EXPERIMENT_LOGS, CREATE_RISK_FEEDBACKS,
    CREATE_USER_PROFILE, CREATE_EMOTION_COUPLING,
    DROP_TABLE, DATABASE,
)
from .data_generator import DiaryRecord

_BATCH_SIZE = 16          # smaller batches avoid OceanBase max_allowed_packet limit
_INSERT_RETRIES = 3       # retry a batch this many times on lost-connection

# Columns to add per table: (table, column, definition)
# OceanBase does not support ADD COLUMN IF NOT EXISTS, so we check
# INFORMATION_SCHEMA.COLUMNS at runtime before issuing each ALTER.
_EMOTION_COLUMNS: list[tuple[str, str, str]] = [
    ("user_diaries",  "emotion_score",         "FLOAT"),
    ("user_diaries",  "anxiety_score",          "FLOAT"),
    ("user_profile",  "emotion_risk_coupling",  "FLOAT DEFAULT 0.0"),
    ("user_profile",  "emotion_volatility",     "FLOAT DEFAULT 0.0"),
    ("user_profile",  "emotion_amplification",  "FLOAT DEFAULT 1.0"),
    ("user_profile",  "emotion_active",         "TINYINT(1) DEFAULT 0"),
]


def _ensure_columns(cursor) -> None:
    """Add emotion columns that don't exist yet (OceanBase-compatible)."""
    cursor.execute("SELECT DATABASE()")
    db_name = cursor.fetchone()[0]
    for table, column, definition in _EMOTION_COLUMNS:
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
            (db_name, table, column),
        )
        if cursor.fetchone()[0] == 0:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def setup_schema(drop_existing: bool = False) -> None:
    """Create the database and all tables."""
    conn = get_connection(database=None)
    cursor = conn.cursor()
    cursor.execute(CREATE_DATABASE)
    conn.commit()
    cursor.close()
    conn.close()

    conn = get_connection()
    cursor = conn.cursor()
    if drop_existing:
        for tbl in ("emotion_coupling", "user_profile", "risk_feedbacks",
                    "experiment_logs", "experiments",
                    "user_baseline", "user_diaries", "patient_diaries"):
            cursor.execute(f"DROP TABLE IF EXISTS {tbl}")
        print("🗑  Dropped existing tables.")
    cursor.execute(CREATE_TABLE)
    cursor.execute(CREATE_USER_DIARIES)
    cursor.execute(CREATE_USER_BASELINE)
    cursor.execute(CREATE_EXPERIMENTS)
    cursor.execute(CREATE_EXPERIMENT_LOGS)
    cursor.execute(CREATE_RISK_FEEDBACKS)
    cursor.execute(CREATE_USER_PROFILE)
    cursor.execute(CREATE_EMOTION_COUPLING)
    _ensure_columns(cursor)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Schema ready (8 tables + emotion columns).")


def ingest_records(records: list[DiaryRecord]) -> None:
    """Embed and insert all population records into SeekDB.

    Bulk-load strategy (avoids OceanBase HNSW index pressure):
      1. DROP the vector + fulltext indexes on patient_diaries
      2. INSERT all rows in batches (no index maintenance overhead)
      3. Recreate the indexes once after all data is loaded
    """
    embedder = get_embedder()

    texts = [r.diary_text for r in records]
    print(f"⚙️  Embedding {len(texts)} diary entries…")
    embeddings = embedder.encode(texts, batch_size=32, show_progress_bar=True)

    insert_sql = """
        INSERT INTO patient_diaries
            (patient_id, diary_date, diary_text, symptoms_keywords,
             glucose_level, blood_pressure, bmi,
             is_pre_danger, days_to_danger, diary_embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    all_rows = [
        (
            rec.patient_id,
            rec.diary_date.isoformat(),
            rec.diary_text,
            rec.symptoms_keywords,
            rec.glucose_level,
            rec.blood_pressure,
            rec.bmi,
            int(rec.is_pre_danger),
            rec.days_to_danger,
            vec_sql(emb.tolist()),
        )
        for rec, emb in zip(records, embeddings)
    ]

    conn = get_connection()
    cursor = conn.cursor()

    # ── Step 1: drop indexes before bulk load ─────────────────────────────────
    print("🗂  Dropping indexes for bulk load…")
    for stmt in (
        "ALTER TABLE patient_diaries DROP INDEX idx_pop_vec",
        "ALTER TABLE patient_diaries DROP INDEX idx_pop_fts",
    ):
        try:
            cursor.execute(stmt)
            conn.commit()
        except Exception:
            pass  # index may not exist yet (first run after drop_existing)

    # ── Step 2: insert rows ───────────────────────────────────────────────────
    print("💾 Inserting into SeekDB…")
    inserted = 0
    with tqdm(total=len(all_rows)) as bar:
        i = 0
        while i < len(all_rows):
            batch = all_rows[i: i + _BATCH_SIZE]
            for attempt in range(1, _INSERT_RETRIES + 1):
                try:
                    cursor.executemany(insert_sql, batch)
                    conn.commit()
                    break
                except Exception as exc:
                    print(f"\n   ⚠️  Batch @{i} failed (attempt {attempt}/{_INSERT_RETRIES}): {exc}")
                    if attempt == _INSERT_RETRIES:
                        raise
                    time.sleep(3)
                    try:
                        cursor.close()
                        conn.close()
                    except Exception:
                        pass
                    conn = get_connection()
                    cursor = conn.cursor()
            i += len(batch)
            inserted += len(batch)
            bar.update(len(batch))

    cursor.close()
    conn.close()

    # ── Step 3: rebuild indexes after all data is loaded ─────────────────────
    # Use a fresh connection with a long net_read/write timeout so the server
    # has time to build the IK fulltext index and HNSW vector index without
    # the connection being dropped mid-operation.
    print("🔨 Rebuilding indexes (this may take a few minutes)…")
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "ALTER TABLE patient_diaries ADD FULLTEXT INDEX idx_pop_fts"
        "(diary_text, symptoms_keywords) WITH PARSER ik"
    )
    conn.commit()
    print("   ✓ Fulltext index ready")

    cursor.execute(
        "ALTER TABLE patient_diaries ADD VECTOR INDEX idx_pop_vec(diary_embedding)"
        " WITH (distance=cosine, type=hnsw, lib=vsag)"
    )
    conn.commit()
    print("   ✓ Vector index ready")

    cursor.close()
    conn.close()
    print(f"✅ Inserted {inserted} records into patient_diaries.")


def get_stats() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM patient_diaries")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM patient_diaries WHERE is_pre_danger = 1")
    pre_danger = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT patient_id) FROM patient_diaries")
    patients = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return {"total_records": total, "pre_danger_records": pre_danger, "patients": patients}
