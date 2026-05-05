# Protocol Plane

最后更新：2026-04-10
状态：可实施版（已进入 shadow-live 审计面，仍未接管默认 live 主链）

## 当前交付

- `QYCLAW-A2A-V1-DRAFT.md`
  - 协议说明草案
- `PROTOCOL-FIELD-DICTIONARY.md`
  - 字段字典
- `qyclaw-a2a-v1.schema.json`
  - 第一版 JSON Schema
- `examples/`
  - 各消息类型示例
- `scripts/validate_qyclaw_a2a.py`
  - 本地校验脚本

## 当前目标

把 `qyclaw-a2a/v1` 从“概念文档”推进到“可以被本地验证的协议交付”。

## V2 方向

Knowledge System V2 确认后，协议层后续不只承接任务协作，还应逐步承接知识生命周期事件，例如：
- `KNOWLEDGE_CANDIDATE_CREATED`
- `KNOWLEDGE_PAGE_UPDATED`
- `MEMORY_CANDIDATE_PROMOTED`
- `PROCEDURE_PROMOTED`
- `REVIEW_DECISION_RECORDED`
- `KNOWLEDGE_ACTION_SUGGESTED`

这意味着协议层未来的默认对接对象，应包含：
- `${QYCLAW_HOME}/workspace/knowledge/`
- `${QYCLAW_HOME}/workspace/memory/20-semantic/review-queue/`
- `${QYCLAW_HOME}/workspace/memory/30-procedures/`

## 当前已接链部分

知识生命周期消息现在不只停留在 schema 和 examples。

当前已经通过：
- `${QYCLAW_HOME}/workspace/scripts/knowledge_base.py`

在 `compile-fusion` 阶段写入 runtime 审计：
- `KNOWLEDGE_PAGE_UPDATED`
- `KNOWLEDGE_CANDIDATE_CREATED`

在 `promote-candidate` 阶段写入 runtime 审计：
- `MEMORY_CANDIDATE_PROMOTED`
- `PROCEDURE_PROMOTED`

当前 `compile-fusion` 还会同步生成 review queue 文档，作为知识候选进入蒸馏层前的显式人工确认入口。

当前 `review-report` 已经能够从这些 review queue 文档自动汇总出周期性待审报告。

当前 `review-decide` 已经能够把人工决策回写到：
- review queue 文档状态
- runtime 审计
- task blackboard

其中 `adopt` 会进一步触发：
- `MEMORY_CANDIDATE_PROMOTED`
- 或 `PROCEDURE_PROMOTED`

当前 `review-decide-batch` 则允许在同一轮中批量记录多个 `review_decision_recorded`，并统一刷新 `review-report`。

当前 `review-decide` 也已经会发出正式协议消息：
- `REVIEW_DECISION_RECORDED`

当前 `main` 侧也已经有了消费入口：
- `${QYCLAW_HOME}/workspace/scripts/main_knowledge_message_consumer.py`

这意味着协议消息不只是入队和留痕，而是已经出现了默认消费方。

当前 `main` 还可以把消费结果继续升级成：
- `KNOWLEDGE_ACTION_SUGGESTED`

并把这些动作消息投递给：
- `main`
- `ops`
- 以及后续扩展订阅方

当前还已经验证：
- 下游 agent 可以把 `KNOWLEDGE_ACTION_SUGGESTED` 继续物化成标准 `TASK`

当前也已经验证：
- 下游 agent 可以把 `TASK` 继续产出标准 `RESULT`
- `main` 可以消费该 `RESULT`
- 并把它重新编译回统一知识主根，发成新的 `KNOWLEDGE_PAGE_UPDATED`
- 当该 `RESULT` 需要进入人工蒸馏时
  - 还会继续发成新的 `KNOWLEDGE_CANDIDATE_CREATED`

这说明协议主链已经开始形成：
- knowledge lifecycle message
- action message
- task message
- result message
- result feedback message
- result-derived candidate message

## 当前主链化进展

知识生命周期消息现在已经不只是“可校验”或“可审计”。

当前已验证：
- 这些消息会被真正 enqueue 到 bus
- 默认投递给 `main`
- 并按订阅规则增量投递给其他 agent
- 会同时出现在：
  - `bus_messages`
  - `bus-runtime/inbox/main/`
  - `bus-runtime/outbox/knowledge-compiler/`

这表示协议层已经开始进入默认运行主链，而不再只是旁路文档。

当前已验证运行面：
- `${QYCLAW_HOME}/workspace/integration/qy_code/runtime/shadow-live/`
- `${QYCLAW_HOME}/workspace/integration/qy_code/runtime/live/`

这一步说明协议层已经开始有真实运行时落点，但还没有完全调整当前默认 live 协作路径。

当前最小闭环已经能写成：

```text
knowledge lifecycle
-> KNOWLEDGE_ACTION_SUGGESTED
-> TASK
-> RESULT
-> KNOWLEDGE_PAGE_UPDATED
-> KNOWLEDGE_CANDIDATE_CREATED
```

## 当前边界

- 不变更当前默认 live 消息主链
- 不改 Feishu channel
- 仍然主要作为后续开发的协议基线

## 本地使用

校验单个消息：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/protocols/scripts/validate_qyclaw_a2a.py \
  ${QYCLAW_HOME}/workspace/integration/qy_code/protocols/examples/task.example.json
```

校验整个 examples 目录：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/protocols/scripts/validate_qyclaw_a2a.py \
  ${QYCLAW_HOME}/workspace/integration/qy_code/protocols/examples
```
