---
title: Emotion Score Computation in Wrong Layer
status: pending
priority: p1
issue_id: 002
tags:
  - code-review
  - architecture
  - separation-of-concerns
  - layered-architecture
dependencies: []
---

# Problem Statement

The plan proposes computing emotion scores inside `save_diary()` function in `user_store.py`, which is a **Data Access Layer (DAL)** module. This creates a **layer separation violation** and tight coupling between data persistence and business logic.

**Why it matters:**
- Violates Single Responsibility Principle - DAL should only handle persistence
- Creates hard dependency from `user_store.py` to `emotion.py` (business logic module)
- Makes testing difficult - can't unit test DAL without emotion computation
- Inconsistent with existing codebase patterns (see `trend_analyzer.py`, `baseline.py`)

**Current Plan (Lines 594-613):**
```python
# In src/user_store.py (DAL module)
def save_diary(...):
    # ... existing code ...

    # NEW: Emotion computation in DAL - VIOLATES LAYER SEPARATION
    from .emotion import compute_emotion_score, compute_anxiety_score
    emotion_score = compute_emotion_score(diary_text)
    anxiety_score = compute_anxiety_score(diary_text)

    cursor.execute("""
        INSERT INTO user_diaries (..., emotion_score, anxiety_score)
        VALUES (..., %s, %s)
    """, (..., emotion_score, anxiety_score))
```

---

# Findings

**Architectural Issues:**

| Issue | Location | Impact |
|-------|----------|--------|
| Business logic in DAL | `user_store.py:594-613` | Layer separation violation |
| Import dependency | DAL → emotion module | Tight coupling |
| Testing complexity | Cannot mock emotion computation | Harder to test |
| Pattern inconsistency | Other modules orchestrate in `app.py` | Architectural debt |

**Evidence from Existing Codebase:**

**Correct Pattern (from app.py):**
```python
# app.py:360-380 - Orchestrator pattern
def _full_analysis(diary_text, ...):
    # 1. Business logic computed in presentation layer
    emb = embedder.embed(diary_text)
    assessment = searcher.assess_risk(diary_text, emb)
    trend = trend_analyzer.analyze_trend(recent)
    baseline = baseline.compute_baseline_score(emb, baseline_emb, count)

    # 2. Then pass to DAL for persistence
    new_id = user_store.save_diary(..., emb, ...)
```

**Why This Matters:**
- `trend_analyzer.analyze_trend()` is NOT called inside `save_diary()`
- `baseline.compute_baseline_score()` is NOT called inside `save_diary()`
- Emotion computation should follow the same pattern

---

# Proposed Solutions

## Solution A: Move Emotion Computation to app.py (RECOMMENDED)

**Description:** Compute emotion scores in the orchestrator (`app.py`) before calling `save_diary()`, passing scores as parameters.

**Implementation:**
```python
# In app.py (_full_analysis or submit handler)
def _submit_diary(diary_text, glucose_val, bp_val):
    # 1. Compute all business logic in orchestrator
    emb = embedder.embed(diary_text)
    emotion_score = emotion.compute_emotion_score(diary_text)
    anxiety_score = emotion.compute_anxiety_score(diary_text)

    # 2. Pass to DAL for persistence
    new_id = user_store.save_diary(
        diary_text=diary_text,
        glucose_level=glucose_val,
        blood_pressure=bp_val,
        diary_embedding=emb,
        emotion_score=emotion_score,  # Pass as parameter
        anxiety_score=anxiety_score,  # Pass as parameter
    )

    # 3. Continue with analysis
    return _full_analysis_from_diary(new_id, ...)

# In user_store.py (DAL - clean separation)
def save_diary(diary_text, glucose_level, blood_pressure, diary_embedding,
                emotion_score=None, anxiety_score=None):
    """Pure data access - no business logic."""
    conn = get_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO user_diaries
                (diary_date, diary_text, glucose_level, blood_pressure,
                 diary_embedding, emotion_score, anxiety_score)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s)
        """, (diary_text, glucose_level, blood_pressure,
              diary_embedding, emotion_score, anxiety_score))

        new_id = cursor.lastrowid
        _refresh_baseline(conn, cursor)  # Existing helper
        conn.commit()
        return new_id
    except Exception as e:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
```

