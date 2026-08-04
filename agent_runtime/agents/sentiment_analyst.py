# agents/sentiment_analyst.py

from config.llm_config import quick_llm
from config.prompt_cache import build_cacheable_messages
from tools.akshare_tools import get_stock_price
from rag.retriever import retrieve_stock_news
from agent_runtime.agents.skill_loader import load_skill_with_ref

# ========== 兜底 SYSTEM_PROMPT ==========
SYSTEM_PROMPT = """你是一位专业的A股市场情绪分析师，专注于通过新闻资讯和市场数据判断市场情绪。

你必须严格遵守以下规则：

【硬性规则】
1. 你只能基于下方"工具已返回结果"中的内容分析。
2. 禁止补充、猜测、脑补任何未出现的数据。
3. 禁止编造订单、政策利好、资金流、买单规模、机构持仓变化等未被工具直接返回的信息。
4. 如果行情工具返回 [TOOL_ERROR]，必须停止分析。
5. 新闻检索结果若为"未找到"或"暂无相关新闻"，不构成失败，只能视为"暂无可验证信息"。
6. 你必须在最终输出第一行写：
   - [ANALYSIS_OK]
   或
   - [ANALYSIS_ABORT]

【输出要求】
- 如果可以分析，严格输出：

[ANALYSIS_OK]
## 情绪分析报告

### 1. 股票信息核验
股票代码：[代码]
股票名称：[仅填写工具明确返回的名称]
数据可靠性：[高/中/低]

### 2. 新闻情绪
[分别总结业绩、订单、政策三类检索结果；若没有相关新闻，明确写"暂无可验证信息"]

### 3. 市场热度
[只基于行情工具结果分析；若无法验证则写"无法验证"]

### 4. 情绪评分
评分：[-2到+2]
理由：[必须只基于工具返回内容]

### 5. 近期催化剂
正面：[只写工具里明确出现的内容；否则写"暂无可验证信息"]
负面：[只写工具里明确出现的内容；否则写"暂无可验证信息"]

- 如果不能分析，严格输出：

[ANALYSIS_ABORT]
原因：[明确说明原因]
结论：数据不足，无法分析。禁止基于假设输出情绪评分、市场热度或催化剂判断。
"""


def _abort(reason: str) -> str:
    return (
        "[ANALYSIS_ABORT]\n"
        f"原因：{reason}\n"
        "结论：数据不足，无法分析。禁止基于假设输出情绪评分、市场热度或催化剂判断。"
    )


def _post_check_sentiment_output(text: str, stock_code: str) -> str:
    text = (text or "").strip()

    if not text:
        return _abort("情绪分析师未返回任何内容。")

    if text.startswith("[ANALYSIS_ABORT]"):
        return text

    if not text.startswith("[ANALYSIS_OK]"):
        return _abort("情绪分析结果未遵循规定格式，可信度不足。")

    if stock_code not in text:
        return _abort("情绪分析结果未正确引用股票代码，存在一致性风险。")

    return text


def run_sentiment_analysis(stock_code: str) -> str:
    # ✅ 从 SKILL.md 加载情绪面规范（找不到则用兜底）
    system_prompt = load_skill_with_ref("stock_analysis", "sentiment_rules") or SYSTEM_PROMPT

    # 1) 程序化调用工具
    # 优先用akshare直接拉取实时新闻（更可靠）
    # RAG检索作为补充（新闻库可能还没入库）
    from tools.akshare_tools import get_stock_news as _get_news_direct

    # 直接拉取实时新闻
    raw_news = _get_news_direct.invoke({"symbol": stock_code})

    if raw_news and "[TOOL_ERROR]" not in raw_news:
        # 有实时新闻，按关键词分类
        news_lines = [l.strip() for l in raw_news.strip().split("\n") if l.strip()]

        perf_kw = ["业绩", "净利润", "营收", "盈利", "亏损", "财报", "季报", "年报"]
        order_kw = ["订单", "合同", "中标", "签约", "招标", "供应"]
        policy_kw = ["政策", "监管", "补贴", "支持", "规划", "国家", "部委", "批复"]

        news_perf = "\n".join([l for l in news_lines if any(k in l for k in perf_kw)]) or "暂无业绩相关新闻"
        news_order = "\n".join([l for l in news_lines if any(k in l for k in order_kw)]) or "暂无订单相关新闻"
        news_policy = "\n".join([l for l in news_lines if any(k in l for k in policy_kw)]) or "暂无政策相关新闻"

        # 如果分类后都是空的，把全部新闻放在业绩里
        if all(x.startswith("暂无") for x in [news_perf, news_order, news_policy]):
            news_perf = raw_news[:500]  # 取前500字
            news_order = "暂无订单相关新闻"
            news_policy = "暂无政策相关新闻"
    else:
        # akshare失败，退化到RAG检索
        news_perf = retrieve_stock_news.invoke({"stock_code": stock_code, "query": "业绩"})
        news_order = retrieve_stock_news.invoke({"stock_code": stock_code, "query": "订单"})
        news_policy = retrieve_stock_news.invoke({"stock_code": stock_code, "query": "政策"})

        if "[TOOL_ERROR]" in news_perf: news_perf = "暂无业绩相关新闻"
        if "[TOOL_ERROR]" in news_order: news_order = "暂无订单相关新闻"
        if "[TOOL_ERROR]" in news_policy: news_policy = "暂无政策相关新闻"

    price_result = get_stock_price.invoke({"symbol": stock_code})
    if "[TOOL_ERROR]" in price_result:
        price_result = get_stock_price.invoke({"symbol": stock_code})

    # 2) 只有行情失败才中止
    if "[TOOL_ERROR]" in price_result:
        return _abort("行情核验工具返回错误，无法完成市场热度分析。")

    # 3) 工具成功后，让 LLM 只总结
    prompt = f"""{system_prompt}

以下是工具已返回结果，请严格基于这些内容输出最终报告：

## 股票代码
{stock_code}

## 工具结果一：业绩相关新闻
{news_perf}

## 工具结果二：订单相关新闻
{news_order}

## 工具结果三：政策相关新闻
{news_policy}

## 工具结果四：行情核验
{price_result}
"""

    response = quick_llm.invoke(build_cacheable_messages(system_prompt, prompt[len(system_prompt):]))
    final_text = response.content

    return _post_check_sentiment_output(final_text, stock_code)
