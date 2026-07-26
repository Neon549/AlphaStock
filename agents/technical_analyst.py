# agents/technical_analyst.py

import os
from langchain_core.messages import HumanMessage
from config.llm_config import quick_llm
from tools.akshare_tools import get_stock_price, get_stock_history
from agents.skill_loader import load_skill_with_ref

# ========== 兜底 SYSTEM_PROMPT（SKILL.md 找不到时使用）==========
SYSTEM_PROMPT = """你是一位专业的A股技术面分析师，专注于通过价格和成交量数据判断股票的短中期走势。

你必须严格遵守以下规则：

【硬性规则】
1. 你只能基于下方"工具已返回结果"中的真实K线/价格数据分析。
2. 禁止补充、猜测、脑补任何未出现的数据。
3. 禁止编造支撑位、压力位、目标价、止损位。
4. 若工具结果中存在 [TOOL_ERROR]，必须停止分析。
5. 你必须在最终输出第一行写：
   - [ANALYSIS_OK]
   或
   - [ANALYSIS_ABORT]

【输出要求】
- 如果可以分析，严格输出：

[ANALYSIS_OK]
## 技术面分析报告

### 1. 股票信息核验
股票代码：[代码]
股票名称：[仅填写工具明确返回的名称]
数据可靠性：[高/中/低]

### 2. 趋势分析
[只基于工具返回的历史价格数据分析]

### 3. 量价关系
[只基于工具返回的成交量与价格关系分析]

### 4. 关键价位
支撑位：[如果无法可靠判断，写"暂不设定"]
压力位：[如果无法可靠判断，写"暂不设定"]

### 5. 短期展望
方向：[看多/看空/中性]
目标价：[若无法可靠判断则写"暂不设定"]
止损位：[若无法可靠判断则写"暂不设定"]

### 6. KDJ策略信号
当前信号：[基于工具结果三，说明K/D/J当前值及是否满足买入条件]
操作建议：[满足全部条件时说明可以考虑买入；不满足时说明需要继续等待哪个条件]

- 如果不能分析，严格输出：

[ANALYSIS_ABORT]
原因：[明确说明原因]
结论：数据不足，无法分析。禁止基于假设生成支撑位、压力位、目标价和止损位。
"""


def _abort(reason: str) -> str:
    return (
        "[ANALYSIS_ABORT]\n"
        f"原因：{reason}\n"
        "结论：数据不足，无法分析。禁止基于假设生成支撑位、压力位、目标价和止损位。"
    )


def _calc_kdj_signal(stock_code: str) -> str:
    """
    计算当前KDJ值，判断是否满足买入条件。
    与量化回测策略保持一致：K<25 D<30 J<15 且价格在MA60上方且MA60向上。
    """
    try:
        from backtest.data_loader import get_stock_data_tushare, get_mock_data

        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            return "KDJ数据：未配置Tushare Token，无法计算实时KDJ信号。"

        df = get_stock_data_tushare(stock_code, "20240101", "20260530", token)

        if df is None or len(df) < 60:
            return "KDJ数据：K线数据不足60根，无法可靠计算KDJ和MA60。"

        # 计算KDJ（与backtrader Stochastic一致）
        low_min = df["low"].rolling(9).min()
        high_max = df["high"].rolling(9).max()
        rsv = (df["close"] - low_min) / (high_max - low_min + 1e-10) * 100
        df["K"] = rsv.ewm(com=2).mean()
        df["D"] = df["K"].ewm(com=2).mean()
        df["J"] = 3 * df["K"] - 2 * df["D"]

        # 计算MA20
        df["MA20"] = df["close"].rolling(20).mean()

        latest = df.iloc[-1]
        prev_ma = df.iloc[-6]["MA20"]  # 5天前

        k = round(float(latest["K"]), 2)
        d = round(float(latest["D"]), 2)
        j = round(float(latest["J"]), 2)
        close = round(float(latest["close"]), 2)
        ma20 = round(float(latest["MA20"]), 2)
        date = str(df.index[-1].date())

        # 判断各条件
        k_ok = k < 25
        d_ok = d < 30
        j_ok = j < 15
        above_ma20 = close > ma20
        ma20_up = float(latest["MA20"]) > float(prev_ma)

        all_ok = k_ok and j_ok and above_ma20 and ma20_up

        # 计算支撑位和压力位
        # 支撑位：近20日最低点 + MA20 取较低者
        recent_low  = round(float(df["low"].tail(20).min()), 2)
        recent_high = round(float(df["high"].tail(20).max()), 2)
        # 近5日低点作为短期支撑
        short_low   = round(float(df["low"].tail(5).min()), 2)
        short_high  = round(float(df["high"].tail(5).max()), 2)
        support     = min(recent_low, ma20) if not above_ma20 else recent_low
        resistance  = max(recent_high, ma20) if above_ma20 else recent_high

        lines = [
            f"数据日期：{date}",
            f"当前价格：¥{close}",
            f"MA20：¥{ma20}  {'📈 向上' if ma20_up else '📉 向下'}",
            f"支撑位：¥{support}（近20日低点¥{recent_low}，短期低点¥{short_low}）",
            f"压力位：¥{resistance}（近20日高点¥{recent_high}，短期高点¥{short_high}）",
            "",
            "KDJ超卖策略条件检测（需全部满足才触发买入）：",
            f"  K={k}  {'✅ 满足(K<25)' if k_ok else f'❌ 不满足(需<25，差{round(k-25,1)}点)'}",
            f"  D={d}  {'✅ 满足(D<30)' if d_ok else f'❌ 不满足(需<30，差{round(d-30,1)}点)'}",
            f"  J={j}  {'✅ 满足(J<15)' if j_ok else f'❌ 不满足(需<15，差{round(j-15,1)}点)'}",
            f"  价格在MA20上方  {'✅ 满足' if above_ma20 else '❌ 不满足'}",
            f"  MA20向上  {'✅ 满足' if ma20_up else '❌ 不满足'}",
            f"综合信号：{'🟢 当前满足KDJ超卖买入条件！可关注买入机会。' if all_ok else '⏳ 当前不满足买入条件，需继续等待KDJ回落至超卖区域。'}",
        ]

        return "\n".join(lines)

    except Exception as e:
        return f"KDJ信号计算失败: {e}"


