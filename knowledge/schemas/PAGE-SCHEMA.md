# Page Schema

## Required Frontmatter
```yaml
---
type: source|entity|concept|project|comparison|contradiction|open-question|overview
status: draft|active|stable|deprecated
updated_at: 2026-04-10T00:00:00+08:00
source_count: 0
confidence: 0.0
related_pages: []
raw_sources: []
---
```

## Rules
- `source` 页记录单条原始材料的编译摘要
- `project` 页承接多来源综合认知
- `concept` 页承接方法论与模式
- `comparison` 页承接方案矩阵比较与决策分析
- `contradiction` 页只记录明确冲突与待澄清点
- `open-question` 页只记录尚未闭环的问题
- 稳定规则不要直接写知识页，先进入 review / distillation，再决定是否进入 procedure 层
