"""
SeekDB DDL for the chronic-disease early-warning demo.

Table: patient_diaries
  ┌─ Structured columns ──────────────────────────────────────┐
  │  patient_id, diary_date, glucose_level, blood_pressure …  │
  ├─ Unstructured column ─────────────────────────────────────┤
  │  diary_text  ← free-form Chinese diary entry              │
  │  symptoms_keywords ← space-separated symptom tokens       │
  ├─ Vector column ───────────────────────────────────────────┤
  │  diary_embedding VECTOR(384)  ← paraphrase-MiniLM-L12-v2  │
  ├─ Label columns ───────────────────────────────────────────┤
  │  is_pre_danger  ← 1 if danger event within next 30 days   │
  │  days_to_danger ← days until next danger event            │
  └───────────────────────────────────────────────────────────┘

Indexes:
  • FULLTEXT (diary_text, symptoms_keywords) WITH PARSER ik  — Chinese keyword search
  • VECTOR   (diary_embedding) HNSW cosine                   — semantic similarity
  Both are queried in a single DBMS_HYBRID_SEARCH.SEARCH call.
"""

DATABASE = "health_demo"

CREATE_DATABASE = f"CREATE DATABASE IF NOT EXISTS {DATABASE}"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS patient_diaries (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    patient_id          INT         NOT NULL,
    diary_date          DATE        NOT NULL,
    diary_text          TEXT        NOT NULL,
    symptoms_keywords   VARCHAR(500),
    glucose_level       FLOAT,
    blood_pressure      INT,
    bmi                 FLOAT,
    is_pre_danger       TINYINT(1)  DEFAULT 0,
    days_to_danger      INT         DEFAULT -1,
    diary_embedding     VECTOR(384),
    FULLTEXT INDEX idx_diary_fts(diary_text)         WITH PARSER ik,
    FULLTEXT INDEX idx_kw_fts(symptoms_keywords)     WITH PARSER ik,
    VECTOR   INDEX idx_diary_vec(diary_embedding)
        WITH (distance=cosine, type=hnsw, lib=vsag)
)
"""

DROP_TABLE = "DROP TABLE IF EXISTS patient_diaries"
