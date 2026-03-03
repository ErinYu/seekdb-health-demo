"""
SeekDB Chronic Disease Early Warning Demo
Gradio UI — run with: python app.py
"""

import os
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import gradio as gr

# Use a CJK-capable font when available, fall back to system default
def _setup_cjk_font():
    """Try to find a font that can render CJK characters."""
    candidates = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "SimHei",
                  "Microsoft YaHei", "PingFang SC", "Arial Unicode MS"]
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            plt.rcParams["font.family"] = name
            return
    # Fall back: use ASCII-only labels in the chart to avoid tofu squares
    plt.rcParams["font.family"] = "sans-serif"

_setup_cjk_font()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.searcher import assess_risk, RiskAssessment
from src.agent import generate_analysis

# ── Pre-built example diaries ──────────────────────────────────────────────
EXAMPLES = [
    [
        "今天状态很好，精力充沛，血糖控制不错，没有口渴或者疲劳感，按时吃药，睡眠也很规律。",
        "🟢 低风险示例",
    ],
    [
        "最近几天总是感觉有些口干，下午疲劳感比较明显，晚上睡眠质量也差了一些，起来上了一次厕所。",
        "🟡 中风险示例",
    ],
    [
        "今天头晕明显，口渴严重一直在喝水，眼睛有些模糊，晚上起来了好几次上厕所，双脚有点麻木感，整个人非常虚弱。",
        "🔴 高风险示例",
    ],
]

# ── Risk gauge HTML ────────────────────────────────────────────────────────

def _risk_badge(level: str, score: float) -> str:
    colors = {"low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}
    labels = {"low": "低风险", "medium": "中等风险", "high": "高风险"}
    icons  = {"low": "✅", "medium": "⚠️", "high": "🚨"}
    color  = colors.get(level, "#6b7280")
    label  = labels.get(level, level)
    icon   = icons.get(level, "")
    bar_pct = int(score)
    return f"""
<div style="font-family:sans-serif; padding:16px; border-radius:12px;
            background:#f8fafc; border:1px solid #e2e8f0;">
  <div style="display:flex; align-items:center; gap:12px; margin-bottom:12px;">
    <span style="font-size:2rem;">{icon}</span>
    <div>
      <div style="font-size:1.3rem; font-weight:700; color:{color};">{label}</div>
      <div style="font-size:0.9rem; color:#64748b;">综合风险评分</div>
    </div>
    <div style="margin-left:auto; font-size:2.5rem; font-weight:800; color:{color};">
      {score:.0f}
    </div>
  </div>
  <div style="background:#e2e8f0; border-radius:99px; height:14px; overflow:hidden;">
    <div style="width:{bar_pct}%; height:100%; border-radius:99px;
                background:linear-gradient(90deg,{color}99,{color});
                transition:width 0.5s ease;">
    </div>
  </div>
  <div style="display:flex; justify-content:space-between;
              font-size:0.75rem; color:#94a3b8; margin-top:4px;">
    <span>0 — 安全</span><span>50 — 注意</span><span>100 — 危险</span>
  </div>
</div>
"""


# ── Comparison bar chart ───────────────────────────────────────────────────

def _comparison_chart(assessment: RiskAssessment):
    # Use ASCII labels to avoid font issues on systems without CJK fonts
    methods = ["Keyword only\n(BM25)", "Vector only\n(HNSW)", "Hybrid\n(SeekDB)"]
    ratios  = [
        assessment.keyword_only_pre_danger_ratio * 100,
        assessment.vector_only_pre_danger_ratio  * 100,
        assessment.hybrid_pre_danger_ratio       * 100,
    ]
    colors = ["#93c5fd", "#86efac", "#f97316"]

    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    bars = ax.bar(methods, ratios, color=colors, width=0.5, zorder=3)

    ax.set_ylim(0, 115)
    ax.set_ylabel("Pre-danger records hit (%)", fontsize=9)
    ax.set_title(
        "Search method comparison\n(higher = more pre-warning records retrieved)",
        fontsize=9,
    )
    ax.yaxis.grid(True, linestyle="--", alpha=0.5, zorder=0)
    ax.set_axisbelow(True)
    ax.spines[["top", "right"]].set_visible(False)

    for bar, val in zip(bars, ratios):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 1.5,
            f"{val:.0f}%",
            ha="center", va="bottom", fontsize=11, fontweight="bold",
        )

    fig.text(
        0.5, -0.02,
        "Hybrid search finds more pre-danger matches than either method alone",
        ha="center", fontsize=8, style="italic", color="#64748b",
    )
    plt.tight_layout()
    return fig


# ── Hit table ─────────────────────────────────────────────────────────────

