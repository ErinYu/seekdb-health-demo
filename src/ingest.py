"""
Embed all diary records with sentence-transformers and bulk-insert into SeekDB.

Embedding model: paraphrase-multilingual-MiniLM-L12-v2
  • 384 dimensions
  • Supports Chinese
  • Apache 2.0 license
  • ~120 MB download (cached after first run)
"""

import json
from tqdm import tqdm
from sentence_transformers import SentenceTransformer

from .db import get_connection
from .schema import CREATE_DATABASE, CREATE_TABLE, DROP_TABLE, DATABASE
from .data_generator import DiaryRecord

_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
_BATCH_SIZE = 64


def _load_embedder() -> SentenceTransformer:
    print(f"📦 Loading embedding model '{_EMBED_MODEL}' (downloads once, ~120 MB)…")
    return SentenceTransformer(_EMBED_MODEL)


def _vec_to_sql(vec: list[float]) -> str:
    """Convert a Python list to SeekDB VECTOR literal '[0.1,0.2,…]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def setup_schema(drop_existing: bool = False) -> None:
    """Create the database and table (optionally drop first)."""
    # Connect without a database to create it
    conn = get_connection(database=None)
    cursor = conn.cursor()
    cursor.execute(CREATE_DATABASE)
    conn.commit()
    cursor.close()
    conn.close()

    conn = get_connection()
    cursor = conn.cursor()
    if drop_existing:
        cursor.execute(DROP_TABLE)
        print("🗑  Dropped existing table.")
    cursor.execute(CREATE_TABLE)
    conn.commit()
    cursor.close()
    conn.close()
    print("✅ Schema ready.")


def ingest_records(records: list[DiaryRecord]) -> None:
    """Embed and insert all records into SeekDB."""
    embedder = _load_embedder()

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
            _vec_to_sql(emb.tolist()),
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
    """Return basic stats about the ingested data."""
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
