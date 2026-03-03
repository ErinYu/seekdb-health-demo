"""
LLM-based clinical analysis agent.

Calls Claude to generate a natural-language risk explanation
based on SeekDB hybrid search results.

If ANTHROPIC_API_KEY is not set, a rule-based fallback explanation is returned
so the demo still works without an API key.
"""

import os
from .searcher import RiskAssessment

try:
    import anthropic
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


_SYSTEM_PROMPT = """你是一位专业的慢性病健康助手，帮助患者理解其健康日记的风险信号。
你的任务是根据系统提供的数据库检索结果，用通俗易懂的中文向患者解释：
1. 他们当前的描述与哪些历史预警案例相似
2. 这些相似性意味着什么健康风险
3. 建议采取什么行动（就医/自我监测/继续观察）

注意：
- 使用温和、鼓励的语气，不要制造恐慌
- 基于给定数据分析，不要凭空捏造症状
- 强调这只是辅助参考，最终诊断需要专业医生
- 回答控制在200字以内"""


def _build_context(query: str, assessment: RiskAssessment) -> str:
    lines = [
        f"患者日记内容：{query}",
        f"\n检索结果摘要：",
        f"  - 混合搜索召回 {assessment.total_hits} 条历史记录",
        f"  - 其中 {assessment.pre_danger_hits} 条来自历史「危险事件前30天」",
        f"  - 加权风险评分：{assessment.risk_score}/100",
        f"\n最相似的历史记录片段（前3条）：",
    ]
    for i, hit in enumerate(assessment.top_hits[:3], 1):
        status = "⚠️ 危险前期" if hit.is_pre_danger else "✅ 正常期"
        lines.append(
            f"  {i}. [{status}] 血糖约{hit.glucose_level:.0f} mg/dL | "
            f"「{hit.diary_text[:60]}…」"
        )
    return "\n".join(lines)


def _rule_based_explanation(assessment: RiskAssessment) -> str:
    """Fallback when no Anthropic API key is configured."""
    score = assessment.risk_score
    n_pre = assessment.pre_danger_hits
    n_total = assessment.total_hits

    if score < 30:
        return (
            f"✅ **风险评估：低风险**\n\n"
            f"您的描述与数据库中 {n_total} 条历史记录进行了对比，"
            f"仅 {n_pre} 条来自危险事件前期，相似度权重较低。\n\n"
            "当前状态与健康期表现接近，建议继续保持规律的血糖监测和良好的生活习惯。"
        )
    elif score < 60:
        return (
            f"⚠️ **风险评估：中等风险**\n\n"
            f"您的描述与数据库中 {n_pre}/{n_total} 条危险事件前期记录存在相似特征。"
            "这些历史案例在类似症状出现后30天内发生了血糖危机事件。\n\n"
            "建议：\n"
            "- 增加血糖自测频率（至少每天2次）\n"
            "- 关注多饮、多尿、视力模糊等症状变化\n"
            "- 在1-2周内预约门诊检查"
        )
    else:
        return (
            f"🚨 **风险评估：高风险**\n\n"
            f"您的描述与 {n_pre}/{n_total} 条历史危险前期记录高度吻合，"
            "这些历史案例出现类似症状后均在短期内发生了血糖危机。\n\n"
            "强烈建议：\n"
            "- **立即**测量血糖，若超过 16.7 mmol/L（300 mg/dL）请就近急诊\n"
            "- 今日内联系主治医师或前往门诊\n"
            "- 暂停剧烈运动，保持充足水分摄入\n\n"
            "*以上为AI辅助分析，不构成医疗诊断，请以专业医生判断为准。*"
        )


def generate_analysis(query: str, assessment: RiskAssessment) -> str:
    """
    Generate a natural-language risk explanation.
    Uses Claude (via Zenmux or direct Anthropic API); otherwise falls back to rule-based.
    """
    zenmux_key = os.getenv("ZENMUX_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")

    if _HAS_ANTHROPIC and zenmux_key:
        client = anthropic.Anthropic(
            api_key=zenmux_key,
            base_url="https://zenmux.ai/api/anthropic"
        )
        context = _build_context(query, assessment)
        message = client.messages.create(
            model="anthropic/claude-opus-4.6",
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        return message.content[0].text
    elif _HAS_ANTHROPIC and anthropic_key and anthropic_key.startswith("sk-"):
        client = anthropic.Anthropic(api_key=anthropic_key)
        context = _build_context(query, assessment)
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": context}],
        )
        return message.content[0].text
    else:
        return _rule_based_explanation(assessment)
