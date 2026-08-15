"""Hybrid intent classification and safe stock-slot extraction.

The parser deliberately separates three concerns:

* deterministic rules handle unambiguous requests and high-risk routes;
* fastText may route only confident, locally resolvable requests;
* the LLM is a fallback extractor, never the authority for a stock code.

Every result includes its decision source and slot provenance.  The runtime may
therefore record coverage, fallback rate, and slot conflicts without changing
the existing ``intent / stock_code / analyst_focus`` contract.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

import config.llm_config as llm_cfg
from agent_runtime.planning.constrained_decomposition import constrained_decompose

logger = logging.getLogger(__name__)

_GREETINGS = {"你好", "hi", "hello", "嗨", "哈喽", "hey", "谢谢", "感谢", "再见", "拜拜", "👋"}
_SYSTEM_KEYWORDS = ["回测", "全市场扫描", "股票筛选", "扫描全市场", "买入信号", "信号扫描"]
_DISCUSSION_KEYWORDS = ["商业模式", "护城河", "竞争格局", "行业地位", "赛道", "怎么看", "怎么看待"]
_ANALYSIS_REQUEST_KEYWORDS = [
    "分析", "评估", "研究", "解读", "走势", "技术面", "基本面", "财务", "行情", "风险", "看看",
]
_BACKTEST_KEYWORDS = ["回测", "策略验证"]
_SCAN_KEYWORDS = ["全市场扫描", "扫描", "买入信号", "信号扫描"]
_SCREEN_KEYWORDS = ["股票筛选", "选股", "筛选"]
_DIRECT_TRADE_KEYWORDS = ["下单", "执行交易", "帮我买入", "帮我卖出", "直接买入", "直接卖出"]
_SEQUENCE_KEYWORDS = ["先", "然后", "再", "接着", "之后", "随后", "基于上述", "根据分析"]

_TECHNICAL_KW = [
    "kdj", "macd", "均线", "技术面", "k线", "趋势", "ma20", "布林", "rsi", "成交量", "technical",
]
_FUNDAMENTAL_KW = [
    "净利润", "pe", "pb", "roe", "基本面", "财务", "营收", "市盈率", "市净率", "fundamental",
]
_SENTIMENT_KW = ["新闻", "情绪", "舆情", "情感", "消息", "利好", "利空", "sentiment"]

_VALID_INTENTS = {1, 2, 3, 4}
_VALID_FOCUSES = {"technical", "fundamental", "sentiment", "all"}
_STOCK_CODE_PATTERN = re.compile(r"(?<!\d)([036]\d{5}|688\d{3})(?!\d)")
_BACKTEST_WINDOW_PATTERN = re.compile(
    r"(?:近\s*[0-9一二三四五六七八九十]+\s*(?:日|天|周|月|年)|过去\s*[0-9一二三四五六七八九十]+\s*(?:日|天|周|月|年)|"
    r"\d{4}[./-]\d{1,2}[./-]\d{1,2}\s*(?:至|到|[-~])\s*\d{4}[./-]\d{1,2}[./-]\d{1,2})"
)
_AMBIGUOUS_STOCK_ALIASES = {
    "半导", "银行", "股份", "集团", "科技", "能源", "智能", "电气", "医药", "证券", "电子",
}
_RULE_TYPO_REPLACEMENTS = {
    "分折": "分析",
    "回册": "回测",
    "筛選": "筛选",
}

_FASTTEXT_MODEL_PATH = Path(__file__).parent.parent / "models" / "intent_classifier.bin"
_FASTTEXT_THRESHOLD = float(os.getenv("INTENT_FASTTEXT_THRESHOLD", "0.85"))
_FASTTEXT_LABELS = {
    "__label__discussion": 1,
    "__label__analysis": 2,
    "__label__system": 3,
    "__label__insufficient": 4,
}
_fasttext_model = None
_fasttext_load_attempted = False

PARSE_PROMPT = """你是股票分析系统的意图解析器。只返回 JSON 对象，不要解释或 Markdown。

字段说明：
- intent: 1=开放性讨论（行业、商业模式、观点）；2=个股操作/技术/基本面分析；3=系统功能（回测、扫描、筛选）；4=信息不足
- stock_name: 用户明确提到的股票名称或简称；没有则 null
- stock_code: 仅在能确认时填写六位 A 股代码，不能确认则 null
- analyst_focus: 只能是 technical、fundamental、sentiment、all 或 null

