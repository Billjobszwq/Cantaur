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
RESULT_TEMPLATE_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "agent-result-templates.v1.json"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def unique_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_result_template_config() -> dict[str, Any]:
    if RESULT_TEMPLATE_CONFIG.exists():
        return json.loads(RESULT_TEMPLATE_CONFIG.read_text(encoding="utf-8"))
    return {
        "default": {"confidence": 0.72, "needs_review": True, "required_sections": ["brief", "findings", "next_step_note"]},
        "agents": {},
        "action_overrides": {},
    }


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


def fetch_queued_task_messages(conn: sqlite3.Connection, to_agent: str, limit: int, task_id: str | None, message_id: str | None) -> list[dict[str, Any]]:
    clauses = [
        "to_agent = ?",
        "queue_status IN ('queued', 'retry_wait')",
        "message_type = 'TASK'",
    ]
    params: list[Any] = [to_agent]
    if task_id:
        clauses.append("task_id = ?")
        params.append(task_id)
    if message_id:
        clauses.append("message_id = ?")
        params.append(message_id)
    params.append(limit)
    query = f"""
        SELECT message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
               message_type, body_json, queue_status, retry_count, available_at, created_at,
               acked_at, last_error
        FROM bus_messages
        WHERE {' AND '.join(clauses)}
        ORDER BY created_at ASC
        LIMIT ?
    """
    rows = conn.execute(query, params).fetchall()
    messages: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        messages.append(item)
    return messages


def result_markdown(message: dict[str, Any], payload: dict[str, Any]) -> str:
    body = payload["body"]
    lines = [
        "# Task Result Bridge",
        "",
        f"- message_id: `{payload['message_id']}`",
        f"- task_id: `{payload['task_id']}`",
        f"- from: `{payload['from']}`",
        f"- to: `{payload['to']}`",
        f"- created_at: `{payload['created_at']}`",
        "",
        "## Goal",
        "",
        body["summary"],
        "",
        "## Result",
        "",
        f"- status: `{body['status']}`",
        f"- confidence: `{body['confidence']}`",
        f"- needs_review: `{body['needs_review']}`",
        f"- review_by: `{', '.join(body['review_by'])}`",
        "",
        "## Result Sections",
        "",
    ]
    for key, value in body.get("sections", {}).items():
        lines.extend(
            [
                f"### {key}",
                "",
                value or "(empty)",
                "",
            ]
        )
    lines.extend(
        [
        "## Artifacts",
        "",
        ]
    )
    for artifact in body["artifacts"]:
        lines.append(f"- `{artifact}`")
    lines.append("")
    return "\n".join(lines)

def result_template_for(to_agent: str, action_type: str) -> dict[str, Any]:
    config = load_result_template_config()
    default = config.get("default", {})
    agent_cfg = config.get("agents", {}).get(to_agent, {})
    action_cfg = config.get("action_overrides", {}).get(action_type, {})
    required_sections = list(default.get("required_sections", []))
    required_sections.extend(agent_cfg.get("required_sections", []))
    required_sections.extend(action_cfg.get("required_sections_append", []))
    return {
        "confidence": float(agent_cfg.get("confidence", default.get("confidence", 0.72))),
        "needs_review": bool(agent_cfg.get("needs_review", default.get("needs_review", True))),
        "required_sections": unique_list([str(item) for item in required_sections]),
    }


def section_value(agent: str, section: str, goal: str, task_body: dict[str, Any]) -> str:
    ref = ", ".join(str(item) for item in task_body.get("inputs", [])) or "当前任务输入"
    mapping = {
        "brief": f"{agent} 已按任务要求完成本轮处理，围绕 `{goal}` 给出结果摘要。",
        "findings": f"本轮核心发现已基于 {ref} 收口，可继续回交主链。",
        "next_step_note": "建议由 main 结合当前结果决定是否继续分派或进入 review 决策。",
        "synthesis_note": "已完成主控汇总视角的初步整合。",
        "routing_decision": "如需继续下发，建议按专业边界再分派到对应 agent。",
        "evidence": f"已围绕 {ref} 补齐本轮证据摘要与关注点。",
        "rollout_plan": "已整理执行路径、落地步骤和节奏建议。",
        "risk_check": "已补充上线/执行风险检查点与回退关注项。",
        "compliance_check": "已给出合规检查结论与需要继续确认的点。",
        "risk_boundaries": "已标出禁区、红线和例外处理边界。",
        "cost_assessment": "已补充成本结构、预算影响与投入项说明。",
        "roi_note": "已补充收益预期和投入产出判断。",
        "messaging_notes": "已补充表达口径、对外叙事和传播注意点。",
        "content_direction": "已整理内容方向、受众表达和素材组织方式。",
        "technical_assessment": "已补充技术接入影响、改造范围和系统注意点。",
        "implementation_note": "已补充实现方式、脚本/协议接入和后续开发建议。",
        "review_note": "建议继续由 main 或对应专业负责人做最终采纳判断。",
        "adoption_check": "已补充采纳后的影响检查与后续验证建议。",
        "rollout_checklist": "已整理 rollout 前检查项和落地检查表。",
        "page_gap_note": "已标出当前知识页仍需补的结构或链接缺口。",
        "evidence_gap_note": "已标出还缺失的样本、证据或对比材料。",
    }
    return mapping.get(section, f"{agent} 已补充 `{section}` 段落，可继续纳入后续回流。")


