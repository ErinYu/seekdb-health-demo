"""
Personal profile — Phase 3 core module.

Computes four personalisation parameters from the user's accumulated
diary entries and prediction feedback:

  glucose_sensitivity  float [0.5, 2.0]   default 1.0
      How strongly blood glucose changes predict risk for this user.
      > 1 → high glucose reliably precedes worsening; < 1 → not so much.
      Requires: ≥ 5 entries with glucose + ≥ 3 feedbacks.

  lag_window           int   [3, 14]       default 7
      How many days before a confirmed "worsened" event the user's risk
      scores start rising (personal early-warning horizon).
      Requires: ≥ 3 confirmed "worsened" feedbacks.

  trigger_symptoms     list[str]           default []
      Up to 5 symptom keywords that appear significantly more often in
      diary entries that precede confirmed worsening events.
      Requires: ≥ 5 confirmed "worsened" feedbacks.

  noise_tolerance      float [5, 40]       default 15.0
      The user's normal day-to-day risk score variation (IQR * 0.8).
      Trend signal weight is reduced when the change is within this band.
      Requires: ≥ 10 diary entries.

Profile is recomputed automatically every 5 new feedback responses
(idempotent, stored in user_profile table).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .db import get_connection


# ── Thresholds for parameter activation ────────────────────────────────────
MIN_ENTRIES_NOISE     = 10   # noise_tolerance
MIN_FEEDBACKS_GLUCOSE = 3    # glucose_sensitivity
MIN_WORSENED_LAG      = 3    # lag_window
MIN_WORSENED_TRIGGER  = 5    # trigger_symptoms
REFRESH_EVERY         = 5    # recompute after every N new feedbacks


# ── Data class ──────────────────────────────────────────────────────────────

@dataclass
class ProfileParams:
    glucose_sensitivity: float     = 1.0
    lag_window: int                = 7
    trigger_symptoms: list[str]    = field(default_factory=list)
    noise_tolerance: float         = 15.0
    data_version: int              = 0
    computed_at: str               = ""

    # Per-parameter activation flags (for UI display)
    glucose_active: bool           = False
    lag_active: bool               = False
    triggers_active: bool          = False
    noise_active: bool             = False


# ── Text utilities (no external deps) ──────────────────────────────────────

# Common stop-characters and words to exclude from trigger analysis
_STOP = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "都",
    "也", "很", "到", "说", "要", "去", "你", "看", "还", "能",
    "好", "但", "天", "今", "多", "一", "他", "她", "它", "这",
    "那", "又", "上", "下", "来", "已", "没", "时", "里", "后",
    "日", "次", "以", "对", "从", "比", "等", "两", "其", "把",
    "被", "让", "向", "因", "所", "而", "与", "或", "但是", "如果",
}


def _tokenize(text: str) -> list[str]:
    """
    Simple Chinese tokenizer: extract runs of 2–4 Chinese chars as n-grams,
    then filter stop words.  No external dependencies.
    """
    # Keep only CJK unified ideographs
    chars = re.sub(r"[^\u4e00-\u9fff]", " ", text)
    tokens: list[str] = []
    for chunk in chars.split():
        # unigrams
        tokens.extend(list(chunk))
        # bigrams
        tokens.extend(chunk[i:i+2] for i in range(len(chunk) - 1))
    return [t for t in tokens if t not in _STOP and len(t) >= 2]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    sorted_v = sorted(values)
    idx = (len(sorted_v) - 1) * pct / 100
    lo = int(idx)
    hi = min(lo + 1, len(sorted_v) - 1)
    return sorted_v[lo] + (idx - lo) * (sorted_v[hi] - sorted_v[lo])


# ── Core computation ────────────────────────────────────────────────────────

def compute_profile(diaries, feedbacks) -> ProfileParams:
    """
    Derive ProfileParams from diary + feedback history.
    Always returns a valid ProfileParams; missing data silently uses defaults.

    Args:
        diaries:   list[UserDiary]  — all user diary entries, any order
        feedbacks: list[dict]       — rows from risk_feedbacks JOIN user_diaries
                                       each has keys: diary_id, actual_outcome,
                                       risk_score, risk_level, diary_date, risk_scores_before
    """
    params = ProfileParams()

    diary_by_id = {d.id: d for d in diaries}
    all_scores  = [d.risk_score for d in diaries if d.risk_score is not None]

    worsened_diary_ids = {
        f["diary_id"] for f in feedbacks if f["actual_outcome"] == "worsened"
    }
    total_feedbacks = len(feedbacks)

    # ── 1. noise_tolerance ─────────────────────────────────────────────────
    if len(all_scores) >= MIN_ENTRIES_NOISE:
        q1 = _percentile(all_scores, 25)
        q3 = _percentile(all_scores, 75)
        iqr = q3 - q1
        raw = iqr * 0.8
        params.noise_tolerance = round(max(5.0, min(40.0, raw)), 1)
        params.noise_active = True

    # ── 2. glucose_sensitivity ─────────────────────────────────────────────
    glucose_feedbacks = [
        f for f in feedbacks
        if f["diary_id"] in diary_by_id
        and diary_by_id[f["diary_id"]].glucose_level is not None
    ]
    if len(glucose_feedbacks) >= MIN_FEEDBACKS_GLUCOSE:
        high_glucose_worsened = sum(
            1 for f in glucose_feedbacks
            if f["actual_outcome"] == "worsened"
            and diary_by_id[f["diary_id"]].glucose_level >= 126
        )
        high_glucose_ok = sum(
            1 for f in glucose_feedbacks
            if f["actual_outcome"] != "worsened"
            and diary_by_id[f["diary_id"]].glucose_level >= 126
        )
        low_glucose_worsened = sum(
            1 for f in glucose_feedbacks
            if f["actual_outcome"] == "worsened"
            and diary_by_id[f["diary_id"]].glucose_level < 126
        )
        low_glucose_ok = sum(
            1 for f in glucose_feedbacks
            if f["actual_outcome"] != "worsened"
            and diary_by_id[f["diary_id"]].glucose_level < 126
        )
        # P(worsened | high glucose) vs P(worsened | low glucose)
        p_high = high_glucose_worsened / (high_glucose_worsened + high_glucose_ok + 1e-9)
        p_low  = low_glucose_worsened  / (low_glucose_worsened  + low_glucose_ok  + 1e-9)
        # ratio: if high glucose → much more likely to worsen, sensitivity > 1
        ratio = (p_high + 1e-9) / (p_low + 1e-9)
        raw = max(0.5, min(2.0, ratio))
        params.glucose_sensitivity = round(raw, 3)
        params.glucose_active = True

    # ── 3. lag_window ──────────────────────────────────────────────────────
    worsened_feedbacks = [f for f in feedbacks if f["actual_outcome"] == "worsened"]
    if len(worsened_feedbacks) >= MIN_WORSENED_LAG:
        gaps: list[int] = []
        # Sort diaries by date to look back
        sorted_diaries = sorted(diaries, key=lambda d: str(d.diary_date))
        diary_dates    = [str(d.diary_date) for d in sorted_diaries]
        diary_scores   = [d.risk_score for d in sorted_diaries]

        for f in worsened_feedbacks:
            if f["diary_id"] not in diary_by_id:
                continue
            target_date = str(diary_by_id[f["diary_id"]].diary_date)
            try:
                idx = diary_dates.index(target_date)
            except ValueError:
                continue
            # Look back up to 14 days to find when score first crossed 35
            for look_back in range(1, min(15, idx + 1)):
                prior_score = diary_scores[idx - look_back]
                if prior_score is not None and prior_score < 35:
                    gaps.append(look_back)
                    break

        if gaps:
            sorted_gaps = sorted(gaps)
            mid = len(sorted_gaps) // 2
            median_gap = sorted_gaps[mid]
            params.lag_window = max(3, min(14, median_gap))
            params.lag_active = True

    # ── 4. trigger_symptoms ────────────────────────────────────────────────
    if len(worsened_feedbacks) >= MIN_WORSENED_TRIGGER:
        worsened_texts = [
            diary_by_id[f["diary_id"]].diary_text
            for f in worsened_feedbacks
            if f["diary_id"] in diary_by_id
        ]
        stable_ids    = {f["diary_id"] for f in feedbacks if f["actual_outcome"] != "worsened"}
        stable_texts  = [
            diary_by_id[did].diary_text
            for did in stable_ids
            if did in diary_by_id
        ]

        w_tokens = _tokenize(" ".join(worsened_texts))
        s_tokens = _tokenize(" ".join(stable_texts))

        w_freq = Counter(w_tokens)
        s_freq = Counter(s_tokens)
        w_total = max(len(w_tokens), 1)
        s_total = max(len(s_tokens), 1)

        trigger_candidates: list[tuple[str, float]] = []
        for token, w_count in w_freq.most_common(40):
            w_rate = w_count / w_total
            s_rate = s_freq.get(token, 0) / s_total
            if w_rate > 0.02 and (w_rate / (s_rate + 1e-9)) > 2.0:
                trigger_candidates.append((token, w_rate / (s_rate + 1e-9)))

        trigger_candidates.sort(key=lambda x: x[1], reverse=True)
        params.trigger_symptoms = [t for t, _ in trigger_candidates[:5]]
        if params.trigger_symptoms:
            params.triggers_active = True

    params.data_version += 1
    params.computed_at = datetime.now().isoformat(timespec="seconds")
    return params


# ── DB helpers ──────────────────────────────────────────────────────────────

def _save_profile(params: ProfileParams) -> None:
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_profile")
    cursor.execute(
        """
        INSERT INTO user_profile
            (glucose_sensitivity, lag_window, trigger_symptoms,
             noise_tolerance, data_version, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            params.glucose_sensitivity,
            params.lag_window,
            json.dumps(params.trigger_symptoms, ensure_ascii=False),
            params.noise_tolerance,
            params.data_version,
            params.computed_at,
        ),
    )
    conn.commit()
    cursor.close()
    conn.close()


