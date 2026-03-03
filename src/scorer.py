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


def fuse(
    assessment: RiskAssessment,
    trend: Optional[TrendAnalysis],
    baseline_score_raw: Optional[float],
    entry_count: int,
    calibration_factor: float = 1.0,
    glucose_provided: bool = False,
    prev_trend_score: float = 0.0,
    profile: Optional[ProfileParams] = None,
) -> DetailedScore:
    """Combine the three signals into a DetailedScore.

    calibration_factor  — global feedback-loop multiplier (Sprint 3).
    glucose_provided    — whether the user entered a glucose value today.
    prev_trend_score    — last diary's trend score, for noise-tolerance damping.
    profile             — Phase 3 personalisation params (defaults = no-op).
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

    # ── Weight fusion ───────────────────────────────────────────────────────
    if entry_count >= MIN_ENTRIES and base_val >= 0:
        final = 0.45 * traj + trend_weight_personal * trend_val + 0.25 * base_val
        mode  = "personal"
    else:
        final = 0.70 * traj + trend_weight_population * trend_val
        mode  = "population"

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

    return DetailedScore(
        trajectory_score=round(traj, 1),
        trend_score=round(trend_val, 1),
        baseline_score=round(base_val, 1),
        final_score=final,
        risk_level=_level(final),
        entry_count=entry_count,
        mode=mode,
        mode_meta=get_baseline_label(entry_count),
        trajectory_explanation=traj_exp,
        trend_explanation=trend_exp,
        baseline_explanation=base_exp,
    )
