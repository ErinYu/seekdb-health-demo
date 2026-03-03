"""
SeekDB 慢病早期预警 Agent  —  Gradio UI
Run: python app.py
"""

from __future__ import annotations

import os
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.dates as mdates
from datetime import datetime
import gradio as gr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.searcher import assess_risk
from src.trend_analyzer import analyze_trend
from src.baseline import compute_baseline_score
from src.scorer import fuse, DetailedScore
from src.user_store import (
    save_diary, get_recent_diaries, get_glucose_trend,
    get_diary_count, get_baseline,
)
from src.embedder import embed
from src.agent import generate_analysis


# ── Font setup ─────────────────────────────────────────────────────────────
def _setup_font():
    cjk_candidates = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "SimHei",
                      "Microsoft YaHei", "PingFang SC", "Arial Unicode MS"]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in cjk_candidates:
        if name in available:
            plt.rcParams["font.family"] = name
            return True
    plt.rcParams["font.family"] = "sans-serif"
    return False

_HAS_CJK = _setup_font()


# ── HTML helpers ───────────────────────────────────────────────────────────

def _mode_badge(entry_count: int) -> str:
    from src.baseline import get_baseline_label, MIN_ENTRIES
    meta = get_baseline_label(entry_count)
    color = meta["color"]
    icon  = meta["icon"]
    label = meta["label"]
    desc  = meta["description"]
    return f"""
<div style="font-family:sans-serif;padding:12px 16px;border-radius:10px;
            background:#f8fafc;border:1px solid #e2e8f0;margin-bottom:8px;">
  <div style="font-size:0.95rem;font-weight:600;color:{color};">
    {icon} {label}
  </div>
  <div style="font-size:0.8rem;color:#64748b;margin-top:4px;">{desc}</div>
</div>"""


def _risk_badge(score: float, level: str) -> str:
    colors = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}
    labels = {"low": "低风险", "medium": "中等风险", "high": "高风险"}
    icons  = {"low": "✅", "medium": "⚠️", "high": "🚨"}
    c = colors.get(level, "#6b7280")
    return f"""
<div style="font-family:sans-serif;padding:16px;border-radius:12px;
            background:#f8fafc;border:1px solid #e2e8f0;">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;">
    <span style="font-size:2rem;">{icons.get(level,"")}</span>
    <div>
      <div style="font-size:1.25rem;font-weight:700;color:{c};">
        {labels.get(level, level)}
      </div>
      <div style="font-size:0.82rem;color:#64748b;">综合风险评分</div>
    </div>
    <div style="margin-left:auto;font-size:2.4rem;font-weight:800;color:{c};">
      {score:.0f}
    </div>
  </div>
  <div style="background:#e2e8f0;border-radius:99px;height:12px;overflow:hidden;">
    <div style="width:{int(score)}%;height:100%;border-radius:99px;
                background:linear-gradient(90deg,{c}88,{c});"></div>
  </div>
  <div style="display:flex;justify-content:space-between;
              font-size:0.72rem;color:#94a3b8;margin-top:3px;">
    <span>0 — 安全</span><span>50 — 注意</span><span>100 — 危险</span>
  </div>
</div>"""


