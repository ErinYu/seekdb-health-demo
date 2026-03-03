#!/usr/bin/env python3
"""
One-command database initialisation.

Usage:
    python scripts/init_db.py

What this does:
    1. Waits for SeekDB (Docker) to be healthy
    2. Creates the 'health_demo' database and 'patient_diaries' table
    3. Generates ~4 500 synthetic diary records (100 patients × 45 days)
    4. Embeds each record with paraphrase-multilingual-MiniLM-L12-v2
    5. Bulk-inserts everything into SeekDB
"""

import sys
import os

# Make sure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import wait_for_seekdb
from src.schema import DATABASE
from src.ingest import setup_schema, ingest_records, get_stats
from src.data_generator import generate_all_patients


def main() -> None:
    print("=" * 60)
    print("  SeekDB Chronic Disease Early Warning — DB Init")
    print("=" * 60)

    # 1. Wait for SeekDB
    wait_for_seekdb()

    # 2. Schema
    print("\n📐 Setting up schema (patient_diaries + user_diaries + user_baseline)…")
    setup_schema(drop_existing=True)

    # 3. Generate data
    print("\n🧬 Generating synthetic patient data…")
    records = generate_all_patients(
        n_danger_patients=40,   # patients who develop a crisis
        n_normal_patients=60,   # patients who remain stable
        normal_days=20,
        pre_danger_days=25,
        seed=42,
    )
    total = len(records)
    pre_danger = sum(1 for r in records if r.is_pre_danger)
    print(f"   Generated {total} records across 100 patients")
    print(f"   Pre-danger records: {pre_danger} ({pre_danger/total*100:.1f}%)")

    # 4 & 5. Embed + insert
    print()
    ingest_records(records)

    # Stats
    print("\n📊 Database summary:")
    stats = get_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")

    print("\n✅ Initialisation complete! Run:  python app.py")


if __name__ == "__main__":
    main()
