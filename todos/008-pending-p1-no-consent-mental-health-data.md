---
title: No Consent Mechanism for Mental Health Data Collection
status: pending
priority: p1
issue_id: 008
tags:
  - code-review
  - security
  - GDPR
  - consent
  - mental-health
dependencies:
  - 001-pending-p1-no-authentication-emotion-data.md
---

# Problem Statement

The plan does **not address user consent** for collecting mental health indicators (emotion scores, anxiety scores). Under GDPR Article 9, **special category data** (including health data) requires **explicit consent** before collection.

**Why it matters:**
- Emotion scores reveal psychological states (mental health information)
- Anxiety scores indicate mental health distress
- GDPR Article 9 requires **explicit consent** for special category data
- No user-facing disclosure about what emotion data is collected
- No opt-out mechanism for emotion tracking
- Potential legal liability for collecting sensitive data without consent

**GDPR Article 9 (Special Category Data):**
> "Processing of personal data revealing [...] health data concerning a data subject shall be prohibited, unless one of the conditions in paragraph 2 applies."

**Paragraph 2(a) - Explicit Consent:**
> "the data subject has given **explicit consent** to the processing of those personal data for one or more specified purposes"

---

# Findings

**Compliance Gaps:**

| Requirement | Status | Gap |
|-------------|--------|-----|
| Explicit consent before collection | ❌ Not implemented | No consent flow |
| Purpose specification | ❌ Not documented | No purpose statement |
| Transparency notice | ❌ Not provided | No disclosure to users |
| Right to withdraw consent | ❌ Not implemented | No opt-out mechanism |
| Data retention period | ❌ Not specified | No retention policy |

**Current User Experience:**
1. User submits diary with text
2. System silently computes emotion_score
3. System silently stores emotion_score
4. User never told this is happening

**Required User Experience (GDPR Compliant):**
1. Before first emotion computation: "We analyze your diary to understand emotional patterns. Continue?"
2. Clear explanation: "This helps identify correlations between mood and health risks."
3. Option to decline: "No thanks, don't track my emotion"
4. Ability to withdraw later: Settings → "Disable emotion tracking"

---

# Proposed Solutions

## Solution A: Consent Flow Before First Analysis (RECOMMENDED)

**Description:** Add explicit consent prompt before first emotion score computation.

**Implementation:**

**1. Add to user_profile table:**
```sql
ALTER TABLE user_profile
ADD COLUMN emotion_consent_given TINYINT(1) DEFAULT 0,
ADD COLUMN emotion_consent_date TIMESTAMP NULL,
ADD COLUMN emotion_consent_version VARCHAR(20) DEFAULT '1.0';
```

**2. Add consent check in emotion computation:**
```python
# In emotion.py
def require_consent() -> bool:
    """Check if user has given consent for emotion analysis."""
    profile = get_profile()
    return profile.emotion_consent_given == 1

def compute_emotion_score(text: str) -> Optional[float]:
    """Return emotion score only if consent given."""
    if not require_consent():
        return None  # Or raise ConsentRequiredException

    # ... existing computation ...
    return score

def save_diary(...):
    # Check consent before computing emotion
    if require_consent():
        emotion_score = compute_emotion_score(diary_text)
        anxiety_score = compute_anxiety_score(diary_text)
    else:
        emotion_score = None
        anxiety_score = None

    # Save with NULL if no consent
    cursor.execute("""
        INSERT INTO user_diaries (..., emotion_score, anxiety_score)
        VALUES (..., %s, %s)
    """, (..., emotion_score, anxiety_score))
```

**3. Add consent UI in app.py:**
```python
consent_modal = gr.HTML("""
<h2>🔔 情绪分析说明</h2>
<p>我们希望分析您的日记来了解情绪状态与健康风险之间的关联。</p>
<p><strong>我们收集什么：</strong></p>
<ul>
  <li>情绪评分（0-100）：基于日记关键词计算</li>
  <li>焦虑评分：检测焦虑相关词汇</li>
</ul>
<p><strong>如何使用：</strong></p>
<ul>
  <li>识别情绪-风险的关联模式</li>
  <li>在您情绪低落时提供额外提醒</li>
  <li>不会与第三方共享您的情绪数据</li>
</ul>
<p><strong>您的权利：</strong></p>
<ul>
  <li>随时可以关闭情绪跟踪</li>
  <li>可以删除所有历史情绪数据</li>
  <li>不会影响其他功能的使用</li>
</ul>
""")

consent_checkbox = gr.Checkbox(
    label="我理解并同意进行情绪分析",
    value=False
)

def handle_consent(agree):
    if agree:
        # Record consent
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE user_profile
            SET emotion_consent_given = 1,
                emotion_consent_date = NOW(),
                emotion_consent_version = '1.0'
        """)
        conn.commit()
        return "✅ 已启用情绪分析"
    else:
        return "⚠️ 情绪分析功能将不会启用"
```

**Pros:**
- ✅ GDPR compliant (explicit consent)
- ✅ Transparent to users
- ✅ User has control
- ✅ Version tracking for consent changes

