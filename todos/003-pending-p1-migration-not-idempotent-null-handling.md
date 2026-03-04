---
title: Migration Script Not Idempotent (NULL Handling)
status: pending
priority: p1
issue_id: 003
tags:
  - code-review
  - data-integrity
  - migration
  - sql
dependencies: []
---

# Problem Statement

The migration script `scripts/migrate_emotion_scores.py` lacks proper NULL handling for `diary_text` and is not fully idempotent. If `diary_text` is NULL or empty, the script will crash mid-execution, leaving the database in an inconsistent state.

**Why it matters:**
- Partial migration creates data inconsistency
- No way to resume after failure without manual intervention
- NULL diary_text is possible in current schema (column not marked NOT NULL)
- Migration should be bulletproof for production use

**Current Plan (Lines 693-712):**
```python
emotion = compute_emotion_score(diary_text)  # Crashes if diary_text is None!
anxiety = compute_anxiety_score(diary_text)  # Crashes if diary_text is None!
```

**Failure Scenario:**
1. Script processes 50 entries successfully
2. Entry #51 has `diary_text = NULL`
3. Script crashes with `AttributeError: 'NoneType' has no attribute '__contains__'`
4. 50 entries have emotion_score, rest don't
5. No way to resume without manual cleanup

---

# Findings

**Issues Identified:**

| Issue | Location | Severity |
|-------|----------|----------|
| No NULL check for diary_text | Line 693 | 🔴 Critical |
| No empty string check | Line 693 | 🟡 Important |
| No verification after migration | Entire script | 🟡 Important |
| No rollback capability | Entire script | 🟡 Important |

**SQL Schema Issue:**
```sql
-- Current schema allows NULL diary_text
CREATE TABLE user_diaries (
    ...
    diary_text TEXT NOT NULL,  -- NOT NULL is present, but...
);
```

**However**, `diary_text` could still be:
- Empty string `""`
- Whitespace only `"   "`
- Migration should handle these edge cases

---

# Proposed Solutions

## Solution A: Robust Migration with NULL/Empty Handling (RECOMMENDED)

**Description:** Add proper NULL/empty handling with fallback defaults and verification.

**Implementation:**
```python
#!/usr/bin/env python3
"""
Backfill emotion_score and anxiety_score for existing diary entries.
Idempotent: can be run multiple times safely.
"""

import sys
sys.path.append('.')

from src.db import get_connection
from src.emotion import compute_emotion_score, compute_anxiety_score

def main():
    print("Starting emotion score migration...")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # Get all entries without emotion_score
        cursor.execute("""
            SELECT id, diary_text
            FROM user_diaries
            WHERE emotion_score IS NULL
            ORDER BY diary_date
            FOR UPDATE  -- Lock rows to prevent concurrent modification
        """)
        entries = cursor.fetchall()
        print(f"Found {len(entries)} entries to migrate")

        if len(entries) == 0:
            print("No entries to migrate - already complete")
            return

        updated = 0
        skipped = 0

        for diary_id, diary_text in entries:
            # Handle NULL/empty text gracefully
            if not diary_text or diary_text.strip() == "":
                # Use neutral defaults for empty entries
                emotion = 50.0  # Neutral emotion
                anxiety = 0.0   # No anxiety
                skipped += 1
                print(f"Entry {diary_id}: Empty diary_text, using defaults")
            else:
                try:
                    emotion = compute_emotion_score(diary_text)
                    anxiety = compute_anxiety_score(diary_text)
                except Exception as e:
                    print(f"Entry {diary_id}: Emotion computation failed: {e}")
                    emotion = 50.0
                    anxiety = 0.0
                    skipped += 1

            # Validate score ranges
            emotion = max(0.0, min(100.0, emotion))
            anxiety = max(0.0, min(100.0, anxiety))

            cursor.execute("""
                UPDATE user_diaries
                SET emotion_score = %s, anxiety_score = %s
                WHERE id = %s
            """, (emotion, anxiety, diary_id))
            updated += 1

            # Commit every 10 entries for progress visibility
            if updated % 10 == 0:
                conn.commit()
                print(f"Progress: {updated}/{len(entries)} entries updated")

        # Final commit
        conn.commit()
        print(f"\nMigration complete: {updated} entries updated, {skipped} skipped")

        # Verification step
        print("\nRunning verification...")
        cursor.execute("""
            SELECT COUNT(*) FROM user_diaries WHERE emotion_score IS NULL
        """)
        null_count = cursor.fetchone()[0]

        if null_count > 0:
            raise Exception(f"Verification failed: {null_count} entries still have NULL emotion_score")

        cursor.execute("""
            SELECT COUNT(*) FROM user_diaries
            WHERE emotion_score < 0 OR emotion_score > 100
        """)
        invalid_count = cursor.fetchone()[0]

        if invalid_count > 0:
            raise Exception(f"Verification failed: {invalid_count} entries have invalid emotion_score")

        print("✅ Verification passed - all entries have valid emotion scores")

    except Exception as e:
        conn.rollback()
        print(f"❌ Migration failed, rolled back: {e}")
        raise
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    main()
```