def _score_breakdown(ds: DetailedScore) -> str:
    def bar(label: str, val: float, color: str, note: str = "") -> str:
        w = max(2, int(val))
        return f"""
  <div style="margin-bottom:10px;">
    <div style="display:flex;justify-content:space-between;
                font-size:0.82rem;color:#374151;margin-bottom:3px;">
      <span>{label}</span>
      <span style="font-weight:600;">{val:.0f}<span style="font-weight:400;color:#94a3b8;">/100</span></span>
    </div>
    <div style="background:#f1f5f9;border-radius:99px;height:8px;overflow:hidden;">
      <div style="width:{w}%;height:100%;background:{color};border-radius:99px;"></div>
    </div>
    {f'<div style="font-size:0.73rem;color:#94a3b8;margin-top:2px;">{note}</div>' if note else ""}
  </div>"""

    base_val  = ds.baseline_score
    base_note = "" if base_val >= 0 else "（需 7+ 条记录后启用）"
    base_display = max(0, base_val)

    html = f"""
<div style="font-family:sans-serif;padding:14px 16px;border-radius:10px;
            background:#f8fafc;border:1px solid #e2e8f0;margin-top:8px;">
  <div style="font-size:0.9rem;font-weight:600;color:#374151;margin-bottom:10px;">
    风险评分构成
  </div>
  {bar("🔍 轨迹相似度", ds.trajectory_score, "#3b82f6",
       ds.trajectory_explanation[:80]+"…" if len(ds.trajectory_explanation)>80 else ds.trajectory_explanation)}
  {bar("📈 近期趋势",   ds.trend_score,      "#f59e0b",
       ds.trend_explanation[:80]+"…" if len(ds.trend_explanation)>80 else ds.trend_explanation)}
  {bar("🧬 基线偏差",   base_display,        "#8b5cf6" if base_val>=0 else "#d1d5db",
       ds.baseline_explanation[:80]+"…" if len(ds.baseline_explanation)>80 else ds.baseline_explanation)}
</div>"""
    return html


# ── Charts ─────────────────────────────────────────────────────────────────

def _comparison_chart(ds: DetailedScore):
    from src.searcher import assess_risk as _ar
    methods = ["Keyword\n(BM25)", "Vector\n(HNSW)", "Hybrid\n(SeekDB)"]
    ratios  = [
        getattr(ds, "_kw_ratio", 0) * 100,
        getattr(ds, "_vec_ratio", 0) * 100,
        getattr(ds, "_hyb_ratio", 0) * 100,
    ]
    colors = ["#93c5fd", "#86efac", "#f97316"]
    fig, ax = plt.subplots(figsize=(4.8, 3.0))
    bars = ax.bar(methods, ratios, color=colors, width=0.45, zorder=3)
    ax.set_ylim(0, 115)
    ax.set_ylabel("Pre-danger records hit (%)", fontsize=8)
    ax.set_title("Search method comparison", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.4, zorder=0)
    ax.set_axisbelow(True)
    ax.spines[["top","right"]].set_visible(False)
    for bar, val in zip(bars, ratios):
        ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+1.5,
                f"{val:.0f}%", ha="center", va="bottom", fontsize=10, fontweight="bold")
    plt.tight_layout()
    return fig


def _history_chart(diaries):
    """Risk score trend line for the My Profile tab."""
    if not diaries:
        fig, ax = plt.subplots(figsize=(7, 2.5))
        ax.text(0.5, 0.5, "No diary entries yet", ha="center", va="center",
                transform=ax.transAxes, color="#9ca3af", fontsize=11)
        ax.axis("off")
        return fig

    dates  = [d.diary_date for d in reversed(diaries)]
    scores = [d.risk_score  for d in reversed(diaries)]
    levels = [d.risk_level  for d in reversed(diaries)]

    color_map = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}
    point_colors = [color_map.get(l, "#6b7280") for l in levels]

    fig, ax = plt.subplots(figsize=(7, 2.8))
    ax.plot(dates, scores, color="#cbd5e1", linewidth=1.5, zorder=2)
    ax.scatter(dates, scores, c=point_colors, s=55, zorder=3)
    ax.axhline(35, color="#f59e0b", linewidth=0.8, linestyle="--", alpha=0.5)
    ax.axhline(60, color="#ef4444", linewidth=0.8, linestyle="--", alpha=0.5)
    ax.set_ylim(0, 105)
    ax.set_ylabel("Risk Score", fontsize=8)
    ax.set_title("My Risk Score Trend", fontsize=9)
    ax.yaxis.grid(True, linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)
    ax.spines[["top","right"]].set_visible(False)
    if len(dates) > 6:
        plt.xticks(rotation=30, fontsize=7)
    plt.tight_layout()
    return fig


