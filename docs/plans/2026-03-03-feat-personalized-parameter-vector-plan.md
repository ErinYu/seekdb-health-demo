---
title: "feat: Phase 3 个性化参数向量"
type: feat
status: completed
date: 2026-03-03
---

# Phase 3：个性化参数向量

## Overview

从「所有用户用同一套权重」升级为「每个用户有一组从自身数据中学到的个人参数」。
这些参数以结构化 JSON 存入 SeekDB 新表 `user_profile`，在每次混合搜索和评分时注入，使风险评估真正个性化。

这是 Claude 在 Sprint 规划中明确提出的 Phase 3 核心设计：
> "让群体模型学会认识你这个人，而不是构建一个数字孪生。"

---

## 问题陈述

当前系统的所有计算均使用固定参数：

| 参数 | 当前值 | 问题 |
|------|--------|------|
| 趋势分析窗口 | 固定 7 天 | 有人症状提前 3 天出现，有人提前 10 天——用同一窗口对部分用户不准 |
| 混合搜索关键词权重 | 所有词等权 | 某些症状词对某个用户来说更有预警意义，但系统无法识别 |
| 血糖在评分中的影响 | 固定进入轨迹分 | 不同人血糖敏感度差异极大，同样 140 mg/dL 对一个人可能是危险信号，对另一个人是正常值 |
| 噪声容忍带 | 无 | 有些用户的日常波动较大，固定阈值导致过度预警 |

现有的反馈闭环（Sprint 3）提供了一维全局矫正（`sensitivity_factor`），但无法区分上述四种不同来源的误差。

---

## 解决方案

新增 `user_profile` 表，存储每用户一组四维参数，由历史日记和反馈数据自动推算，并在分析管道中注入。

### 四个参数维度

#### 1. `glucose_sensitivity`（float, 默认 1.0）
血糖对风险的个人放大系数。
- 从"血糖偏高日" vs "血糖正常日"的风险评分差异中推算。
- 若用户高血糖时确实频繁确认"变差了" → sensitivity > 1；若血糖高但反馈"没变化" → sensitivity < 1。
- 应用：在 `fuse()` 中将 `trajectory_score` 乘以 `glucose_sensitivity`（当当日有血糖输入时）。
- 范围：`[0.5, 2.0]`

#### 2. `lag_window`（int, 默认 7 天）
用户的个人预警提前量（天数）。
- 在确认过"确实变差了"的反馈中，回溯该反馈对应日记之前几天的风险分开始上升。
- 应用：替换 `trend_analyzer.py` 中固定的 `window=7`。
- 范围：`[3, 14]`，需至少 3 次确认"变差"的反馈。

#### 3. `trigger_symptoms`（list[str], 默认 []）
对该用户预测价值最高的症状关键词（最多 5 个）。
- 对比"变差前"日记 vs "没变化/好转"前日记的高频词，找出差异显著的词。
- 应用：在 `hybrid_search()` 中，对这些词添加 `boost` 参数，提高其 BM25 权重。
- 需至少 5 次确认"变差了"的反馈。

#### 4. `noise_tolerance`（float, 默认 15.0）
用户的正常风险波动范围（分）。
- 从历史风险评分的四分位距（IQR）估算：`noise_tolerance = IQR * 0.8`。
- 应用：在 `scorer.py` 中，若本次评分相比上次的变化在 `noise_tolerance` 以内，降低趋势信号的权重。
- 需至少 10 条记录。

---

## 技术方案

### 新增文件

#### `src/user_profile.py`

```python
@dataclass
class ProfileParams:
    glucose_sensitivity: float = 1.0
    lag_window: int = 7
    trigger_symptoms: list[str] = field(default_factory=list)
    noise_tolerance: float = 15.0
    data_version: int = 0          # 每次 recompute 递增
    computed_at: str = ""

def compute_profile(diaries, feedbacks) -> ProfileParams: ...
def get_profile() -> ProfileParams: ...               # 读 DB，不存在则返回默认值
def maybe_refresh_profile() -> None: ...              # feedback 每增加 5 条触发一次
```

核心逻辑草图：

