"""
Personal trend analysis from the user's own diary history.

Signals computed
────────────────
  glucose_slope     Linear regression slope (mg/dL per day) over last 7 entries.
                    Positive = rising. > +3 is a concern; > +6 is high concern.

  severity_slope    Same regression on a 0–4 severity tier derived from glucose:
                    normal=0  borderline=1  elevated=2  high=3  critical=4
                    Detects worsening even when glucose readings are missing.

  symptom_shift     Fraction of recent entries (last 5) that are in a "bad" tier
                    compared to the older half of the window. Detects keyword drift.

trend_score         0–100 composite of the three signals above.
                    0 = stable / improving, 100 = rapidly worsening.

Requires at least 3 diary entries. Returns None if insufficient data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .user_store import UserDiary


def _glucose_to_severity(glucose: float | None) -> float | None:
    if glucose is None:
        return None
    if glucose < 100:
        return 0.0
    if glucose < 126:
        return 1.0
    if glucose < 180:
        return 2.0
    if glucose < 250:
        return 3.0
    return 4.0


def _linreg_slope(ys: list[float]) -> float:
    """Slope of the least-squares fit y = a + b*x for x = 0,1,…,n-1."""
    n = len(ys)
    if n < 2:
        return 0.0
    xs = list(range(n))
    x_mean = (n - 1) / 2
    y_mean = sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    return num / den if den else 0.0


@dataclass
class TrendAnalysis:
    glucose_slope: float          # mg/dL per day
    severity_slope: float         # 0–4 scale per day
    symptom_shift: float          # 0–1 fraction of bad-tier entries in recent half
    trend_score: float            # 0–100
    direction: str                # "worsening" | "stable" | "improving"
    summary: str
    data_points: int


def analyze_trend(diaries: list[UserDiary], window: int = 7) -> Optional[TrendAnalysis]:
    """
    Args:
        diaries: list from user_store.get_recent_diaries(), NEWEST FIRST.
        window:  how many entries to include in the trend window.
    Returns None if < 3 entries available.
    """
    # Work oldest-first within the window
    window_entries = list(reversed(diaries[:window]))
    if len(window_entries) < 3:
        return None

    # ── Glucose slope ──────────────────────────────────────────────────────
    glucose_vals = [e.glucose_level for e in window_entries if e.glucose_level is not None]
    glucose_slope = _linreg_slope(glucose_vals) if len(glucose_vals) >= 2 else 0.0

    # ── Severity slope (works even without glucose readings) ───────────────
    severity_vals = [
        _glucose_to_severity(e.glucose_level)
        for e in window_entries
        if _glucose_to_severity(e.glucose_level) is not None
    ]
    severity_slope = _linreg_slope(severity_vals) if len(severity_vals) >= 2 else 0.0

    # ── Symptom shift: compare older half vs recent half ───────────────────
    mid = len(window_entries) // 2
    older_half = window_entries[:mid] or window_entries[:1]
    newer_half = window_entries[mid:] or window_entries[-1:]

    def _bad_ratio(entries: list[UserDiary]) -> float:
        bad = sum(
            1 for e in entries
            if e.glucose_level is not None and e.glucose_level >= 126
        )
        total = sum(1 for e in entries if e.glucose_level is not None)
        return bad / total if total else 0.0

    old_bad = _bad_ratio(older_half)
    new_bad = _bad_ratio(newer_half)
    symptom_shift = max(0.0, new_bad - old_bad)  # positive = worsening

    # ── Composite trend score ──────────────────────────────────────────────
    # glucose_slope: +10 mg/dL/day → 100 pts   (clamp at ±10)
    glucose_component = min(100.0, max(0.0, glucose_slope / 10.0 * 100))
    # severity_slope: +1 tier/day → 100 pts    (clamp at 0–1)
    severity_component = min(100.0, max(0.0, severity_slope * 100))
    # symptom_shift: +1.0 → 100 pts
    shift_component = min(100.0, symptom_shift * 100)

    trend_score = (
        0.50 * glucose_component
        + 0.30 * severity_component
        + 0.20 * shift_component
    )

    # ── Direction & summary ────────────────────────────────────────────────
    if trend_score >= 55:
        direction = "worsening"
        if glucose_slope > 3:
            summary = (
                f"血糖近 {len(glucose_vals)} 次记录平均每天上升 "
                f"{glucose_slope:.1f} mg/dL，趋势持续恶化。"
            )
        else:
            summary = f"近期症状描述正在向高风险方向偏移（趋势评分 {trend_score:.0f}/100）。"
    elif trend_score >= 25:
        direction = "stable"
        summary = f"近期健康状态基本稳定，有轻微波动（趋势评分 {trend_score:.0f}/100）。"
    else:
        direction = "improving"
        if glucose_slope < -2:
            summary = (
                f"血糖近 {len(glucose_vals)} 次记录平均每天下降 "
                f"{abs(glucose_slope):.1f} mg/dL，趋势向好。"
            )
        else:
            summary = "近期健康趋势稳定向好。"

    return TrendAnalysis(
        glucose_slope=round(glucose_slope, 2),
        severity_slope=round(severity_slope, 3),
        symptom_shift=round(symptom_shift, 3),
        trend_score=round(trend_score, 1),
        direction=direction,
        summary=summary,
        data_points=len(window_entries),
    )
