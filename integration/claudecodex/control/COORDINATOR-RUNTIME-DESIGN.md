# CoordinatorRuntime 设计

最后更新：2026-04-03
状态：设计版

## 目标

定义 `main` 未来作为协议化协调器时应具备的状态、职责和决策步骤。

## 生命周期

1. Intake
2. Scope
3. Route
4. Observe
5. Review
6. Synthesize
7. Deliver
8. Archive

## 关键决策动作

### `direct`
由 `main` 自己完成。

### `delegate`
把任务交给单个专业 agent。

### `fork`
在不污染主上下文的前提下派生分支工作。

### `verify`
对结果做二次复核。

### `escalate`
因风险、冲突、信息不足而升级。

## 输入

- 用户需求
- 历史记忆
- registry
- task board
- mailbox 状态

## 输出

- 标准化 TASK
- REVIEW 请求
- 最终对外结果
- 审计记录

## 失败场景

- 路由目标不存在
- 权限不足
- 工具不可用
- worker 超时
- 结果冲突

## 处理策略

- 重路由
- 改为 direct
- 请求 review
- 向用户 escalate