def _post_check_technical_output(text: str, stock_code: str) -> str:
    text = (text or "").strip()

    if not text:
        return _abort("技术面分析师未返回任何内容。")

    if text.startswith("[ANALYSIS_ABORT]"):
        return text

    if not text.startswith("[ANALYSIS_OK]"):
        return _abort("技术面分析结果未遵循规定格式，可信度不足。")

    if stock_code not in text:
        return _abort("技术面分析结果未正确引用股票代码，存在一致性风险。")

    return text


def run_technical_analysis(stock_code: str, check_eligibility: bool = True) -> str:
    # ✅ 市值和策略条件检查（按需实时查，不做批量扫描）
    if check_eligibility:
        try:
            from backtest.stock_universe import check_stock_eligibility
            eligible, reason = check_stock_eligibility(stock_code, min_cap_billion=300)
            if not eligible:
                return (
                    "[ANALYSIS_ABORT]\n"
                    f"原因：{reason}\n"
                    "结论：该股票不满足策略条件（市值<300亿或ST股），停止分析。"
                )
            print(f"[技术面] 市值校验通过：{reason}")
        except Exception as e:
            print(f"[技术面] 市值校验跳过：{e}")

    # ✅ 从 SKILL.md 加载技术面规范（找不到则用兜底 SYSTEM_PROMPT）
    system_prompt = load_skill_with_ref("stock_analysis", "technical_rules") or SYSTEM_PROMPT

    # 1) 调用行情工具
    history_result = get_stock_history.invoke({"symbol": stock_code, "days": 30})
    if "[TOOL_ERROR]" in history_result:
        history_result = get_stock_history.invoke({"symbol": stock_code, "days": 30})

    price_result = get_stock_price.invoke({"symbol": stock_code})
    if "[TOOL_ERROR]" in price_result:
        price_result = get_stock_price.invoke({"symbol": stock_code})

    # 2) 工具失败则中止
    if "[TOOL_ERROR]" in history_result:
        return _abort("历史K线工具返回错误，无法完成技术面分析。")

    if "[TOOL_ERROR]" in price_result:
        return _abort("行情核验工具返回错误，无法完成股票信息交叉核验。")

    # 3) 计算KDJ实时信号
    kdj_signal = _calc_kdj_signal(stock_code)

    # 4) 让LLM综合分析
    prompt = f"""{system_prompt}

以下是工具已返回结果，请严格基于这些内容输出最终报告：

## 股票代码
{stock_code}

## 工具结果一：历史K线（近30日）
{history_result}

## 工具结果二：行情核验
{price_result}

## 工具结果三：KDJ量化信号（基于回测验证的买入策略，请在第6节如实呈现）
{kdj_signal}
"""

    # 优先用TechLens本地模型（低延迟、低成本）
    from config.llm_config import techlens_client
    if techlens_client.is_available():
        try:
            resp = techlens_client.analyze(stock_code, history_result, price_result, kdj_signal)
            if resp.get("success") and resp.get("result"):
                r = resp["result"]
                if r.get("status") == "ABORT":
                    return _abort(r.get("reason", "TechLens中止分析"))
                sig = "KDJ满足超卖买入条件" if r["kdj"]["signal"] == "buy_ready" else "KDJ暂不满足买入条件"
                trend_zh = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}.get(r["trend"], r["trend"])
                final_text = f"""[ANALYSIS_OK]
## 技术面分析报告

### 1. 股票信息核验
股票代码：{r['stock_code']}
数据可靠性：{r['confidence']}

### 2. 趋势分析
趋势方向：{r['trend']}

### 3. 量价关系
{r['volume_price']}

### 4. 关键价位
支撑位：{r['support']}
压力位：{r['resistance']}

### 5. 短期展望
方向：{trend_zh}

### 6. KDJ策略信号
当前信号：K={r['kdj']['K']} D={r['kdj']['D']} J={r['kdj']['J']}
操作建议：{sig}

### 7. 综合结论
{r['summary']}
"""
                return _post_check_technical_output(final_text, stock_code)
        except Exception as e:
            print(f"TechLens调用失败，降级到DeepSeek: {e}")

    # 降级：调用DeepSeek云端API
    response = quick_llm.invoke([HumanMessage(content=prompt)])
    final_text = response.content

    return _post_check_technical_output(final_text, stock_code)