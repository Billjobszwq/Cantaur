#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".qyclaw"
WORKSPACE_ROOT = ROOT / "workspace"
RUNTIME_BASE = WORKSPACE_ROOT / "integration" / "qy_code" / "runtime"
BUS_SCRIPT = WORKSPACE_ROOT / "integration" / "qy_code" / "bus" / "scripts" / "bus_cli.py"
SUBSCRIPTION_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "agent-knowledge-subscriptions.v1.json"
TASK_TEMPLATE_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "agent-task-templates.v1.json"
CONVERGENCE_POLICY_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "knowledge-convergence-policy.v1.json"
KNOWLEDGE_MESSAGE_TYPES = {
    "KNOWLEDGE_ACTION_SUGGESTED",
    "KNOWLEDGE_PAGE_UPDATED",
    "KNOWLEDGE_CANDIDATE_CREATED",
    "MEMORY_CANDIDATE_PROMOTED",
    "PROCEDURE_PROMOTED",
    "REVIEW_DECISION_RECORDED",
}


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def convergence_output_paths(rt_root: Path, task_id: str, actor: str) -> tuple[Path, Path]:
    out_dir = rt_root / "knowledge-convergence" / task_id
    return out_dir / f"{actor}-convergence.json", out_dir / f"{actor}-convergence.md"


def load_subscription_config() -> dict[str, Any]:
    if SUBSCRIPTION_CONFIG.exists():
        return json.loads(SUBSCRIPTION_CONFIG.read_text(encoding="utf-8"))
    return {"action_routes": {}}