示例：
用户：宁德时代的商业模式如何
输出：{{"intent":1,"stock_name":"宁德时代","stock_code":"300750","analyst_focus":null}}
用户：分析平安银行 KDJ 和均线
输出：{{"intent":2,"stock_name":"平安银行","stock_code":"000001","analyst_focus":"technical"}}
用户：帮我回测均线策略
输出：{{"intent":3,"stock_name":null,"stock_code":null,"analyst_focus":null}}
用户：分析一下
输出：{{"intent":4,"stock_name":null,"stock_code":null,"analyst_focus":null}}

用户：{query}
输出："""


# The cache is preferred because it is the current tradable universe.  The
# static backtest universe is a no-network fallback so name slots continue to
# work on a fresh checkout or after a cache expiry.
_STOCK_MAP: dict[str, str] = {}  # normalized name -> code
_CODE_MAP: dict[str, str] = {}   # code -> canonical name
_NAME_SOURCE: dict[str, str] = {}
_CODE_SOURCE: dict[str, str] = {}
_stock_maps_loaded = False


def _clean_stock_name(value: object) -> str:
    return str(value or "").replace(" ", "").replace("　", "").strip()


def _register_stock(code: object, name: object, source: str) -> None:
    normalized_code = str(code or "").strip()
    normalized_name = _clean_stock_name(name)
    if not _STOCK_CODE_PATTERN.fullmatch(normalized_code) or not normalized_name:
        return
    # Runtime cache has priority over the static fallback.  Do not replace a
    # known canonical mapping with an overlapping sector entry.
    if normalized_name not in _STOCK_MAP:
        _STOCK_MAP[normalized_name] = normalized_code
        _NAME_SOURCE[normalized_name] = source
    if normalized_code not in _CODE_MAP:
        _CODE_MAP[normalized_code] = normalized_name
        _CODE_SOURCE[normalized_code] = source


def _load_stock_map() -> None:
    global _stock_maps_loaded
    if _stock_maps_loaded:
        return
    _stock_maps_loaded = True

    try:
        from config.runtime_paths import STOCK_UNIVERSE_CACHE_FILE

        with open(STOCK_UNIVERSE_CACHE_FILE, "r", encoding="utf-8") as file:
            payload = json.load(file)
        for item in payload.get("data", []):
            _register_stock(item.get("code"), item.get("name"), "runtime_cache")
    except FileNotFoundError:
        logger.info("Stock universe runtime cache is absent; using static fallback.")
    except Exception as exc:  # A broken optional cache must not break routing.
        logger.warning("Unable to load runtime stock universe cache: %s", exc)

    try:
        from backtest.stock_universe import ALL_STOCKS

        for code, info in ALL_STOCKS.items():
            _register_stock(code, info.get("name"), "static_universe")
    except Exception as exc:  # Keep LLM/akshare fallback available if this optional module fails.
        logger.warning("Unable to load static stock universe: %s", exc)


def _lookup_local_name(stock_name: object) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Return canonical ``(name, code, source)`` without network access."""
    _load_stock_map()
    clean = _clean_stock_name(stock_name)
    if not clean:
        return None, None, None
    if clean in _STOCK_MAP:
        code = _STOCK_MAP[clean]
        return _CODE_MAP.get(code, clean), code, _NAME_SOURCE.get(clean, "local_map")

    # Exact substring matching is deliberately constrained to two characters
    # or more.  It helps common abbreviations ("茅台") without turning a single
    # Chinese character into a random ticker.
    candidates = [
        name for name in _STOCK_MAP
        if len(clean) >= 2 and (clean in name or name in clean)
    ]
    if len(candidates) == 1:
        name = candidates[0]
        code = _STOCK_MAP[name]
        return _CODE_MAP.get(code, name), code, _NAME_SOURCE.get(name, "local_map")
    return None, None, None


