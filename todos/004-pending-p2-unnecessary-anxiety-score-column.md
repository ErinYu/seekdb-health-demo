---
title: Unnecessary anxiety_score Column (Duplicate Functionality)
status: pending
priority: p2
issue_id: 004
tags:
  - code-review
  - simplicity
  - YAGNI
  - database-schema
dependencies: []
---

# Problem Statement

The plan proposes adding a separate `anxiety_score` column to store anxiety-specific emotion scores. However, the existing `_NEGATIVE` lexicon in `src/emotion.py` already contains anxiety keywords. This creates **duplicate functionality** without adding new signal value.

**Why it matters:**
- Duplicates existing functionality (anxiety is subset of general emotion)
- Doubles computation cost for marginal differentiation
- Adds unnecessary schema complexity
- Violates YAGNI principle
- Plan acknowledges anxiety is subset of emotion but still treats as separate feature

**Current Evidence:**

**Existing Emotion Lexicon (src/emotion.py:52-69):**
```python
_NEGATIVE: list[tuple[str, int]] = [
    # ... fatigue keywords ...
    # Mood / emotional distress
    ("焦虑", 2), ("担忧", 2), ("担心", 1), ("烦躁", 2),
    ("情绪低落", 3), ("心情差", 3), ("情绪差", 2), ("压力大", 2),
    ("抑郁", 3), ("绝望", 3), ("烦", 1),
    ...
]
```

**Proposed Anxiety Lexicon (Plan):**
```python
_ANXIETY: list[tuple[str, int]] = [
    ("极度焦虑", 3), ("严重焦虑", 3), ("恐慌", 3),
    ("焦虑", 2), ("担忧", 2), ("不安", 2),
    ("紧张", 1), ("压力大", 1), ("烦躁", 1),
]
```

**Overlap Analysis:**
- 8 keywords total in proposed anxiety lexicon
- 5 keywords (62%) already exist in general emotion lexicon
- Only 3 truly new keywords: "极度焦虑", "严重焦虑", "恐慌", "不安"

---

# Findings

**Duplication Analysis:**

| Aspect | General Emotion | Proposed Anxiety | Overlap |
|--------|----------------|------------------|---------|
| Keywords | 49 entries | 8 entries | 5 (62%) |
| Functionality | Comprehensive emotion scoring | Anxiety-specific scoring | Significant |
| Computation | Keyword matching | Keyword matching | Identical |
| Storage | emotion_score FLOAT | anxiety_score FLOAT | Separate columns |

**Code Duplication:**
```python
# These are essentially the same function
def compute_emotion_score(text):
    # Checks 49 keywords
    for word, w in _POSITIVE: if word in text: pos += w
    for word, w in _NEGATIVE: if word in text: neg += w
    return pos / (pos + neg) * 100

def compute_anxiety_score(text):
    # Checks 8 keywords (5 overlapping!)
    for word, w in _ANXIETY: if word in text: score += w
    return scaled_score
```

**YAGNI Violation:**
- **Y**ou **A**ren't **G**onna **N**eed **I**t
- Anxiety-specific insights not proven to be more valuable than general emotion
- Can add later if users request anxiety-specific features
- Premature optimization for differentiation

---

# Proposed Solutions

## Solution A: Remove anxiety_score Entirely (RECOMMENDED)

**Description:** Don't create separate `anxiety_score` column. Rely on existing `emotion_score` which already captures anxiety through the negative emotion lexicon.

**Implementation:**
```python
# Remove from plan:
# - ALTER TABLE user_diaries ADD COLUMN anxiety_score FLOAT
# - compute_anxiety_score() function
# - _ANXIETY lexicon

# Alternative: Simple flag for anxiety presence
def has_anxiety_keywords(text: str) -> bool:
    """Check if diary contains anxiety-specific keywords."""
    anxiety_keywords = ["极度焦虑", "严重焦虑", "恐慌", "不安"]
    return any(kw in text for kw in anxiety_keywords)
```

**Pros:**
- ✅ Eliminates 80+ lines of code
- ✅ Eliminates unnecessary schema change
- ✅ Simpler data model
- ✅ Faster computation (half the keyword checks)
- ✅ Consistent with existing architecture

**Cons:**
- ❌ No separate anxiety metric (but is this needed?)
- ❌ Cannot query "anxiety-only" days (use case not proven)

**Effort:** Small (removal = less work)
**Risk:** None (removing unnecessary complexity)

---

## Solution B: Keep anxiety_score but Simplify (Alternative)

**Description:** Keep `anxiety_score` but make it a computed column, not stored.