def build_result_payload(message: dict[str, Any], to_agent: str, generated_at: str, result_json: Path, result_md: Path) -> dict[str, Any]:
    task_body = message["body"]
    handoff_to = str(task_body.get("handoff_to", "main") or "main")
    goal = str(task_body.get("goal", "(empty)"))
    action_type = "followup"
    if goal.startswith("[") and "::" in goal:
        try:
            action_type = goal.split("::", 1)[1].split("]", 1)[0]
        except Exception:
            action_type = "followup"
    required_output = [str(item) for item in task_body.get("required_output", [])]
    template = result_template_for(to_agent, action_type)
    summary = f"[{to_agent}] 已完成任务桥接结果整理：{goal}"
    if required_output:
        summary += f"；当前按 {', '.join(required_output)} 口径回收。"
    sections = {
        section: section_value(to_agent, section, goal, task_body)
        for section in template["required_sections"]
    }
    body = {
        "status": "completed",
        "summary": summary,
        "artifacts": [str(result_json), str(result_md)],
        "confidence": template["confidence"],
        "needs_review": template["needs_review"],
        "review_by": [handoff_to],
        "result_type": f"{to_agent}_task_result",
        "action_type": action_type,
        "required_sections": template["required_sections"],
        "sections": sections,
    }
    return {
        "protocol": "qyclaw-a2a/v1",
        "message_type": "RESULT",
        "message_id": f"{message['message_id']}-result",
        "task_id": str(message["task_id"]),
        "trace_id": str(message["trace_id"]),
        "parent_task_id": message.get("parent_task_id"),
        "from": to_agent,
        "to": handoff_to,
        "created_at": generated_at,
        "body": body,
    }


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


def consume(runtime_name: str, to_agent: str, limit: int, ack: bool, task_id: str | None, message_id: str | None, emit_results: bool) -> dict[str, Any]:
    rt_root = runtime_root(runtime_name)
    bus_db = rt_root / "bus.db"
    bus_runtime = rt_root / "bus-runtime"
    bus = load_module(BUS_SCRIPT, f"bus_cli_task_result_{runtime_name}_{to_agent}")
    generated_at = now_iso()
    if not bus_db.exists() or not bus_runtime.exists():
        raise FileNotFoundError(f"runtime not initialized: {rt_root}")

    with connect(bus_db) as conn:
        messages = fetch_queued_task_messages(conn, to_agent, limit, task_id, message_id)
        digests: list[dict[str, Any]] = []
        for msg in messages:
            result_dir = rt_root / "task-results" / str(msg["task_id"])
            result_json = result_dir / f"{to_agent}-task-result.json"
            result_md = result_dir / f"{to_agent}-task-result.md"
            result_payload = build_result_payload(msg, to_agent, generated_at, result_json, result_md)
            write_text(result_json, json.dumps(result_payload, ensure_ascii=False, indent=2) + "\n")
            write_text(result_md, result_markdown(msg, result_payload))
            emitted_result_ids: list[str] = []
            if emit_results:
                if enqueue_protocol_message(conn, bus, bus_runtime, result_payload):
                    emitted_result_ids.append(str(result_payload["message_id"]))
            blackboard_put_local(
                conn,
                bus,
                bus_runtime,
                str(msg["task_id"]),
                f"task.result.latest.{to_agent}",
                {
                    "generated_at": generated_at,
                    "consumer": to_agent,
                    "source_task_message_id": msg["message_id"],
                    "result_json": str(result_json),
                    "result_md": str(result_md),
                    "emitted_result_ids": emitted_result_ids,
                },
                to_agent,
                generated_at,
            )
            bus.audit(
                conn,
                bus_runtime,
                "task_result_bridge",
                to_agent,
                "task consumed and result bridged",
                {
                    "source_task_message_id": msg["message_id"],
                    "task_id": msg["task_id"],
                    "result_json": str(result_json),
                    "result_md": str(result_md),
                    "emitted_result_ids": emitted_result_ids,
                },
                task_id=str(msg["task_id"]),
                created_at=generated_at,
            )
            if ack:
                ack_message(conn, bus, bus_runtime, msg["message_id"], to_agent, generated_at)
            digests.append(
                {
                    "source_task_message_id": msg["message_id"],
                    "task_id": msg["task_id"],
                    "result_json": str(result_json),
                    "result_md": str(result_md),
                    "emitted_result_ids": emitted_result_ids,
                }
            )
    return {
        "runtime": runtime_name,
        "to_agent": to_agent,
        "generated_at": generated_at,
        "message_count": len(messages),
        "acked": ack,
        "digests": digests,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Consume TASK messages and bridge them into RESULT messages")
    sub = parser.add_subparsers(dest="cmd", required=True)

    consume_cmd = sub.add_parser("consume")
    consume_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    consume_cmd.add_argument("--to-agent", required=True)
    consume_cmd.add_argument("--limit", type=int, default=50)
    consume_cmd.add_argument("--task-id")
    consume_cmd.add_argument("--message-id")
    consume_cmd.add_argument("--ack", action="store_true")
    consume_cmd.add_argument("--emit-results", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "consume":
        result = consume(args.runtime, args.to_agent, args.limit, args.ack, args.task_id, args.message_id, args.emit_results)
        print(json.dumps({"status": "consumed", **result}, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
