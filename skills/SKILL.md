# AlphaStock Skills 总索引
版本：v1.0  最后更新：2026-07

## 可用 Skill 列表

| Skill | 路径 | 触发场景 |
|---|---|---|
| stock_analysis | skills/stock_analysis/SKILL.md | 用户问个股分析、买卖建议、技术/基本/情绪面 |
| backtest | skills/backtest/SKILL.md | 用户要求回测策略、查看历史收益、参数优化 |

## 加载规则

1. skill_loader.py 根据 intent_recognition 的结果决定加载哪个 skill
2. 意图2（操作性分析）→ 加载 stock_analysis
3. 意图涉及"回测/历史/策略验证" → 加载 backtest
4. 任何 skill 加载前必须先加载对应域的 common_rules.md

## 禁止行为

- 禁止同时加载多个域的 skill 注入同一个 analyst
- 禁止跳过 common_rules.md 直接加载专属规则