def _glucose_chart(points):
    """Glucose trend line for the My Profile tab."""
    if not points:
        return None
    dates  = [p[0] for p in points]
    values = [p[1] for p in points]
    fig, ax = plt.subplots(figsize=(7, 2.5))
    ax.plot(dates, values, color="#60a5fa", linewidth=2, marker="o", markersize=4)
    ax.axhline(126, color="#f59e0b", linewidth=0.8, linestyle="--", alpha=0.6, label="Pre-diabetic threshold")
    ax.axhline(180, color="#ef4444", linewidth=0.8, linestyle="--", alpha=0.6, label="High glucose threshold")
    ax.set_ylim(60, max(values) * 1.1 + 20)
    ax.set_ylabel("Glucose (mg/dL)", fontsize=8)
    ax.set_title("My Glucose Readings", fontsize=9)
    ax.legend(fontsize=7)
    ax.yaxis.grid(True, linestyle="--", alpha=0.3)
    ax.set_axisbelow(True)
    ax.spines[["top","right"]].set_visible(False)
    if len(dates) > 6:
        plt.xticks(rotation=30, fontsize=7)
    plt.tight_layout()
    return fig


# ── Core analysis pipeline ─────────────────────────────────────────────────

def _full_analysis(diary_text: str, glucose: float | None, bp: int | None) -> DetailedScore:
    """
    Run the full 3-signal pipeline:
      1. Population hybrid search  (SeekDB DBMS_HYBRID_SEARCH)
      2. Personal trend analysis   (SQL time-series over user_diaries)
      3. Personal baseline check   (cosine distance from stored centroid)
    Fuse into a DetailedScore.
    """
    # Embed once, reuse everywhere
    emb = embed(diary_text)

    # 1. Population trajectory
    assessment = assess_risk(diary_text, k=15)

    # 2. Personal trend
    recent = get_recent_diaries(n=7)
    trend  = analyze_trend(recent)

    # 3. Personal baseline
    entry_count   = get_diary_count()
    baseline_info = get_baseline()
    if baseline_info:
        centroid, _, cnt = baseline_info
        base_score = compute_baseline_score(emb, centroid, cnt)
    else:
        base_score = None

    # Fuse
    ds = fuse(assessment, trend, base_score, entry_count)

    # Carry comparison ratios for the chart (attach as dynamic attrs)
    ds._kw_ratio  = assessment.keyword_only_pre_danger_ratio
    ds._vec_ratio = assessment.vector_only_pre_danger_ratio
    ds._hyb_ratio = assessment.hybrid_pre_danger_ratio
    ds._assessment = assessment
    ds._emb        = emb

    return ds


# ── Tab 1: submit handler ──────────────────────────────────────────────────

EXAMPLES = [
    ["今天状态很好，精力充沛，血糖控制不错，没有口渴或疲劳感，按时吃药，睡眠规律。",
     None, None, "🟢 低风险示例"],
    ["最近几天总是感觉有些口干，下午疲劳感明显，晚上睡眠差，起来上了一次厕所。",
     138.0, None, "🟡 中风险示例"],
    ["今天头晕明显，口渴严重一直在喝水，眼睛有些模糊，晚上好几次如厕，双脚有点麻木，整个人非常虚弱。",
     215.0, None, "🔴 高风险示例"],
]


def run_check(diary_text: str, glucose_val: float | None, bp_val: int | None):
    if not diary_text.strip():
        return ("<p style='color:#9ca3af'>请输入今天的健康日记…</p>",
                None, None, [], "", None)
    try:
        ds = _full_analysis(diary_text, glucose_val, bp_val)
    except Exception as e:
        err = (f"<div style='padding:12px;background:#fef2f2;border-radius:8px;"
               f"border:1px solid #fca5a5;color:#b91c1c'>"
               f"<b>错误</b>：{e}<br>请确认 SeekDB 已启动：<code>docker-compose up -d</code>"
               f"</div>")
        return err, None, None, [], "", None

    # Save to DB
    save_diary(
        diary_text=diary_text,
        glucose_level=glucose_val,
        blood_pressure=bp_val,
        risk_score=ds.final_score,
        risk_level=ds.risk_level,
        trajectory_score=ds.trajectory_score,
        trend_score=ds.trend_score,
        baseline_score=max(0, ds.baseline_score),
        embedding=ds._emb,
    )

    # AI analysis
    analysis = generate_analysis(diary_text, ds._assessment)

    # Mode badge
    mode_html = _mode_badge(ds.entry_count + 1)  # +1 because we just saved

    # Risk badge + breakdown
    risk_html = _risk_badge(ds.final_score, ds.risk_level)
    breakdown_html = _score_breakdown(ds)
    combined_html = mode_html + risk_html + breakdown_html

    # Comparison chart
    chart = _comparison_chart(ds)

    # Top hits table
    hits_table = []
    for h in ds._assessment.top_hits:
        status = "⚠️ 预警前期" if h.is_pre_danger else "✅ 稳定期"
        days_info = f"距危险还有 {h.days_to_danger} 天" if h.days_to_danger >= 0 else "—"
        hits_table.append([
            status,
            f"{h.glucose_level:.0f}",
            h.diary_text[:70] + ("…" if len(h.diary_text) > 70 else ""),
            f"{h.keyword_score:.3f}",
            f"{h.semantic_score:.3f}",
            days_info,
        ])

    return combined_html, chart, analysis, hits_table, analysis, ds


