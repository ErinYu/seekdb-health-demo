---
title: Coupling Analysis Cache Missing TTL Strategy
status: pending
priority: p1
issue_id: 007
tags:
  - code-review
  - performance
  - caching
  - data-freshness
dependencies: []
---

# Problem Statement

The `emotion_coupling` table has **no cache invalidation strategy** - no TTL, version tracking, or expiry mechanism. Once coupling data is computed, it never refreshes, leading to **stale correlation data** being displayed to users.

**Why it matters:**
- User's emotion-risk patterns change over time
- Coupling computed from 10 diaries differs from coupling computed from 100 diaries
- Stale data misleads users about their current emotional patterns
- No way to detect when recompute is needed
- DELETE without WHERE clause is not scalable

**Current Plan (Lines 438-472):**
```python
def save_coupling(result: CouplingResult) -> None:
    cursor.execute("DELETE FROM emotion_coupling")  # Deletes ALL, no WHERE
    cursor.execute("INSERT INTO emotion_coupling (...)")
```

**Issues:**
1. No `expires_at` column for TTL
2. No `data_version` for invalidation
3. No `diary_count` for change detection
4. Uses global DELETE (not multi-user safe)

---

# Findings

**Stale Data Scenarios:**

| Scenario | Current Behavior | Expected Behavior |
|----------|------------------|-------------------|
| User adds 50 new diaries | Shows old coupling (stale) | Recompute coupling |
| User's emotional patterns shift | Shows old correlation (stale) | Show updated correlation |
| Time passes (30 days) | Shows old coupling (stale) | Refresh periodically |
| Diary entries deleted | Shows old coupling (stale) | Invalidate cache |

**Cache Effectiveness:**
- Without TTL: **100% hit rate** but **0% freshness guarantee**
- This is NOT a good cache - it's a **stale data store**

**Current Schema:**
```sql
CREATE TABLE emotion_coupling (
    id INT AUTO_INCREMENT PRIMARY KEY,
    correlation FLOAT,
    ...
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP  -- Created but NEVER USED
)
```

**Problem:** `computed_at` exists but is never queried for invalidation!

---

# Proposed Solutions

## Solution A: TTL-Based Expiration (RECOMMENDED)

**Description:** Add `expires_at` column and check cache freshness on read.

**Schema:**
```sql
ALTER TABLE emotion_coupling
ADD COLUMN expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL 7 DAY);

CREATE INDEX idx_expires ON emotion_coupling(expires_at);
```

**Implementation:**
```python
def get_coupling() -> Optional[CouplingResult]:
    """Load cached coupling if not expired."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get latest non-expired coupling
    cursor.execute("""
        SELECT * FROM emotion_coupling
        WHERE expires_at > NOW()
        ORDER BY id DESC LIMIT 1
    """)
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return None  # Cache miss - recompute

    return CouplingResult(
        correlation=row[1],
        lag1_correlation=row[2],
        mean_emotion_low_risk=row[3],
        mean_emotion_high_risk=row[4],
        interpretation=row[5],
        data_points=row[6],
    )

def save_coupling(result: CouplingResult, ttl_days=7) -> None:
    """Save coupling with expiration."""
    import datetime
    expires = datetime.datetime.now() + datetime.timedelta(days=ttl_days)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM emotion_coupling WHERE expires_at < NOW()")
    cursor.execute("""
        INSERT INTO emotion_coupling
            (correlation, ..., expires_at)
        VALUES (%s, ..., %s)
    """, (result.correlation, ..., expires))
    conn.commit()
    cursor.close()
    conn.close()
```

**Pros:**
- ✅ Automatic expiration
- ✅ Fresh data guaranteed
- ✅ Configurable TTL (7 days default)
- ✅ Index on expires_at for efficient cleanup

**Cons:**
- ❌ Requires schema change
- ❌ Must clean expired rows periodically

**Effort:** Small (1 hour)
**Risk:** Low

---

## Solution B: Version-Based Invalidation (Alternative)

**Description:** Track diary count and invalidate when count changes.

**Schema:**
```sql
ALTER TABLE emotion_coupling
ADD COLUMN diary_count INT;

CREATE INDEX idx_diary_count ON emotion_coupling(diary_count);
```

