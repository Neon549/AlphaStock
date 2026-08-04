# agents/fundamental_analyst.py

from config.llm_config import deep_llm
from config.prompt_cache import build_cacheable_messages
from tools.akshare_tools import get_stock_price, get_financial_indicator
from agent_runtime.agents.skill_loader import load_skill_with_ref

SYSTEM_PROMPT = """你是一位专业的A股基本面分析师，专注于通过财务数据评估股票的内在价值。

你必须严格遵守以下规则：

【硬性规则】
1. 你只能基于下方"工具已返回结果"中的内容分析。
2. 禁止补充、猜测、脑补任何未出现的数据。
3. 财务指标数据若标注"暂时无法获取"，说明接口暂时不可用，此时：
   - 只基于行情数据（市值/价格/涨跌）做有限分析
   - 在报告中明确标注"财务数据暂缺，以下为有限分析"
   - 不得因财务数据缺失而ABORT（行情数据仍可做基础评估）
4. 只有行情工具也返回 [TOOL_ERROR] 时，才能ABORT。
5. 你必须在最终输出第一行写：
   - [ANALYSIS_OK]
   或
   - [ANALYSIS_ABORT]

【输出要求】
- 如果可以分析（哪怕只有行情数据），严格输出：

[ANALYSIS_OK]
## 基本面分析报告

### 1. 股票信息核验
股票代码：[代码]
股票名称：[填写工具返回的名称；若为"名称未验证"则填写股票代码]
数据可靠性：[高/中/低]
核验结论：[简述，若财务数据缺失需注明]

### 2. 估值分析
[基于已有数据分析；若PE/PB缺失，写"财务数据暂缺，无法评估估值"]

### 3. 盈利能力
[基于已有数据分析；若ROE缺失，写"财务数据暂缺，无法评估盈利能力"]

### 4. 财务健康
[基于已有数据分析；若缺失写"未提供"]

### 5. 综合评级
评级：[强烈买入/买入/中性/卖出/强烈卖出/数据不足暂无法评级]
理由：[基于已验证数据，数据不足时说明]
风险提示：[主要风险]

- 只有行情工具也失败时才输出：

[ANALYSIS_ABORT]
原因：[明确说明原因]
结论：数据不足，无法分析。
"""


def _abort(reason: str) -> str:
    return (
        "[ANALYSIS_ABORT]\n"
        f"原因：{reason}\n"
        "结论：数据不足，无法分析。禁止基于假设给出评级或投资建议。"
    )


def _post_check_fundamental_output(text: str, stock_code: str) -> str:
    text = (text or "").strip()
    if not text:
        return _abort("基本面分析师未返回任何内容。")
    if text.startswith("[ANALYSIS_ABORT]"):
        return text
    if not text.startswith("[ANALYSIS_OK]"):
        return _abort("基本面分析结果未遵循规定格式，可信度不足。")
    return text


def run_fundamental_analysis(stock_code: str, document_evidence: str = "") -> str:
    system_prompt = load_skill_with_ref("stock_analysis", "fundamental_rules") or SYSTEM_PROMPT

    # 1. 调用财务工具
    financial_result = get_financial_indicator.invoke({"symbol": stock_code})
    price_result = get_stock_price.invoke({"symbol": stock_code})

    # 2. 财务数据失败 → 降级处理，不ABORT
    if "[TOOL_ERROR]" in financial_result:
        financial_result = "【财务数据暂时无法获取】接口暂时不可用，请基于行情数据做有限分析，并在报告中注明数据缺失。"

    # 3. 行情数据失败 → 才ABORT（连基础数据都没有）
    if "[TOOL_ERROR]" in price_result:
        return _abort("行情核验工具返回错误，连基础行情数据都无法获取，无法完成任何分析。")

    prompt = f"""{system_prompt}

以下是已验证的工具结果和可追溯文档证据，请严格基于这些内容输出最终报告：

## 股票代码
{stock_code}

## 工具结果一：财务指标
{financial_result}

## 工具结果二：行情核验
{price_result}

## 用户上传文档证据
{document_evidence or "未提供上传文档证据。"}

若使用上传文档中的事实、数字或判断，必须在对应句子末尾保留原始
`[evidence_id=...]` 标记；若文档中的公司或报告期与当前股票不一致，必须说明不一致，
不得将其当作当前股票事实。
"""

    response = deep_llm.invoke(build_cacheable_messages(system_prompt, prompt[len(system_prompt):]))
    final_text = response.content
    return _post_check_fundamental_output(final_text, stock_code)
