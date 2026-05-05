# qyclaw-a2a/v1 协议草案

最后更新：2026-04-03
状态：Phase 1 草案

## 目标

定义 QYclaw 内部多 Agent 协作的第一版结构化协议。

该协议用于：
- 分派任务
- 回收结果
- 记录状态
- 发起复核
- 触发扩展
- 管理权限请求

当前仅是协议草案，不接入现网运行链路。

## 协议版本

- `protocol = qyclaw-a2a/v1`

## 消息总类型

- `TASK`
- `RESULT`
- `REVIEW`
- `CONSULT`
- `EVENT`
- `ESCALATE`
- `PERMISSION_REQUEST`
- `PERMISSION_RESPONSE`
- `KNOWLEDGE_CANDIDATE_CREATED`
- `KNOWLEDGE_PAGE_UPDATED`
- `MEMORY_CANDIDATE_PROMOTED`
- `PROCEDURE_PROMOTED`

## 通用 envelope

```json
{
  "protocol": "qyclaw-a2a/v1",
  "message_type": "TASK",
  "message_id": "msg-uuid",
  "task_id": "task-uuid",
  "trace_id": "trace-uuid",
  "parent_task_id": null,
  "from": "main",
  "to": "research",
  "created_at": "2026-04-03T14:30:00+08:00"
}
```

## 通用字段约束

- `message_id`
  - 单条消息唯一标识
- `task_id`
  - 主任务标识
- `trace_id`
  - 链路追踪标识
- `parent_task_id`
  - 子任务来源；顶层任务可为 `null`
- `from`
  - 发送 agent / system id
- `to`
  - 接收 agent / system id
- `created_at`
  - ISO8601 时间

## TASK

```json
{
  "goal": "完成示例行业系统研究底稿",
  "constraints": [
    "仅限中国市场",
    "必须带来源",
    "输出中文 markdown"
  ],
  "inputs": [],
  "required_output": ["brief", "findings", "review_note"],
  "priority": "high",
  "deadline": "2026-04-03T18:00:00+08:00",
  "handoff_to": "main"
}
```

字段说明：
- `goal`
  - 明确任务目标
- `constraints`
  - 任务限制条件
- `inputs`
  - 引用的前置资料或文件路径
- `required_output`
  - 期望交付物列表
- `priority`
  - `low / medium / high / critical`
- `deadline`
  - 截止时间
- `handoff_to`
  - 默认回收对象

## RESULT

```json
{
  "status": "completed",
  "summary": "已完成研究底稿与执行摘要",
  "artifacts": [
    "/abs/path/brief.md",
    "/abs/path/findings.md",
    "/abs/path/review-note.md"
  ],
  "confidence": 0.82,
  "needs_review": true,
  "review_by": ["main"]
}
```

## REVIEW

```json
{
  "review_target": "task-uuid",
  "review_scope": ["facts", "structure", "risk", "compliance"],
  "review_note": "需要确认数据口径是否一致",
  "requested_by": "main"
}
```

## CONSULT

```json
{
  "consult_topic": "该方案是否存在法务红线",
  "context_summary": "研究阶段发现存在平台规则风险",
  "required_response": "high-level opinion",
  "handoff_back_to": "research"
}
```

## EVENT

```json
{
  "state": "in_progress",
  "progress": 0.45,
  "note": "已完成公开资料搜集，进入矩阵比较分析",
  "blockers": []
}
```

状态建议集合：
- `queued`
- `claimed`
- `in_progress`
- `blocked`
- `completed`
- `failed`
- `timed_out`
- `cancelled`

## ESCALATE

```json
{
  "reason": "evidence_insufficient",
  "summary": "当前结论证据不足，建议缩小范围或补资料",
  "options": [
    "继续补资料",
    "缩小研究范围",
    "先输出底稿"
  ],
  "escalate_to": "main"
}
```

## PERMISSION_REQUEST

```json
{
  "resource_type": "tool",
  "resource_name": "playwright-cli",
  "reason": "需要登录态网页验证",
  "requested_scope": "single_task"
}
```

## PERMISSION_RESPONSE

