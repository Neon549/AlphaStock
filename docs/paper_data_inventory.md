# 论文数据清单与可引用边界

更新时间：2026-08-16

## 当前可直接引用

| 数据集 | 数量 | 层级 | 当前用途 | 论文可用性 |
|---|---:|---|---|---|
| FinanceBench open-source v1 | 150 | external_gold | 公开金融问答、答案和证据页码 | 可作为外部基准 |
| rag-contract-v1 | 3 | contract | 检索、引用、拒答回归 | 只能写回归测试 |
| workflow-governance-contract-v1 | 8 | contract | 治理和发布门禁回归 | 只能写回归测试 |

FinanceBench 当前已有端到端参考报告：

- `runtime/reports/financebench-v1.e2e.full.page-citations.json`
- 150 条，148 条可判定；
- Retrieval Hit@20：64.67%；
- Answer Accuracy：43.24%；
- Citation Accuracy：42.57%；
- Grounded Answer Accuracy：30.41%。

这些结果可以作为公开外部基准结果，但不能写成 AlphaStock 线上准确率。

本轮新生成的 V0 检索报告：

- `runtime/reports/paper-v0-financebench-retrieval.json`；
- BM25 global Recall@20：21.67%；
- 实体/时间约束 BM25 Recall@20：38.33%；
- Gold-document scoped BM25 Recall@20：51.67%；
- 其中 Gold-document scoped 结果使用了 FinanceBench 提供的目标文档范围，只能作为检索诊断，不能代表开放文档发现能力。

## 当前只能作为开发诊断

以下数据已经存在，但尚未完成独立人工复核，因此不能作为最终论文 test 或 production Gold：

- `public-filings-rag-candidate-v1`：22 条；
- `public-filings-query-robustness-candidate-v1`：88 条；
- `public-filings-query-rewrite-stress-candidate-v1`：16 条；
- `financial-agent-e2e-candidate-v1`：12 条；
- `financial-agent-e2e-review-queue-v1`：96 条；
- held-out public filings v1/v2：25 条和 22 条；
- external CFQA mapping：9 条；
- intent robustness candidate：21 条。

这些数据可以用于：

1. 发现 bad case；
2. 调试评测器；
3. 选择下一轮实验配置；
4. 生成论文中的开发集诊断图。

它们不能用于：

1. 宣称线上准确率；
2. 宣称生产流量代表性；
3. 作为最终 untouched test；
4. 作为简历中的“真实成功率”。

## 论文最终数据集要求

还需要建立一批独立的最终测试数据，建议规模：

- 80–120 条金融 Agent 任务；
- 两名 reviewer 独立标注；
- 冲突进入仲裁；
- 按公司和时间隔离 train/validation/test；
- 每条任务记录答案事实、Evidence ID、页码、时点、拒答条件和理想工具轨迹；
- test 集在确定方法后冻结，之后不再调参。

## 现阶段数据决策

当前先使用 FinanceBench 做公开 baseline，使用 candidate 数据做开发和错误分析；在最终测试集完成前，不给论文写“production quality”或“线上提升”结论。
