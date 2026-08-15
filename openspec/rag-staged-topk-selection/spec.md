# RAG 分阶段 Top-K 选择实验

状态：实验完成，未进入默认生产路径

## 背景

当前新闻 RAG 的生成上下文为 Top-5。直接把 BGE 的候选池从 5 扩到 20 后，
固定集 RAGAS 出现 Faithfulness 与 Context Recall 下降；只在 Top-5 内重排则
改善了部分 Recall/Precision，但没有形成全指标提升。

## 目标

验证通用的三级漏斗是否能同时减少噪声并保留多意图覆盖：

```text
实体校验新闻语料
  -> 分面 BM25 Top-20
  -> BGE Cross-Encoder Top-10
  -> 分面覆盖 + BGE 排序的生成上下文 Top-5
```

## 约束

1. 本实验只改变离线检索策略，不自动修改线上默认策略。
2. Top-20 只能来自已完成实体校验的当前股票新闻语料。
3. BGE 不得新增语料；Top-10 必须是 Top-20 的子集，Top-5 必须是 Top-10 的子集。
4. 多分面问题在 Top-5 内优先保留每个分面的标题命中证据，剩余位置按 BGE 分数填充。
5. 评测先报告固定集关键词覆盖诊断，再以相同生成器、Judge、RAGAS 配置报告
   Faithfulness、Answer Relevancy、Context Recall、Context Precision。
6. 只有当关键指标不回退且人工检查没有引入实体/时间错配时，才允许讨论上线；
   任何单项上升都不能单独作为默认策略依据。

## 产物

- `evaluation/run_remote_db_retrieval_eval.py` 中的
  `bm25_scoped_bge_20_10_5_faceted` 方法；
- 原始本地 report 与 RAGAS samples；
- 中文实验报告，明确样本数、语料快照、基线和不确定性。

## 非目标

- 不把 Top-20 当作直接喂给生成器的上下文。
- 不将本实验结果写成生产质量或真实用户泛化结果。
- 不修改交易、发布或工具权限。

## 当前结论

实体校验后的固定 10 题诊断中，`BM25 Top-20 -> BGE Top-10 -> Top-5`
没有超过当前分面 BM25 基线，因此不进入默认生产路径。生产默认只在
BM25/分面已经选出的 Top-5 集合内做 BGE 安全重排，并在模型缺失、输出
数量不匹配或非有限分数时回退 BM25。离线评测器与生产查询扩展保持同一
确定性口径，并对异常 Cross-Encoder 输出失败关闭，避免静默截断造成虚假
指标。
