---
title: Non-Atomic Schema Migration
status: pending
priority: p1
issue_id: 006
tags:
  - code-review
  - data-integrity
  - migration
  - sql
dependencies:
  - 003-pending-p1-migration-not-idempotent-null-handling.md
---

# Problem Statement

The plan proposes executing multiple ALTER TABLE statements separately without transaction wrapping. If one ALTER succeeds but another fails (network timeout, database error), the schema will be left in an **inconsistent state** with some columns added and others missing.

**Why it matters:**
- Partial schema migration breaks application code expecting all columns
- No automatic rollback on failure
- Database left in undefined state
- Recovery requires manual intervention

**Current Plan:**
```sql
-- Executed separately - NOT ATOMIC
ALTER TABLE user_diaries ADD COLUMN emotion_score FLOAT;
ALTER TABLE user_diaries ADD COLUMN anxiety_score FLOAT;
ALTER TABLE user_profile ADD COLUMN emotion_risk_coupling FLOAT;
-- ... if any fail, schema is inconsistent
```

---

# Findings

**Failure Scenario:**
1. `ALTER TABLE user_diaries ADD COLUMN emotion_score FLOAT;` ✅ Succeeds
2. Network interruption
3. `ALTER TABLE user_diaries ADD COLUMN anxiety_score FLOAT;` ❌ Fails
4. **Result:** Schema has `emotion_score` but NOT `anxiety_score`
5. **Application breaks:** Code expects both columns to exist

**Affected Statements (Plan lines 127-148):**
- `ALTER_USER_DIARIES_EMOTION`
- `ALTER_USER_PROFILE_EMOTION`
- `CREATE_EMOTION_COUPLING`

---

# Proposed Solutions

## Solution A: Transactional Schema Migration (RECOMMENDED)

**Description:** Wrap all ALTER TABLE statements in a single transaction.

**Implementation:**
```python
# src/schema.py or new file: scripts/migrate_schema.py
def migrate_schema():
    """Execute all schema changes atomically."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # All DDL in one transaction
        cursor.execute("""
            ALTER TABLE user_diaries
            ADD COLUMN emotion_score FLOAT,
            ADD COLUMN anxiety_score FLOAT
        """)

        cursor.execute("""
            ALTER TABLE user_profile
            ADD COLUMN emotion_risk_coupling FLOAT DEFAULT 0.0,
            ADD COLUMN emotion_volatility FLOAT DEFAULT 0.0,
            ADD COLUMN emotion_amplification FLOAT DEFAULT 1.0,
            ADD COLUMN emotion_active TINYINT(1) DEFAULT 0
        """)

        cursor.execute(CREATE_EMOTION_COUPLING)

        conn.commit()
        print("✅ Schema migration complete")

    except Exception as e:
        conn.rollback()
        print(f"❌ Schema migration failed, rolled back: {e}")
        raise
    finally:
        cursor.close()
        conn.close()
```

**Pros:**
- ✅ All-or-nothing: either all columns added or none
- ✅ Automatic rollback on error
- ✅ Consistent schema state

**Cons:**
- ❌ Requires SeekDB/MySQL DDL transaction support (available in modern versions)

**Effort:** Small (1 hour)
**Risk:** Low (standard pattern)

---

## Solution B: Idempotent Migration with Verification (Alternative)

**Description:** Make migration idempotent and verify after each ALTER.

**Implementation:**
```python
def migrate_schema_with_verification():
    """Execute schema changes with verification after each step."""
    changes = [
        ("ALTER TABLE user_diaries ADD COLUMN emotion_score FLOAT", "emotion_score"),
        ("ALTER TABLE user_diaries ADD COLUMN anxiety_score FLOAT", "anxiety_score"),
        # ... other changes
    ]

    for sql, column_name in changes:
        try:
            # Check if column already exists
            if column_exists("user_diaries", column_name):
                print(f"Column {column_name} already exists, skipping")
                continue

            # Execute ALTER
            cursor.execute(sql)
            conn.commit()

            # Verify
            if not column_exists("user_diaries", column_name):
                raise Exception(f"Verification failed: {column_name} not created")

            print(f"✅ Added column {column_name}")

        except Exception as e:
            conn.rollback()
            print(f"❌ Failed to add {column_name}: {e}")
            raise
```

**Pros:**
- ✅ Can resume after partial failure
- ✅ Verification after each step

**Cons:**
- ❌ Still possible to have partial schema if verification fails
- ❌ More complex than transactional approach

**Effort:** Medium (2 hours)
**Risk:** Medium

---

# Recommended Action

**Go with Solution A** - Transactional schema migration.

**Additional Safeguards:**
1. **Pre-migration backup:**
   ```bash
   mysqldump health_demo > backup_before_migration_$(date +%Y%m%d).sql
   ```

2. **Test on staging first:**
   ```bash
   # Copy database
   mysqldump health_demo | mysql health_demo_test

   # Run migration on test copy
   python scripts/migrate_schema.py

   # Verify application works
   ```

3. **Rollback script ready:**
   ```python
   def rollback_schema():
       cursor.execute("DROP TABLE emotion_coupling")
       cursor.execute("ALTER TABLE user_profile DROP COLUMN emotion_risk_coupling, ...")
       cursor.execute("ALTER TABLE user_diaries DROP COLUMN emotion_score, ...")
   ```

---

# Acceptance Criteria

- [ ] All schema changes wrapped in single transaction
- [ ] Migration commits only if all changes succeed
- [ ] Migration rolls back automatically on any error
- [ ] Pre-migration backup created
- [ ] Rollback script tested on staging
- [ ] Verification confirms all columns exist
- [ ] Application works correctly after migration

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Data integrity review completed | Non-atomic migration identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Dependencies:**
- Todo 003 - Migration script NULL handling (address together)

**Database References:**
- [MySQL DDL Transactions](https://dev.mysql.com/doc/refman/8.0/en/ddl-transactions.html)
- [SeekDB DDL Documentation](https://www.oceanbase.com/docs/seekdb)

**Rollback Template:**
```python
# scripts/rollback_schema.py
DROP TABLE IF EXISTS emotion_coupling;
ALTER TABLE user_profile
    DROP COLUMN emotion_risk_coupling,
    DROP COLUMN emotion_volatility,
    DROP COLUMN emotion_amplification,
    DROP COLUMN emotion_active;
ALTER TABLE user_diaries
    DROP COLUMN emotion_score,
    DROP COLUMN anxiety_score;
```
