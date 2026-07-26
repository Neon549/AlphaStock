# Skill: 股票多维度分析
版本：v1.1  最后更新：2026-07

## 触发条件

✅ 用户提供了具体股票代码（000xxx / 300xxx / 600xxx / 688xxx）
✅ 用户问技术面、基本面、情绪面、买卖建议、KDJ、MACD、ROE、新闻
✅ intent_recognition 返回意图2（操作性分析）
✅ Slot Extraction 已成功提取 stock_code

❌ 用户只提了股票名称但 Slot Extraction 未能解析出 stock_code → 追问用户
❌ 用户问大盘/行业整体走势（无具体 stock_code）→ 走意图1直接回复
❌ 用户问港股/美股 → 告知不支持，不加载此 skill
❌ stock_code 对应 ST 股票 → 告知超出分析范围，不加载此 skill

## 加载顺序（skill_loader.py 执行）

```
1. 加载 refs/common_rules.md        （所有 analyst 必须注入）
2. 根据 analyst_focus 加载专属规则：
   - fundamental → refs/fundamental_rules.md
   - technical   → refs/technical_rules.md
   - sentiment   → refs/sentiment_rules.md
3. 两个文件拼接后注入 System Prompt
```

## 更新记录

- v1.1 加入 ST 股票和港股/美股的触发排除条件
- v1.0 初版