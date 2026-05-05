# Control Plane 设计

最后更新：2026-04-03
状态：设计版

## 目标

把当前 `main` 的“主控经验”升级为正式控制平面设计，明确 coordinator、worker、verification、handoff 的关系。

## 核心原则

1. 主控优先
2. 协议优先
3. 结果回收优先
4. 有限横向咨询
5. 默认可审计

## Control Plane 主要模块

### `CoordinatorRuntime`
负责：
- intake
- scoping
- routing
- escalation
- synthesis
- delivery

### `WorkerRuntime`
负责：
- claim task
- execute
- emit event
- return result
- request review

### `VerificationPath`
负责：
- 交叉复核
- 质量门槛
- 风险复核

### `DecisionPolicy`
负责：
- direct
- delegate
- fork
- verify
- escalate

## 与当前 OpenClaw 的对应关系

- `main` 是默认 coordinator
- 其他 agent 是默认 worker
- 现有 `ROLE.md` / `MAIN-ROUTING-RULES.md` 是临时策略层
- 未来应逐步转为 bus-aware control logic