def _lookup_code_by_name_with_source(stock_name: object) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve a name locally first; query AkShare only as a last fallback."""
    canonical_name, code, source = _lookup_local_name(stock_name)
    if code:
        return canonical_name, code, source

    clean = _clean_stock_name(stock_name)
    if not clean:
        return None, None, None
    try:
        import akshare as ak

        frame = ak.stock_info_a_code_name()
        matches = frame[frame["name"].astype(str).str.replace(" ", "", regex=False) == clean]
        if len(matches) == 1:
            matched = matches.iloc[0]
            code = str(matched["code"])
            if _STOCK_CODE_PATTERN.fullmatch(code):
                name = _clean_stock_name(matched["name"])
                _register_stock(code, name, "akshare")
                return name, code, "akshare"
    except Exception as exc:
        logger.debug("AkShare stock lookup failed for %s: %s", clean, exc)
    return None, None, None


def lookup_code_by_name(stock_name: str) -> Optional[str]:
    """Backward-compatible code-only wrapper used by callers outside this module."""
    _, code, _ = _lookup_code_by_name_with_source(stock_name)
    return code


def _extract_stock_code(value: object) -> Optional[str]:
    if value is None:
        return None
    match = _STOCK_CODE_PATTERN.search(str(value).strip())
    return match.group(1) if match else None


def _resolve_stock_slots(
    query: str,
    candidate_name: object = None,
    candidate_code: object = None,
) -> tuple[Optional[str], Optional[str], dict[str, str], list[str]]:
    """Resolve ticker slots, with explicit user input always taking precedence.

    A code generated only by an LLM is accepted only when it is also present in
    a local universe.  This prevents an unverified LLM hallucination from
    becoming a tool argument.  A user-supplied six-digit code remains usable
    even if the optional local universe is incomplete.
    """
    _load_stock_map()
    warnings: list[str] = []
    sources: dict[str, str] = {}
    explicit_code = _extract_stock_code(query)
    extracted_name, extracted_code, extracted_source = _lookup_local_name(candidate_name)
    llm_code = _extract_stock_code(candidate_code)

    if explicit_code:
        code = explicit_code
        name = _CODE_MAP.get(code) or extracted_name
        sources["stock_code"] = "explicit_query"
        if name:
            sources["stock_name"] = _CODE_SOURCE.get(code, extracted_source or "local_map")
        if llm_code and llm_code != code:
            warnings.append("llm_stock_code_conflicts_with_explicit_query")
        elif extracted_code and extracted_code != code:
            warnings.append("stock_name_conflicts_with_explicit_query")
        return name, code, sources, warnings

    if extracted_code:
        sources["stock_name"] = extracted_source or "local_map"
        sources["stock_code"] = extracted_source or "local_map"
        if llm_code and llm_code != extracted_code:
            warnings.append("llm_stock_code_conflicts_with_name_resolution")
        return extracted_name, extracted_code, sources, warnings

    if llm_code and llm_code in _CODE_MAP:
        sources["stock_code"] = "llm_code_validated_local"
        sources["stock_name"] = _CODE_SOURCE.get(llm_code, "local_map")
        return _CODE_MAP[llm_code], llm_code, sources, warnings
    if llm_code:
        warnings.append("unverified_llm_stock_code_rejected")

    # Only unresolved names may use the network fallback.  It is intentionally
    # not used to validate arbitrary codes fabricated by the model.
    name, code, source = _lookup_code_by_name_with_source(candidate_name)
    if code:
        sources["stock_name"] = source or "akshare"
        sources["stock_code"] = source or "akshare"
    return name, code, sources, warnings


def _detect_focus(query: str) -> Optional[str]:
    """Map deterministic focus keywords; multi-focus requests become ``all``."""
    q = query.lower()
    matched = {
        "technical": any(keyword in q for keyword in _TECHNICAL_KW),
        "fundamental": any(keyword in q for keyword in _FUNDAMENTAL_KW),
        "sentiment": any(keyword in q for keyword in _SENTIMENT_KW),
    }
    selected = [focus for focus, hit in matched.items() if hit]
    if not selected:
        return None
    return selected[0] if len(selected) == 1 else "all"


def _normalise_focus(value: object) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    aliases = {
        "技术": "technical", "技术面": "technical",
        "基本面": "fundamental", "财务": "fundamental",
        "新闻": "sentiment", "情绪": "sentiment", "舆情": "sentiment",
        "综合": "all", "全面": "all", "综合分析": "all",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in _VALID_FOCUSES else None


def _reply_hint(stock_name: Optional[str] = None) -> str:
    if stock_name:
        return f"我没有确认到“{stock_name}”对应的股票代码，请核对名称或直接输入六位代码。"
    return "请告诉我需要分析的股票名称或六位代码，并说明关注技术面、基本面还是新闻情绪。"


def _result(
    *,
    intent: int,
    stock_name: Optional[str] = None,
    stock_code: Optional[str] = None,
    analyst_focus: Optional[str] = None,
    reply_hint: Optional[str] = None,
    source: str,
    confidence: Optional[float],
    slot_sources: Optional[dict[str, str]] = None,
    slot_warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "intent": intent,
        "stock_name": stock_name,
        "stock_code": stock_code,
        "analyst_focus": analyst_focus,
        "reply_hint": reply_hint,
        # Observability fields.  Existing callers may ignore them safely.
        "source": source,
        "confidence": confidence,
        "slot_sources": slot_sources or {},
        "slot_warnings": slot_warnings or [],
    }


def _load_fasttext_model():
    global _fasttext_model, _fasttext_load_attempted
    if _fasttext_load_attempted:
        return _fasttext_model
    _fasttext_load_attempted = True
    if not _FASTTEXT_MODEL_PATH.exists():
        logger.info("fastText intent model is unavailable; LLM fallback will be used.")
        return None
    try:
        import fasttext

        _fasttext_model = fasttext.load_model(str(_FASTTEXT_MODEL_PATH))
    except Exception as exc:
        logger.warning("Unable to load fastText intent model: %s", exc)
    return _fasttext_model


def _find_stock_matches_in_query(query: str) -> list[tuple[str, str, str]]:
    """Find distinct locally-verifiable stocks, ordered by their mention.

    A task contract currently contains one stock slot.  Returning all matches
    lets callers fail closed rather than silently binding a multi-stock
    request to whichever name happens to be longest in the local dictionary.
    The short aliases are accepted only when they uniquely identify a local
    canonical name (for example, ``茅台``), so a common two-character fragment
    cannot choose a ticker accidentally.
    """
    _load_stock_map()
    clean_query = _clean_stock_name(query)
    candidates: dict[str, tuple[int, str, str]] = {}

    def add(code: str, position: int, name: Optional[str], source: Optional[str]) -> None:
        if position < 0:
            return
        current = candidates.get(code)
        candidate = (position, name or _CODE_MAP.get(code, ""), source or "local_map")
        if current is None or candidate[0] < current[0]:
            candidates[code] = candidate

    for match in _STOCK_CODE_PATTERN.finditer(clean_query):
        code = match.group(1)
        add(code, match.start(), _CODE_MAP.get(code), "explicit_query")

    aliases: dict[str, set[str]] = {}
    for name, code in _STOCK_MAP.items():
        position = clean_query.find(name)
        add(code, position, _CODE_MAP.get(code, name), _NAME_SOURCE.get(name, "local_map"))
        for length in range(2, min(4, len(name)) + 1):
            alias = name[-length:]
            aliases.setdefault(alias, set()).add(code)
    for alias, codes in aliases.items():
        if len(codes) != 1 or alias in _AMBIGUOUS_STOCK_ALIASES:
            continue
        code = next(iter(codes))
        add(code, clean_query.find(alias), _CODE_MAP.get(code), _CODE_SOURCE.get(code, "local_map"))

    return [
        (name, code, source)
        for _position, name, source, code in sorted(
            (position, name, source, code) for code, (position, name, source) in candidates.items()
        )
    ]


def _find_stock_in_query(query: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    matches = _find_stock_matches_in_query(query)
    return matches[0] if matches else (None, None, None)


def _detect_backtest_window(query: str) -> Optional[str]:
    match = _BACKTEST_WINDOW_PATTERN.search(query)
    return match.group(0).strip() if match else None


def _has_direct_trade_request(query: str) -> bool:
    """Recognise explicit buy/sell wording without confusing scan signals with orders."""
    if any(keyword in query for keyword in _DIRECT_TRADE_KEYWORDS):
        return True
    return bool(
        _extract_stock_code(query)
        and any(keyword in query for keyword in ("买入", "卖出"))
        and "信号" not in query
    )


def _fasttext_layer(query: str) -> Optional[dict[str, Any]]:
    """Route only high-confidence fastText outcomes with validated slots."""
    model = _load_fasttext_model()
    if model is None:
        return None
    try:
        labels, probabilities = model.predict(query.replace("\n", " "), k=1)
        label, confidence = labels[0], float(probabilities[0])
        intent = _FASTTEXT_LABELS.get(label)
    except Exception as exc:
        logger.warning("fastText inference failed: %s", exc)
        return None
    if intent is None or confidence < _FASTTEXT_THRESHOLD:
        return None

    stock_name = stock_code = None
    slot_sources: dict[str, str] = {}
    if intent == 2:
        stock_name, stock_code, stock_source = _find_stock_in_query(query)
        if not stock_code:
            return None
        slot_sources = {"stock_name": stock_source or "local_map", "stock_code": stock_source or "local_map"}

    focus = _detect_focus(query)
    if focus:
        slot_sources["analyst_focus"] = "rule_keywords"
    return _result(
        intent=intent,
        stock_name=stock_name,
        stock_code=stock_code,
        analyst_focus=focus or ("all" if intent == 2 else None),
        reply_hint=_reply_hint() if intent == 4 else None,
        source="fasttext",
        confidence=confidence,
        slot_sources=slot_sources,
    )


def _rule_layer(query: str) -> Optional[dict[str, Any]]:
    q = query.strip()
    if len(q) < 2:
        return _result(intent=4, reply_hint=_reply_hint(), source="rule", confidence=1.0)

    if q.lower() in _GREETINGS:
        return _result(intent=1, source="rule", confidence=1.0)

    matched_stocks = _find_stock_matches_in_query(q)
    operational_request = any(keyword in q.lower() for keyword in (
        *_ANALYSIS_REQUEST_KEYWORDS,
        *_SYSTEM_KEYWORDS,
        *_DIRECT_TRADE_KEYWORDS,
    ))
    if len(matched_stocks) > 1 and operational_request:
        return _result(
            intent=4,
            reply_hint=_reply_hint(),
            source="rule",
            confidence=1.0,
            slot_warnings=["multiple_stock_references_require_clarification"],
        )

    # A transaction is never a normal research route.  Decomposition below
    # still preserves preceding research tasks, but execution must stop at the
    # explicit confirmation boundary.
    if _has_direct_trade_request(q):
        return _result(
            intent=4,
            reply_hint="检测到下单或交易请求。系统不会直接交易；请先确认研究结果和交易参数。",
            source="rule",
            confidence=1.0,
            slot_warnings=["direct_trade_action_requires_confirmation"],
        )

    if any(keyword in q for keyword in _SYSTEM_KEYWORDS):
        return _result(intent=3, source="rule", confidence=1.0)

    # This protects the documented hard negative "宁德时代的商业模式如何"
    # from a confidently wrong analysis label in the tiny startup classifier.
    if any(keyword in q for keyword in _DISCUSSION_KEYWORDS):
        name, code, sources, warnings = _resolve_stock_slots(q, *_find_stock_in_query(q)[:2])
        return _result(
            intent=1,
            stock_name=name,
            stock_code=code,
            source="rule",
            confidence=1.0,
            slot_sources=sources,
            slot_warnings=warnings,
        )

    explicit_code = _extract_stock_code(q)
    if explicit_code:
        name, code, sources, warnings = _resolve_stock_slots(q)
        detected_focus = _detect_focus(q)
        focus = detected_focus or "all"
        sources["analyst_focus"] = "rule_keywords" if detected_focus else "default"
        return _result(
            intent=2,
            stock_name=name,
            stock_code=code,
            analyst_focus=focus,
            source="rule",
            confidence=1.0,
            slot_sources=sources,
            slot_warnings=warnings,
        )

    # Do not make the tiny fastText model the only way to handle a local
    # company name or an unambiguous abbreviation.  This remains below the
    # discussion hard-negative rule, so a research-looking company mention is
    # never forced into an analysis route.
    if matched_stocks and any(keyword in q.lower() for keyword in _ANALYSIS_REQUEST_KEYWORDS):
        name, code, source = matched_stocks[0]
        focus = _detect_focus(q) or "all"
        return _result(
            intent=2,
            stock_name=name,
            stock_code=code,
            analyst_focus=focus,
            source="rule",
            confidence=1.0,
            slot_sources={
                "stock_name": source,
                "stock_code": source,
                "analyst_focus": "rule_keywords" if focus != "all" else "default",
            },
        )
    if _detect_focus(q) and any(keyword in q.lower() for keyword in _ANALYSIS_REQUEST_KEYWORDS):
        return _result(
            intent=4,
            reply_hint=_reply_hint(),
            source="rule",
            confidence=1.0,
            slot_warnings=["analysis_requires_resolved_stock_code"],
        )
    return None


def _parse_llm_payload(raw: object) -> dict[str, Any]:
    cleaned = re.sub(r"```(?:json)?|```", "", str(raw)).strip()
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("LLM intent payload must be a JSON object")
    return parsed


def _llm_result(query: str) -> dict[str, Any]:
    try:
        raw = llm_cfg.quick_llm.invoke(PARSE_PROMPT.format(query=query)).content.strip()
        payload = _parse_llm_payload(raw)
    except Exception as exc:
        logger.warning("LLM intent/slot extraction failed: %s", exc)
        return _result(
            intent=4,
            reply_hint=_reply_hint(),
            source="llm_fallback",
            confidence=None,
            slot_warnings=["llm_parse_failed"],
        )

    warnings: list[str] = []
    raw_intent = payload.get("intent")
    try:
        intent = int(raw_intent)
    except (TypeError, ValueError):
        intent = 4
        warnings.append("invalid_llm_intent")
    if intent not in _VALID_INTENTS:
        intent = 4
        warnings.append("invalid_llm_intent")

    requested_focus = _normalise_focus(payload.get("analyst_focus"))
    rule_focus = _detect_focus(query)
    if payload.get("analyst_focus") is not None and requested_focus is None:
        warnings.append("invalid_llm_analyst_focus")
    # Explicit query wording overrides the model's category.  It is less
    # surprising and makes multi-focus requests consistently observable.
    analyst_focus = rule_focus or requested_focus
    sources: dict[str, str] = {}
    if analyst_focus:
        sources["analyst_focus"] = "rule_keywords" if rule_focus else "llm"

    stock_name, stock_code, stock_sources, slot_warnings = _resolve_stock_slots(
        query,
        payload.get("stock_name"),
        payload.get("stock_code"),
    )
    sources.update(stock_sources)
    warnings.extend(slot_warnings)

    if intent == 2 and not stock_code:
        intent = 4
        warnings.append("analysis_requires_resolved_stock_code")
    if intent == 2 and not analyst_focus:
        analyst_focus = "all"
        sources["analyst_focus"] = "default"

    return _result(
        intent=intent,
        stock_name=stock_name,
        stock_code=stock_code,
        analyst_focus=analyst_focus,
        reply_hint=_reply_hint(_clean_stock_name(payload.get("stock_name")) or None) if intent == 4 else None,
        source="llm",
        confidence=None,
        slot_sources=sources,
        slot_warnings=warnings,
    )


def _task_slots(query: str, parsed: dict[str, Any]) -> tuple[dict[str, Optional[str]], dict[str, str], list[str]]:
    """Resolve one server-validated slot set to reuse across sibling tasks."""
    matched_stocks = _find_stock_matches_in_query(query)
    if len(matched_stocks) > 1:
        return {
            "stock_name": None,
            "stock_code": None,
            "analyst_focus": _detect_focus(query),
            "backtest_window": _detect_backtest_window(query),
        }, {}, ["multiple_stock_references_require_clarification"]
    candidate_name = parsed.get("stock_name")
    candidate_code = parsed.get("stock_code")
    if not candidate_name:
        name_in_query, code_in_query, _ = _find_stock_in_query(query)
        candidate_name = name_in_query
        candidate_code = candidate_code or code_in_query
    stock_name, stock_code, sources, warnings = _resolve_stock_slots(
        query,
        candidate_name,
        candidate_code,
    )
    # A strategy name in a sibling backtest task (e.g. "分析基本面，然后回测
    # 均线策略") must not silently turn the research task into an all-focus
    # request.  Prefer the clause that actually asks for analysis.
    focus_query = re.split(r"然后|随后|之后|同时|并且|，并|,并", query, maxsplit=1)[0]
    if "回测" in focus_query:
        focus_query = focus_query.split("回测", maxsplit=1)[0]
    focus = _detect_focus(focus_query) or _normalise_focus(parsed.get("analyst_focus"))
    if focus:
        sources["analyst_focus"] = "rule_keywords" if _detect_focus(focus_query) else "parser"
    return {
        "stock_name": stock_name,
        "stock_code": stock_code,
        "analyst_focus": focus,
        "backtest_window": _detect_backtest_window(query),
    }, sources, warnings


def _new_sub_intent(
    task_id: str,
    intent: str,
    *,
    slots: dict[str, Optional[str]],
    depends_on: Optional[list[str]] = None,
    requires_confirmation: bool = False,
) -> dict[str, Any]:
    task_slots = {
        key: value for key, value in slots.items()
        if value is not None
        and key in (
            {"stock_code", "stock_name", "analyst_focus"}
            if intent == "investment_analysis"
            else {"stock_code", "stock_name", "backtest_window"}
        )
        and intent in {"investment_analysis", "backtest", "discussion", "trade_action"}
    }
    missing_slots = []
    if intent in {"investment_analysis", "backtest", "trade_action"} and not task_slots.get("stock_code"):
        missing_slots.append("stock_code")
    if intent == "backtest" and not task_slots.get("backtest_window"):
        missing_slots.append("backtest_window")
    return {
        "task_id": task_id,
        "intent": intent,
        "depends_on": depends_on or [],
        "slots": task_slots,
        "missing_slots": missing_slots,
        "requires_confirmation": requires_confirmation,
    }


def _first_keyword_position(query: str, keywords: list[str]) -> int:
    positions = [query.find(keyword) for keyword in keywords if query.find(keyword) >= 0]
    return min(positions) if positions else len(query) + 1


def _compound_metadata(query: str, sub_intents: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify task orchestration without adding a fifth top-level intent."""

    task_intents = [str(task["intent"]) for task in sub_intents]
    executable = [intent for intent in task_intents if intent != "clarify"]
    if len(executable) <= 1:
        return {
            "detected": False,
            "classification": "single",
            "execution_policy": "single_task",
            "task_intents": task_intents,
            "source": "deterministic_orchestration",
        }
    if "trade_action" in executable:
        classification = "confirmation_gated"
        execution_policy = "confirmation_gate"
    elif any(keyword in query for keyword in _SEQUENCE_KEYWORDS):
        classification = "sequential"
        execution_policy = "sequential_stages"
    else:
        classification = "parallel"
        execution_policy = "parallel_stage"
    return {
        "detected": True,
        "classification": classification,
        "execution_policy": execution_policy,
        "task_intents": task_intents,
        "source": "deterministic_orchestration",
    }