def _hits_to_table(assessment: RiskAssessment) -> list[list]:
    rows = []
    for h in assessment.top_hits:
        status = "⚠️ 预警前期" if h.is_pre_danger else "✅ 稳定期"
        days_info = (
            f"距危险事件还有 {h.days_to_danger} 天" if h.days_to_danger >= 0 else "—"
        )
        rows.append([
            status,
            f"{h.glucose_level:.0f}",
            h.diary_text[:80] + ("…" if len(h.diary_text) > 80 else ""),
            f"{h.keyword_score:.3f}",
            f"{h.semantic_score:.3f}",
            days_info,
        ])
    return rows


# ── Main inference function ────────────────────────────────────────────────

def run_analysis(diary_text: str):
    if not diary_text.strip():
        return (
            "<p style='color:#6b7280'>请输入今天的健康日记后点击「分析风险」</p>",
            None,
            [],
            "",
        )

    try:
        assessment = assess_risk(diary_text, k=15)
    except Exception as e:
        err_html = (
            "<div style='padding:12px;background:#fef2f2;border-radius:8px;"
            "border:1px solid #fca5a5;color:#b91c1c'>"
            f"<b>数据库连接失败</b>：{e}<br><br>"
            "请确认 SeekDB 已启动：<code>docker-compose up -d</code><br>"
            "并已完成数据初始化：<code>python scripts/init_db.py</code>"
            "</div>"
        )
        return err_html, None, [], ""

    analysis   = generate_analysis(diary_text, assessment)
    badge_html = _risk_badge(assessment.risk_level, assessment.risk_score)
    chart      = _comparison_chart(assessment)
    table      = _hits_to_table(assessment)

    return badge_html, chart, table, analysis


# ── Gradio UI ──────────────────────────────────────────────────────────────

CSS = """
#title { text-align: center; }
#subtitle { text-align: center; color: #64748b; margin-bottom: 8px; }
.gr-button-primary { background: #3b82f6 !important; }
"""

with gr.Blocks(css=CSS, title="SeekDB 慢病早期预警") as demo:
    gr.HTML(
        "<h1 id='title'>🩺 慢病早期预警 Agent</h1>"
        "<p id='subtitle'>基于 SeekDB 混合搜索（向量 + 全文 + SQL）的智能健康风险评估</p>"
    )

    with gr.Row():
        # ── LEFT: Input ────────────────────────────────────────────────────
        with gr.Column(scale=1):
            gr.Markdown("### 📝 今天的健康日记")
            diary_input = gr.Textarea(
                placeholder="用自己的话描述今天的身体感受，例如：今天感觉有些口渴，头有点晕…",
                lines=6,
                label="",
                show_label=False,
            )

            with gr.Row():
                for text, label in EXAMPLES:
                    gr.Button(label, size="sm").click(
                        fn=lambda t=text: t,
                        outputs=diary_input,
                    )

            analyze_btn = gr.Button("🔍 分析风险", variant="primary", size="lg")

            gr.Markdown(
                """
---
**工作原理**

SeekDB 的 `DBMS_HYBRID_SEARCH.SEARCH` 在一次查询中同时执行：
1. **BM25 全文搜索**（IK 中文分词）→ 精确匹配症状关键词
2. **HNSW 向量搜索**（384 维 cosine）→ 语义相似度匹配
3. **SQL 过滤**（`is_pre_danger`、`days_to_danger`）→ 结构化标签筛选

三路结合，让「眼前发花」和「视力模糊」互相召回，让「起来喝水」和「多饮多尿」互相加分。
                """
            )

        # ── RIGHT: Output ──────────────────────────────────────────────────
        with gr.Column(scale=2):
            gr.Markdown("### 📊 风险评估结果")

            risk_badge = gr.HTML("<p style='color:#9ca3af'>等待分析…</p>")

            with gr.Row():
                chart_out = gr.Plot(label="三种检索方式对比")

            gr.Markdown("#### 🔎 最相似的历史案例（混合搜索 Top 10）")
            hits_table = gr.Dataframe(
                headers=["状态", "血糖(mg/dL)", "日记摘要", "关键词分", "语义分", "距危险事件"],
                datatype=["str", "str", "str", "str", "str", "str"],
                row_count=10,
                wrap=True,
            )

            gr.Markdown("#### 🤖 AI 健康助手分析")
            analysis_out = gr.Markdown()

    analyze_btn.click(
        fn=run_analysis,
        inputs=[diary_input],
        outputs=[risk_badge, chart_out, hits_table, analysis_out],
    )

    gr.Markdown(
        """
---
<small>
⚠️ **免责声明**：本演示仅供技术展示用途，所有数据均为合成数据，分析结果不构成任何医疗建议。
数据来源：合成数据，统计参数参考 ADA Standards of Medical Care in Diabetes 2024 等公开文献。
</small>
        """
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
