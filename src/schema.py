"""
SeekDB DDL for the chronic-disease early-warning demo.

Tables
──────
patient_diaries   Synthetic population data (100 patients × 45 days).
                  Provides the "historical library" for trajectory matching.

user_diaries      Real user entries, one row per check-in.
                  Drives personal trend analysis and baseline computation.

user_baseline     Materialised personal baseline: embedding centroid of the
                  user's stable-period entries, recomputed on every save.

Index strategy
──────────────
  • FULLTEXT(diary_text, symptoms_keywords) WITH PARSER ik  → BM25 / IK Chinese tokeniser
  • VECTOR(diary_embedding) HNSW cosine                     → semantic similarity
  Both are queried together via DBMS_HYBRID_SEARCH.SEARCH in one round-trip.
"""

DATABASE = "health_demo"

CREATE_DATABASE = f"CREATE DATABASE IF NOT EXISTS {DATABASE}"

# ── Population reference table (synthetic, loaded by init_db.py) ───────────
CREATE_POPULATION_TABLE = """
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
    FULLTEXT INDEX idx_pop_fts(diary_text, symptoms_keywords) WITH PARSER ik,
    VECTOR   INDEX idx_pop_vec(diary_embedding)
        WITH (distance=cosine, type=hnsw, lib=vsag)
)
"""

# ── User's own diary entries ───────────────────────────────────────────────
CREATE_USER_DIARIES = """
CREATE TABLE IF NOT EXISTS user_diaries (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    diary_date          DATE        NOT NULL,
    diary_text          TEXT        NOT NULL,
    symptoms_keywords   VARCHAR(500),
    glucose_level       FLOAT,
    blood_pressure      INT,
    risk_score          FLOAT,
    risk_level          VARCHAR(10),
    trajectory_score    FLOAT,
    trend_score         FLOAT,
    baseline_score      FLOAT,
    diary_embedding     VECTOR(384),
    created_at          TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    FULLTEXT INDEX idx_user_fts(diary_text, symptoms_keywords) WITH PARSER ik,
    VECTOR   INDEX idx_user_vec(diary_embedding)
        WITH (distance=cosine, type=hnsw, lib=vsag)
)
"""

# ── Materialised personal baseline ────────────────────────────────────────
CREATE_USER_BASELINE = """
CREATE TABLE IF NOT EXISTS user_baseline (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    computed_at         TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    entry_count         INT         NOT NULL,
    avg_glucose         FLOAT,
    baseline_embedding  VECTOR(384)
)
"""

# ── Experiment tracking ────────────────────────────────────────────────────
CREATE_EXPERIMENTS = """
CREATE TABLE IF NOT EXISTS experiments (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(200) NOT NULL,
    variable    VARCHAR(200),
    hypothesis  TEXT,
    status      VARCHAR(20) DEFAULT 'active',
    start_date  DATE        NOT NULL,
    target_days INT         DEFAULT 7,
    created_at  TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_EXPERIMENT_LOGS = """
CREATE TABLE IF NOT EXISTS experiment_logs (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    experiment_id   INT         NOT NULL,
    log_date        DATE        NOT NULL,
    executed        TINYINT(1)  NOT NULL,
    diary_id        INT,
    note            VARCHAR(500),
    created_at      TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_exp_date (experiment_id, log_date)
)
"""

# Back-compat alias used by the original ingest.py
CREATE_TABLE = CREATE_POPULATION_TABLE
DROP_TABLE   = "DROP TABLE IF EXISTS patient_diaries"