**Implementation:**
```python
# No storage, just computation on-demand
def get_anxiety_score(diary_id: int) -> float:
    """Compute anxiety score on-demand from diary_text."""
    diary = get_diary_by_id(diary_id)
    return compute_anxiety_score(diary.diary_text)
```

**Pros:**
- ✅ No schema change
- ✅ Available when needed
- ✅ Can add storage later if proven valuable

**Cons:**
- ❌ Still has code duplication
- ❌ Computation on every read

**Effort:** Medium
**Risk:** Low

---

## Solution C: Combine into Emotion Score with Anxiety Flag (Hybrid)

**Description:** Keep single `emotion_score` but add boolean `has_anxiety` flag computed from keywords.

**Implementation:**
```python
@dataclass
class EmotionResult:
    score: float           # 0-100 wellness score
    has_anxiety: bool      # True if anxiety keywords present
    dominant_emotion: str  # "positive", "negative", "anxious", "neutral"

def analyze_emotion(text: str) -> EmotionResult:
    score = compute_emotion_score(text)
    has_anxiety = any(kw in text for kw in ["极度焦虑", "严重焦虑", "恐慌"])

    if has_anxiety:
        dominant = "anxious"
    elif score > 70:
        dominant = "positive"
    elif score < 30:
        dominant = "negative"
    else:
        dominant = "neutral"

    return EmotionResult(score, has_anxiety, dominant)
```

**Schema:**
```sql
ALTER TABLE user_diaries ADD COLUMN emotion_score FLOAT;
ALTER TABLE user_diaries ADD COLUMN has_anxiety TINYINT(1) DEFAULT 0;
```

**Pros:**
- ✅ Single source of truth
- ✅ Anxiety detection without separate score
- ✅ Richer emotion context
- ✅ Easy to query (WHERE has_anxiety = 1)

**Cons:**
- ❌ Still adds schema column (has_anxiety)
- ❌ More complex than Solution A

**Effort:** Medium
**Risk:** Low

---

# Recommended Action

**Go with Solution A** - Remove `anxiety_score` entirely.

**Rationale:**
1. **YAGNI:** No proven need for separate anxiety metric
2. **Duplication:** 62% keyword overlap with existing lexicon
3. **Simplicity:** Less code = less maintenance
4. **Performance:** Fewer keyword checks = faster computation
5. **Extensibility:** Can add later if users request anxiety-specific features

**If Anxiety Detection is Needed:**
Use Solution C's `has_anxiety` boolean flag instead of separate score. This provides the differentiation needed without the complexity of a second scoring system.

**Deferral Strategy:**
- Phase 3: Use general emotion_score only
- Phase 4+: Add has_anxiety flag if users request anxiety-specific insights

---

# Technical Details

**Code Reduction:**

| Component | Lines Removed |
|-----------|---------------|
| `compute_anxiety_score()` function | ~20 |
| `_ANXIETY` lexicon definition | ~10 |
| `anxiety_score` column in schema | ~5 |
| `anxiety_score` in INSERT statement | ~5 |
| `anxiety_score` in migration script | ~15 |
| `anxiety_score` tests | ~30 |
| **Total** | **~85 lines** |

**Schema Changes (Simplified):**

**Before (Plan):**
```sql
ALTER TABLE user_diaries
ADD COLUMN emotion_score FLOAT,
ADD COLUMN anxiety_score FLOAT;  -- REMOVE THIS
```

**After (Solution A):**
```sql
ALTER TABLE user_diaries
ADD COLUMN emotion_score FLOAT;  -- Single column only
```

---

# Acceptance Criteria

- [ ] No `anxiety_score` column in schema
- [ ] No `compute_anxiety_score()` function
- [ ] No `_ANXIETY` lexicon (or minimal for `has_anxiety` flag)
- [ ] General `emotion_score` captures all emotion states including anxiety
- [ ] Code reduced by ~80 lines
- [ ] Tests simplified (no anxiety-specific tests)
- [ ] Documentation updated to reflect simplified approach

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Code review completed | Duplicate functionality identified |
| 2026-03-03 | Todo created | Awaiting decision on approach |

---

# Resources

**Related Issues:**
- Todo 005 - Premature emotion_coupling table optimization

**YAGNI Reference:**
- [You Aren't Gonna Need It (YAGNI)](https://martinfowler.com/bliki/Yagni.html)
- [Rule of Three](https://en.wikipedia.org/wiki/Rule_of_three_(computer_programming)) - Don't abstract until third use

**Alternative Approaches:**
- If anxiety-specific features are requested later, can be added as:
  - User-contributed lexicon (custom anxiety words)
  - LLM-based emotion classification (more nuanced)
  - Emotion sub-category analysis
