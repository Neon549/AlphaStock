from rag.query_rewrite import rewrite_retrieval_query


def test_rewrite_preserves_original_and_uses_verified_entity_metadata() -> None:
    result = rewrite_retrieval_query("茅台最近调价了吗", stock_code="600519")

    assert result["original_query"] == "茅台最近调价了吗"
    assert "茅台最近调价了吗" in result["rewritten_query"]
    assert "贵州茅台 600519" in result["rewritten_query"]
    assert "上调零售价" in result["rewritten_query"]
    assert result["filters"] == {"stock_code": "600519", "news_days": 30}
    assert "entity_canonicalized_local_mapping" in result["rewrite_reason"]


def test_annual_report_period_is_a_filter_and_original_numbers_are_not_replaced() -> None:
    result = rewrite_retrieval_query("600519 2025 年年报利润是多少")

    assert result["filters"]["report_period"] == 2025
    assert "600519 2025 年年报利润是多少" in result["rewritten_query"]
    assert "归母净利润" in result["rewritten_query"]


def test_precise_statement_label_is_not_needlessly_expanded() -> None:
    result = rewrite_retrieval_query("600519 2025 年年报营业收入和净利润")

    assert result["rewritten_query"] == "600519 2025 年年报营业收入和净利润 贵州茅台"


def test_unknown_ticker_is_not_canonicalized() -> None:
    result = rewrite_retrieval_query("399999 最近利润", stock_code="399999")

    assert "stock_code" not in result["filters"]
    assert "entity_canonicalized_local_mapping" not in result["rewrite_reason"]


def test_curated_alias_resolves_to_one_local_entity_without_a_ticker() -> None:
    result = rewrite_retrieval_query("茅台 2024 年营收")

    assert result["filters"]["stock_code"] == "600519"
    assert "贵州茅台 600519" in result["rewritten_query"]


def test_exact_canonical_name_keeps_entity_as_a_filter_without_duplicate_lexical_terms() -> None:
    result = rewrite_retrieval_query("贵州茅台 2024 年营收")

    assert result["filters"]["stock_code"] == "600519"
    assert result["rewritten_query"] == "贵州茅台 2024 年营收 营业收入 营收 收入"


def test_ambiguous_generic_suffix_does_not_resolve_an_entity() -> None:
    result = rewrite_retrieval_query("科技公司 2024 年利润")

    assert "stock_code" not in result["filters"]


def test_colloquial_bank_margin_is_normalised_to_the_disclosure_term() -> None:
    result = rewrite_retrieval_query("平银 2025 年息差")

    assert result["filters"]["stock_code"] == "000001"
    assert "净息差" in result["rewritten_query"]


def test_context_entity_is_inherited_only_when_query_has_no_explicit_ticker() -> None:
    inherited = rewrite_retrieval_query("这家公司最近营收如何", context_stock_code="600519")
    explicit = rewrite_retrieval_query("这家公司最近营收如何，300750 呢", context_stock_code="600519")

    assert inherited["filters"]["stock_code"] == "600519"
    assert "entity_inherited_from_context" in inherited["rewrite_reason"]
    assert "贵州茅台 600519" in inherited["rewritten_query"]
    assert explicit["filters"]["stock_code"] == "300750"


def test_finance_typo_is_normalised_additively_without_mutating_original_query() -> None:
    result = rewrite_retrieval_query("茅台 2024 年凈利润")

    assert result["original_query"] == "茅台 2024 年凈利润"
    assert "净利润" in result["rewritten_query"]
    assert "finance_typo_normalized_deterministic" in result["rewrite_reason"]
