#!/usr/bin/env python3
"""
Backfill emotion_score and anxiety_score for existing diary entries.

Run once after schema update to populate emotion data for historical entries.
The script is idempotent - it will skip entries that already have emotion_score.

Usage:
    python scripts/migrate_emotion_scores.py
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db import get_connection
from src.emotion import compute_emotion_score, compute_anxiety_score


def main():
    print("Starting emotion score migration...")

    # First, ensure new columns exist
    conn = get_connection()
    cursor = conn.cursor()

    # Apply schema changes if needed
    print("Checking database schema...")
    try:
        # Check if emotion_score column exists
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'user_diaries' AND COLUMN_NAME = 'emotion_score'
        """)
        if not cursor.fetchone():
            # Need to add the columns
            print("Adding emotion_score and anxiety_score columns...")
            cursor.execute("""
                ALTER TABLE user_diaries
                ADD COLUMN emotion_score FLOAT,
                ADD COLUMN anxiety_score FLOAT
            """)
            conn.commit()
            print("Columns added successfully.")
    except Exception as e:
        print(f"Schema check error: {e}")
        # Continue anyway - columns might already exist

    # Check for emotion_coupling table
    try:
        cursor.execute("""
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'emotion_coupling'
        """)
        if not cursor.fetchone():
            print("Creating emotion_coupling table...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS emotion_coupling (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    correlation FLOAT,
                    lag1_correlation FLOAT,
                    mean_emotion_low_risk FLOAT,
                    mean_emotion_high_risk FLOAT,
                    interpretation TEXT,
                    data_points INT,
                    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
            print("emotion_coupling table created.")
    except Exception as e:
        print(f"Error checking emotion_coupling table: {e}")

    # Check for emotion columns in user_profile
    try:
        cursor.execute("""
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = 'user_profile' AND COLUMN_NAME = 'emotion_risk_coupling'
        """)
        if not cursor.fetchone():
            print("Adding emotion columns to user_profile...")
            cursor.execute("""
                ALTER TABLE user_profile
                ADD COLUMN emotion_risk_coupling FLOAT DEFAULT 0.0,
                ADD COLUMN emotion_volatility FLOAT DEFAULT 0.0,
                ADD COLUMN emotion_amplification FLOAT DEFAULT 1.0,
                ADD COLUMN emotion_active TINYINT(1) DEFAULT 0
            """)
            conn.commit()
            print("user_profile emotion columns added.")
    except Exception as e:
        print(f"Error checking user_profile columns: {e}")

    # Get all entries without emotion_score
    cursor.execute("""
        SELECT id, diary_text
        FROM user_diaries
        WHERE emotion_score IS NULL
        ORDER BY diary_date
    """)
    entries = cursor.fetchall()
    print(f"Found {len(entries)} entries to migrate")

    if not entries:
        print("No entries need migration. Exiting.")
        cursor.close()
        conn.close()
        return

    # Process in batches for better progress reporting
    batch_size = 50
    updated = 0

    for i in range(0, len(entries), batch_size):
        batch = entries[i:i + batch_size]
        for diary_id, diary_text in batch:
            try:
                emotion = compute_emotion_score(diary_text)
                anxiety = compute_anxiety_score(diary_text)
                cursor.execute("""
                    UPDATE user_diaries
                    SET emotion_score = %s, anxiety_score = %s
                    WHERE id = %s
                """, (emotion, anxiety, diary_id))
                updated += 1
            except Exception as e:
                print(f"Error processing diary_id {diary_id}: {e}")

        # Commit batch
        conn.commit()
        print(f"Progress: {updated}/{len(entries)} entries updated")

    cursor.close()
    conn.close()
    print(f"Migration complete: {updated} entries updated")


if __name__ == "__main__":
    main()