def load_task_template_config() -> dict[str, Any]:
    if TASK_TEMPLATE_CONFIG.exists():
        return json.loads(TASK_TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    return {"default": {"required_output": ["brief", "findings", "next_step_note"], "constraints": [], "handoff_to": "main"}, "agents": {}, "action_overrides": {}}


def load_convergence_policy() -> dict[str, Any]:
    if CONVERGENCE_POLICY_CONFIG.exists():
        return json.loads(CONVERGENCE_POLICY_CONFIG.read_text(encoding="utf-8"))
    return {
        "max_action_task_depth": 2,
        "max_result_feedback_task_depth": 2,
        "stop_candidate_types_at_depth": {},
        "human_review_only_at_depth": 2,
    }


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def runtime_root(runtime_name: str) -> Path:
    return RUNTIME_BASE / runtime_name


def blackboard_put_local(conn: sqlite3.Connection, bus, bus_runtime: Path, task_id: str, entry_key: str, entry_value: dict[str, Any], updated_by: str, updated_at: str) -> None:
    conn.execute(
        """
        INSERT INTO blackboard_entries (task_id, entry_key, entry_value_json, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(task_id, entry_key) DO UPDATE SET
          entry_value_json = excluded.entry_value_json,
          updated_by = excluded.updated_by,
          updated_at = excluded.updated_at
        """,
        (
            task_id,
            entry_key,
            json.dumps(entry_value, ensure_ascii=False),
            updated_by,
            updated_at,
        ),
    )
    payload = {
        "task_id": task_id,
        "entry_key": entry_key,
        "entry_value": entry_value,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }
    bus.audit(
        conn,
        bus_runtime,
        "blackboard_put",
        updated_by,
        "blackboard updated",
        payload,
        task_id=task_id,
        created_at=updated_at,
    )
    bb_dir = bus_runtime / "blackboard" / task_id
    bb_dir.mkdir(parents=True, exist_ok=True)
    (bb_dir / f"{entry_key}.json").write_text(json.dumps(entry_value, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_protocol_message(conn: sqlite3.Connection, bus, bus_runtime: Path, payload: dict[str, Any]) -> bool:
    bus.ensure_runtime_dirs(bus_runtime)
    bus.validate_message(payload)
    existing = conn.execute(
        "SELECT message_id FROM bus_messages WHERE message_id = ?",
        (str(payload["message_id"]),),
    ).fetchone()
    if existing is not None:
        bus.audit(
            conn,
            bus_runtime,
            "enqueue_skipped_duplicate",
            str(payload["from"]),
            f"duplicate message skipped for {payload['to']}",
            payload,
            message_id=str(payload["message_id"]),
            task_id=str(payload["task_id"]),
            created_at=str(payload["created_at"]),
        )
        return False
    cache_dir = WORKSPACE_ROOT / "knowledge" / "compiler" / "bus-messages"
    cache_dir.mkdir(parents=True, exist_ok=True)
    message_path = cache_dir / f"{payload['message_id']}.json"
    message_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    inbox_dir = bus_runtime / "inbox" / str(payload["to"])
    outbox_dir = bus_runtime / "outbox" / str(payload["from"])
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_target = inbox_dir / f"{payload['message_id']}.json"
    outbox_target = outbox_dir / f"{payload['message_id']}.json"
    message_json = json.dumps(payload, ensure_ascii=False, indent=2)
    inbox_target.write_text(message_json, encoding="utf-8")
    outbox_target.write_text(message_json, encoding="utf-8")
    conn.execute(
        """
        INSERT INTO bus_messages (
          message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
          message_type, body_json, queue_status, retry_count, available_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["message_id"],
            payload["task_id"],
            payload["trace_id"],
            payload["parent_task_id"],
            payload["from"],
            payload["to"],
            payload["message_type"],
            json.dumps(payload["body"], ensure_ascii=False),
            "queued",
            0,
            payload["created_at"],
            payload["created_at"],
        ),
    )
    bus.audit(
        conn,
        bus_runtime,
        "enqueue",
        str(payload["from"]),
        f"queued for {payload['to']}",
        payload,
        message_id=str(payload["message_id"]),
        task_id=str(payload["task_id"]),
        created_at=str(payload["created_at"]),
    )
    return True


def fetch_queued_knowledge_messages(conn: sqlite3.Connection, to_agent: str, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
               message_type, body_json, queue_status, retry_count, available_at, created_at,
               acked_at, last_error
        FROM bus_messages
        WHERE to_agent = ?
          AND queue_status IN ('queued', 'retry_wait')
          AND message_type IN ('KNOWLEDGE_ACTION_SUGGESTED', 'KNOWLEDGE_PAGE_UPDATED', 'KNOWLEDGE_CANDIDATE_CREATED', 'MEMORY_CANDIDATE_PROMOTED', 'PROCEDURE_PROMOTED', 'REVIEW_DECISION_RECORDED')
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (to_agent, limit),
    ).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        messages.append(item)
    return messages


def build_task_digest(task_id: str, messages: list[dict[str, Any]], generated_at: str, runtime_name: str, to_agent: str) -> dict[str, Any]:
    counts = {message_type: 0 for message_type in sorted(KNOWLEDGE_MESSAGE_TYPES)}
    items: list[dict[str, Any]] = []
    for msg in messages:
        counts[msg["message_type"]] = counts.get(msg["message_type"], 0) + 1
        body = msg["body"]
        item = {
            "message_id": msg["message_id"],
            "message_type": msg["message_type"],
            "from": msg["from_agent"],
            "to": msg["to_agent"],
            "created_at": msg["created_at"],
            "summary": body.get("summary") or body.get("title") or body.get("page_path") or "",
            "body": body,
        }
        items.append(item)
    latest = messages[-1]["created_at"] if messages else generated_at
    return {
        "task_id": task_id,
        "runtime": runtime_name,
        "to_agent": to_agent,
        "generated_at": generated_at,
        "message_count": len(messages),
        "latest_message_at": latest,
        "counts": counts,
        "items": items,
    }


def build_next_actions(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    config = load_subscription_config()
    action_routes = config.get("action_routes", {})
    candidate_type_routes = config.get("candidate_type_routes", {})
    keyword_routes = config.get("keyword_routes", {})
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for msg in messages:
        body = msg["body"]
        message_type = msg["message_type"]
        if message_type == "KNOWLEDGE_CANDIDATE_CREATED":
            title = str(body.get("title", "") or msg["message_id"])
            review_queue_ref = str(body.get("review_queue_ref", ""))
            candidate_type = str(body.get("candidate_type", "") or "")
            action_type = "review_candidate"
            reason = "新知识候选已进入 review queue，建议尽快给出 adopt / observe / reject。"
            recommended_command = f"python3 ${QYCLAW_HOME}/workspace/scripts/knowledge_base.py review-decide --month {dt.date.today().strftime('%Y-%m')} --id <RQ-ID> --decision observe --reviewer main --note \"补充判断\""
            if candidate_type == "decision":
                action_type = "review_decision_candidate"
                reason = "该候选更像执行/收口决策，建议由 main 与 ops 先做落地判断，再决定是否固化。"
            elif candidate_type == "rule":
                action_type = "review_rule_candidate"
                reason = "该候选更像规则/边界，建议优先由法务与运营联合复核，再由 main 收口。"
            elif candidate_type == "fact":
                action_type = "verify_fact_candidate"
                reason = "该候选更像事实/财务影响，建议先补成本、ROI 或证据确认，再进入采纳。"
            elif candidate_type == "lesson":
                action_type = "refine_lesson_candidate"
                reason = "该候选更像经验教训，建议继续补样本、技术注记或研究证据，再决定是否升格。"
            elif candidate_type == "preference":
                action_type = "align_preference_candidate"
                reason = "该候选更像表达/偏好，建议先由内容侧统一口径，再决定是否固化。"
            action = {
                "priority": "high",
                "action_type": action_type,
                "title": title,
                "reason": reason,
                "recommended_command": recommended_command,
                "ref": review_queue_ref,
                "target_agents": list(action_routes.get(action_type, action_routes.get("review_candidate", ["main"]))),
                "source_message_ids": [msg["message_id"]],
            }
            for agent in candidate_type_routes.get(candidate_type, []):
                if agent not in action["target_agents"]:
                    action["target_agents"].append(agent)
            routing_text = "\n".join(
                [
                    title,
                    str(body.get("summary", "")),
                    str(body.get("evidence", "")),
                    str(body.get("proposed_target", "")),
                ]
            ).lower()
            for agent, keywords in keyword_routes.items():
                if any(str(keyword).lower() in routing_text for keyword in keywords):
                    if agent not in action["target_agents"]:
                        action["target_agents"].append(agent)
        elif message_type == "KNOWLEDGE_PAGE_UPDATED":
            page_path = str(body.get("page_path", ""))
            action = {
                "priority": "medium",
                "action_type": "inspect_page_update",
                "title": str(body.get("summary", "") or msg["message_id"]),
                "reason": "知识页已更新，建议快速检查页面是否需要补链接、补项目页或补对比页。",
                "recommended_command": "",
                "ref": page_path,
                "target_agents": list(action_routes.get("inspect_page_update", ["research"])),
                "source_message_ids": [msg["message_id"]],
            }
            routing_text = "\n".join(
                [
                    str(body.get("summary", "")),
                    str(body.get("page_path", "")),
                    " ".join(str(item) for item in body.get("source_refs", [])),
                ]
            ).lower()
            for agent, keywords in keyword_routes.items():
                if any(str(keyword).lower() in routing_text for keyword in keywords):
                    if agent not in action["target_agents"]:
                        action["target_agents"].append(agent)
        elif message_type == "MEMORY_CANDIDATE_PROMOTED":
            target_path = str(body.get("target_path", ""))
            action = {
                "priority": "medium",
                "action_type": "verify_memory_promotion",
                "title": str(body.get("title", "") or msg["message_id"]),
                "reason": "候选已经进入长期蒸馏层，建议确认是否需要继续沉淀为 procedure 或同步到相关项目页。",
                "recommended_command": "",
                "ref": target_path,
                "target_agents": list(action_routes.get("verify_memory_promotion", ["main"])),
                "source_message_ids": [msg["message_id"]],
            }
        elif message_type == "PROCEDURE_PROMOTED":
            procedure_path = str(body.get("procedure_path", ""))
            action = {
                "priority": "high",
                "action_type": "verify_procedure_rollout",
                "title": str(body.get("title", "") or msg["message_id"]),
                "reason": "新 procedure 已产生，建议检查 rollout_scope 并决定是否进入默认运行面。",
                "recommended_command": "",
                "ref": procedure_path,
                "target_agents": list(action_routes.get("verify_procedure_rollout", ["ops"])),
                "source_message_ids": [msg["message_id"]],
            }
        elif message_type == "REVIEW_DECISION_RECORDED":
            decision = str(body.get("decision", ""))
            review_id = str(body.get("review_id", ""))
            queue_path = str(body.get("queue_path", ""))
            status_after = str(body.get("status_after", ""))
            if decision == "observe":
                action = {
                    "priority": "medium",
                    "action_type": "collect_more_evidence",
                    "title": review_id or msg["message_id"],
                    "reason": "该候选被保留观察，建议在后续同类任务里继续累计证据，再决定是否采纳。",
                    "recommended_command": "",
                    "ref": queue_path,
                    "target_agents": list(action_routes.get("collect_more_evidence", ["research"])),
                    "source_message_ids": [msg["message_id"]],
                }
            elif decision == "reject":
                action = {
                    "priority": "low",
                    "action_type": "archive_rejected_candidate",
                    "title": review_id or msg["message_id"],
                    "reason": "该候选已被排除，建议后续避免重复提炼同类噪音。",
                    "recommended_command": "",
                    "ref": queue_path,
                    "target_agents": list(action_routes.get("archive_rejected_candidate", ["main"])),
                    "source_message_ids": [msg["message_id"]],
                }
            else:
                action = {
                    "priority": "medium",
                    "action_type": "verify_adopted_candidate",
                    "title": review_id or msg["message_id"],
                    "reason": f"该候选已变为 `{status_after}`，建议确认蒸馏结果与知识页之间是否需要继续同步。",
                    "recommended_command": "",
                    "ref": str(body.get("promoted_path", "") or queue_path),
                    "target_agents": list(action_routes.get("verify_adopted_candidate", ["main"])),
                    "source_message_ids": [msg["message_id"]],
                }
        else:
            continue
        key = (action["action_type"], action["ref"])
        if key in seen:
            continue
        seen.add(key)
        actions.append(action)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda item: (priority_order.get(item["priority"], 99), item["action_type"], item["title"]))
    return actions


def task_hop_depth(task_id: str) -> int:
    return str(task_id).count("-task-")


def apply_convergence_policy(task_id: str, messages: list[dict[str, Any]], actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    policy = load_convergence_policy()
    depth = task_hop_depth(task_id)
    max_depth = int(policy.get("max_action_task_depth", 2))
    terminal_action_types = {str(item) for item in policy.get("terminal_action_types_after_result", [])}
    stop_candidate_types = {
        str(key): int(value)
        for key, value in dict(policy.get("stop_candidate_types_at_depth", {})).items()
    }
    task_markers = {
        action_type
        for action_type in terminal_action_types
        if action_type and action_type in str(task_id)
    }
    filtered: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    if task_markers:
        return (
            [],
            [
                {
                    "task_id": task_id,
                    "depth": depth,
                    "action_type": "convergence_stop",
                    "title": task_id,
                    "reason": f"当前 task 已命中二轮动作标记 `{', '.join(sorted(task_markers))}`，进入人工收敛，不再继续自动发动作。",
                    "mode": "human_review_only",
                }
            ],
        )
    if depth < max_depth:
        return actions, decisions

    candidate_types = sorted(
        {
            str(msg.get("body", {}).get("candidate_type", "") or "")
            for msg in messages
            if msg["message_type"] == "KNOWLEDGE_CANDIDATE_CREATED"
        }
    )
    for action in actions:
        blocked = False
        reason = f"task depth `{depth}` 已达到自动动作阈值 `{max_depth}`，停止继续自动扩圈。"
        if candidate_types:
            blocked_types = [
                candidate_type
                for candidate_type in candidate_types
                if candidate_type and depth >= stop_candidate_types.get(candidate_type, max_depth)
            ]
            if blocked_types and not blocked:
                blocked = True
                reason = f"候选类型 `{', '.join(blocked_types)}` 在 depth `{depth}` 进入人工收敛，不再继续自动发动作。"
        else:
            blocked = True
        if blocked:
            decisions.append(
                {
                    "task_id": task_id,
                    "depth": depth,
                    "action_type": action["action_type"],
                    "title": action["title"],
                    "reason": reason,
                    "mode": "human_review_only",
                }
            )
            continue
        filtered.append(action)
    return filtered, decisions


def emit_action_messages(conn: sqlite3.Connection, bus, bus_runtime: Path, digest: dict[str, Any], suggested_by: str) -> list[str]:
    emitted: list[str] = []
    for index, action in enumerate(digest.get("suggested_next_actions", []), start=1):
        targets = action.get("target_agents", [])
        for target in targets:
            payload = {
                "protocol": "qyclaw-a2a/v1",
                "message_type": "KNOWLEDGE_ACTION_SUGGESTED",
                "message_id": f"{digest['task_id']}-knowledge-action-{index:03d}-to-{target}",
                "task_id": digest["task_id"],
                "trace_id": f"{digest['task_id']}-knowledge-action-trace",
                "parent_task_id": None,
                "from": suggested_by,
                "to": target,
                "created_at": digest["generated_at"],
                "body": {
                    "action_type": action["action_type"],
                    "title": action["title"],
                    "reason": action["reason"],
                    "priority": action["priority"],
                    "target_agent": target,
                    "ref": action.get("ref", ""),
                    "recommended_command": action.get("recommended_command", ""),
                    "source_message_ids": action.get("source_message_ids", []),
                    "suggested_by": suggested_by,
                },
            }
            if enqueue_protocol_message(conn, bus, bus_runtime, payload):
                emitted.append(str(payload["message_id"]))
    return emitted


def digest_markdown(digest: dict[str, Any]) -> str:
    lines = [
        "# Knowledge Consumption Digest",
        "",
        f"- task_id: `{digest['task_id']}`",
        f"- runtime: `{digest['runtime']}`",
        f"- to_agent: `{digest['to_agent']}`",
        f"- generated_at: {digest['generated_at']}",
        f"- message_count: `{digest['message_count']}`",
        f"- latest_message_at: `{digest['latest_message_at']}`",
        "",
        "## Counts",
        "",
    ]
    for key, value in digest["counts"].items():
        lines.append(f"- {key}: `{value}`")
    lines.extend(["", "## Messages", ""])
    for item in digest["items"]:
        lines.extend(
            [
                f"### {item['message_type']} · {item['message_id']}",
                "",
                f"- from: `{item['from']}`",
                f"- created_at: `{item['created_at']}`",
                f"- summary: {item['summary'] or '(empty)' }",
                "",
            ]
        )
    lines.extend(["## Suggested Next Actions", ""])
    actions = digest.get("suggested_next_actions", [])
    if not actions:
        lines.extend(["(none)", ""])
    else:
        for idx, action in enumerate(actions, start=1):
            lines.extend(
                [
                    f"### Action {idx} · {action['action_type']}",
                    "",
                    f"- priority: `{action['priority']}`",
                    f"- title: {action['title']}",
                    f"- reason: {action['reason']}",
                    f"- ref: `{action['ref']}`" if action.get("ref") else "- ref: `(none)`",
                    f"- command: `{action['recommended_command']}`" if action.get("recommended_command") else "- command: `(none)`",
                    "",
                ]
            )
    lines.extend(["## Convergence", ""])
    convergence_decisions = digest.get("convergence_decisions", [])
    if not convergence_decisions:
        lines.extend(["(none)", ""])
    else:
        for item in convergence_decisions:
            lines.extend(
                [
                    f"### {item['action_type']}",
                    "",
                    f"- depth: `{item['depth']}`",
                    f"- mode: `{item['mode']}`",
                    f"- title: {item['title']}",
                    f"- reason: {item['reason']}",
                    "",
                ]
            )
    lines.extend(["## Emitted Messages", ""])
    action_message_ids = digest.get("action_message_ids", [])
    materialized_task_ids = digest.get("materialized_task_ids", [])
    if not action_message_ids and not materialized_task_ids:
        lines.extend(["(none)", ""])
    else:
        if action_message_ids:
            lines.append("### Action Messages")
            lines.append("")
            for message_id in action_message_ids:
                lines.append(f"- `{message_id}`")
            lines.append("")
        if materialized_task_ids:
            lines.append("### Materialized Tasks")
            lines.append("")
            for message_id in materialized_task_ids:
                lines.append(f"- `{message_id}`")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def convergence_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Knowledge Convergence Summary",
        "",
        f"- task_id: `{summary['task_id']}`",
        f"- runtime: `{summary['runtime']}`",
        f"- actor: `{summary['actor']}`",
        f"- generated_at: `{summary['generated_at']}`",
        f"- mode: `{summary['mode']}`",
        "",
        "## Reason",
        "",
        summary.get("reason", "(none)"),
        "",
        "## Blocked Actions",
        "",
    ]
    blocked = summary.get("blocked_actions", [])
    if not blocked:
        lines.extend(["(none)", ""])
    else:
        for item in blocked:
            lines.extend(
                [
                    f"### {item['action_type']}",
                    "",
                    f"- title: {item['title']}",
                    f"- reason: {item['reason']}",
                    "",
                ]
            )
    lines.extend(["## Suggested Human Steps", ""])
    steps = summary.get("suggested_human_steps", [])
    if not steps:
        lines.extend(["(none)", ""])
    else:
        for step in steps:
            lines.append(f"- {step}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_convergence_summary(task_id: str, runtime_name: str, actor: str, generated_at: str, convergence_decisions: list[dict[str, Any]]) -> dict[str, Any]:
    reason = "；".join(unique_list([str(item.get("reason", "")).strip() for item in convergence_decisions if str(item.get("reason", "")).strip()]))
    return {
        "task_id": task_id,
        "runtime": runtime_name,
        "actor": actor,
        "generated_at": generated_at,
        "mode": "human_review_only",
        "reason": reason or "已命中收敛规则，转人工收口。",
        "blocked_actions": [
            {
                "action_type": str(item.get("action_type", "")),
                "title": str(item.get("title", "")),
                "reason": str(item.get("reason", "")),
            }
            for item in convergence_decisions
        ],
        "suggested_human_steps": [
            "查看 knowledge-consumption digest，确认这次循环停在哪个任务。",
            "查看 review-queue / review-report，决定 adopt / observe / reject。",
            "如需继续推进，手动发起下一轮任务，而不是依赖自动扩圈。",
        ],
    }


def compute_deadline(generated_at: str, priority: str) -> str:
    base = dt.datetime.fromisoformat(generated_at)
    hours = {"high": 4, "medium": 8, "low": 24}.get(priority, 8)
    return (base + dt.timedelta(hours=hours)).isoformat()


def unique_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def task_template_for(to_agent: str, action_type: str) -> dict[str, Any]:
    config = load_task_template_config()
    default = config.get("default", {})
    agent_cfg = config.get("agents", {}).get(to_agent, {})
    action_cfg = config.get("action_overrides", {}).get(action_type, {})
    required_output = list(default.get("required_output", []))
    required_output.extend(agent_cfg.get("required_output", []))
    required_output.extend(action_cfg.get("required_output_append", []))
    constraints = list(default.get("constraints", []))
    constraints.extend(agent_cfg.get("constraints", []))
    constraints.extend(action_cfg.get("constraints", []))
    return {
        "required_output": unique_list([str(item) for item in required_output]),
        "constraints": unique_list([str(item) for item in constraints]),
        "handoff_to": str(agent_cfg.get("handoff_to") or default.get("handoff_to") or "main"),
    }


def specialized_goal_prefix(to_agent: str) -> str:
    mapping = {
        "main": "主控综合判断",
        "research": "研究补证与分析",
        "ops": "运营执行与落地检查",
        "law": "法务合规边界审查",
        "finance": "财务成本与收益评估",
        "content": "内容表达与传播策略整理",
        "dev": "技术实现与系统影响评估",
    }
    return mapping.get(to_agent, "知识动作执行")


def build_task_from_action_message(message: dict[str, Any], to_agent: str, generated_at: str) -> dict[str, Any] | None:
    body = message["body"]
    target_agent = str(body.get("target_agent", "") or "").strip()
    if target_agent and target_agent != to_agent:
        return None
    action_type = str(body.get("action_type", "") or "followup")
    title = str(body.get("title", "") or message["message_id"])
    reason = str(body.get("reason", "") or "执行知识消息建议")
    priority = str(body.get("priority", "") or "medium")
    ref = str(body.get("ref", "") or "")
    recommended_command = str(body.get("recommended_command", "") or "")
    suggested_by = str(body.get("suggested_by", "") or message["from_agent"])
    source_message_ids = [str(item) for item in body.get("source_message_ids", [])]
    message_id = f"{message['message_id']}-task"
    task_id = f"{message['task_id']}-task-{to_agent}-{action_type}"
    template = task_template_for(to_agent, action_type)
    constraints = list(template["constraints"])
    if ref:
        constraints.append(f"优先参考：{ref}")
    if recommended_command:
        constraints.append(f"可参考建议命令：{recommended_command}")
    inputs = []
    if ref:
        inputs.append(f"ref={ref}")
    if source_message_ids:
        inputs.extend([f"source_message_id={item}" for item in source_message_ids])
    required_output = list(template["required_output"])
    payload = {
        "protocol": "qyclaw-a2a/v1",
        "message_type": "TASK",
        "message_id": message_id,
        "task_id": task_id,
        "trace_id": f"{message['trace_id']}-task-{to_agent}",
        "parent_task_id": str(message["task_id"]),
        "from": suggested_by,
        "to": to_agent,
        "created_at": generated_at,
        "body": {
            "goal": f"[{specialized_goal_prefix(to_agent)}::{action_type}] {title}：{reason}",
            "constraints": constraints,
            "inputs": inputs,
            "required_output": required_output,
            "priority": priority if priority in {"low", "medium", "high", "critical"} else "medium",
            "deadline": compute_deadline(generated_at, priority),
            "handoff_to": template["handoff_to"],
        },
    }
    return payload


def emit_task_messages_from_actions(conn: sqlite3.Connection, bus, bus_runtime: Path, messages: list[dict[str, Any]], to_agent: str, generated_at: str) -> list[str]:
    emitted: list[str] = []
    for message in messages:
        if message["message_type"] != "KNOWLEDGE_ACTION_SUGGESTED":
            continue
        payload = build_task_from_action_message(message, to_agent, generated_at)
        if payload is None:
            continue
        if enqueue_protocol_message(conn, bus, bus_runtime, payload):
            emitted.append(str(payload["message_id"]))
    return emitted


def ack_message(conn: sqlite3.Connection, bus, bus_runtime: Path, message_id: str, actor: str, acked_at: str) -> None:
    row = conn.execute("SELECT * FROM bus_messages WHERE message_id = ?", (message_id,)).fetchone()
    if row is None:
        raise ValueError(f"message not found: {message_id}")
    conn.execute(
        "UPDATE bus_messages SET queue_status = 'acked', acked_at = ? WHERE message_id = ?",
        (acked_at, message_id),
    )
    payload = dict(row)
    payload["acked_at"] = acked_at
    bus.audit(
        conn,
        bus_runtime,
        "ack",
        actor,
        "message acknowledged",
        payload,
        message_id=message_id,
        task_id=row["task_id"],
        created_at=acked_at,
    )


def consume(runtime_name: str, to_agent: str, limit: int, ack: bool, emit_actions: bool, emit_tasks: bool) -> dict[str, Any]:
    rt_root = runtime_root(runtime_name)
    bus_db = rt_root / "bus.db"
    bus_runtime = rt_root / "bus-runtime"
    bus = load_module(BUS_SCRIPT, f"bus_cli_{runtime_name}_{to_agent}")
    generated_at = now_iso()
    if not bus_db.exists() or not bus_runtime.exists():
        raise FileNotFoundError(f"runtime not initialized: {rt_root}")

    with connect(bus_db) as conn:
        messages = fetch_queued_knowledge_messages(conn, to_agent, limit)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for msg in messages:
            grouped.setdefault(msg["task_id"], []).append(msg)

        digests: list[dict[str, Any]] = []
        for task_id, task_messages in grouped.items():
            digest = build_task_digest(task_id, task_messages, generated_at, runtime_name, to_agent)
            raw_actions = build_next_actions(task_messages)
            filtered_actions, convergence_decisions = apply_convergence_policy(task_id, task_messages, raw_actions)
            digest["suggested_next_actions"] = filtered_actions
            digest["convergence_decisions"] = convergence_decisions
            action_message_ids: list[str] = []
            materialized_task_ids: list[str] = []
            if emit_actions and to_agent == "main":
                action_message_ids = emit_action_messages(conn, bus, bus_runtime, digest, to_agent)
            if emit_tasks:
                materialized_task_ids = emit_task_messages_from_actions(conn, bus, bus_runtime, task_messages, to_agent, generated_at)
            digest["action_message_ids"] = action_message_ids
            digest["materialized_task_ids"] = materialized_task_ids
            digest_dir = rt_root / "knowledge-consumption" / task_id
            json_path = digest_dir / f"{to_agent}-knowledge-consumption.json"
            md_path = digest_dir / f"{to_agent}-knowledge-consumption.md"
            write_text(json_path, json.dumps(digest, ensure_ascii=False, indent=2) + "\n")
            write_text(md_path, digest_markdown(digest))
            blackboard_put_local(
                conn,
                bus,
                bus_runtime,
                task_id,
                "knowledge.consumer.latest_digest",
                {
                    "digest_json": str(json_path),
                    "digest_md": str(md_path),
                    "message_count": digest["message_count"],
                    "counts": digest["counts"],
                    "generated_at": generated_at,
                    "consumer": to_agent,
                    "suggested_next_actions": digest["suggested_next_actions"],
                    "convergence_decisions": digest["convergence_decisions"],
                    "action_message_ids": action_message_ids,
                    "materialized_task_ids": materialized_task_ids,
                },
                to_agent,
                generated_at,
            )
            blackboard_put_local(
                conn,
                bus,
                bus_runtime,
                task_id,
                "knowledge.consumer.next_actions",
                {
                    "generated_at": generated_at,
                    "consumer": to_agent,
                    "items": digest["suggested_next_actions"],
                    "convergence_decisions": digest["convergence_decisions"],
                    "action_message_ids": action_message_ids,
                    "materialized_task_ids": materialized_task_ids,
                },
                to_agent,
                generated_at,
            )
            if digest["convergence_decisions"]:
                convergence_summary = build_convergence_summary(task_id, runtime_name, to_agent, generated_at, digest["convergence_decisions"])
                convergence_json, convergence_md = convergence_output_paths(rt_root, task_id, to_agent)
                write_text(convergence_json, json.dumps(convergence_summary, ensure_ascii=False, indent=2) + "\n")
                write_text(convergence_md, convergence_markdown(convergence_summary))
                blackboard_put_local(
                    conn,
                    bus,
                    bus_runtime,
                    task_id,
                    "knowledge.convergence.latest",
                    {
                        "generated_at": generated_at,
                        "actor": to_agent,
                        "mode": convergence_summary["mode"],
                        "reason": convergence_summary["reason"],
                        "summary_json": str(convergence_json),
                        "summary_md": str(convergence_md),
                    },
                    to_agent,
                    generated_at,
                )
            bus.audit(
                conn,
                bus_runtime,
                "knowledge_consume",
                to_agent,
                f"consumed {digest['message_count']} knowledge message(s)",
                {
                    "task_id": task_id,
                    "digest_json": str(json_path),
                    "digest_md": str(md_path),
                    "message_ids": [msg["message_id"] for msg in task_messages],
                    "counts": digest["counts"],
                    "convergence_decisions": digest["convergence_decisions"],
                    "action_message_ids": action_message_ids,
                    "materialized_task_ids": materialized_task_ids,
                },
                task_id=task_id,
                created_at=generated_at,
            )
            if ack:
                for msg in task_messages:
                    ack_message(conn, bus, bus_runtime, msg["message_id"], to_agent, generated_at)
            digest["digest_json"] = str(json_path)
            digest["digest_md"] = str(md_path)
            digests.append(digest)

    return {
        "runtime": runtime_name,
        "to_agent": to_agent,
        "generated_at": generated_at,
        "task_count": len(digests),
        "message_count": len(messages),
        "acked": ack,
        "digests": [
            {
                "task_id": digest["task_id"],
                "message_count": digest["message_count"],
                "counts": digest["counts"],
                "digest_json": digest["digest_json"],
                "digest_md": digest["digest_md"],
                "action_message_ids": digest["action_message_ids"],
                "materialized_task_ids": digest["materialized_task_ids"],
            }
            for digest in digests
        ],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume knowledge lifecycle messages for mainline agents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    consume_cmd = sub.add_parser("consume")
    consume_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    consume_cmd.add_argument("--to-agent", default="main")
    consume_cmd.add_argument("--limit", type=int, default=200)
    consume_cmd.add_argument("--ack", action="store_true")
    consume_cmd.add_argument("--emit-actions", action="store_true")
    consume_cmd.add_argument("--emit-tasks", action="store_true")

    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "consume":
        result = consume(args.runtime, args.to_agent, args.limit, args.ack, args.emit_actions, args.emit_tasks)
        print(json.dumps({"status": "consumed", **result}, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
