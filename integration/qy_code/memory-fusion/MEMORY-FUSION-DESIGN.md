# Memory Fusion 设计

最后更新：2026-04-03
状态：设计版

## 目标

在不改动生产记忆主链的前提下，把旁路协作内核产生的结构化结果转换成可沉淀的记忆包。

## 输入

### 来自 Task Board
- root task
- subtasks
- task events
- task artifacts
- task reviews
- task dependencies

### 来自 Bus
- messages
- audit
- blackboard

## 输出

### Markdown 摘要
用于人读和 Obsidian 镜像

### JSON 摘要
用于后续结构化处理

### 候选记忆片段
用于后续接入：
- `project-updates`
- `decisions`
- `lessons`
- `preferences`

## 第一版原则

1. 不直接写生产 `memory.db`
2. 不直接修改长期规则层
3. 先做 sidecar 输出
4. 先保证聚合质量，再考虑自动接入

## 第一版输出结构

- `summary.json`
- `summary.md`
- `semantic-candidates.json`

## 后续接入方向

当 sidecar 稳定后，可逐步映射到：
- `workspace/memory/01-working/tasks/`
- `workspace/memory/20-semantic/project-updates/`
- `workspace/memory/20-semantic/decisions/`
- `workspace/memory/20-semantic/lessons/`

