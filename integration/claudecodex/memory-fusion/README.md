# Memory Fusion

最后更新：2026-04-03
状态：可实施版（旁路，不接管现网）

## 当前交付

- `MEMORY-FUSION-DESIGN.md`
- `scripts/memory_fusion_cli.py`
- `output/`

## 当前目标

把旁路协作层中的：
- task board
- bus
- blackboard
- audit

聚合成一份可沉淀、可索引、可镜像的“记忆包”。

## 当前边界

- 默认不写入生产 `workspace/memory/40-structured/memory.db`
- 默认不写入生产 `workspace/memory/20-semantic/`
- 先输出到独立 sidecar 目录
- 等规则稳定后，再决定如何接回主记忆链

## V2 接入方向

从 Knowledge System V2 开始，`memory-fusion` 的推荐接入目标不再是直接写 `20-semantic/`。

推荐主链：

```text
memory-fusion output
-> knowledge compiler
-> ${OPENCLAW_HOME}/workspace/knowledge/
-> review queue
-> approved memory / procedures / structured facts
```

统一知识主根见：
- `${OPENCLAW_HOME}/workspace/knowledge/README.md`
- `${OPENCLAW_HOME}/workspace/KNOWLEDGE-SYSTEM-V2.md`

## 默认输出目录

```bash
${OPENCLAW_HOME}/workspace/integration/claudecodex/memory-fusion/output/<task_id>/
```

## 本地使用

```bash
python3 ${OPENCLAW_HOME}/workspace/integration/claudecodex/memory-fusion/scripts/memory_fusion_cli.py \
  summarize \
  /path/to/task-board.db \
  /path/to/agent-bus.db \
  task-20260403-report-001
```
