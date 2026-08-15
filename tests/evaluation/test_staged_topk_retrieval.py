from evaluation.run_remote_db_retrieval_eval import (
    _subset_bge_news_cascade,
    _subset_bge_news_reranked,
    build_bge_comparison,
)


def _item(evidence_id: str, title: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "stock_code": "600036",
        "title": title,
        "content": title,
        "source_kind": "news",
        "source_url": "",
    }


def test_cascade_20_10_5_keeps_multi_facet_coverage_inside_bge_top10():
    corpus = [
        _item("a", "招商银行高管人事变动"),
        _item("b", "招商银行主力资金净流入"),
        _item("c", "招商银行业绩增长"),
        *[_item(f"f{index}", f"招商银行普通新闻{index}") for index in range(4, 22)],
    ]
    score_by_id = {item["evidence_id"]: float(30 - index) for index, item in enumerate(corpus)}

    result = _subset_bge_news_cascade(
        corpus,
        lambda _query, passages: [score_by_id[next(item["evidence_id"] for item in corpus if item["content"] == passage)] for passage in passages],
        "600036",
        "招商银行近期有什么人事变动或资金流动消息？",
        retrieve_k=20,
        rerank_k=10,
        top_k=5,
    )

    titles = [item["title"] for item in result]
    assert len(result) == 5
    assert any("人事变动" in title for title in titles)
    assert any("资金净流入" in title for title in titles)
    assert all(item["evidence_id"] in {row["evidence_id"] for row in corpus[:10]} for item in result)


def test_cascade_rejects_invalid_stage_sizes():
    try:
        _subset_bge_news_cascade([], lambda _query, _passages: [], "600036", "问题", retrieve_k=5, rerank_k=10, top_k=5)
    except ValueError as exc:
        assert "retrieve_k" in str(exc)
    else:
        raise AssertionError("invalid stage sizes must fail")


def test_bge_rerank_honours_candidate_pool_before_selecting_top_k():
    corpus = [_item(str(index), f"招商银行新闻{index}") for index in range(20)]
    observed_pool_sizes = []

    def rerank(_query, passages):
        observed_pool_sizes.append(len(passages))
        return [float(index) for index, _ in enumerate(passages)]

    result = _subset_bge_news_reranked(
        corpus,
        rerank,
        "600036",
        "招商银行新闻",
        top_k=5,
        candidate_k=20,
    )
    assert observed_pool_sizes == [20]
    assert len(result) == 5


def test_bge_rerank_rejects_score_count_mismatch():
    corpus = [_item(str(index), f"招商银行新闻{index}") for index in range(3)]

    try:
        _subset_bge_news_reranked(
            corpus,
            lambda _query, _passages: [1.0],
            "600036",
            "招商银行新闻",
            top_k=2,
            candidate_k=3,
        )
    except ValueError as exc:
        assert "scores" in str(exc)
    else:
        raise AssertionError("malformed reranker output must fail closed")


def test_bge_comparison_reports_delta_against_faceted_bm25():
    report = build_bge_comparison({
        "bm25_scoped_faceted": {"keyword_context_recall_diagnostic": 0.4836},
        "bm25_scoped_bge_reranked": {"keyword_context_recall_diagnostic": 0.4351},
        "hybrid_scoped": {"keyword_context_recall_diagnostic": 0.4},
    })

    assert report["baseline_method"] == "bm25_scoped_faceted"
    assert report["methods"]["bm25_scoped_bge_reranked"]["delta"] == -0.0485
    assert "hybrid_scoped" not in report["methods"]