**Pros:**
- ✅ Maintains layered architecture
- ✅ Consistent with existing patterns (trend, baseline)
- ✅ Easier to test (mock emotion computation separately)
- ✅ DAL remains pure (no business logic)
- ✅ Follows Single Responsibility Principle

**Cons:**
- ❌ Requires updating `save_diary()` signature (breaking change)
- ❌ More parameters in function call

**Effort:** Small (1-2 hours)
**Risk:** Low (architecturally sound)

---

## Solution B: Compute Emotion in Transaction Block (Alternative)

**Description:** Keep emotion computation in `save_diary()` but ensure it's transactional and add error handling.

**Implementation:**
```python
def save_diary(diary_text, ...):
    emotion_score = None
    anxiety_score = None

    try:
        emotion_score = compute_emotion_score(diary_text)
        anxiety_score = compute_anxiety_score(diary_text)
    except Exception as e:
        logger.warning(f"Emotion computation failed: {e}")
        emotion_score = 50.0  # Neutral default
        anxiety_score = 0.0

    # Continue with database operation
```

**Pros:**
- ✅ No signature changes needed
- ✅ Emotion computation is transactional

**Cons:**
- ❌ Still violates layer separation
- ❌ Creates dependency from DAL to emotion module
- ❌ Inconsistent with existing patterns
- ❌ Harder to test

**Effort:** Small (1 hour)
**Risk:** Medium (architectural debt)

---

# Recommended Action

**Go with Solution A** - Move emotion computation to `app.py`.

**Implementation Steps:**
1. Update `save_diary()` signature to accept `emotion_score` and `anxiety_score` parameters
2. Move emotion computation to `_submit_diary()` or `_full_analysis()` in `app.py`
3. Update all calls to `save_diary()` to pass emotion scores
4. Add tests for emotion computation separately from DAL tests
5. Update plan documentation to reflect architectural decision

---

# Technical Details

**Affected Files:**

| File | Change | Lines |
|------|--------|-------|
| `src/user_store.py` | Add parameters to `save_diary()` | ~10 |
| `src/user_store.py` | Remove emotion imports | ~5 |
| `app.py` | Add emotion computation before `save_diary()` | ~15 |
| `tests/test_user_store.py` | Update test calls | ~20 |
| `tests/test_emotion.py` | Add unit tests for emotion functions | ~30 |

**Data Flow (Corrected):**
```
User Input (diary_text)
    │
    ▼
┌─────────────────────────────────────────┐
│  app.py (Orchestrator/Presentation)    │
│  ┌─────────────────────────────────────┐ │
│  │ 1. embedder.embed(diary_text)       │ │
│  │ 2. emotion.compute_emotion_score()  │ │  ← Business logic here
│  │ 3. emotion.compute_anxiety_score()  │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│  user_store.py (Data Access Layer)      │
│  ┌─────────────────────────────────────┐ │
│  │ save_diary(..., emotion_score,      │ │  ← Pure persistence
│  │             anxiety_score)           │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
    │
    ▼
  Database
```

---

# Acceptance Criteria

- [ ] `save_diary()` accepts `emotion_score` and `anxiety_score` as parameters
- [ ] No emotion computation logic in `user_store.py`
- [ ] No imports from `emotion.py` in `user_store.py`
- [ ] Emotion computation happens in `app.py` before database call
- [ ] Unit tests for `save_diary()` mock emotion scores (don't compute)
- [ ] Unit tests for emotion functions exist separately
- [ ] Code follows existing architectural patterns (trend, baseline)

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Code review completed | Architectural violation identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Related Issues:**
- Todo 003 - Schema evolution inconsistency (UserDiary dataclass)

**Architectural References:**
- [Layered Architecture Pattern](https://martinfowler.com/bliki/PresentationDomainDataLayering.html)
- [Single Responsibility Principle](https://en.wikipedia.org/wiki/Single-responsibility_principle)

**Codebase Patterns:**
- `src/trend_analyzer.py:45-80` - Trend computation (separate module)
- `src/baseline.py:23-65` - Baseline computation (separate module)
- `src/searcher.py:107-165` - Risk assessment (separate module)
