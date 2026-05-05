# Bus MVP

最后更新：2026-04-10
状态：可实施版（bus 审计已开始承接知识事件，仍未接管默认 live 主链）

## 当前交付

- `bus.schema.sql`
- `scripts/bus_cli.py`
- `examples/`
- `runtime/`

## 当前目标

提供 Agent Bus 的最小旁路实现，具备：
- enqueue
- inbox / outbox
- ack
- retry
- dead-letter
- audit
- shared blackboard

并为后续主线化保留两类语义：
- 任务协作消息
- 知识生命周期消息

## 当前边界

- 默认使用独立 SQLite 数据库
- 默认使用独立 runtime 目录
- 不接入现网 Feishu / session / gateway
- 不替换当前协作路径

## V2 协议方向

Knowledge System V2 之后，Bus 不只服务任务流，也要服务知识流。

推荐承接的新增协议消息：
- `KNOWLEDGE_CANDIDATE_CREATED`
- `KNOWLEDGE_PAGE_UPDATED`
- `MEMORY_CANDIDATE_PROMOTED`
- `PROCEDURE_PROMOTED`
- `REVIEW_DECISION_RECORDED`
- `KNOWLEDGE_ACTION_SUGGESTED`

推荐主链：

```text
task/coordination messages
-> bus
-> memory-fusion
-> knowledge compiler
-> knowledge lifecycle messages
-> review / promotion
```

## 当前已落地部分

当前 `bus_audit` 和 `bus-runtime/audit` 已经可以接收由知识编译器写入的知识生命周期事件。

已验证事件类型：
- `KNOWLEDGE_PAGE_UPDATED`
- `KNOWLEDGE_CANDIDATE_CREATED`
- `MEMORY_CANDIDATE_PROMOTED`

当前 task 级 blackboard 也已开始承接最小知识状态：
- `knowledge.page`
- `knowledge.candidates`
- `knowledge.latest_memory_promotion`

当前 review queue 文档虽然还不是总线消息本身，但已经成为知识候选进入长期蒸馏层前的稳定挂点。

在当前阶段，bus 承接：
- 知识事件审计
- task 级 blackboard 共享状态

而 review report 则承接：
- 人工审阅入口
- 周期性收口视图

当前 review decision 之后，bus 还能看到：
- `review_decision_recorded`
- `blackboard_put`
- `memory_candidate_promoted` / `procedure_promoted`

当前 review decision 也已经可以形成正式协议消息：
- `REVIEW_DECISION_RECORDED`

当前批量 review decision 时，这些事件会按 task 分别留下审计记录，不会丢失单条候选的可追溯性。

## 当前默认入队消息

当前 bus 已验证可承接并入队的知识生命周期消息：
- `KNOWLEDGE_PAGE_UPDATED`
- `KNOWLEDGE_CANDIDATE_CREATED`
- `MEMORY_CANDIDATE_PROMOTED`
- `PROCEDURE_PROMOTED`
- `REVIEW_DECISION_RECORDED`
- `KNOWLEDGE_ACTION_SUGGESTED`

当前默认行为：
- 消息写入 `bus_messages`
- 消息复制到 `bus-runtime/inbox/main/`
- 消息复制到 `bus-runtime/outbox/knowledge-compiler/`

这一步意味着 bus 已从“知识事件审计层”进一步推进到“知识事件消息层”。

当前也已经有对应消费器：
- `${QYCLAW_HOME}/workspace/scripts/main_knowledge_message_consumer.py`

消费器职责：
- 读取 `main` inbox 中的知识生命周期消息
- 按 task 聚合成消费 digest
- 回写 blackboard
- 生成下一步动作建议
- 可选 `ack`

当 `main` 使用 `--emit-actions` 时，消费器还会把动作建议升级成正式协议消息：
- `KNOWLEDGE_ACTION_SUGGESTED`

当前已验证这些动作消息可进入：
- `bus-runtime/inbox/main/`
- `bus-runtime/inbox/ops/`

当前还已经验证：
- `bus-runtime/inbox/research/` 中的 `KNOWLEDGE_ACTION_SUGGESTED`
- 可以被消费器继续物化成标准 `TASK`
- 新任务会重新进入 `bus_messages` 与 `inbox/research/`

当前还已经验证：
- 下游 agent 可以继续消费该 `TASK`
- 通过 `${QYCLAW_HOME}/workspace/scripts/agent_task_result_bridge.py` 产出标准 `RESULT`
- `RESULT` 可以重新进入 `bus_messages`
- 再由 `${QYCLAW_HOME}/workspace/scripts/main_result_feedback_consumer.py` 消费并回流成新的 `KNOWLEDGE_PAGE_UPDATED`
- 如果该 `RESULT` 带有 `needs_review = true`
  - 还会继续发出新的 `KNOWLEDGE_CANDIDATE_CREATED`
  - 并生成对应的 `review-queue` 文档

当前消息入队也已经做了幂等保护：
- 重复 `message_id` 不再导致 SQLite 唯一键失败
- 会留下 `enqueue_skipped_duplicate` 审计记录

当前知识消息也已经开始按订阅规则投递到其他 agent inbox：
- 规则文件：
  - `${QYCLAW_HOME}/workspace/knowledge/schemas/agent-knowledge-subscriptions.v1.json`
- 已验证接收方：
  - `research`
  - `ops`

已验证接入点：
- `${QYCLAW_HOME}/workspace/scripts/knowledge_base.py`

已验证运行面：
- `${QYCLAW_HOME}/workspace/integration/qy_code/runtime/shadow-live/`

这表示 bus 现在已经开始承接“知识事件审计”，但还没有把所有知识生命周期都升级成默认总线驱动流程。

当前已形成的最小总线闭环：

```text
KNOWLEDGE_ACTION_SUGGESTED
-> TASK
-> RESULT
-> KNOWLEDGE_PAGE_UPDATED
-> KNOWLEDGE_CANDIDATE_CREATED
```

这意味着 bus 现在已经不只是知识留痕层，而开始承接知识驱动执行链。

## 默认建议路径

数据库：

```bash
${QYCLAW_HOME}/workspace/integration/qy_code/bus/agent-bus.db
```

运行目录：

```bash
${QYCLAW_HOME}/workspace/integration/qy_code/bus/runtime
```

## 本地使用

初始化：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/bus/scripts/bus_cli.py \
  init \
  ${QYCLAW_HOME}/workspace/integration/qy_code/bus/agent-bus.db \
  ${QYCLAW_HOME}/workspace/integration/qy_code/bus/runtime
```

投递消息：

```bash
python3 ${QYCLAW_HOME}/workspace/integration/qy_code/bus/scripts/bus_cli.py \
  enqueue \
  ${QYCLAW_HOME}/workspace/integration/qy_code/bus/agent-bus.db \
  ${QYCLAW_HOME}/workspace/integration/qy_code/bus/runtime \
  ${QYCLAW_HOME}/workspace/integration/qy_code/protocols/examples/task.example.json
```