```json
{
  "decision": "approved",
  "granted_scope": "single_task",
  "constraints": ["仅允许截图与只读验证"],
  "note": "禁止执行高风险发送动作"
}
```

## KNOWLEDGE_CANDIDATE_CREATED

```json
{
  "knowledge_scope": "project",
  "candidate_type": "decision",
  "title": "示例行业巡检系统适合先走正式协同版验证",
  "summary": "多 agent 协作结果显示，应先按正式协同版交付验证，而不是直接扩大 live 暴露面。",
  "evidence": "来源于 memory-fusion 汇总与正式测试结果。",
  "proposed_target": "20-semantic/decisions",
  "source_refs": [
    "/abs/path/summary.md",
    "/abs/path/summary.json"
  ],
  "related_pages": [
    "/abs/path/project-page.md"
  ],
  "review_queue_ref": "/abs/path/review-queue/RQ-20260410-001.md"
}
```

## KNOWLEDGE_PAGE_UPDATED

```json
{
  "page_type": "project",
  "page_path": "/abs/path/knowledge/projects/convenience-ai.md",
  "operation": "update",
  "summary": "已把正式协同版测试产物写入项目知识页。",
  "source_refs": [
    "/abs/path/memory-fusion/output/task-001"
  ],
  "related_pages": [
    "/abs/path/knowledge/sources/task-001-fusion.md"
  ],
  "compiler": "knowledge_base.py"
}
```

## MEMORY_CANDIDATE_PROMOTED

```json
{
  "candidate_ref": "/abs/path/review-queue/RQ-20260410-001.md",
  "adopted_target": "20-semantic/decisions",
  "title": "正式协同版优先作为主验证路径",
  "summary": "将该知识候选提升为长期决策语义项。",
  "adopted_by": "main",
  "decision_note": "用户确认采纳。"
}
```

## PROCEDURE_PROMOTED

```json
{
  "procedure_path": "/abs/path/memory/30-procedures/KNOWLEDGE-COMPILATION-SOP.md",
  "title": "统一知识编译流程",
  "summary": "规定 memory-fusion 输出必须先编译进 knowledge 主根。",
  "promoted_from": "/abs/path/review-queue/RQ-20260410-009.md",
  "approved_by": "main",
  "rollout_scope": "default"
}
```

## REVIEW_DECISION_RECORDED

```json
{
  "review_id": "RQ-001",
  "decision": "observe",
  "status_after": "observed",
  "reviewer": "main",
  "note": "先保留观察，等待更多样本。",
  "queue_path": "/abs/path/review-queue/RQ-20260410-001.md",
  "promoted_path": ""
}
```

## KNOWLEDGE_ACTION_SUGGESTED

```json
{
  "action_type": "verify_procedure_rollout",
  "title": "检查新 procedure 是否应进入默认运行面",
  "reason": "新 procedure 已产生，建议由 ops 评估 rollout_scope 和落地方式。",
  "priority": "high",
  "target_agent": "ops",
  "ref": "/abs/path/memory/30-procedures/xxx.md",
  "recommended_command": "",
  "source_message_ids": [
    "task-001-procedure-promoted-001"
  ],
  "suggested_by": "main"
}
```

## 第一版协议边界

第一版只处理：
- 结构化任务协作
- 状态事件
- 复核与扩展
- 权限申请与响应

第一版暂不处理：
- 真正的分布式执行
- 远程 transport
- 多组织跨实例同步
- 强一致事务

## V2 补充说明

从 Knowledge System V2 开始，A2A 协议不只描述任务协作，也描述知识生命周期：

```text
TASK / RESULT / REVIEW / CONSULT / EVENT / ESCALATE
+ KNOWLEDGE_CANDIDATE_CREATED
+ KNOWLEDGE_PAGE_UPDATED
+ MEMORY_CANDIDATE_PROMOTED
+ PROCEDURE_PROMOTED
+ REVIEW_DECISION_RECORDED
+ KNOWLEDGE_ACTION_SUGGESTED
```

这四类新增消息的目标不是替代原有任务消息，而是把“协作产生知识、知识扩展为规则”的过程显式化。