**Cons:**
- ❌ Requires UI changes
- ❌ Adds friction to onboarding

**Effort:** Medium (3-4 hours)
**Risk:** Low (required for compliance)

---

## Solution B: Opt-In with Granular Controls (Enhanced)

**Description:** Provide granular consent options for different aspects of emotion analysis.

**Implementation:**
```python
@dataclass
class EmotionConsent:
    analysis: bool = False      # Can analyze emotion from diary
    storage: bool = False        # Can store emotion scores
    display: bool = False        # Can show emotion insights
    risk_integration: bool = False  # Can use emotion in risk scoring
```

**UI:**
```
🔔 情绪功能权限设置

☐ 分析情绪：从日记中提取情绪信号
☐ 保存数据：将情绪评分保存到数据库
☐ 显示洞察：在档案页面显示情绪-风险关联
☐ 风险评分：将情绪纳入健康风险评估

[全部启用] [全部禁用] [保存设置]
```

**Pros:**
- ✅ Maximum user control
- ✅ Granular permissions
- ✅ Users can opt-in to specific features

**Cons:**
- ❌ More complex UI
- ❌ More complex logic
- ❌ May confuse users

**Effort:** Large (6-8 hours)
**Risk:** Medium

---

## Solution C: Deferred Consent (Simple Alternative)

**Description:** Use emotion analysis only for display (not storage) until consent given.

**Implementation:**
```python
# Always compute for immediate display
emotion_score = compute_emotion_score(diary_text)

# But only store if consent given
if profile.emotion_consent_given:
    cursor.execute("INSERT INTO ... emotion_score = %s", (emotion_score,))
else:
    cursor.execute("INSERT INTO ... emotion_score = NULL")
```

**Pros:**
- ✅ Simpler than full consent flow
- ✅ Users see emotion insights immediately
- ✅ Can ask for consent later

**Cons:**
- ❌ Still computing without consent (gray area)
- ❌ No historical data until consent

**Effort:** Small (1-2 hours)
**Risk:** Medium (may not satisfy GDPR)

---

# Recommended Action

**Go with Solution A** - Explicit consent flow before first analysis.

**Implementation Phases:**

1. **Phase 1: Schema & Backend** (Add consent tracking)
2. **Phase 2: UI Consent Modal** (Explain and ask for consent)
3. **Phase 3: Settings Page** (Allow consent withdrawal)
4. **Phase 4: Data Deletion** (Handle "delete all emotion data" request)

**Consent Text Requirements:**
- Clear explanation of what is collected
- Purpose specification (why we need it)
- Who can access it (only user, no third parties)
- How to withdraw consent
- Link to privacy policy

**Consent Withdrawal:**
```python
def withdraw_emotion_consent():
    """User withdraws consent - delete all emotion data."""
    conn = get_connection()
    cursor = conn.cursor()

    # Delete emotion scores
    cursor.execute("UPDATE user_diaries SET emotion_score = NULL, anxiety_score = NULL")

    # Update consent status
    cursor.execute("""
        UPDATE user_profile
        SET emotion_consent_given = 0,
            emotion_consent_withdrawn_date = NOW()
    """)

    conn.commit()
    print("All emotion data has been deleted per your request")
```

---

# Acceptance Criteria

- [ ] User consent stored in user_profile table
- [ ] Emotion scores not computed without consent (or stored as NULL)
- [ ] Consent modal displayed before first emotion analysis
- [ ] Consent modal explains what data is collected and why
- [ ] User can decline consent without breaking other features
- [ ] User can withdraw consent at any time
- [ ] Consent withdrawal deletes all emotion data
- [ ] Consent version tracked (for consent changes)
- [ ] Privacy policy updated with emotion data details

---

# Work Log

| Date | Action | Outcome |
|------|--------|---------|
| 2026-03-03 | Security review completed | Missing consent identified |
| 2026-03-03 | Todo created | Awaiting implementation |

---

# Resources

**Dependencies:**
- Todo 001 - Authentication (should be implemented together)

**GDPR References:**
- [GDPR Article 9 - Special Category Data](https://gdpr-info.eu/art-9-gdpr/)
- [GDPR Article 13 - Information to be Provided](https://gdpr-info.eu/art-13-gdpr/)
- [GDPR Article 17 - Right to Erasure](https://gdpr-info.eu/art-17-gdpr/)

**Consent Best Practices:**
- [GDPR Consent Requirements](https://gdpr.eu/checklist/#consent)
- [ICO Guide to Consent](https://ico.org.uk/for-organisations/guide-to-consent/)

**Sample Consent Text:**
```
🔔 情绪功能说明

我们使用自然语言处理技术分析您的日记，以了解情绪状态
与健康风险之间的关系。

收集的数据：
• 情绪评分（0-100）：评估整体情绪状态
• 焦虑评分：检测焦虑相关词汇

数据用途：
• 识别您的情绪-健康关联模式
• 在情绪低落时提供额外提醒
• 永不与第三方共享

您的权利：
• 随时可以关闭此功能
• 可以删除所有历史情绪数据
• 关闭后不影响其他功能使用

详情请参阅隐私政策。
```