# ── Tab 2: my profile handler ──────────────────────────────────────────────

def load_profile():
    diaries  = get_recent_diaries(n=30)
    glucose  = get_glucose_trend(days=30)
    entry_count = get_diary_count()

    risk_chart = _history_chart(diaries)
    gluc_chart = _glucose_chart(glucose) if glucose else None

    # Summary stats
    if diaries:
        avg_risk = sum(d.risk_score for d in diaries) / len(diaries)
        high_cnt = sum(1 for d in diaries if d.risk_level == "high")
        last_level = diaries[0].risk_level
        level_icons = {"low": "✅", "medium": "⚠️", "high": "🚨"}
        stats_html = f"""
<div style="font-family:sans-serif;display:flex;gap:20px;flex-wrap:wrap;padding:8px 0;">
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
              padding:14px 20px;min-width:130px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:800;">{entry_count}</div>
    <div style="font-size:0.8rem;color:#64748b;">累计记录</div>
  </div>
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
              padding:14px 20px;min-width:130px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:800;">{avg_risk:.0f}</div>
    <div style="font-size:0.8rem;color:#64748b;">平均风险评分</div>
  </div>
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
              padding:14px 20px;min-width:130px;text-align:center;">
    <div style="font-size:1.8rem;font-weight:800;">{high_cnt}</div>
    <div style="font-size:0.8rem;color:#64748b;">高风险天数（近30天）</div>
  </div>
  <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;
              padding:14px 20px;min-width:130px;text-align:center;">
    <div style="font-size:1.8rem;">{level_icons.get(last_level,"")}</div>
    <div style="font-size:0.8rem;color:#64748b;">最近一次风险等级</div>
  </div>
</div>"""
    else:
        stats_html = "<p style='color:#9ca3af'>还没有任何记录，请先在「今日检测」标签页提交一条日记。</p>"

    # Recent entries table
    table_rows = []
    for d in diaries:
        icons = {"low": "✅", "medium": "⚠️", "high": "🚨"}
        table_rows.append([
            d.diary_date,
            f"{icons.get(d.risk_level,'')} {d.risk_score:.0f}",
            f"{d.glucose_level:.0f}" if d.glucose_level else "—",
            d.diary_text[:60] + "…",
        ])

    return stats_html, risk_chart, gluc_chart, table_rows


# ── Gradio layout ──────────────────────────────────────────────────────────

CSS = """
#app-title { text-align:center; margin-bottom:0; }
#app-sub   { text-align:center; color:#64748b; margin-top:4px; margin-bottom:16px; }
"""

