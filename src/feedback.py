"""
Prediction feedback loop — Sprint 3 core feature.

Design
──────
• 48 h after each diary entry the app asks: "当时的预警准不准？"
• User selects: 确实变差了 / 没明显变化 / 反而好转了
• System accumulates feedback and computes a personal sensitivity_factor
  that calibrates subsequent risk scores.

Feedback classification
───────────────────────
  TP: predicted risk≥35, user confirms "worsened"        → accurate
  FP: predicted risk≥35, user says "no change/improved"  → over-alert
  TN: predicted risk<35,  user says "no change/improved"  → accurate
  FN: predicted risk<35,  user confirms "worsened"        → missed

sensitivity_factor = 1.0 + (fn_rate - fp_rate) × 0.3
  > 1 → too many misses  → raise scores
  < 1 → too many alarms  → lower scores
  Range clamped to [0.7, 1.3]. Activates after MIN_FEEDBACK responses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import get_connection


MIN_FEEDBACK = 5   # minimum feedback count before calibration is applied


@dataclass
class PendingFeedback:
    diary_id: int
    diary_date: str
    risk_score: float
    risk_level: str
    diary_text: str


def get_pending_feedbacks() -> list[PendingFeedback]:
    """
    Return diaries from 24–72 hours ago that have not received feedback yet.
    Limited to 3 at a time to avoid overwhelming the user.
    """
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now()
    cutoff_near = (now - timedelta(hours=72)).date().isoformat()
    cutoff_far  = (now - timedelta(hours=24)).date().isoformat()
    cursor.execute(
        """
        SELECT ud.id, ud.diary_date, ud.risk_score, ud.risk_level, ud.diary_text
        FROM user_diaries ud
        LEFT JOIN risk_feedbacks rf ON ud.id = rf.diary_id
        WHERE rf.diary_id IS NULL
          AND ud.diary_date >= %s
          AND ud.diary_date <= %s
        ORDER BY ud.diary_date DESC
        LIMIT 3
        """,
        (cutoff_near, cutoff_far),
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [
        PendingFeedback(
            diary_id=int(r[0]),
            diary_date=str(r[1]),
            risk_score=float(r[2]) if r[2] is not None else 0.0,
            risk_level=str(r[3]) if r[3] else "low",
            diary_text=str(r[4]) if r[4] else "",
        )
        for r in rows
    ]


def submit_feedback(diary_id: int, actual_outcome: str) -> None:
    """
    Record user feedback for a diary entry.

    actual_outcome: 'worsened' | 'no_change' | 'improved'
    Uses ON DUPLICATE KEY UPDATE so the user can change their mind.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO risk_feedbacks (diary_id, actual_outcome)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE actual_outcome = VALUES(actual_outcome)
        """,
        (diary_id, actual_outcome),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_calibration_stats() -> dict:
    """
    Return TP/FP/TN/FN counts and overall accuracy.

    Predictions are classified as "risk" when risk_level is medium or high.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ud.risk_level, rf.actual_outcome
        FROM risk_feedbacks rf
        JOIN user_diaries ud ON rf.diary_id = ud.id
        """
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    tp = fp = tn = fn = 0
    for risk_level, actual_outcome in rows:
        predicted_risk  = risk_level in ("medium", "high")
        actually_worse  = actual_outcome == "worsened"

        if predicted_risk and actually_worse:
            tp += 1
        elif predicted_risk and not actually_worse:
            fp += 1
        elif not predicted_risk and not actually_worse:
            tn += 1
        else:
            fn += 1

    total    = tp + fp + tn + fn
    accuracy = (tp + tn) / total if total > 0 else None
    return {
        "total": total,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "accuracy": accuracy,
    }


def get_sensitivity_factor() -> float:
    """
    Compute the personal calibration multiplier from accumulated feedback.
    Returns 1.0 (no change) until MIN_FEEDBACK responses are collected.

    Returns a float in [0.7, 1.3].
    """
    stats = get_calibration_stats()
    if stats["total"] < MIN_FEEDBACK:
        return 1.0

    tp, fp, tn, fn = stats["tp"], stats["fp"], stats["tn"], stats["fn"]

    # false-positive rate: over-alerted among "risk" predictions
    fp_rate = fp / (tp + fp) if (tp + fp) > 0 else 0.0
    # false-negative rate: missed among "safe" predictions
    fn_rate = fn / (tn + fn) if (tn + fn) > 0 else 0.0

    factor = 1.0 + (fn_rate - fp_rate) * 0.3
    return round(max(0.7, min(1.3, factor)), 3)