**Pros:**
- ✅ Handles NULL diary_text gracefully
- ✅ Handles empty string diary_text
- ✅ Validates score ranges (0-100)
- ✅ Includes verification step
- ✅ Transactional with rollback on error
- ✅ Idempotent (checks `emotion_score IS NULL`)
- ✅ Progress reporting
- ✅ FOR UPDATE locking prevents concurrent modification

**Cons:**
- ❌ None - this is the correct approach

**Effort:** Small (1 hour)
**Risk:** Low (defensive programming)

---

## Solution B: Fix Schema at Source (Alternative)

**Description:** Add database constraint to prevent NULL diary_text before migration.

**Implementation:**
```sql
-- First, fix any existing NULL diary_text entries
UPDATE user_diaries SET diary_text = '' WHERE diary_text IS NULL;

-- Then add NOT NULL constraint
ALTER TABLE user_diaries MODIFY diary_text TEXT NOT NULL;
```

**Pros:**
- ✅ Prevents NULL diary_text at database level
- ✅ Cleaner schema

**Cons:**
- ❌ Requires schema change before migration
- ❌ Doesn't handle empty strings
- ❌ More aggressive (may not be appropriate for demo)

**Effort:** Small (30 minutes)
**Risk:** Medium (schema change)

---

# Recommended Action

**Go with Solution A** - Robust migration with NULL/empty handling.

**Additional Safeguards:**
1. **Test migration on staging copy first:**
   ```bash
   # Create test database copy
   mysqldump health_demo | mysql health_demo_test

   # Run migration on test copy
   python scripts/migrate_emotion_scores.py

   # Verify results
   mysql health_demo_test -e "SELECT COUNT(*) FROM user_diaries WHERE emotion_score IS NULL;"
   ```

2. **Create rollback script:**
   ```python
   # scripts/rollback_emotion_migration.py
   def rollback():
       conn = get_connection()
       cursor = conn.cursor()
       try:
           cursor.execute("UPDATE user_diaries SET emotion_score = NULL, anxiety_score = NULL")
           conn.commit()
           print("Rollback complete")
       except Exception as e:
           conn.rollback()
           raise
   ```

3. **Add dry-run mode:**
   ```python
   # Add --dry-run flag to preview changes
   if args.dry_run:
       print("DRY RUN: Would update X entries")
       return
   ```

---

# Technical Details

**Migration Execution Plan:**

1. **Pre-migration checks:**
   - Backup database: `mysqldump health_demo > backup.sql`
   - Check entry count: `SELECT COUNT(*) FROM user_diaries;`
   - Check existing NULL emotion_score: `SELECT COUNT(*) FROM user_diaries WHERE emotion_score IS NULL;`

2. **Run migration:**
   - Execute: `python scripts/migrate_emotion_scores.py`
   - Monitor progress output
   - Verify completion message

3. **Post-migration validation:**
   - Check for NULLs: `SELECT COUNT(*) FROM user_diaries WHERE emotion_score IS NULL;` (should be 0)
   - Check for invalid values: `SELECT COUNT(*) FROM user_diaries WHERE emotion_score < 0 OR emotion_score > 100;` (should be 0)
   - Sample verification: `SELECT id, diary_text, emotion_score, anxiety_score FROM user_diaries LIMIT 10;`

4. **Keep rollback available for 7 days:**
   - Don't delete backup until verified in production

---

# Acceptance Criteria

- [ ] Migration handles NULL diary_text without crashing
- [ ] Migration handles empty string diary_text
- [ ] Migration validates emotion_score range (0-100)
- [ ] Migration validates anxiety_score range (0-100)
- [ ] Migration is idempotent (can be run multiple times)
- [ ] Migration includes verification step
- [ ] Migration rolls back on error
- [ ] Progress reported every 10 entries
- [ ] Dry-run mode supported
- [ ] Rollback script created and tested

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Code review completed | NULL handling issue identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Related Issues:**
- Todo 004 - Non-atomic schema migration
- Todo 005 - Missing foreign key relationship

**Migration Best Practices:**
- [Stripe Migration Guide](https://stripe.com/blog/migrations)
- [GitHub Migration SQL](https://github.com/github/gh-ost/blob/master/doc/matrices.md)

**Rollback Script Template:**
```python
# scripts/rollback_emotion_migration.py
def rollback_migration():
    """Rollback emotion migration by setting scores to NULL."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        affected = cursor.execute("UPDATE user_diaries SET emotion_score = NULL, anxiety_score = NULL")
        conn.commit()
        print(f"Rollback complete: {affected} entries reverted")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
```
