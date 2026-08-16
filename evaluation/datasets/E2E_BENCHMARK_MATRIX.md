# 端到端 RAG 评测矩阵

| 数据集 | 数量 | 证据状态 | 能否报告答案正确率 | 声明边界 |
|---|---:|---|---|---|
| CFQA 页锚定候选子集 | 9 | 公开问题、官方年报页码和 Evidence ID；独立复核待完成 | 否；当前只报告检索/引用页诊断 | 中文主基准候选集，不是线上流量 |
| CFQA 测试候选 | 2,036 | 已固定源仓库；年报 PDF 映射待完成 | 否 | 只能报告导入完整性，不能称为 RAG Gold |
| `rag_golden_seed` | 3 | 人工复核的回归夹具 | 可以，但只限回归正确率 | 小型确定性回归集 |
| `production_candidate_v1` | 22 | 候选标签；独立复核待完成 | 只能作为内部候选诊断 | 不得称为生产 Gold |
| `heldout_public_filings_v1/v2` | 25 / 22 | 专家候选映射；冻结/复核待完成 | 只能作为验证诊断 | 暂不用于最终简历结论 |
| FinanceBench 开源样本 | 150 | 公开人工答案/证据/页码标注 | 可以，但必须明确自动判定器 | 可选英文跨市场对照，不是中文主基准 |

CFQA 本次运行摘要记录在
[`CFQA_RAG_REPORT.md`](CFQA_RAG_REPORT.md)。FinanceBench 结果仍与内部候选/验证集
分开保存，并且不参与中文主结论。

端到端运行器分别报告 `answer_accuracy` 和 `grounded_answer_accuracy`。
`grounded_answer_accuracy` 要求答案正确、引用齐全且每个引用页都在检索证据中。
RAGAS Faithfulness 仍是独立的支持性指标，不能替代答案正确率。
