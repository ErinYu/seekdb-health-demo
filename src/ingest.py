"""
Embed synthetic population records and bulk-insert into SeekDB.
"""

from tqdm import tqdm

from .db import get_connection
from .embedder import get_embedder, vec_sql
from .schema import (
    CREATE_DATABASE, CREATE_TABLE, CREATE_USER_DIARIES, CREATE_USER_BASELINE,
    CREATE_EXPERIMENTS, CREATE_EXPERIMENT_LOGS, CREATE_RISK_FEEDBACKS,
    DROP_TABLE, DATABASE,
)
from .data_generator import DiaryRecord

_BATCH_SIZE = 64


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
        for tbl in ("risk_feedbacks", "experiment_logs", "experiments",
                    "user_baseline", "user_diaries", "patient_diaries"):
            cursor.execute(f"DROP TABLE IF EXISTS {tbl}")
        print("🗑  Dropped existing tables.")
    cursor.execute(CREATE_TABLE)
    cursor.execute(CREATE_USER_DIARIES)
    cursor.execute(CREATE_USER_BASELINE)
    cursor.execute(CREATE_EXPERIMENTS)
    cursor.execute(CREATE_EXPERIMENT_LOGS)
    cursor.execute(CREATE_RISK_FEEDBACKS)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Schema ready (6 tables).")


def ingest_records(records: list[DiaryRecord]) -> None:
    """Embed and insert all population records into SeekDB."""
    embedder = get_embedder()

    texts = [r.diary_text for r in records]
    print(f"⚙️  Embedding {len(texts)} diary entries…")
    embeddings = embedder.encode(texts, batch_size=_BATCH_SIZE, show_progress_bar=True)

    conn = get_connection()
    cursor = conn.cursor()

    insert_sql = """
        INSERT INTO patient_diaries
            (patient_id, diary_date, diary_text, symptoms_keywords,
             glucose_level, blood_pressure, bmi,
             is_pre_danger, days_to_danger, diary_embedding)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    print("💾 Inserting into SeekDB…")
    batch_data = []
    for rec, emb in tqdm(zip(records, embeddings), total=len(records)):
        batch_data.append((
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
        ))
        if len(batch_data) >= _BATCH_SIZE:
            cursor.executemany(insert_sql, batch_data)
            conn.commit()
            batch_data = []

    if batch_data:
        cursor.executemany(insert_sql, batch_data)
        conn.commit()

    cursor.close()
    conn.close()
    print(f"✅ Inserted {len(records)} records into patient_diaries.")


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