with gr.Blocks(css=CSS, title="SeekDB 慢病早期预警") as demo:

    gr.HTML("<h1 id='app-title'>🩺 慢病早期预警 Agent</h1>")
    gr.HTML(
        "<p id='app-sub'>基于 SeekDB 混合搜索（向量 + 全文 + SQL）的个性化健康风险评估</p>"
    )

    with gr.Tabs():

        # ── Tab 1: Today's check-in ────────────────────────────────────────
        with gr.Tab("📝 今日检测"):
            with gr.Row():

                # Left: input
                with gr.Column(scale=1):
                    gr.Markdown("### 今天的健康日记")
                    diary_in = gr.Textarea(
                        placeholder="用自己的话描述今天的感受…",
                        lines=5, label="", show_label=False,
                    )
                    with gr.Row():
                        glucose_in = gr.Number(
                            label="血糖（mg/dL，可选）", precision=1,
                            minimum=50, maximum=500, value=None,
                        )
                        bp_in = gr.Number(
                            label="收缩压（mmHg，可选）", precision=0,
                            minimum=60, maximum=220, value=None,
                        )

                    submit_btn = gr.Button("🔍 分析并记录", variant="primary", size="lg")

                    gr.Markdown("**快速示例**")
                    with gr.Row():
                        for text, gl, bp, label in EXAMPLES:
                            gr.Button(label, size="sm").click(
                                fn=lambda t=text, g=gl, b=bp: (t, g, b),
                                outputs=[diary_in, glucose_in, bp_in],
                            )

                    gr.Markdown("""
---
**SeekDB 混合搜索工作原理**

每次分析在 SeekDB 里发出一条 SQL：

```sql
SELECT DBMS_HYBRID_SEARCH.SEARCH(
  'patient_diaries', @parm   -- 同时包含 knn + bool query
)
```

三路信号一次返回：
- **BM25**（IK 中文分词）精确匹配症状关键词
- **HNSW cosine**（384 维）语义相似度
- **SQL 过滤** `is_pre_danger`、`days_to_danger`
""")

                # Right: output
                with gr.Column(scale=2):
                    score_html  = gr.HTML("<p style='color:#9ca3af'>等待分析…</p>")
                    comp_chart  = gr.Plot(label="检索方式对比")
                    ai_analysis = gr.Markdown()
                    gr.Markdown("#### 🔎 最相似历史案例（混合搜索 Top 10）")
                    hits_tbl = gr.Dataframe(
                        headers=["状态", "血糖", "日记摘要", "关键词分", "语义分", "距危险"],
                        datatype=["str","str","str","str","str","str"],
                        row_count=10, wrap=True,
                    )

            # hidden state to pass ds between callbacks
            _ds_state = gr.State()

            submit_btn.click(
                fn=run_check,
                inputs=[diary_in, glucose_in, bp_in],
                outputs=[score_html, comp_chart, ai_analysis, hits_tbl, ai_analysis, _ds_state],
            )

        # ── Tab 2: My profile ──────────────────────────────────────────────
        with gr.Tab("📊 我的档案"):
            refresh_btn  = gr.Button("🔄 刷新", size="sm")
            stats_html   = gr.HTML()
            with gr.Row():
                risk_chart_out = gr.Plot(label="风险趋势")
                gluc_chart_out = gr.Plot(label="血糖趋势")
            gr.Markdown("#### 历史记录（最近 30 条）")
            history_tbl = gr.Dataframe(
                headers=["日期", "风险评分", "血糖", "日记摘要"],
                datatype=["str","str","str","str"],
                row_count=15, wrap=True,
            )

            refresh_btn.click(
                fn=load_profile,
                outputs=[stats_html, risk_chart_out, gluc_chart_out, history_tbl],
            )
            demo.load(
                fn=load_profile,
                outputs=[stats_html, risk_chart_out, gluc_chart_out, history_tbl],
            )

        # ── Tab 3: Health Experiments (Phase 2 placeholder) ───────────────
        with gr.Tab("🧪 健康实验（即将推出）"):
            gr.Markdown("""
### 健康实验模式

*本功能正在开发中（Phase 2）。*

---
**核心概念**

用户自己设计一个"变量控制实验"，系统追踪前后差异：

```
实验名称：晚饭后散步 30 分钟的影响
自变量：是否执行晚餐后散步
观察窗口：7 天
对照基线：系统自动提取过去 21 天同期数据

实验结束后系统输出：
  散步执行的5天里，次日口渴描述的语义强度平均下降 34%
  未执行的2天则维持原水平
  相关系数：0.71（中等置信度，基于7天数据）
```

**重要**：系统只呈现相关性数据，所有因果解释由用户自己判断。
""")

    gr.HTML("""
<div style="text-align:center;font-size:0.75rem;color:#9ca3af;padding:16px 0;">
⚠️ 演示项目，数据完全合成，分析结果不构成任何医疗建议。
</div>
""")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
