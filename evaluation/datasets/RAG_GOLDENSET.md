# RAG 金标集规范

这不是让模型“蒸馏出标准答案”的文件。每一条金标首先来自可追溯证据，再经过人工复核；模型只能用于生成候选问法、边界变体和标注草稿。

## 一条样本应包含

```json
{
  "id": "doc-income-statement-001",
  "split": "regression",
  "source_type": "uploaded_document",
  "corpus_version": "sha256:...",
  "query": "这份年报的经营现金流是多少？",
  "expected": {
    "answer_facts": [{"name": "经营活动现金流", "value": "...", "unit": "元"}],
    "relevant_evidence_ids": ["chunk:annual-report-2025:p32:3"],
    "required_citations": [{"filename": "...pdf", "page": 32, "section": "现金流量表"}],
    "abstain_allowed": false
  },
  "tags": ["pdf", "table", "financial-statement"],
  "provenance": {"origin": "manual|production_bad_case|llm_candidate", "reviewer": "", "reviewed_at": ""}
}
```

`corpus_version` 与 `relevant_evidence_ids` 是回归可复现的前提：评测时不能重新抓实时新闻，再拿结果和历史分数比较。

## 建集与扩样本

1. 先人工构造 30--50 条核心金标：上传文档问答、策略知识、股票新闻、拒答/数据不足、跨章节与表格问题。
2. 从 LangFuse trace、用户反馈和线上错误中分层抽样；按意图、数据源、置信度、是否调用工具和失败类型打标签。
3. 用强模型只生成“候选 query / 期望事实 / 可能证据”，绝不直接入金标；人工检查事实、证据页码、拒答条件后才合并。
4. 划分 immutable `regression`（每次必跑）和可更新 `validation`（用于选择检索/Pipeline 版本）。同一文档或同一事实的改写不能跨 split。
5. 每次线上 Bad Case 修复后，补一条最小可复现样本和归因标签，例如 `retrieval_miss`、`wrong_citation`、`unsupported_claim`、`tool_failure`。

## 指标与门禁

| 层级 | 离线无 API 指标 | 可选模型评估 | 建议门禁 |
| --- | --- | --- | --- |
| 检索 | Recall@k、MRR、nDCG、citation hit rate | RAGAS context precision/recall | 回归集不得低于基线 |
| 生成 | JSON/schema、数值/单位、引用页码、拒答 | Faithfulness、answer relevancy、人工抽检 | 安全/结构化规则 100% |
| 工作流 | 路由、权限、fallback、重试上限、发布治理 | 轨迹 judge | 冒烟集 100% |

不要设一个脱离样本规模的固定总分（如“88% 就上线”）。先记录当前基线，再为每个高风险指标设置“不低于基线且关键样本 100%”的门禁；样本扩大后才根据置信区间调整阈值。