```
compute_profile(diaries, feedbacks):

  # glucose_sensitivity
  worsened_ids = {f.diary_id for f in feedbacks if f.actual_outcome == "worsened"}
  high_glucose_diaries = [d for d in diaries if d.glucose_level and d.glucose_level >= 126]
  ...

  # lag_window
  for each worsened feedback:
      look back 1..14 days in risk_score history
      find the day when risk_score first crossed 35
      record the gap
  lag_window = median(gaps) if enough data else 7

  # trigger_symptoms
  worsened_texts = [d.diary_text for d in diaries if d.id in worsened_ids]
  stable_texts   = [d.diary_text for d in diaries if d.id not in worsened_ids]
  high_freq_in_worsened = Counter(tokenize(worsened_texts)).most_common(20)
  trigger_symptoms = [w for w, c in high_freq if freq_ratio(w, worsened, stable) > 2.0][:5]

  # noise_tolerance
  scores = [d.risk_score for d in diaries]
  q1, q3 = percentile(scores, 25), percentile(scores, 75)
  noise_tolerance = (q3 - q1) * 0.8
```

### 修改文件

#### `src/schema.py`

```sql
CREATE TABLE IF NOT EXISTS user_profile (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    glucose_sensitivity FLOAT       DEFAULT 1.0,
    lag_window          INT         DEFAULT 7,
    trigger_symptoms    JSON,           -- ["口渴", "头晕", ...]
    noise_tolerance     FLOAT       DEFAULT 15.0,
    data_version        INT         DEFAULT 0,
    computed_at         TIMESTAMP   DEFAULT CURRENT_TIMESTAMP
)
```

#### `src/ingest.py`

- `setup_schema()` 新增 `CREATE_USER_PROFILE`（第 7 张表）
- drop list 加入 `user_profile`

#### `src/searcher.py` — `hybrid_search()`

增加 `boost_keywords: list[str] = []` 参数：

```python
# 如果 boost_keywords 非空，在 bool.should 中加 boosted match
if boost_keywords:
    for kw in boost_keywords:
        parm["query"]["bool"]["should"].append({
            "match": {"diary_text": {"query": kw, "boost": 2.5}}
        })
```

#### `src/scorer.py` — `fuse()`

增加 `profile: ProfileParams` 参数：

```python
# glucose_sensitivity: 有血糖输入时放大/缩小轨迹分
if glucose_provided and profile.glucose_sensitivity != 1.0:
    traj = traj * profile.glucose_sensitivity
    traj = min(100.0, max(0.0, traj))

# noise_tolerance: 小幅变化降低趋势信号权重
if trend and abs(trend.trend_score - prev_trend_score) < profile.noise_tolerance:
    trend_weight *= 0.6   # 波动在噪声范围内，降权
```

#### `src/trend_analyzer.py` — `analyze_trend()`

```python
# window 参数改为从 profile 读取
def analyze_trend(diaries, window: int = 7) -> ...
# 调用方改为: analyze_trend(recent, window=profile.lag_window)
```

#### `app.py` — `_full_analysis()`

```python
from src.user_profile import get_profile, maybe_refresh_profile

maybe_refresh_profile()          # 如需要则重算（幂等）
profile = get_profile()

# 搜索时注入个人症状关键词
assessment = assess_risk(diary_text, k=15, boost_keywords=profile.trigger_symptoms)

# 趋势分析使用个人窗口
recent = get_recent_diaries(n=profile.lag_window * 2)
trend  = analyze_trend(recent, window=profile.lag_window)

# 评分时注入完整 profile
ds = fuse(assessment, trend, base_score, entry_count,
          calibration_factor, profile=profile)
```

#### `app.py` — Tab 2

在「系统学习进度」卡片中新增「个人参数」区块：

```
🎯 个人参数（已激活 X/4 个）
  血糖敏感度：偏高（1.4×）      ← 需 5+ feedbacks
  预警提前量：约 5 天            ← 需 3+ 变差记录
  个人风险词：口渴、头晕、疲劳  ← 需 5+ 变差记录
  正常波动带：±12 分             ← 需 10+ 条记录
```

---

## 数据流图

```
用户日记 + 血糖
      │
      ▼
_full_analysis()
      │
      ├─ get_profile()  ←─── user_profile 表
      │        │
      │   ProfileParams
      │  (sensitivity, lag, triggers, tolerance)
      │        │
      ├─ hybrid_search(boost_keywords=triggers)     ← SeekDB 混合搜索
      │
      ├─ analyze_trend(window=lag_window)
      │
      ├─ compute_baseline_score()
      │
      └─ fuse(..., profile=profile)
              │
         DetailedScore（个性化评分）
              │
          save_diary()  → maybe_refresh_profile() ← risk_feedbacks 增加时
```

