"""
Synthetic patient data generator.

Data source: fully synthetic — generated from medically validated
statistical ranges for Type-2 diabetes markers (no real patient data).

References used for physiological ranges:
  • ADA Standards of Medical Care in Diabetes (2024)
  • WHO Global Report on Diabetes (2016)
  • NHANES public summary statistics
"""

import random
import math
from datetime import date, timedelta
from dataclasses import dataclass, field

# ── Symptom vocabulary ────────────────────────────────────────────────────────

# Tuples of (diary_template, symptom_keywords)
# Templates are Chinese free-text that a patient might write.
# Keywords are the important medical tokens for full-text search.

SYMPTOM_TEMPLATES: dict[str, list[tuple[str, str]]] = {
    # Glucose  70–100  (normal)
    "normal": [
        ("今天状态很好，精力充沛，没有什么不舒服的地方。饮食和睡眠都很规律，血糖控制得不错。",
         "精力 饮食正常 睡眠好"),
        ("整体感觉不错，没有口渴或疲劳感，今天走了六千步，身体很轻盈。",
         "无口渴 精神好 运动"),
        ("今天心情愉快，吃了三顿正常的饭，没有嗜睡感，晚上睡眠质量也不错。",
         "饮食正常 心情好 睡眠正常"),
        ("感觉还行，没什么特别的不适，体力也可以，正常上班没有问题。",
         "正常 体力好"),
        ("今天血糖稳定，用药也按时，没有头晕或者手脚麻木的情况。",
         "血糖稳定 按时用药 无头晕 无麻木"),
    ],
    # Glucose  100–126  (borderline / impaired fasting glucose)
    "borderline": [
        ("今天有些疲倦，下午特别容易犯困，口渴感比平时明显一些，喝了比平时多的水。",
         "疲倦 犯困 口渴 多饮"),
        ("最近容易感到疲劳，饭后嗜睡比较明显，精神状态一般，需要休息一会儿才缓过来。",
         "疲劳 饭后嗜睡 精神差"),
        ("有轻微口干的感觉，精力不太足，工作效率有所下降，不知道是不是最近压力大。",
         "口干 精力不足 疲劳"),
        ("下午感觉有些头重，腿有点酸软，走路不如以前轻快了，晚上上了一次厕所。",
         "头重 腿酸 夜尿"),
        ("今天吃完饭后血糖偏高一些，感觉饱腹感来得很快，饭量下降了不少。",
         "饭后血糖高 食欲下降"),
    ],
    # Glucose  126–180  (elevated / mild hyperglycemia)
    "elevated": [
        ("今天口渴感比较明显，一直在喝水，上厕所的次数也多了不少。感觉有些乏力，腿很沉。",
         "口渴 多饮 多尿 乏力 腿沉"),
        ("头有些轻微的晕眩感，眼睛也有点干涩模糊，整体感觉比较疲惫，工作很难集中精神。",
         "头晕 眼睛干涩 视力模糊 疲惫"),
        ("晚上起来上了两次厕所，影响了睡眠质量，早上感觉没睡够，整个人身体沉重。",
         "夜尿频繁 睡眠差 身体沉重 多尿"),
        ("饿得比较快，吃了饭不到两小时又感觉饿了，但喝水很多，嘴巴老是干。",
         "多食 口渴 多饮 口干"),
        ("皮肤最近有些干燥瘙痒，腿上有个小伤口愈合很慢，感觉身体有些异常。",
         "皮肤干燥 瘙痒 伤口愈合慢"),
    ],
    # Glucose  180–250  (high / moderate hyperglycemia)
    "high": [
        ("今天口渴非常严重，喝了很多水还是觉得很渴，尿频明显，去了好几次洗手间。视力有些模糊，眼前偶尔发花。",
         "严重口渴 多饮 尿频 多尿 视力模糊"),
        ("头晕头疼比较明显，全身乏力，手脚有点麻木感。食欲下降了，但嘴里有甜腻的异味，精神很差。",
         "头晕 头疼 乏力 手脚麻木 食欲下降 口中异味"),
        ("感觉非常疲惫虚弱，精力完全不足，眼睛看东西有些不清晰，尤其是远处的字看不太清楚。夜间尿频严重。",
         "疲惫虚弱 视力下降 夜间尿频 多尿"),
        ("今天体重又轻了一点，但明明吃了不少东西，感觉身体在消耗能量。手脚时常有刺痛感和麻木感。",
         "体重下降 多食 手脚刺痛 手脚麻木"),
        ("嘴巴一直很干，喝了大量水也缓解不了口渴，尿量很多，颜色也变浅了。感觉头脑不太清醒，有点恍惚。",
         "口干 多饮 多尿 头脑不清 恍惚"),
    ],
    # Glucose  250+  (very high / severe hyperglycemia — near-crisis)
    "critical": [
        ("今天状态极差，严重口渴多尿，头晕目眩几乎站不稳，视力明显下降。全身无力，感觉必须要去看医生了。",
         "严重口渴 多尿 头晕目眩 站立困难 视力下降 全身无力 需就医"),
        ("整个人虚弱不堪，大量喝水却仍口渴难忍，频繁上厕所。头痛剧烈，视力模糊严重，出现了恶心感。",
         "虚弱 多饮 口渴 多尿 头痛剧烈 视力模糊 恶心"),
        ("极度疲劳，手脚发麻发凉，脚趾刺痛明显。呼吸有些急促，嘴里有一股酸甜的奇怪气味，情况令人担忧。",
         "极度疲劳 手脚发麻 脚趾刺痛 呼吸急促 口中异味 病情恶化"),
        ("今天突然觉得很不对劲，大量出汗，心跳很快，脑子有些转不过来，视力一阵一阵模糊，家人建议我去急诊。",
         "大量出汗 心跳加快 意识模糊 视力模糊 急诊"),
        ("整晚几乎没睡，夜尿八九次，白天头晕眼花，走路踉跄，手在发抖。血糖仪显示超出范围，情况紧急。",
         "夜尿频繁 头晕眼花 走路不稳 手抖 血糖超标 紧急"),
    ],
}


