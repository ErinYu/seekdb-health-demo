"""
Emotion scoring and emotion-physiology coupling analysis.

Emotion score  (0–100)
──────────────────────
  A wellness score derived from diary text keywords.
    0   = very negative (exhaustion, anxiety, pain)
   50   = neutral (no clear signal)
  100   = very positive (energetic, relaxed, happy)

The score captures *perceived wellness* — a blend of mood and subjective
physical sensation appropriate for a chronic disease diary context.

Coupling analysis
─────────────────
  Analyzes the Pearson correlation between emotion scores and risk scores
  across the user's diary history, including a lag-1 analysis
  (emotion today → risk tomorrow).

  Requires ≥ 5 diary entries.  Returns None if insufficient data.

No external dependencies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# ── Wellness keyword lexicon ─────────────────────────────────────────────────
# Each entry: (keyword, weight)   weight ∈ {1, 2, 3}

_POSITIVE: list[tuple[str, int]] = [
    # Energy / physical vitality
    ("精力充沛", 3), ("充沛", 2), ("有劲", 2), ("有精神", 2),
    ("精神好", 2),  ("神清气爽", 3), ("清醒", 1), ("有力", 1),
    # Mood / emotional
    ("开心", 2), ("高兴", 2), ("愉快", 2), ("心情好", 3),
    ("轻松", 2), ("放松", 2), ("舒畅", 2), ("积极", 1),
    # Health / wellbeing
    ("好转", 2), ("改善", 2), ("状态好", 3), ("稳定", 1),
    ("舒服", 2), ("舒适", 2), ("正常", 1), ("满意", 1),
    # Sleep / appetite
    ("睡得好", 3), ("睡眠好", 3), ("睡得香", 3), ("食欲好", 2),
    ("胃口好", 2),
    # Generic positive
    ("好", 1),
]

_NEGATIVE: list[tuple[str, int]] = [
    # Fatigue / weakness
    ("极度疲劳", 3), ("非常疲惫", 3), ("疲劳", 2), ("疲倦", 2),
    ("乏力", 2), ("无力", 2), ("虚弱", 2), ("没劲", 2),
    ("身体沉重", 2), ("累", 1), ("乏", 1),
    # Mood / emotional distress
    ("焦虑", 2), ("担忧", 2), ("担心", 1), ("烦躁", 2),
    ("情绪低落", 3), ("心情差", 3), ("情绪差", 2), ("压力大", 2),
    ("抑郁", 3), ("绝望", 3), ("烦", 1),
    # Physical symptoms (subjective distress)
    ("非常难受", 3), ("难受", 2), ("不舒服", 2), ("痛苦", 3),
    ("头晕", 1), ("头痛", 1), ("恶心", 2), ("麻木", 1),
    # Sleep / appetite issues
    ("失眠", 2), ("睡不好", 2), ("睡眠差", 2), ("睡眠不好", 2),
    ("食欲差", 2), ("没食欲", 2), ("胃口差", 2),
    # Diabetes-specific complaints (carry mild negative valence)
    ("口渴", 1), ("多尿", 1), ("视力模糊", 1), ("眼前发花", 1),
]


# ── Anxiety-specific lexicon (Phase 3A) ──────────────────────────────────────

_ANXIETY: list[tuple[str, int]] = [
    # Severe anxiety
    ("极度焦虑", 3), ("严重焦虑", 3), ("恐慌", 3), ("惊恐", 3),
    ("恐慌发作", 3), ("极度不安", 3),
    # Moderate anxiety
    ("焦虑", 2), ("担忧", 2), ("不安", 2), ("忧虑", 2),
    ("焦虑不安", 2), ("心慌", 2),
    # Mild anxiety / stress
    ("紧张", 1), ("压力大", 1), ("烦躁", 1), ("焦虑感", 1),
    ("有些担心", 1), ("有点紧张", 1),
]


def compute_emotion_score(text: str) -> float:
    """
    Return a wellness/emotion score in [0, 100].

    Algorithm:
      - Accumulate weighted positive and negative keyword hits.
      - score = pos_weight / (pos_weight + neg_weight) * 100
      - If no keywords found, return 50.0 (neutral).
    """
    pos = 0.0
    neg = 0.0
    for word, w in _POSITIVE:
        if word in text:
            pos += w
    for word, w in _NEGATIVE:
        if word in text:
            neg += w
    total = pos + neg
    if total < 1e-9:
        return 50.0
    return round(pos / total * 100, 1)


# ── Anxiety scoring (Phase 3A) ───────────────────────────────────────────────

def compute_anxiety_score(text: str) -> float:
    """
    Return an anxiety score in [0, 100].

    Higher scores indicate more anxiety. 0 = no anxiety signals.

    Algorithm:
      - Sum weighted anxiety keyword hits.
      - Scale to 0-100 range.
      - If no keywords found, return 0.0.
    """
    score = 0
    max_possible = 0
    for word, weight in _ANXIETY:
        max_possible += weight * 3  # Assume max 3 occurrences per word
        if word in text:
            # Count multiple occurrences (up to 3 per keyword)
            count = min(3, text.count(word))
            score += weight * count * 33  # Scale to 0-100

    if max_possible == 0:
        return 0.0
    return round(min(100.0, score), 1)


# ── Emotion volatility (Phase 3A) ─────────────────────────────────────────────

def compute_volatility(diaries) -> float:
    """
    Compute rolling standard deviation of emotion scores (last 30 entries).

    Higher values indicate more emotional variability.

    Args:
        diaries: list[UserDiary] — emotion score is computed from diary_text.

    Returns:
        Volatility score (standard deviation), 0.0 if insufficient data.
    """
    # Compute emotion score from diary_text (UserDiary has no emotion_score field)
    entries = [
        compute_emotion_score(d.diary_text)
        for d in diaries
        if d.diary_text
    ][-30:]  # Last 30 entries

    if len(entries) < 3:
        return 0.0

    mean = sum(entries) / len(entries)
    variance = sum((s - mean) ** 2 for s in entries) / len(entries)
    return round(variance ** 0.5, 1)


# ── Coupling analysis ────────────────────────────────────────────────────────

@dataclass
class CouplingResult:
    data_points: int
    correlation: float          # Pearson r: emotion vs risk (same day)
    lag1_correlation: float     # emotion[t] vs risk[t+1]
    mean_emotion_low_risk: float   # avg emotion score on low-risk days (risk < 35)
    mean_emotion_high_risk: float  # avg emotion score on high-risk days (risk ≥ 60)
    interpretation: str


def analyze_coupling(diaries) -> Optional[CouplingResult]:
    """
    Compute the emotion-physiology coupling statistics from diary history.

    Args:
        diaries: list[UserDiary], newest first (as returned by get_recent_diaries).

    Returns:
        CouplingResult, or None if < 5 entries with risk scores.
    """
    # Work chronologically (oldest first)
    entries = [d for d in reversed(diaries) if d.risk_score is not None]
    if len(entries) < 5:
        return None

    emotions = [compute_emotion_score(d.diary_text) for d in entries]
    risks    = [d.risk_score for d in entries]

    # Same-day Pearson correlation
    r = _pearson(emotions, risks)

    # Lag-1: emotion today → risk tomorrow
    lag1_r = _pearson(emotions[:-1], risks[1:]) if len(entries) > 4 else 0.0

    # Mean emotion by risk category
    low_emos  = [e for e, rs in zip(emotions, risks) if rs < 35]
    high_emos = [e for e, rs in zip(emotions, risks) if rs >= 60]
    mean_low  = sum(low_emos)  / len(low_emos)  if low_emos  else 50.0
    mean_high = sum(high_emos) / len(high_emos) if high_emos else 50.0

    # Human-readable interpretation
    interpretation = _interpret(
        r, lag1_r, mean_low, mean_high, len(entries),
        has_low=bool(low_emos), has_high=bool(high_emos),
    )

    return CouplingResult(
        data_points=len(entries),
        correlation=round(r, 3),
        lag1_correlation=round(lag1_r, 3),
        mean_emotion_low_risk=round(mean_low, 1),
        mean_emotion_high_risk=round(mean_high, 1),
        interpretation=interpretation,
    )


def _interpret(
    r: float,
    lag1_r: float,
    mean_low: float,
    mean_high: float,
    n: int,
    has_low: bool = False,
    has_high: bool = False,
) -> str:
    abs_r = abs(r)

    if abs_r < 0.15:
        base = "目前情绪状态与风险评分尚无明显关联，继续积累数据中。"
    elif r < -0.3:
        base = (
            f"情绪/状态越好时，风险评分越低（相关度 r={r:.2f}）"
            "—— 你的心理状态与生理风险呈现良好的协同规律。"
        )
    elif r > 0.3:
        base = (
            f"情绪/状态变化与风险评分同步上升（r={r:.2f}）"
            "—— 当身体不舒服时，你的日记描述也随之更消极，两者紧密联动。"
        )
    else:
        base = f"情绪状态与风险评分存在一定关联（r={r:.2f}），持续观察中。"

    # Add lag insight if signal is meaningful
    lag_note = ""
    if n > 7 and lag1_r < -0.25:
        lag_note = " 有迹象显示情绪低落后次日风险略有升高，值得留意。"
    elif n > 7 and lag1_r > 0.25:
        lag_note = " 当天情绪偏好时，次日风险也倾向于偏低。"

    # Compare emotion means across risk categories
    gap = mean_low - mean_high
    gap_note = ""
    if has_low and has_high and abs(gap) > 8:
        if gap > 0:
            gap_note = (
                f" 低风险天的情绪均值（{mean_low:.0f}分）"
                f"比高风险天（{mean_high:.0f}分）高出 {gap:.0f} 分。"
            )
        else:
            gap_note = (
                f" 高风险天的情绪均值（{mean_high:.0f}分）"
                f"反而略高于低风险天（{mean_low:.0f}分），模式较为特殊。"
            )

    return base + lag_note + gap_note


# ── Math utility ─────────────────────────────────────────────────────────────

def _pearson(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation coefficient.  Returns 0.0 if underdetermined."""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    sx  = math.sqrt(sum((xs[i] - mx) ** 2 for i in range(n)))
    sy  = math.sqrt(sum((ys[i] - my) ** 2 for i in range(n)))
    denom = sx * sy
    return num / denom if denom > 1e-9 else 0.0


# ── DB helpers for coupling cache (Phase 3A) ──────────────────────────────────

def save_coupling(result: CouplingResult) -> None:
    """Cache coupling analysis result to database."""
    from .db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM emotion_coupling")
    cursor.execute("""
        INSERT INTO emotion_coupling
            (correlation, lag1_correlation, mean_emotion_low_risk,
             mean_emotion_high_risk, interpretation, data_points)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (result.correlation, result.lag1_correlation,
          result.mean_emotion_low_risk, result.mean_emotion_high_risk,
          result.interpretation, result.data_points))
    conn.commit()
    cursor.close()
    conn.close()


def get_coupling() -> Optional[CouplingResult]:
    """Load cached coupling result from database."""
    from .db import get_connection
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM emotion_coupling ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return None
    return CouplingResult(
        correlation=float(row[1]) if row[1] is not None else 0.0,
        lag1_correlation=float(row[2]) if row[2] is not None else 0.0,
        mean_emotion_low_risk=float(row[3]) if row[3] is not None else 50.0,
        mean_emotion_high_risk=float(row[4]) if row[4] is not None else 50.0,
        interpretation=str(row[5]) if row[5] else "",
        data_points=int(row[6]) if row[6] is not None else 0,
    )
