# WorkerRuntime 设计

最后更新：2026-04-03
状态：设计版

## 目标

定义 worker 在 Agent Bus 中的标准行为，不再依赖纯自然语言习惯。

## 三种 worker 语义

### Fresh Worker
- 干净上下文
- 任务边界最清楚
- 适合高独立性任务

### Fork Worker
- 继承部分上下文
- 适合短期分支分析

### Persistent Teammate
- 稳定身份
- 稳定 inbox
- 稳定记忆

## 标准行为

1. claim task
2. emit `EVENT: claimed`
3. 执行任务
4. 需要时 emit `EVENT: in_progress`
5. 卡住时 emit `ESCALATE` 或 `PERMISSION_REQUEST`
6. 完成后返回 `RESULT`

## worker 不应做的事

- 私自改变主任务目标
- 私自扩 scope
- 随意横向发消息
- 绕过 `main` 对外发布最终结果

## 与当前体系的兼容

第一版仍允许 worker 继续输出自然语言 markdown 结果，但外层应逐步补上结构化 envelope。

