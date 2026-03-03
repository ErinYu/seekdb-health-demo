"""
Personal baseline — cosine distance from the user's "healthy normal".

The baseline is the centroid of ALL diary embeddings stored so far.
It is materialised in the user_baseline table by user_store._refresh_baseline().

This module reads that centroid and computes today's deviation from it.

MIN_ENTRIES threshold
─────────────────────
Below MIN_ENTRIES the centroid is too noisy to be meaningful, so we return
None and the scorer falls back to population-only mode (cold start).
After MIN_ENTRIES we switch to "personal model mode" and blend in the
baseline signal at 25% weight.
"""

from __future__ import annotations

import math
from typing import Optional

MIN_ENTRIES = 7   # entries needed before baseline is considered reliable


def cosine_distance(v1: list[float], v2: list[float]) -> float:
    """1 − cosine_similarity.  Range: 0 (identical) → 2 (opposite)."""
    dot = sum(a * b for a, b in zip(v1, v2))
    n1  = math.sqrt(sum(a * a for a in v1))
    n2  = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return 1.0 - dot / (n1 * n2)


def compute_baseline_score(
    today_embedding: list[float],
    baseline_centroid: list[float],
    entry_count: int,
) -> Optional[float]:
    """
    Returns a 0–100 score where:
      0   = today's diary is identical to the personal baseline ("normal day")
      100 = maximally different from baseline (unusual state)

    Returns None if entry_count < MIN_ENTRIES.
    """
    if entry_count < MIN_ENTRIES:
        return None

    dist = cosine_distance(today_embedding, baseline_centroid)
    # Cosine distance is in [0, 2].  We map [0, 0.6] → [0, 100].
    # A distance of 0.6 or above is already very unusual for diary text.
    score = min(100.0, dist / 0.6 * 100)
    return round(score, 1)


def get_baseline_label(entry_count: int) -> dict:
    """
    Returns UI metadata about the current operating mode.
    """
    if entry_count < MIN_ENTRIES:
        remaining = MIN_ENTRIES - entry_count
        return {
            "mode": "population",
            "label": f"人群对比模式（还需 {remaining} 条记录建立个人基线）",
            "color": "#f59e0b",
            "icon": "🟡",
            "description": (
                f"当前已记录 {entry_count} 条日记。"
                f"再记录 {remaining} 条后系统将切换为个人模型，"
                "基于你自己的健康基线进行偏差检测。"
            ),
        }
    return {
        "mode": "personal",
        "label": f"个人模型模式（已建立 {entry_count} 条健康档案）",
        "color": "#22c55e",
        "icon": "🟢",
        "description": (
            f"已根据你的 {entry_count} 条历史记录建立个人健康基线。"
            "系统同时比对人群轨迹、个人趋势和基线偏差三路信号。"
        ),
    }
