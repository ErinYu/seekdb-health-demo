"""
Micro-intervention recommendation engine.

Recommends ONE specific, actionable intervention per analysis session
based on the user's current risk level, diary content, trend direction,
and personal profile (Phase 3).

Design principles
─────────────────
  • Single recommendation per session — not a list.
  • Concretely actionable: "今晚饭后散步20分钟" not "多运动".
  • Personalized: uses trigger_symptoms from ProfileParams to
    bias category selection when available.
  • Avoids duplication: skips domains already covered by an
    active health experiment.
  • Emergency-first: life-safety checks always take priority.

Priority order
──────────────
  P3 Emergency  → immediate medical risk
  P2 High risk  → urgent monitoring/action
  P2 Worsening  → proactive monitoring
  P1 Content    → diary-keyword-driven suggestion
  P1 Personal   → trigger_symptoms-profile-driven
  P0 Default    → hydration (always safe baseline)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .user_profile import ProfileParams
from .trend_analyzer import TrendAnalysis


@dataclass
class Intervention:
    text: str       # The recommendation sentence shown to the user
    category: str   # "就医" | "血糖管理" | "运动" | "睡眠" | "饮食" | "放松"
    icon: str       # Emoji for the card header
    reason: str     # One-line rationale shown below the text
    urgency: int    # 3=emergency, 2=important, 1=routine


# ── Keyword detection helpers ────────────────────────────────────────────────

def _hit(text: str, words: list[str]) -> bool:
    """Return True if any keyword appears in the diary text."""
    return any(w in text for w in words)


_EMERGENCY_WORDS   = ["急诊", "晕倒", "意识", "呼吸困难", "极度虚弱", "呕吐不止",
                       "昏迷", "抽搐"]
_DANGER_SX_WORDS   = ["严重头晕", "视力模糊", "眼前发花", "双脚麻木",
                       "口渴严重", "极度疲劳", "意识模糊"]
_SLEEP_WORDS       = ["失眠", "睡不好", "睡眠差", "熬夜", "睡得晚",
                       "难以入睡", "夜里醒", "睡眠不好", "辗转反侧"]
_STRESS_WORDS      = ["焦虑", "担心", "压力", "紧张", "烦躁",
                       "心情差", "情绪低落", "心烦", "担忧"]
_DIET_WORDS        = ["食欲", "暴食", "多吃", "零食", "甜食",
                       "油腻", "饮食不规律", "吃多了", "甜点"]
_EXERCISE_WORDS    = ["久坐", "没运动", "懒得动", "没动", "坐了一天",
                       "不想动", "运动少", "活动少"]


# ── Intervention library ─────────────────────────────────────────────────────

_MEDICAL_EMERGENCY = Intervention(
    "建议立刻联系家属或拨打急救电话，不要独自等待",
    "就医", "🚨",
    "描述中出现了需要紧急处理的症状信号",
    urgency=3,
)

_MEDICAL_HIGH_GLUCOSE = Intervention(
    "立即复测血糖；若结果仍超过 16.7 mmol/L（300 mg/dL），请就近急诊",
    "就医", "🚨",
    "当前血糖读数偏高，需要立即确认实际数值",
    urgency=3,
)

_MEDICAL_CHECK = Intervention(
    "今日内联系主治医生，告知近期症状变化，必要时预约门诊",
    "就医", "🏥",
    "当前风险评估偏高，专业医疗评估是最重要的下一步",
    urgency=3,
)

_GLUCOSE_FASTING = Intervention(
    "明天早晨空腹（未进食8小时）测一次血糖，并记录结果",
    "血糖管理", "🩸",
    "定期空腹血糖监测有助于发现趋势变化，避免漏报",
    urgency=2,
)

_GLUCOSE_POSTMEAL = Intervention(
    "今天午餐或晚餐后2小时再测一次血糖，观察餐后波动",
    "血糖管理", "🩸",
    "餐后血糖是了解血糖控制质量的关键窗口",
    urgency=2,
)

_EXERCISE_WALK = Intervention(
    "今天晚饭后出门散步20分钟（轻松步速，不需要出汗）",
    "运动", "🚶",
    "饭后低强度有氧运动有助于促进餐后血糖消耗",
    urgency=1,
)

_EXERCISE_STRETCH = Intervention(
    "每坐满1小时，起身活动5分钟——做几个简单拉伸或绕房间走一圈",
    "运动", "🤸",
    "打断久坐，促进血液循环，降低胰岛素抵抗",
    urgency=1,
)

_SLEEP_EARLY = Intervention(
    "今晚10点关闭手机屏幕，10点半前上床，目标睡够7小时",
    "睡眠", "💤",
    "睡眠不足会显著削弱血糖调节能力，对慢病患者影响尤为明显",
    urgency=2,
)

_SLEEP_RELAX = Intervention(
    "睡前用温水泡脚10分钟，或听轻柔音乐帮助放松入睡",
    "睡眠", "🛁",
    "改善入睡质量的简单方法，有助于稳定睡眠节律",
    urgency=1,
)

_DIET_REDUCE_CARBS = Intervention(
    "今晚主食减少约1/4碗，用蔬菜补足饱腹感",
    "饮食", "🥗",
    "减少精制碳水有助于控制餐后血糖峰值",
    urgency=1,
)

_DIET_WATER = Intervention(
    "今天把甜饮料和果汁换成白开水，全天保证喝够1500 mL",
    "饮食", "💧",
    "充足水分有助于稀释血液中的葡萄糖浓度，是血糖管理的基础",
    urgency=1,
)

_RELAX_BREATHING = Intervention(
    "找一个安静地方，做5分钟腹式深呼吸（吸气4秒，呼气6秒）",
    "放松", "🧘",
    "深呼吸可激活副交感神经，缓解应激激素对血糖的负面影响",
    urgency=1,
)

_RELAX_SCREEN = Intervention(
    "今晚睡前1小时放下手机，做点让你真正放松的事",
    "放松", "🌙",
    "减少蓝光和信息刺激，有助于情绪稳定和血糖的夜间调节",
    urgency=1,
)

_DEFAULT_HYDRATION = Intervention(
    "今天均匀地喝够6~8杯水（约1500 mL），不要等到口渴才喝",
    "饮食", "💧",
    "充足水分是血糖管理最基础也最容易做到的日常习惯",
    urgency=1,
)


# ── Main recommendation function ─────────────────────────────────────────────

def recommend(
    diary_text: str,
    risk_level: str,
    glucose: Optional[float],
    trend: Optional[TrendAnalysis],
    profile: Optional[ProfileParams] = None,
    active_experiment_variables: Optional[list[str]] = None,
) -> Intervention:
    """
    Return ONE actionable Intervention for the current session.

    Args:
        diary_text:                  Today's diary entry.
        risk_level:                  "low" | "medium" | "high"
        glucose:                     Today's glucose reading (mg/dL), or None.
        trend:                       TrendAnalysis from trend_analyzer.py, or None.
        profile:                     ProfileParams from user_profile.py.
        active_experiment_variables: List of experiment variable strings currently
                                     being tracked — avoids recommending the same thing.

    Returns:
        An Intervention chosen by priority rules.
    """
    if profile is None:
        profile = ProfileParams()
    active_vars = active_experiment_variables or []

    # Helper: check if a domain word already has an active experiment
    def _free(domain_kw: str) -> bool:
        return not any(domain_kw in v for v in active_vars)

    trend_worsening = trend is not None and trend.direction == "worsening"

    # ── P3: Emergency ────────────────────────────────────────────────────────
    if _hit(diary_text, _EMERGENCY_WORDS):
        return _MEDICAL_EMERGENCY

    # ── P3: Very high glucose ────────────────────────────────────────────────
    if glucose is not None and glucose > 280:
        return _MEDICAL_HIGH_GLUCOSE

    # ── P3: High risk + dangerous symptoms ──────────────────────────────────
    if risk_level == "high" and _hit(diary_text, _DANGER_SX_WORDS):
        return _MEDICAL_CHECK

    # ── P2: High risk → see doctor or glucose monitoring ────────────────────
    if risk_level == "high":
        # If no glucose reading today, prioritize getting one
        if glucose is None:
            return _GLUCOSE_FASTING
        return _MEDICAL_CHECK

    # ── P2: Medium risk + worsening trend → monitor glucose ─────────────────
    if risk_level == "medium" and trend_worsening:
        return _GLUCOSE_FASTING

    # ── P1: Diary-content-driven, respecting active experiments ─────────────
    if _hit(diary_text, _SLEEP_WORDS) and _free("睡"):
        return _SLEEP_EARLY

    if _hit(diary_text, _STRESS_WORDS) and _free("放松") and _free("压力"):
        return _RELAX_BREATHING

    if _hit(diary_text, _DIET_WORDS) and _free("饮食") and _free("甜食"):
        return _DIET_REDUCE_CARBS

    if _hit(diary_text, _EXERCISE_WORDS) and _free("散步") and _free("运动"):
        return _EXERCISE_WALK

    # ── P1: Profile-personalized (trigger_symptoms hint) ────────────────────
    if profile.trigger_symptoms:
        triggers = " ".join(profile.trigger_symptoms)
        if _hit(triggers, ["血糖", "口渴", "多尿", "多饮"]):
            return _GLUCOSE_POSTMEAL
        if _hit(triggers, ["疲", "乏", "睡", "无力"]) and _free("睡"):
            return _SLEEP_RELAX
        if _hit(triggers, ["焦", "烦", "压力", "情绪"]) and _free("放松"):
            return _RELAX_SCREEN

    # ── P0: Default by risk level ────────────────────────────────────────────
    if risk_level == "medium":
        return _GLUCOSE_POSTMEAL

    return _DEFAULT_HYDRATION
