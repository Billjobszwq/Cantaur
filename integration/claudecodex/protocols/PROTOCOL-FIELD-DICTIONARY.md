# openclaw-a2a/v1 字段字典

最后更新：2026-04-03
状态：Phase 1 设计稿

## 目标

明确 `openclaw-a2a/v1` 中每个关键字段的业务含义、格式约束和使用场景，避免后续实现时字段漂移。

## Envelope 字段

### `protocol`
- 类型：`string`
- 约束：固定值
- 示例：`openclaw-a2a/v1`
- 作用：标识协议版本

### `message_type`
- 类型：`string`
- 可选值：
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
  - `REVIEW_DECISION_RECORDED`
  - `KNOWLEDGE_ACTION_SUGGESTED`
- 作用：标识消息类别

### `message_id`
- 类型：`string`
- 约束：全局唯一
- 作用：消息主键

### `task_id`
- 类型：`string`
- 约束：同一主任务链路固定不变
- 作用：任务聚合主键

### `trace_id`
- 类型：`string`
- 约束：同一协作链路固定不变
- 作用：串联任务分派、咨询、复核、升级

### `parent_task_id`
- 类型：`string | null`
- 作用：表示当前任务是否从父任务派生

### `from`
- 类型：`string`
- 示例：
  - `main`
  - `research`
  - `system`
- 作用：发送方标识

### `to`
- 类型：`string`
- 作用：接收方标识

### `created_at`
- 类型：`string`
- 格式：ISO8601
- 作用：时间戳

## TASK 体字段

### `goal`
- 类型：`string`
- 作用：任务最终目标

### `constraints`
- 类型：`string[]`

## KNOWLEDGE_CANDIDATE_CREATED 体字段

### `knowledge_scope`
- 类型：`string`
- 建议值：
  - `source`
  - `entity`
  - `concept`
  - `project`
  - `comparison`
  - `contradiction`
  - `open-question`
  - `overview`

### `candidate_type`
- 类型：`string`
- 建议值：
  - `project_update`
  - `decision`
  - `lesson`
  - `preference`
  - `rule`
  - `fact`

### `title`
- 类型：`string`

### `summary`
- 类型：`string`

### `evidence`
- 类型：`string`

### `proposed_target`
- 类型：`string`
- 建议值：
  - `20-semantic/decisions`
  - `20-semantic/preferences`
  - `20-semantic/lessons`
  - `20-semantic/project-updates`
  - `30-procedures`
  - `40-structured`

### `source_refs`
- 类型：`string[]`

### `related_pages`
- 类型：`string[]`

### `review_queue_ref`
- 类型：`string`

## KNOWLEDGE_PAGE_UPDATED 体字段

### `page_type`
- 类型：`string`
- 约束：与 `knowledge_scope` 同域

### `page_path`
- 类型：`string`

### `operation`
- 类型：`string`
- 建议值：
  - `create`
  - `update`
  - `merge`
  - `link`

### `summary`
- 类型：`string`

### `source_refs`
- 类型：`string[]`

### `related_pages`
- 类型：`string[]`

### `compiler`
- 类型：`string`
- 作用：记录触发知识页更新的编译器或入口脚本

## MEMORY_CANDIDATE_PROMOTED 体字段

### `candidate_ref`
- 类型：`string`

### `adopted_target`
- 类型：`string`
- 建议值同 `proposed_target`

### `title`
- 类型：`string`

### `summary`
- 类型：`string`

### `adopted_by`
- 类型：`string`

### `decision_note`
- 类型：`string`

## PROCEDURE_PROMOTED 体字段

### `procedure_path`
- 类型：`string`

### `title`
- 类型：`string`

### `summary`
- 类型：`string`

### `promoted_from`
- 类型：`string`

### `approved_by`
- 类型：`string`

### `rollout_scope`
- 类型：`string`
- 建议值：
  - `single_project`
  - `single_agent`
  - `default`
  - `global`
- 作用：限制条件

## REVIEW_DECISION_RECORDED 体字段

### `review_id`
- 类型：`string`

### `decision`
- 类型：`string`
- 建议值：
  - `adopt`
  - `observe`
  - `reject`