def glucose_to_tier(glucose: float) -> str:
    if glucose < 100:
        return "normal"
    elif glucose < 126:
        return "borderline"
    elif glucose < 180:
        return "elevated"
    elif glucose < 250:
        return "high"
    else:
        return "critical"


@dataclass
class DiaryRecord:
    patient_id: int
    diary_date: date
    diary_text: str
    symptoms_keywords: str
    glucose_level: float
    blood_pressure: int
    bmi: float
    is_pre_danger: bool
    days_to_danger: int  # -1 if no upcoming danger event


def _jitter(val: float, sigma: float) -> float:
    return val + random.gauss(0, sigma)


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def generate_patient_diaries(
    patient_id: int,
    n_normal_days: int,
    n_pre_danger_days: int,
    rng: random.Random | None = None,
) -> list[DiaryRecord]:
    """
    Generate daily diary records for one patient.

    n_normal_days   : days with stable / normal health
    n_pre_danger_days : days in deteriorating pre-crisis phase
                        (is_pre_danger=True, days_to_danger counts down)

    Glucose trajectory:
      • normal days:    base_glucose ± noise          (70–130 range)
      • pre-danger days: linearly rises to 200–320    (worsening hyperglycemia)
    """
    if rng is None:
        rng = random.Random()

    records: list[DiaryRecord] = []
    today = date.today()
    total_days = n_normal_days + n_pre_danger_days
    start_date = today - timedelta(days=total_days - 1)

    # Patient-level baseline parameters
    base_glucose = rng.uniform(85, 125)        # mg/dL
    base_bp = rng.randint(110, 135)            # systolic mmHg
    base_bmi = round(rng.uniform(22.0, 32.0), 1)

    crisis_glucose_peak = rng.uniform(210, 320)  # reached on last pre-danger day

    for day_idx in range(total_days):
        current_date = start_date + timedelta(days=day_idx)
        is_pre_danger = day_idx >= n_normal_days
        days_to_danger = (total_days - 1 - day_idx) if is_pre_danger else -1

        # ── Glucose ────────────────────────────────────────────────────────
        if not is_pre_danger:
            glucose = _clamp(_jitter(base_glucose, 12), 70, 140)
        else:
            # Sigmoid-shaped deterioration over pre-danger window
            progress = (day_idx - n_normal_days) / max(n_pre_danger_days - 1, 1)
            smooth = 1 / (1 + math.exp(-8 * (progress - 0.5)))  # S-curve 0→1
            glucose = base_glucose + smooth * (crisis_glucose_peak - base_glucose)
            glucose = _clamp(_jitter(glucose, 15), 80, 400)

        # ── Blood pressure ─────────────────────────────────────────────────
        bp_add = 0
        if is_pre_danger:
            progress = (day_idx - n_normal_days) / max(n_pre_danger_days - 1, 1)
            bp_add = progress * rng.uniform(10, 25)
        bp = int(_clamp(_jitter(base_bp + bp_add, 5), 95, 190))

        # ── BMI (slow drift) ───────────────────────────────────────────────
        bmi = round(_clamp(_jitter(base_bmi, 0.3), 17.0, 45.0), 1)

        # ── Diary text ─────────────────────────────────────────────────────
        tier = glucose_to_tier(glucose)
        template, keywords = rng.choice(SYMPTOM_TEMPLATES[tier])

        # Append a random context line for variety
        context_lines = [
            f"今天测了一下血压，收缩压大约{bp}。",
            f"体重没什么变化，大概{bmi}公斤。",
            "天气不错，出去散了散步。",
            "最近压力有点大，睡眠受了些影响。",
            "按时服药，没有忘记。",
            "饮食上控制了一下，少吃了些碳水。",
            "家人说我最近脸色不太好。",
        ]
        diary_text = template + rng.choice(context_lines)

        records.append(
            DiaryRecord(
                patient_id=patient_id,
                diary_date=current_date,
                diary_text=diary_text,
                symptoms_keywords=keywords,
                glucose_level=round(glucose, 1),
                blood_pressure=bp,
                bmi=bmi,
                is_pre_danger=is_pre_danger,
                days_to_danger=days_to_danger,
            )
        )

    return records


def generate_all_patients(
    n_danger_patients: int = 40,
    n_normal_patients: int = 60,
    normal_days: int = 20,
    pre_danger_days: int = 25,
    seed: int = 42,
) -> list[DiaryRecord]:
    """
    Generate a full synthetic dataset.

    danger_patients  : patients who develop a crisis at the end
    normal_patients  : patients who remain stable
    """
    rng = random.Random(seed)
    all_records: list[DiaryRecord] = []

    for pid in range(1, n_danger_patients + 1):
        records = generate_patient_diaries(
            patient_id=pid,
            n_normal_days=normal_days,
            n_pre_danger_days=pre_danger_days,
            rng=rng,
        )
        all_records.extend(records)

    for pid in range(n_danger_patients + 1, n_danger_patients + n_normal_patients + 1):
        # Normal patients: all days are "normal" (no pre-danger phase)
        records = generate_patient_diaries(
            patient_id=pid,
            n_normal_days=normal_days + pre_danger_days,
            n_pre_danger_days=0,
            rng=rng,
        )
        all_records.extend(records)

    return all_records
