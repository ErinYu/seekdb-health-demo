"""
Three-signal risk score fusion.

Signals
───────
  trajectory_score  (0–100)  How much today resembles historical pre-danger
                             moments in the population library.
                             Source: DBMS_HYBRID_SEARCH (searcher.py)

  trend_score       (0–100)  Direction & speed of the user's own recent trend.
                             0 = stable/improving, 100 = rapidly worsening.
                             Source: linear regression on user_diaries (trend_analyzer.py)

  baseline_score    (0–100)  Deviation from the user's personal healthy baseline.
                             0 = normal day, 100 = maximally unusual.
                             Source: cosine distance from centroid (baseline.py)
                             Only available after MIN_ENTRIES = 7 check-ins.

Fusion weights
──────────────
  Cold-start mode (< 7 entries):
    final = 0.70 × trajectory + 0.30 × trend

  Personal model mode (≥ 7 entries):
    final = 0.45 × trajectory + 0.30 × trend + 0.25 × baseline

Rationale: trajectory carries most weight because the population library
is large and calibrated. Trend captures recency. Baseline adds
personalisation once we have enough user data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .searcher import RiskAssessment
from .trend_analyzer import TrendAnalysis
from .baseline import get_baseline_label, MIN_ENTRIES
from .user_profile import ProfileParams


LEVEL_THRESHOLDS = {"low": 35, "medium": 60}   # < 35 low, 35–60 medium, > 60 high


def _level(score: float) -> str:
    if score < LEVEL_THRESHOLDS["low"]:
        return "low"
    if score < LEVEL_THRESHOLDS["medium"]:
        return "medium"
    return "high"


@dataclass
class DetailedScore:
    # ── Components ─────────────────────────────────────────────────────────
    trajectory_score: float          # population library signal
    trend_score: float               # personal trend signal
    baseline_score: float            # personal baseline signal  (-1 if unavailable)
    emotion_score: float             # Phase 3A: emotion signal  (-1 if unavailable)

    # ── Final ───────────────────────────────────────────────────────────────
    final_score: float               # 0–100
    risk_level: str                  # "low" | "medium" | "high"

    # ── Mode ────────────────────────────────────────────────────────────────
    entry_count: int
    mode: str                        # "population" | "personal"
    mode_meta: dict = field(default_factory=dict)

    # ── Explanations ────────────────────────────────────────────────────────
    trajectory_explanation: str = ""
    trend_explanation: str = ""
    baseline_explanation: str = ""
    emotion_explanation: str = ""     # Phase 3A: emotion insight


def fuse(
    assessment: RiskAssessment,
    trend: Optional[TrendAnalysis],
    baseline_score_raw: Optional[float],
    entry_count: int,
    calibration_factor: float = 1.0,
    glucose_provided: bool = False,
    prev_trend_score: float = 0.0,
    profile: Optional[ProfileParams] = None,
    emotion_score: Optional[float] = None,  # Phase 3A: emotion signal
) -> DetailedScore:
    """Combine the signals into a DetailedScore.

    calibration_factor  — global feedback-loop multiplier (Sprint 3).
    glucose_provided    — whether the user entered a glucose value today.
    prev_trend_score    — last diary's trend score, for noise-tolerance damping.
    profile             — Phase 3 personalisation params (defaults = no-op).
    emotion_score       — Phase 3A: emotion/wellness score (0-100, higher = better).
    """
    if profile is None:
        profile = ProfileParams()

    traj = assessment.risk_score          # already 0–100
    trend_val = trend.trend_score if trend else 0.0
    base_val  = baseline_score_raw if baseline_score_raw is not None else -1.0

    # ── Phase 3: glucose_sensitivity ────────────────────────────────────────
    # When the user provided a reading today, scale trajectory by personal factor.
    if glucose_provided and profile.glucose_sensitivity != 1.0:
        traj = round(min(100.0, max(0.0, traj * profile.glucose_sensitivity)), 1)

    # ── Phase 3: noise_tolerance ────────────────────────────────────────────
    # If the trend change is within the user's normal variation band, dampen it.
    trend_weight_personal   = 0.30
    trend_weight_population = 0.30
    if trend and abs(trend_val - prev_trend_score) < profile.noise_tolerance:
        trend_weight_personal   *= 0.6
        trend_weight_population *= 0.6

    # ── Phase 3A: Emotion coupling ───────────────────────────────────────────
    # Convert emotion score to risk signal: 0-100 emotion → 0-1 risk (inverted)
    # Low emotion (poor wellness) = higher risk signal
    emotion_signal = 0.0
    emotion_val = -1  # Default: unavailable

    if emotion_score is not None and profile.emotion_active:
        # Normalize emotion to signal: 0-100 → 0-1 (inverted: low emotion = high risk)
        emotion_signal = (100 - emotion_score) / 100

        # Apply personal amplification factor based on coupling strength
        if profile.emotion_amplification != 1.0:
            emotion_signal *= profile.emotion_amplification

        emotion_val = emotion_score

    # ── Weight fusion ───────────────────────────────────────────────────────
    # Four-signal mode when emotion is active and baseline is available:
    #   0.35×trajectory + 0.25×trend + 0.25×baseline + 0.15×emotion
    # Three-signal modes when emotion is inactive:
    #   Personal: 0.45×trajectory + 0.30×trend + 0.25×baseline
    #   Cold-start: 0.70×trajectory + 0.30×trend

    if entry_count >= MIN_ENTRIES and base_val >= 0:
        # Personal model mode (baseline available)
        if profile.emotion_active and emotion_val >= 0:
            # Four-signal mode with emotion
            final = (0.35 * traj + trend_weight_personal * trend_val +
                     0.25 * base_val + 0.15 * (emotion_signal * 100))
            mode = "personal_emotion"
        else:
            # Three-signal mode (original)
            final = 0.45 * traj + trend_weight_personal * trend_val + 0.25 * base_val
            mode = "personal"
    else:
        # Cold-start mode (no baseline yet)
        if profile.emotion_active and emotion_val >= 0:
            # Add emotion signal even in cold-start
            final = (0.60 * traj + trend_weight_population * trend_val +
                     0.15 * (emotion_signal * 100))
        else:
            # Original cold-start
            final = 0.70 * traj + trend_weight_population * trend_val
        mode = "population"

    # Apply global calibration (feedback loop, Sprint 3)
    final = round(min(100.0, max(0.0, final * calibration_factor)), 1)

    # ── Explanations ────────────────────────────────────────────────────────
    n_pre   = assessment.pre_danger_hits
    n_total = assessment.total_hits

    if traj >= 60:
        traj_exp = (
            f"在历史人群数据中，与今日描述最相似的 {n_total} 条记录里，"
            f"{n_pre} 条来自危险事件发生前 30 天（占 {n_pre/max(n_total,1)*100:.0f}%）。"
        )
    elif traj >= 30:
        traj_exp = (
            f"历史相似记录中有部分来自预警期（{n_pre}/{n_total} 条），需要关注。"
        )
    else:
        traj_exp = "与你今日描述相似的历史记录大多来自平稳期，说明今天状态正常。"

    if trend:
        trend_exp = trend.summary
    else:
        trend_exp = "历史记录不足，暂无个人趋势分析（需至少 3 条）。"

    if base_val < 0:
        base_exp = f"再记录 {MIN_ENTRIES - entry_count} 次后，系统将开始与你自身平日状态对比。"
    elif base_val >= 60:
        base_exp = "今天的状态与你平时明显不同，这个差异值得留意。"
    elif base_val >= 30:
        base_exp = "今天的状态与平时有一些不同，属于可观察的范围。"
    else:
        base_exp = "今天的状态与你平时非常接近，整体表现正常。"

    # ── Phase 3A: Emotion explanation ────────────────────────────────────────
    emotion_exp = ""
    if profile.emotion_active and emotion_val >= 0:
        if emotion_val < 30:
            emotion_exp = (
                f"今天的情绪状态较为低落（{emotion_val:.0f}分），"
                f"这与你的健康风险呈正相关，请留意心理调节。"
            )
        elif emotion_val > 70:
            emotion_exp = (
                f"今天情绪状态良好（{emotion_val:.0f}分），"
                "保持积极心态有助于稳定生理指标。"
            )
        # Medium emotion (30-70) - no special explanation needed

    return DetailedScore(
        trajectory_score=round(traj, 1),
        trend_score=round(trend_val, 1),
        baseline_score=round(base_val, 1),
        emotion_score=round(emotion_val, 1),
        final_score=final,
        risk_level=_level(final),
        entry_count=entry_count,
        mode=mode,
        mode_meta=get_baseline_label(entry_count),
        trajectory_explanation=traj_exp,
        trend_explanation=trend_exp,
        baseline_explanation=base_exp,
        emotion_explanation=emotion_exp,
    )