---

## 系统影响评估

### 接口变更（向后兼容）

所有新参数均有默认值，`profile=ProfileParams()` 是默认无操作参数。冷启动（0 feedbacks）行为与当前完全一致。

### 与反馈闭环（Sprint 3）的关系

两者并存、互补：
- `sensitivity_factor`（Sprint 3）：全局乘数，快速粗调，5 条 feedbacks 后生效
- `ProfileParams`（Phase 3）：多维精调，不同来源的误差由对应参数分别处理，10–20 条数据后逐步激活

### SeekDB 的新角色

Phase 3 为 SeekDB 增加了第三种使用场景：
1. 混合搜索（已有）
2. 向量存储 + 质心计算（已有）
3. **个人参数持久化**：`user_profile` 表作为用户"认知模型"的存储介质，支撑下游搜索和评分的个性化注入

未来扩展：`trigger_symptoms` 可改为 VECTOR 列，实现"找到与我症状关键词最相似的历史人群子集"的语义过滤，而非精确匹配。

---

## 验收标准

### 功能要求

- [x] `user_profile` 表随 `setup_schema()` 自动创建
- [x] `ProfileParams` 默认值使系统行为与 Phase 3 前完全一致（无 regression）
- [x] 每个参数有独立的数据充足性判断，不足时静默回退默认值
- [x] `maybe_refresh_profile()` 在 feedback 每增加 5 条时自动触发，幂等
- [x] `boost_keywords` 正确传入 `DBMS_HYBRID_SEARCH` 的 bool.should
- [x] Tab 2「个人参数」区块显示各参数的激活状态

### 质量要求

- [x] `compute_profile()` 在 0 feedbacks 时不抛出异常
- [x] `ProfileParams` 所有字段有合理的 clamp 范围
- [x] 分词（`trigger_symptoms` 提取）仅用 `re` + bigrams + Counter，不引入新依赖

---

## 依赖与风险

| 风险 | 缓解措施 |
|------|----------|
| `trigger_symptoms` 提取需要中文分词 | 优先用简单词频统计（split + Counter），可选引入 jieba |
| `lag_window` 需要至少 3 次确认"变差"才有意义 | 不足时静默使用默认值 7，UI 显示「数据积累中」 |
| `glucose_sensitivity` 计算依赖用户输入血糖 | 若用户从未输入血糖，该参数保持 1.0 |
| boost_keywords 在 DBMS_HYBRID_SEARCH 中是否支持 boost 字段 | 已在 `hybrid_search` 中有 `parm` JSON 构造模式，可测试验证 |

---

## 交付顺序建议

1. **schema + ingest**（30 min）— 建表，确保新表被创建和 drop
2. **`user_profile.py`**（2 h）— 核心计算逻辑 + CRUD，含充足性判断
3. **`searcher.py` 更新**（30 min）— boost_keywords 注入混合搜索
4. **`scorer.py` / `trend_analyzer.py` 更新**（45 min）— 参数注入
5. **`app.py` 接线**（45 min）— `_full_analysis()` 串联 + Tab 2 展示
6. **集成验证**（30 min）— 0 feedbacks 和 10+ feedbacks 两种状态下均正常运行

---

## 未来扩展

- `trigger_symptoms` → `VECTOR(384)` 列：存储"高风险感受"的语义质心，用于 knn filter
- `user_profile` 向量化后可做用户聚类：找到"与我最相似的患者群体"，从中提取更精准的预警模式
- 多用户模式：`user_profile` 增加 `user_id` 字段，支持家庭/医生多账户场景

---

## Sources

- 原始设计参考：Claude Phase 3 重构方案（见对话记录 2026-03-03）
- 现有相关实现：
  - `src/feedback.py:get_calibration_stats()` — TP/FP/TN/FN 分类，可复用
  - `src/searcher.py:hybrid_search()` — parm JSON 构造模式（第 58–82 行）
  - `src/scorer.py:fuse()` — 现有融合权重逻辑（第 75–95 行）
  - `src/trend_analyzer.py:analyze_trend()` — window 参数已存在，只需改调用方
  - `src/schema.py` — 建表模式参考 `CREATE_USER_BASELINE`