**Implementation:**
```python
def get_coupling() -> Optional[CouplingResult]:
    """Load cached coupling if diary count unchanged."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get current diary count
    cursor.execute("SELECT COUNT(*) FROM user_diaries")
    current_count = cursor.fetchone()[0]

    # Get cached coupling with matching count
    cursor.execute("""
        SELECT * FROM emotion_coupling
        WHERE diary_count = %s
        ORDER BY id DESC LIMIT 1
    """, (current_count,))

    row = cursor.fetchone()
    cursor.close()
    conn.close()

    return row_to_coupling(row) if row else None

def save_coupling(result: CouplingResult) -> None:
    """Save coupling with current diary count."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM user_diaries")
    current_count = cursor.fetchone()[0]

    cursor.execute("DELETE FROM emotion_coupling")
    cursor.execute("""
        INSERT INTO emotion_coupling
            (correlation, ..., diary_count)
        VALUES (%s, ..., %s)
    """, (result.correlation, ..., current_count))
    conn.commit()
    cursor.close()
    conn.close()
```

**Pros:**
- ✅ Immediate invalidation on new diary
- ✅ No stale data possible
- ✅ No cleanup needed

**Cons:**
- ❌ Recomputes on every new diary (could be expensive)
- ❌ May not need frequent recomputation

**Effort:** Small (1 hour)
**Risk:** Low

---

## Solution C: Hybrid Approach (Best of Both)

**Description:** Use TTL for periodic refresh AND version for immediate invalidation on significant changes.

**Schema:**
```sql
ALTER TABLE emotion_coupling
ADD COLUMN expires_at TIMESTAMP DEFAULT (NOW() + INTERVAL 7 DAY),
ADD COLUMN diary_count INT,
ADD COLUMN data_version INT DEFAULT 1;
```

**Invalidation Logic:**
```python
def get_coupling() -> Optional[CouplingResult]:
    """Load cached coupling if not expired AND diary count matches."""
    conn = get_connection()
    cursor = conn.cursor()

    # Get current state
    cursor.execute("SELECT COUNT(*) FROM user_diaries")
    current_count = cursor.fetchone()[0]

    # Get cached coupling
    cursor.execute("""
        SELECT * FROM emotion_coupling
        WHERE expires_at > NOW() AND diary_count = %s
        ORDER BY id DESC LIMIT 1
    """, (current_count,))

    row = cursor.fetchone()
    cursor.close()
    conn.close()

    return row_to_coupling(row) if row else None
```

**Pros:**
- ✅ Freshness guarantee (TTL)
- ✅ Immediate invalidation on count change
- ✅ Best cache hit rate with freshness

**Cons:**
- ❌ More complex schema
- ❌ Slightly more code

**Effort:** Medium (2 hours)
**Risk:** Low

---

# Recommended Action

**Go with Solution A** - TTL-based expiration with 7-day default.

**Rationale:**
- Simpler than hybrid approach
- 7-day TTL is reasonable for emotional patterns (they don't change daily)
- Automatic expiration prevents stale data
- Can adjust TTL based on usage patterns

**TTL Recommendations:**
- Development: 1 day (test frequently)
- Production: 7 days (balance freshness vs computation)
- Power users: 3 days (more frequent updates)

**Cache Hit Rate Projection:**
- With 7-day TTL and daily diary usage: **~95% hit rate**
- Cleanup of expired rows can run weekly via cron

---

# Acceptance Criteria

- [ ] Coupling cache has expires_at column
- [ ] get_coupling() checks expires_at before returning cached value
- [ ] save_coupling() sets expires_at = NOW() + TTL
- [ ] Expired rows are excluded from cache reads
- [ ] Index on expires_at for efficient querying
- [ ] TTL is configurable (default 7 days)
- [ ] Cache hit rate > 90% in production
- [ ] Stale data is never displayed to users

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Performance review completed | Missing TTL identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Related Issues:**
- Todo 008 - Coupling analysis uses redundant computation

**Caching Best Practices:**
- [Cache Invalidation Strategies](https://martinfowler.com/bliki/TwoHardThings.html)
- [TTL Best Practices](https://aws.amazon.com/elasticache/redis/ttl/)

**Cleanup Script:**
```python
# Run weekly via cron
def cleanup_expired_coupling():
    """Remove expired coupling entries."""
    conn = get_connection()
    cursor = conn.cursor()
    deleted = cursor.execute("DELETE FROM emotion_coupling WHERE expires_at < NOW()")
    conn.commit()
    print(f"Cleaned up {deleted} expired coupling entries")
```
