# RAG 最终 Test 查询采集卡

这不是评测数据本身，而是采集和人工标注前的工作卡。最终 `test` 只接受脱敏真实会话或已发生的线上 Bad Case，不能把 validation 的题改写后放进来。

## 每条记录需要的内容

```text
原始查询（已脱敏）：
来源：deidentified_session | production_bad_case
来源日期（只保留日）：
文档类型：annual_report | quarterly_report | announcement | research_report | news
新文档/语料版本：sha256:...

人工答案事实：指标 / 数值 / 单位 / 报告期
可接受 Evidence ID：
页码引用：文件名 / 页码 / 章节
是否应拒答：true | false
失败类型（如适用）：retrieval_miss | wrong_period | wrong_citation | unsupported_claim | abstention_failure

审阅人：
审阅日期：YYYY-MM-DD
```

## 脱敏和隔离规则

- 删除姓名、邮箱、手机号、身份证号、`user_id`、`session_id`、trace ID 和原始对话上下文；只保留完成任务所需的查询文本。
- 最终 test 的来源字段只能是 `deidentified_session` 或 `production_bad_case`。
- 不能与 `rag_candidates.jsonl`、`rag_query_variants.jsonl` 共享规范化 query、引用文档或同一条「文档 + 指标 + 数值」事实。
- 每条题独立标注 `answer_facts`、`relevant_evidence_ids`、`required_citations`；LLM 可产出草稿，但不可直接成为 Gold。
- 冻结前必须通过：

```bash
python -m evaluation.real_rag_test_admission ^
  --dataset path/to/rag-real-final-test.jsonl ^
  --out runtime/reports/rag-real-final-test.admission.json
```

通过后再把数据哈希、审阅协议和 train/validation/test 隔离说明写入 `DATASET_MANIFEST.json` 的 `production` 条目。