def _decompose_sub_intents(query: str, parsed: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    """Build deterministic AlphaStock task candidates from a compound query.

    We do not trust an LLM to invent dependencies or side-effect permissions.
    The LLM may still classify a hard single request; this layer recognises the
    bounded finance operations supported by AlphaStock and derives a stable
    task graph candidate from the original wording.
    """
    q = query.lower()
    slots, slot_sources, warnings = _task_slots(query, parsed)
    requested_tasks: list[tuple[str, int, bool]] = []

    analysis_requested = (
        parsed.get("intent") == 2
        or bool(slots.get("stock_code")) and any(keyword in q for keyword in _ANALYSIS_REQUEST_KEYWORDS)
    )
    if analysis_requested:
        if not slots.get("analyst_focus"):
            slots["analyst_focus"] = "all"
            slot_sources["analyst_focus"] = "default"
        requested_tasks.append(("investment_analysis", _first_keyword_position(q, _ANALYSIS_REQUEST_KEYWORDS), False))
    elif parsed.get("intent") == 1:
        requested_tasks.append(("discussion", len(q) + 1, False))

    system_tasks: list[str] = []
    if any(keyword in q for keyword in _BACKTEST_KEYWORDS):
        system_tasks.append("backtest")
    if any(keyword in q for keyword in _SCAN_KEYWORDS):
        system_tasks.append("market_scan")
    if any(keyword in q for keyword in _SCREEN_KEYWORDS):
        system_tasks.append("strategy_screen")
    if parsed.get("intent") == 3 and not system_tasks:
        system_tasks.append("system_action")

    for task_intent in list(dict.fromkeys(system_tasks)):
        keywords = {
            "backtest": _BACKTEST_KEYWORDS,
            "market_scan": _SCAN_KEYWORDS,
            "strategy_screen": _SCREEN_KEYWORDS,
            "system_action": _SYSTEM_KEYWORDS,
        }[task_intent]
        requested_tasks.append((task_intent, _first_keyword_position(q, keywords), False))

    if _has_direct_trade_request(query):
        requested_tasks.append(("trade_action", _first_keyword_position(q, _DIRECT_TRADE_KEYWORDS), True))

    # Explicit sequence words form a chain in the action order expressed by
    # the user.  Without one, read-only actions remain parallel.  Trading is
    # always confirmation-gated after all previous work.
    sequential = any(keyword in q for keyword in _SEQUENCE_KEYWORDS)
    requested_tasks.sort(key=lambda task: (task[1], task[0]))
    sub_intents: list[dict[str, Any]] = []
    completed_task_ids: list[str] = []
    intent_counts: dict[str, int] = {}
    for task_intent, _position, requires_confirmation in requested_tasks:
        intent_counts[task_intent] = intent_counts.get(task_intent, 0) + 1
        task_id = "analysis-1" if task_intent == "investment_analysis" else (
            "trade-action-1" if task_intent == "trade_action" else f"{task_intent}-{intent_counts[task_intent]}"
        )
        if requires_confirmation:
            dependencies = list(completed_task_ids)
        elif sequential and completed_task_ids:
            dependencies = [completed_task_ids[-1]]
        else:
            dependencies = []
        sub_intents.append(
            _new_sub_intent(
                task_id,
                task_intent,
                slots=slots,
                depends_on=dependencies,
                requires_confirmation=requires_confirmation,
            )
        )
        completed_task_ids.append(task_id)

    if not sub_intents:
        sub_intents.append(_new_sub_intent("clarify-1", "clarify", slots=slots))
    return sub_intents, slot_sources, warnings


def _attach_sub_intents(query: str, parsed: dict[str, Any]) -> dict[str, Any]:
    sub_intents, decomposition_sources, decomposition_warnings = _decompose_sub_intents(query, parsed)
    parsed = dict(parsed)
    parsed["sub_intents"] = sub_intents
    parsed["multi_intent"] = len(sub_intents) > 1
    parsed["sub_intent_source"] = "deterministic_decomposition"
    parsed["compound_intent"] = _compound_metadata(query, sub_intents)
    parsed["slot_sources"] = {**decomposition_sources, **parsed.get("slot_sources", {})}
    parsed["slot_warnings"] = list(dict.fromkeys([
        *parsed.get("slot_warnings", []),
        *decomposition_warnings,
    ]))

    analysis_task = next((task for task in sub_intents if task["intent"] == "investment_analysis"), None)
    if analysis_task:
        parsed["primary_task_id"] = analysis_task["task_id"]
        parsed["stock_code"] = analysis_task["slots"].get("stock_code")
        parsed["stock_name"] = analysis_task["slots"].get("stock_name")
        parsed["analyst_focus"] = analysis_task["slots"].get("analyst_focus") or "all"
        if parsed["stock_code"]:
            # Mixed "research + backtest" requests enter the governed research
            # harness; the compiled task plan tells it whether backtest may run
            # in parallel or must await research completion.
            parsed["intent"] = 2
        else:
            parsed["intent"] = 4
            parsed["reply_hint"] = _reply_hint(parsed.get("stock_name"))
    else:
        parsed["primary_task_id"] = sub_intents[0]["task_id"]
    return parsed


def _attach_constrained_decomposition(query: str, parsed: dict[str, Any]) -> dict[str, Any]:
    """Upgrade only complex read-only requests after strict plan validation.

    The LLM receives neither permissions nor execution authority.  If it is
    unavailable or returns an invalid plan, the deterministic result (usually
    a clarification for multi-stock input) is left untouched.
    """
    known_codes = [code for _name, code, _source in _find_stock_matches_in_query(query)]
    proposal = constrained_decompose(query, llm_cfg.quick_llm, known_stock_codes=known_codes)
    if proposal is None:
        return parsed
    sub_intents = proposal["sub_intents"]
    result = dict(parsed)
    result["sub_intents"] = sub_intents
    result["multi_intent"] = len(sub_intents) > 1
    result["sub_intent_source"] = "constrained_llm_decomposition"
    result["decomposition_audit"] = {
        "original_query": proposal["original_query"],
        "source": proposal["decomposition_source"],
        "reason": proposal["decomposition_reason"],
        "execution_authority": "validated_task_graph_only",
    }
    result["compound_intent"] = _compound_metadata(query, sub_intents)
    result["intent"] = 2
    result["stock_codes"] = list(dict.fromkeys(
        code for task in sub_intents for code in task.get("slots", {}).get("stock_codes", [])
    ))
    analysis_task = next((task for task in sub_intents if task["intent"] == "investment_analysis"), None)
    if analysis_task:
        result["primary_task_id"] = analysis_task["task_id"]
        result["stock_code"] = analysis_task["slots"].get("stock_code")
        result["stock_name"] = analysis_task["slots"].get("stock_name")
        result["analyst_focus"] = analysis_task["slots"].get("analyst_focus")
    else:
        # A comparison has multiple entities; do not pretend it is a
        # single-stock analysis or choose an arbitrary primary ticker.
        result["primary_task_id"] = sub_intents[0]["task_id"]
        result["stock_code"] = None
        result["stock_name"] = None
        result["analyst_focus"] = None
    return result


def parse_intent(query: str) -> dict[str, Any]:
    """Return validated intent and slots for the runtime's routing contract."""
    normalized_query = str(query or "").strip()
    for typo, corrected in _RULE_TYPO_REPLACEMENTS.items():
        normalized_query = normalized_query.replace(typo, corrected)
    rule_result = _rule_layer(normalized_query)
    if rule_result is not None:
        return _attach_constrained_decomposition(normalized_query, _attach_sub_intents(normalized_query, rule_result))

    fasttext_result = _fasttext_layer(normalized_query)
    if fasttext_result is not None:
        return _attach_constrained_decomposition(normalized_query, _attach_sub_intents(normalized_query, fasttext_result))
    return _attach_constrained_decomposition(normalized_query, _attach_sub_intents(normalized_query, _llm_result(normalized_query)))
