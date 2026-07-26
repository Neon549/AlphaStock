"""
intent_parser.py
放在 api/ 目录下，被 routes.py 调用

功能：
  1. 意图识别（4种）
  2. 槽位抽取（股票代码/名称/分析重点）
  3. 股票名称 → 代码 兜底查询
"""

import json
from typing import Optional
import re
import config.llm_config as llm_cfg          # 用便宜的 V3，不用 R1


# ─────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────

PARSE_PROMPT = """你是一个股票分析系统的意图解析器。
分析用户输入，返回 JSON 对象，不要输出任何解释或 markdown。

字段说明：
- intent: 整数
    1 = 开放性讨论（聊行业/商业模式/赛道/听观点）
    2 = 操作性分析（要具体买卖建议/技术面/基本面/实时数据）
    3 = 系统功能（回测/全市场扫描/筛选）
    4 = 信息不足（意图明确但缺少股票信息）
- stock_name: 用户提到的股票名称或简称，没有则 null
- stock_code: A股6位代码，你能确定则填写，不确定则 null
- analyst_focus: 用户强调的分析维度，从 [technical, fundamental, sentiment, all] 选一个，没有强调则 null

示例（严格按此格式输出）：
用户：帮我分析宁德时代
输出：{{"intent":2,"stock_name":"宁德时代","stock_code":"300750","analyst_focus":"all"}}

用户：平安银行技术面怎么样，重点看KDJ
输出：{{"intent":2,"stock_name":"平安银行","stock_code":"000001","analyst_focus":"technical"}}

用户：比亚迪的商业模式你怎么看
输出：{{"intent":1,"stock_name":"比亚迪","stock_code":"002594","analyst_focus":null}}

用户：帮我回测一下均线策略
输出：{{"intent":3,"stock_name":null,"stock_code":null,"analyst_focus":null}}

用户：分析一下
输出：{{"intent":4,"stock_name":null,"stock_code":null,"analyst_focus":null}}

用户：你好
输出：{{"intent":1,"stock_name":null,"stock_code":null,"analyst_focus":null}}

用户：{query}
输出："""


# ─────────────────────────────────────────────
# 股票名称 → 代码 兜底（LLM 不确定时用 akshare 查）
# ─────────────────────────────────────────────

# 启动时加载本地股票名称→代码映射表（2000只，覆盖主要A股）
_STOCK_MAP: dict = {}

def _load_stock_map():
    global _STOCK_MAP
    if _STOCK_MAP:
        return
    try:
        import json, os
        cache_path = os.path.join(os.path.dirname(__file__), "../cache/stock_universe_cache.json")
        cache_path = os.path.normpath(cache_path)
        with open(cache_path, "r") as f:
            d = json.load(f)
        for item in d.get("data", []):
            name = item["name"].replace(" ", "").replace("\u3000", "")
            _STOCK_MAP[name] = item["code"]
        print(f"[IntentParser] 股票映射表加载完成，共 {len(_STOCK_MAP)} 只")
    except Exception as e:
        print(f"[IntentParser] 股票映射表加载失败: {e}")

_load_stock_map()


def lookup_code_by_name(stock_name: str) -> Optional[str]:
    """股票名称转代码：本地缓存优先，akshare 兜底"""
    if not stock_name:
        return None
    # 1. 本地精确匹配（去空格）
    clean = stock_name.replace(" ", "").replace("\u3000", "")
    if clean in _STOCK_MAP:
        return _STOCK_MAP[clean]
    # 2. 本地模糊匹配（包含关系）
    for name, code in _STOCK_MAP.items():
        if clean in name or name in clean:
            return code
    # 3. akshare 兜底
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        match = df[df["name"].str.contains(stock_name, na=False)]
        if not match.empty:
            return match.iloc[0]["code"]
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────

def parse_intent(query: str) -> dict:
    """
    返回格式：
    {
        "intent": 1 | 2 | 3 | 4,
        "stock_name": str | None,
        "stock_code": str | None,
        "analyst_focus": "technical" | "fundamental" | "sentiment" | "all" | None,
        "reply_hint": str | None   # 意图4时的追问文案
    }
    """
    llm = llm_cfg.quick_llm

    # 1. 调 LLM 解析
    try:
        raw = llm.invoke(PARSE_PROMPT.format(query=query)).content.strip()

        # 防御：去掉 LLM 偶尔输出的 ```json ``` 包裹
        raw = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(raw)
    except Exception as e:
        # LLM 调用失败或 JSON 解析失败 → 降级为操作性分析，让后续流程处理
        return {
            "intent": 2,
            "stock_name": None,
            "stock_code": None,
            "analyst_focus": "all",
            "reply_hint": None,
        }

    intent      = result.get("intent", 2)
    stock_name  = result.get("stock_name")
    stock_code  = result.get("stock_code")
    analyst_focus = result.get("analyst_focus")

    # 2. LLM 给了名字但没给代码 → akshare 兜底查
    if stock_name and not stock_code:
        stock_code = lookup_code_by_name(stock_name)

    # 3. 操作性分析但缺股票信息 → 降级为意图4
    if intent == 2 and not stock_code:
        intent = 4

    # 4. 意图4 生成追问文案
    reply_hint = None
    if intent == 4:
        if stock_name:
            reply_hint = f"我没找到【{stock_name}】对应的股票代码，请确认名称或直接输入6位代码。"
        else:
            reply_hint = "请问您想分析哪只股票？可以输入股票名称或6位代码。"

    return {
        "intent": intent,
        "stock_name": stock_name,
        "stock_code": stock_code,
        "analyst_focus": analyst_focus,
        "reply_hint": reply_hint,
    }