### `status_after`
- 类型：`string`
- 建议值：
  - `adopted`
  - `observed`
  - `rejected`

### `reviewer`
- 类型：`string`

### `note`
- 类型：`string`

### `queue_path`
- 类型：`string`

### `promoted_path`
- 类型：`string`
- 说明：如果本次不是 `adopt`，允许为空字符串

## KNOWLEDGE_ACTION_SUGGESTED 体字段

### `action_type`
- 类型：`string`

### `title`
- 类型：`string`

### `reason`
- 类型：`string`

### `priority`
- 类型：`string`
- 建议值：
  - `low`
  - `medium`
  - `high`

### `target_agent`
- 类型：`string`

### `ref`
- 类型：`string`

### `recommended_command`
- 类型：`string`
- 说明：允许为空字符串

### `source_message_ids`
- 类型：`string[]`

### `suggested_by`
- 类型：`string`

### `inputs`
- 类型：`string[]`
- 作用：输入文件、上下文文件、路径、链接

### `required_output`
- 类型：`string[]`
- 作用：约束输出包

### `priority`
- 类型：`string`
- 建议值：
  - `low`
  - `medium`
  - `high`
  - `critical`

### `deadline`
- 类型：`string | null`
- 作用：任务时限

### `handoff_to`
- 类型：`string`
- 作用：默认结果回收对象

## RESULT 体字段

### `status`
- 类型：`string`
- 建议值：
  - `completed`
  - `partial`
  - `failed`

### `summary`
- 类型：`string`
- 作用：结果摘要

### `artifacts`
- 类型：`string[]`
- 作用：产物路径

### `confidence`
- 类型：`number`
- 范围：`0.0 ~ 1.0`
- 作用：结果置信度

### `needs_review`
- 类型：`boolean`
- 作用：是否需要复核

### `review_by`
- 类型：`string[]`
- 作用：指定复核对象

## REVIEW 体字段

### `review_target`
- 类型：`string`
- 作用：被复核的任务或结果对象

### `review_scope`
- 类型：`string[]`
- 建议值：
  - `facts`
  - `structure`
  - `risk`
  - `compliance`
  - `finance`
  - `quality`

### `review_note`
- 类型：`string`
- 作用：复核说明

### `requested_by`
- 类型：`string`
- 作用：发起复核的角色

## CONSULT 体字段

### `consult_topic`
- 类型：`string`
- 作用：咨询主题

### `context_summary`
- 类型：`string`
- 作用：必要上下文摘要

### `required_response`
- 类型：`string`
- 建议值：
  - `high-level opinion`
  - `risk opinion`
  - `numbers only`
  - `approve/reject`

### `handoff_back_to`
- 类型：`string`
- 作用：咨询后结果回流对象

## EVENT 体字段

### `state`
- 类型：`string`
- 状态集合：
  - `queued`
  - `claimed`
  - `in_progress`
  - `blocked`
  - `completed`
  - `failed`
  - `timed_out`
  - `cancelled`

### `progress`
- 类型：`number`
- 范围：`0.0 ~ 1.0`

### `note`
- 类型：`string`
- 作用：进度说明

### `blockers`
- 类型：`string[]`
- 作用：阻塞项

## ESCALATE 体字段

### `reason`
- 类型：`string`
- 建议值：
  - `evidence_insufficient`
  - `permission_denied`
  - `policy_conflict`
  - `deadline_risk`
  - `scope_unclear`

### `summary`
- 类型：`string`

### `options`
- 类型：`string[]`

### `escalate_to`
- 类型：`string`

## PERMISSION_REQUEST 体字段

### `resource_type`
- 类型：`string`
- 建议值：
  - `tool`
  - `skill`
  - `channel`
  - `session`
  - `filesystem`

### `resource_name`
- 类型：`string`

### `reason`
- 类型：`string`

### `requested_scope`
- 类型：`string`
- 建议值：
  - `single_task`
  - `single_session`
  - `persistent`

## PERMISSION_RESPONSE 体字段

### `decision`
- 类型：`string`
- 建议值：
  - `approved`
  - `rejected`
  - `restricted`

### `granted_scope`
- 类型：`string | null`

### `constraints`
- 类型：`string[]`

### `note`
- 类型：`string`