def get_profile() -> ProfileParams:
    """
    Load PersonalProfile from DB.  Returns defaults if no profile exists yet.
    """
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT glucose_sensitivity, lag_window, trigger_symptoms,
               noise_tolerance, data_version, computed_at
        FROM user_profile LIMIT 1
        """
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        return ProfileParams()

    raw_triggers = row[2]
    try:
        triggers = json.loads(raw_triggers) if raw_triggers else []
    except Exception:
        triggers = []

    return ProfileParams(
        glucose_sensitivity = float(row[0]) if row[0] is not None else 1.0,
        lag_window          = int(row[1])   if row[1] is not None else 7,
        trigger_symptoms    = triggers,
        noise_tolerance     = float(row[3]) if row[3] is not None else 15.0,
        data_version        = int(row[4])   if row[4] is not None else 0,
        computed_at         = str(row[5])   if row[5] else "",
        # Infer activation flags from stored values
        glucose_active      = row[0] is not None and float(row[0]) != 1.0,
        lag_active          = row[1] is not None and int(row[1]) != 7,
        triggers_active     = bool(triggers),
        noise_active        = row[3] is not None and float(row[3]) != 15.0,
    )


def _count_feedbacks() -> int:
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM risk_feedbacks")
    n = cursor.fetchone()[0]
    cursor.close()
    conn.close()
    return n


def _current_data_version() -> int:
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT data_version FROM user_profile LIMIT 1")
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return int(row[0]) if row else 0


def maybe_refresh_profile() -> None:
    """
    Recompute and save the profile when the feedback count has crossed
    a new multiple of REFRESH_EVERY.  Idempotent: uses data_version
    stored in DB to avoid redundant recomputation.
    """
    try:
        total_fb = _count_feedbacks()
        if total_fb == 0:
            return
        expected_version = (total_fb // REFRESH_EVERY)
        if expected_version <= _current_data_version():
            return  # already up-to-date

        # Load raw data needed for computation
        from .user_store import get_recent_diaries
        diaries = get_recent_diaries(n=200)  # use all available

        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT rf.diary_id, rf.actual_outcome,
                   ud.risk_score, ud.risk_level, ud.diary_date, ud.glucose_level
            FROM risk_feedbacks rf
            JOIN user_diaries ud ON rf.diary_id = ud.id
            """
        )
        fb_rows = cursor.fetchall()
        cursor.close()
        conn.close()

        feedbacks = [
            {
                "diary_id":      int(r[0]),
                "actual_outcome": str(r[1]),
                "risk_score":    float(r[2]) if r[2] is not None else 0.0,
                "risk_level":    str(r[3]) if r[3] else "low",
                "diary_date":    str(r[4]),
                "glucose_level": float(r[5]) if r[5] is not None else None,
            }
            for r in fb_rows
        ]

        params = compute_profile(diaries, feedbacks)
        params.data_version = expected_version
        _save_profile(params)

    except Exception:
        pass   # Never crash the main analysis pipeline
