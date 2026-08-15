"""Auditable, deterministic query rewriting for financial retrieval.

The output is a retrieval aid, never a statement of fact.  In particular,
the original query remains available for audit and execution policy; the
rewritten query is only supplied to retrieval rankers.
"""

from __future__ import annotations

import re
from typing import Any

from tools.stock_name_dict import STOCK_NAMES, get_stock_name


_STOCK_CODE = re.compile(r"(?<!\d)([036]\d{5}|688\d{3})(?!\d)")
_YEARLY_REPORT = re.compile(r"(?<!\d)((?:20)\d{2})\s*年\s*(?:年报|年度报告)")
_RECENT = re.compile(r"(?:最近|近期|近一个月|本月)")
_AMBIGUOUS_ALIASES = {"银行", "股份", "集团", "科技", "能源", "智能", "电气", "医药", "证券", "电子"}
# Curated aliases are deliberately small and version-controlled.  They are
# useful for frequent investor wording that is not always a suffix of the
# official security name (for example, 平银), without treating an LLM guess as
# an entity resolution source.
_CURATED_ALIASES = {"茅台": "600519", "宁德": "300750", "平银": "000001", "隆基": "601012", "海康": "002415", "牧原": "002714", "美的": "000333"}
# Keep this intentionally narrow: it fixes recurring financial-field typos,
# not arbitrary natural language.  The correction is appended for retrieval
# while the original wording remains the audit record.
_FINANCE_TYPO_NORMALISATIONS = {
    "营收入": "营收",
    "凈利润": "净利润",
    "淨利潤": "净利润",
    "净利闰": "净利润",
    "回够": "回购",
    "年報": "年报",
}

_SYNONYMS: tuple[tuple[tuple[str, ...], str, tuple[str, ...]], ...] = (
    (("回购",), "股份回购 股票回购 回购注销", ()),
    # Standard financial statement labels are already high-precision BM25
    # anchors. Expand conversational “利润”, not an explicit net-profit line.
    (("利润",), "净利润 归母净利润 业绩预告", ("净利润", "归属于上市公司股东的净利润", "归属于母公司股东的净利润")),
    (("营收",), "营业收入 营收 收入", ("营业收入",)),
    (("调价",), "提价 上调零售价 合同价调整", ("提价", "上调零售价", "合同价调整")),
    (("息差",), "净息差", ("净息差",)),
)


def _canonical_entity(
    stock_code: str | None,
    query: str,
    context_stock_code: str | None = None,
) -> tuple[str | None, str | None, bool]:
    """Return a verified code/name pair; do not infer entities from an LLM."""
    # A ticker stated in the user request wins over inherited context.  This
    # avoids silently answering a follow-up about a different company.
    match = _STOCK_CODE.search(query)
    explicit_code = match.group(1) if match else ""
    direct_code = str(stock_code or "").strip()
    inherited_code = str(context_stock_code or "").strip()
    code = explicit_code if explicit_code else direct_code
    inherited = False
    if not _STOCK_CODE.fullmatch(code) and _STOCK_CODE.fullmatch(inherited_code):
        code = inherited_code
        inherited = True
    if not code:
        compact = "".join(query.split())
        candidates: set[str] = set()
        for alias, candidate_code in _CURATED_ALIASES.items():
            if alias in compact:
                candidates.add(candidate_code)
        for candidate_code, candidate_name in STOCK_NAMES.items():
            if candidate_name and candidate_name in compact:
                candidates.add(candidate_code)
            for length in range(2, min(len(candidate_name), 4) + 1):
                alias = candidate_name[-length:]
                if alias not in _AMBIGUOUS_ALIASES and alias in compact:
                    candidates.add(candidate_code)
        if len(candidates) != 1:
            return None, None, False
        code = next(iter(candidates))
    name = get_stock_name(code)
    if not name or name == "名称未验证":
        return None, None, False
    return code, name, inherited


def rewrite_retrieval_query(
    query: str,
    *,
    stock_code: str | None = None,
    context_stock_code: str | None = None,
) -> dict[str, Any]:
    """Build an additive retrieval query plus safe metadata filters.

    Numbers, years and explicit ticker codes are never replaced.  A recognised
    period is exposed as metadata instead of being fabricated into prose.
    Current news windows use 30 days only when the user explicitly asks for
    recency; the caller owns whether that filter is applicable to its corpus.
    """
    original = str(query or "").strip()
    additions: list[str] = []
    reasons: list[str] = []
    filters: dict[str, Any] = {}

    code, name, inherited = _canonical_entity(stock_code, original, context_stock_code)
    if code and name:
        filters["stock_code"] = code
        # An exact company name is already sufficient for the scoped retriever.
        # Adding its ticker changes lexical weights without adding recall.
        entity_terms = [] if name in original else [term for term in (name, code) if term not in original]
        if entity_terms:
            additions.append(" ".join(entity_terms))
            reasons.append("entity_inherited_from_context" if inherited else "entity_canonicalized_local_mapping")

    report_match = _YEARLY_REPORT.search(original)
    if report_match:
        filters["report_period"] = int(report_match.group(1))
        reasons.append("report_period_extracted")
    if _RECENT.search(original):
        filters["news_days"] = 30
        reasons.append("recent_news_window_30d")

    normalized_terms = [
        corrected
        for typo, corrected in _FINANCE_TYPO_NORMALISATIONS.items()
        if typo in original and corrected not in original
    ]
    if normalized_terms:
        additions.extend(normalized_terms)
        reasons.append("finance_typo_normalized_deterministic")

    normalized_query = " ".join([original, *normalized_terms])
    lowered = normalized_query.lower()
    for triggers, expansion, precise_terms in _SYNONYMS:
        if any(trigger.lower() in lowered for trigger in triggers) and not any(term in normalized_query for term in precise_terms):
            additions.append(expansion)
            reasons.append("finance_synonym_expansion")

    rewritten = " ".join(dict.fromkeys([original, *additions])).strip()
    return {
        "original_query": original,
        "rewritten_query": rewritten,
        "rewrite_reason": list(dict.fromkeys(reasons)),
        "filters": filters,
        "rewrite_source": "deterministic",
        "applied": rewritten != original or bool(filters),
    }
