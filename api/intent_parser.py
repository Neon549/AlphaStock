"""
intent_parser.py
放在 api/ 目录下，被 routes.py 调用

功能：
  1. 规则层：毫秒级拦截明确意图（问候/系统指令/6位股票代码）
  2. 意图识别（4种）
  3. 槽位抽取（股票代码/名称/分析重点）
  4. 股票名称 → 代码 兜底查询
"""

import json
from typing import Optional
import re
import config.llm_config as llm_cfg          # 用便宜的 V3，不用 R1

# ─────────────────────────────────────────────
# 规则层常量
# ─────────────────────────────────────────────

_GREETINGS = {"你好", "hi", "hello", "嗨", "哈喽", "hey", "谢谢", "感谢", "再见", "拜拜", "👋"}

_SYSTEM_KEYWORDS = ["回测", "全市场扫描", "股票筛选", "扫描全市场", "买入信号", "信号扫描"]

_TECHNICAL_KW = ["kdj", "macd", "均线", "技术面", "k线", "趋势", "ma20", "布林", "rsi", "成交量"]
_FUNDAMENTAL_KW = ["净利润", "pe", "pb", "roe", "基本面", "财务", "营收", "市盈率", "市净率"]
_SENTIMENT_KW = ["新闻", "情绪", "舆情", "情感", "消息", "利好", "利空"]


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
_STOCK_MAP: dict = {}   # name → code
_CODE_MAP: dict = {}    # code → name

def _load_stock_map():
    global _STOCK_MAP, _CODE_MAP
    if _STOCK_MAP:
        return
    try:
        import json, os
        cache_path = os.path.join(os.path.dirname(__file__), "../cache/stock_universe_cache.json")
        cache_path = os.path.normpath(cache_path)
        with open(cache_path, "r") as f:
            d = json.load(f)
        for item in d.get("data", []):
            name = item["name"].replace(" ", "").replace("　", "")
            _STOCK_MAP[name] = item["code"]
            _CODE_MAP[item["code"]] = name
        print(f"[IntentParser] 股票映射表加载完成，共 {len(_STOCK_MAP)} 只")
    except Exception as e:
        print(f"[IntentParser] 股票映射表加载失败: {e}")

_load_stock_map()


def lookup_code_by_name(stock_name: str) -> Optional[str]:
    """股票名称转代码：本地缓存优先，akshare 兜底"""
    if not stock_name:
        return None
    # 1. 本地精确匹配（去空格）
    clean = stock_name.replace(" ", "").replace("　", "")
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
# 规则层
# ─────────────────────────────────────────────

def _detect_focus(query: str) -> Optional[str]:
    """从文本中提取分析维度关键词"""
    q = query.lower()
    if any(kw in q for kw in _TECHNICAL_KW):
        return "technical"
    if any(kw in q for kw in _FUNDAMENTAL_KW):
        return "fundamental"
    if any(kw in q for kw in _SENTIMENT_KW):
        return "sentiment"
    return None


def _rule_layer(query: str) -> Optional[dict]:
    """
    规则层：毫秒级拦截明确意图，搞不定返回 None 交给 LLM。

    拦截顺序：
      1. 过短输入 → intent 4
      2. 明确问候/闲聊 → intent 1
      3. 系统功能关键词 → intent 3
      4. 包含6位A股代码 → intent 2（直接跳过 LLM）
    """
    q = query.strip()

    # 1. 过短
    if len(q) < 2:
        return {"intent": 4, "stock_name": None, "stock_code": None,
                "analyst_focus": None,
                "reply_hint": "请问您想分析哪只股票？可以输入股票名称或6位代码。"}

    # 2. 问候/闲聊
    if q.lower() in _GREETINGS:
        return {"intent": 1, "stock_name": None, "stock_code": None,
                "analyst_focus": None, "reply_hint": None}

    # 3. 系统功能
    if any(kw in q for kw in _SYSTEM_KEYWORDS):
        return {"intent": 3, "stock_name": None, "stock_code": None,
                "analyst_focus": None, "reply_hint": None}

    # 4. 包含6位A股代码（主板0/6开头，创业板300，科创板688）
    match = re.search(r'\b([036]\d{5}|688\d{3})\b', q)
    if match:
        code = match.group(1)
        name = _CODE_MAP.get(code)
        focus = _detect_focus(q)
        return {"intent": 2, "stock_name": name, "stock_code": code,
                "analyst_focus": focus or "all", "reply_hint": None}

    return None  # 规则搞不定，交给 LLM


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
    # 0. 规则层先过——能拦截的不调 LLM
    rule_result = _rule_layer(query)
    if rule_result is not None:
        return rule_result

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

    intent        = result.get("intent", 2)
    stock_name    = result.get("stock_name")
    stock_code    = result.get("stock_code")
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
