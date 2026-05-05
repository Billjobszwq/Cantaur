#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw"
WORKSPACE_ROOT = ROOT / "workspace"
RUNTIME_BASE = WORKSPACE_ROOT / "integration" / "claudecodex" / "runtime"
BUS_SCRIPT = WORKSPACE_ROOT / "integration" / "claudecodex" / "bus" / "scripts" / "bus_cli.py"
KNOWLEDGE_BASE_SCRIPT = WORKSPACE_ROOT / "scripts" / "knowledge_base.py"
CONVERGENCE_POLICY_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "knowledge-convergence-policy.v1.json"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


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


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def convergence_output_paths(rt_root: Path, task_id: str, actor: str) -> tuple[Path, Path]:
    out_dir = rt_root / "knowledge-convergence" / task_id
    return out_dir / f"{actor}-convergence.json", out_dir / f"{actor}-convergence.md"


def convergence_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Result Feedback Convergence Summary",
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
        "## Suggested Human Steps",
        "",
    ]
    steps = summary.get("suggested_human_steps", [])
    if not steps:
        lines.extend(["(none)", ""])
    else:
        for step in steps:
            lines.append(f"- {step}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_convergence_summary(task_id: str, runtime_name: str, actor: str, generated_at: str, convergence_reason: str, page_path: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "runtime": runtime_name,
        "actor": actor,
        "generated_at": generated_at,
        "mode": "human_review_only",
        "reason": convergence_reason or "结果回流已进入人工收敛。",
        "page_path": page_path,
        "suggested_human_steps": [
            "查看 result feedback 对应知识页，确认这次结果应进入哪类长期沉淀。",
            "必要时手动创建或更新 review-queue 文档，再做人为采纳判断。",
            "如需继续推进，手动分派下一轮任务，而不是继续自动扩 result-derived candidate。",
        ],
    }


def load_convergence_policy() -> dict[str, Any]:
    if CONVERGENCE_POLICY_CONFIG.exists():
        return json.loads(CONVERGENCE_POLICY_CONFIG.read_text(encoding="utf-8"))
    return {
        "max_result_feedback_task_depth": 2,
        "human_review_only_at_depth": 2,
    }


def task_hop_depth(task_id: str) -> int:
    return str(task_id).count("-task-")


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


def fetch_queued_result_messages(conn: sqlite3.Connection, to_agent: str, limit: int, task_id: str | None, message_id: str | None) -> list[dict[str, Any]]:
    clauses = [
        "to_agent = ?",
        "queue_status IN ('queued', 'retry_wait')",
        "message_type = 'RESULT'",
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


def result_feedback_markdown(message: dict[str, Any]) -> str:
    body = message["body"]
    lines = [
        f"# {message['task_id']} {message['from_agent']} result feedback",
        "",
        f"- result_message_id: `{message['message_id']}`",
        f"- task_id: `{message['task_id']}`",
        f"- parent_task_id: `{message['parent_task_id'] or ''}`",
        f"- source_agent: `{message['from_agent']}`",
        f"- created_at: `{message['created_at']}`",
        "",
        "## Summary",
        "",
        body.get("summary", "(empty)"),
        "",
        "## Review",
        "",
        f"- needs_review: `{body.get('needs_review')}`",
        f"- review_by: `{', '.join(body.get('review_by', []))}`",
        f"- confidence: `{body.get('confidence')}`",
        "",
        "## Artifacts",
        "",
    ]
    for artifact in body.get("artifacts", []):
        lines.append(f"- `{artifact}`")
    lines.append("")
    return "\n".join(lines)


def infer_project_name(message: dict[str, Any]) -> str:
    task_id = str(message["task_id"])
    parent_task_id = str(message.get("parent_task_id") or "")
    base = parent_task_id or task_id
    if "-中国示例行业ai巡检系统" in base:
        return "示例行业巡检系统正式融合版"
    return base


def ingest_feedback_source(knowledge_base, message: dict[str, Any], rt_root: Path) -> Path:
    source_dir = rt_root / "task-result-feedback" / str(message["task_id"])
    source_file = source_dir / f"{message['from_agent']}-result-feedback.md"
    write_text(source_file, result_feedback_markdown(message) + "\n")
    title = f"{message['task_id']} {message['from_agent']} result feedback"
    return knowledge_base.ingest_source(
        source_file,
        title,
        "task_result_feedback",
        infer_project_name(message),
        False,
    )


def build_result_review_candidates(message: dict[str, Any]) -> list[dict[str, Any]]:
    body = message["body"]
    if not body.get("needs_review"):
        return []
    agent = str(message["from_agent"])
    result_type = str(body.get("result_type", "") or f"{agent}_task_result")
    action_type = str(body.get("action_type", "") or "followup")
    sections = body.get("sections", {})
    if not isinstance(sections, dict):
        sections = {}
    review_by = [str(item) for item in body.get("review_by", [])]
    review_hint = f"；建议由 {', '.join(review_by)} 复核" if review_by else ""
    candidate_type = "decision"
    title_suffix = "result follow-up"
    section_priority = ["brief", "findings", "next_step_note"]

    if result_type == "law_task_result":
        candidate_type = "rule"
        title_suffix = "compliance boundary follow-up"
        section_priority = ["compliance_check", "risk_boundaries", "review_note", "brief"]
    elif result_type == "finance_task_result":
        candidate_type = "fact" if action_type == "verify_adopted_candidate" else "decision"
        title_suffix = "financial impact follow-up"
        section_priority = ["cost_assessment", "roi_note", "adoption_check", "brief"]
    elif result_type == "content_task_result":
        candidate_type = "preference"
        title_suffix = "messaging follow-up"
        section_priority = ["messaging_notes", "content_direction", "page_gap_note", "brief"]
    elif result_type == "dev_task_result":
        candidate_type = "lesson"
        title_suffix = "technical implementation follow-up"
        section_priority = ["technical_assessment", "implementation_note", "page_gap_note", "brief"]
    elif result_type == "ops_task_result":
        candidate_type = "decision"
        title_suffix = "rollout follow-up"
        section_priority = ["rollout_plan", "risk_check", "rollout_checklist", "brief"]
    elif result_type == "research_task_result":
        candidate_type = "lesson"
        title_suffix = "evidence follow-up"
        section_priority = ["evidence", "findings", "evidence_gap_note", "brief"]
    elif result_type == "main_task_result":
        candidate_type = "decision"
        title_suffix = "synthesis follow-up"
        section_priority = ["synthesis_note", "routing_decision", "brief"]

    section_chunks = [str(sections.get(key, "")).strip() for key in section_priority if str(sections.get(key, "")).strip()]
    section_summary = " ".join(section_chunks[:2]).strip()
    title = f"{message['task_id']} {agent} {title_suffix}"
    summary = section_summary or str(body.get("summary", "(empty)"))
    return [
        {
            "type": candidate_type,
            "title": title,
            "summary": f"{summary}{review_hint}",
            "source_task_id": str(message["task_id"]),
            "source_result_message_id": str(message["message_id"]),
            "source_result_type": result_type,
            "source_action_type": action_type,
        }
    ]


def ensure_result_review_queue_docs(knowledge_base, message: dict[str, Any], page_path: Path, candidates: list[dict[str, Any]]) -> list[str]:
    outputs: list[str] = []
    for idx, candidate in enumerate(candidates, start=1):
        raw_type = str(candidate.get("type", "") or "decision")
        normalized_type = knowledge_base.normalized_candidate_type(raw_type)
        title = str(candidate.get("title", f"{message['task_id']} result candidate {idx}"))
        output = knowledge_base.review_queue_output_path(str(message["task_id"]), idx, title)
        content = knowledge_base.review_queue_markdown(
            title,
            str(candidate.get("summary", "")),
            str(candidate.get("source_task_id") or message["task_id"]),
            normalized_type,
            knowledge_base.candidate_target(raw_type),
            str(message["message_id"]),
            str(page_path),
        )
        write_text(output, content + "\n")
        outputs.append(str(output))
    return outputs


def emit_result_feedback_candidate_events(knowledge_base, conn: sqlite3.Connection, bus, bus_runtime: Path, message: dict[str, Any], page_path: Path, candidates: list[dict[str, Any]], generated_at: str) -> list[str]:
    emitted: list[str] = []
    for idx, candidate in enumerate(candidates, start=1):
        raw_type = str(candidate.get("type", "") or "decision")
        normalized_type = knowledge_base.normalized_candidate_type(raw_type)
        review_queue_ref = knowledge_base.review_queue_output_path(str(message["task_id"]), idx, str(candidate.get("title", f"{message['task_id']} result candidate {idx}")))
        payload = {
            "protocol": "openclaw-a2a/v1",
            "message_type": "KNOWLEDGE_CANDIDATE_CREATED",
            "message_id": f"{message['task_id']}-{knowledge_base.safe_slug(message['from_agent'], 16)}-result-feedback-candidate-{idx:03d}",
            "task_id": str(message["task_id"]),
            "trace_id": str(message["trace_id"]),
            "parent_task_id": message.get("parent_task_id"),
            "from": "result-feedback-compiler",
            "to": "main",
            "created_at": generated_at,
            "body": {
                "knowledge_scope": knowledge_base.candidate_scope(raw_type),
                "candidate_type": normalized_type,
                "title": str(candidate.get("title", f"{message['task_id']} result candidate {idx}")),
                "summary": str(candidate.get("summary", "")),
                "evidence": f"derived from RESULT {message['message_id']}",
                "proposed_target": knowledge_base.candidate_target(raw_type),
                "source_refs": [str(message["message_id"]), str(page_path)],
                "related_pages": [str(page_path)],
                "review_queue_ref": str(review_queue_ref),
            },
        }
        bus.audit(
            conn,
            bus_runtime,
            "knowledge_candidate_created",
            "result-feedback-compiler",
            "result feedback candidate created",
            payload,
            message_id=payload["message_id"],
            task_id=str(message["task_id"]),
            created_at=generated_at,
        )
        emitted.extend(knowledge_base.enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, payload))
    return emitted


def refresh_review_report(knowledge_base, limit: int = 50) -> Path:
    month_key = dt.date.today().strftime("%Y-%m")
    return knowledge_base.build_review_report(month_key, limit)


def emit_feedback_knowledge_page(knowledge_base, conn: sqlite3.Connection, bus, bus_runtime: Path, message: dict[str, Any], page_path: Path, generated_at: str) -> list[str]:
    title = f"ingested task result feedback from {message['from_agent']}"
    payload = {
        "protocol": "openclaw-a2a/v1",
        "message_type": "KNOWLEDGE_PAGE_UPDATED",
        "message_id": f"{message['task_id']}-{knowledge_base.safe_slug(message['from_agent'], 16)}-result-feedback-page-updated",
        "task_id": str(message["task_id"]),
        "trace_id": str(message["trace_id"]),
        "parent_task_id": message.get("parent_task_id"),
        "from": "result-feedback-compiler",
        "to": "main",
        "created_at": generated_at,
        "body": {
            "page_type": "source",
            "page_path": str(page_path),
            "operation": "update",
            "summary": title,
            "source_refs": [str(item) for item in message["body"].get("artifacts", [])],
            "related_pages": [str(page_path)],
            "compiler": "main_result_feedback_consumer.py",
        },
    }
    bus.audit(
        conn,
        bus_runtime,
        "knowledge_page_updated",
        "result-feedback-compiler",
        "task result feedback page updated",
        payload,
        message_id=payload["message_id"],
        task_id=str(message["task_id"]),
        created_at=generated_at,
    )
    return knowledge_base.enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, payload)


def consume(runtime_name: str, to_agent: str, limit: int, ack: bool, task_id: str | None, message_id: str | None, emit_knowledge: bool) -> dict[str, Any]:
    rt_root = runtime_root(runtime_name)
    bus_db = rt_root / "bus.db"
    bus_runtime = rt_root / "bus-runtime"
    bus = load_module(BUS_SCRIPT, f"bus_cli_result_feedback_{runtime_name}_{to_agent}")
    knowledge_base = load_module(KNOWLEDGE_BASE_SCRIPT, "knowledge_base_for_result_feedback")
    generated_at = now_iso()
    if not bus_db.exists() or not bus_runtime.exists():
        raise FileNotFoundError(f"runtime not initialized: {rt_root}")

    with connect(bus_db) as conn:
        messages = fetch_queued_result_messages(conn, to_agent, limit, task_id, message_id)
        digests: list[dict[str, Any]] = []
        for msg in messages:
            page_path = ingest_feedback_source(knowledge_base, msg, rt_root)
            emitted_message_ids: list[str] = []
            emitted_candidate_ids: list[str] = []
            review_queue_docs: list[str] = []
            review_report_path: str | None = None
            convergence_mode = "auto_continue"
            convergence_reason = ""
            if emit_knowledge:
                emitted_message_ids = emit_feedback_knowledge_page(knowledge_base, conn, bus, bus_runtime, msg, page_path, generated_at)
                policy = load_convergence_policy()
                depth = task_hop_depth(str(msg["task_id"]))
                max_depth = int(policy.get("max_result_feedback_task_depth", 2))
                terminal_action_types = {str(item) for item in policy.get("terminal_action_types_after_result", [])}
                result_action_type = str(msg["body"].get("action_type", "") or "")
                if result_action_type in terminal_action_types:
                    convergence_mode = "human_review_only"
                    convergence_reason = f"结果动作 `{result_action_type}` 已被标记为二轮收敛动作，停止继续自动扩 result-derived candidate。"
                elif depth >= max_depth:
                    convergence_mode = "human_review_only"
                    convergence_reason = f"task depth `{depth}` 已达到 result feedback 阈值 `{max_depth}`，停止继续自动扩 result-derived candidate。"
                else:
                    candidates = build_result_review_candidates(msg)
                    review_queue_docs = ensure_result_review_queue_docs(knowledge_base, msg, page_path, candidates)
                    emitted_candidate_ids = emit_result_feedback_candidate_events(knowledge_base, conn, bus, bus_runtime, msg, page_path, candidates, generated_at)
                    if review_queue_docs:
                        review_report_path = str(refresh_review_report(knowledge_base))
            blackboard_put_local(
                conn,
                bus,
                bus_runtime,
                str(msg["task_id"]),
                f"knowledge.result_feedback.latest.{msg['from_agent']}",
                {
                    "generated_at": generated_at,
                    "source_result_message_id": msg["message_id"],
                    "page_path": str(page_path),
                    "emitted_message_ids": emitted_message_ids,
                    "emitted_candidate_ids": emitted_candidate_ids,
                    "review_queue_docs": review_queue_docs,
                    "review_report_path": review_report_path,
                    "convergence_mode": convergence_mode,
                    "convergence_reason": convergence_reason,
                },
                to_agent,
                generated_at,
            )
            if convergence_mode == "human_review_only":
                convergence_summary = build_convergence_summary(str(msg["task_id"]), runtime_name, to_agent, generated_at, convergence_reason, str(page_path))
                convergence_json, convergence_md = convergence_output_paths(rt_root, str(msg["task_id"]), to_agent)
                write_text(convergence_json, json.dumps(convergence_summary, ensure_ascii=False, indent=2) + "\n")
                write_text(convergence_md, convergence_markdown(convergence_summary))
                blackboard_put_local(
                    conn,
                    bus,
                    bus_runtime,
                    str(msg["task_id"]),
                    "knowledge.convergence.latest",
                    {
                        "generated_at": generated_at,
                        "actor": to_agent,
                        "mode": convergence_summary["mode"],
                        "reason": convergence_summary["reason"],
                        "summary_json": str(convergence_json),
                        "summary_md": str(convergence_md),
                        "page_path": str(page_path),
                    },
                    to_agent,
                    generated_at,
                )
            bus.audit(
                conn,
                bus_runtime,
                "result_feedback_ingested",
                to_agent,
                "result feedback ingested into knowledge root",
                {
                    "source_result_message_id": msg["message_id"],
                    "task_id": msg["task_id"],
                    "page_path": str(page_path),
                    "emitted_message_ids": emitted_message_ids,
                    "emitted_candidate_ids": emitted_candidate_ids,
                    "review_queue_docs": review_queue_docs,
                    "review_report_path": review_report_path,
                    "convergence_mode": convergence_mode,
                    "convergence_reason": convergence_reason,
                },
                task_id=str(msg["task_id"]),
                created_at=generated_at,
            )
            if ack:
                ack_message(conn, bus, bus_runtime, msg["message_id"], to_agent, generated_at)
            digests.append(
                {
                    "source_result_message_id": msg["message_id"],
                    "task_id": msg["task_id"],
                    "page_path": str(page_path),
                    "emitted_message_ids": emitted_message_ids,
                    "emitted_candidate_ids": emitted_candidate_ids,
                    "review_queue_docs": review_queue_docs,
                    "review_report_path": review_report_path,
                    "convergence_mode": convergence_mode,
                    "convergence_reason": convergence_reason,
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
    parser = argparse.ArgumentParser(description="Consume RESULT messages and feed them back into unified knowledge")
    sub = parser.add_subparsers(dest="cmd", required=True)
    consume_cmd = sub.add_parser("consume")
    consume_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    consume_cmd.add_argument("--to-agent", default="main")
    consume_cmd.add_argument("--limit", type=int, default=50)
    consume_cmd.add_argument("--task-id")
    consume_cmd.add_argument("--message-id")
    consume_cmd.add_argument("--ack", action="store_true")
    consume_cmd.add_argument("--emit-knowledge", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "consume":
        result = consume(args.runtime, args.to_agent, args.limit, args.ack, args.task_id, args.message_id, args.emit_knowledge)
        print(json.dumps({"status": "consumed", **result}, ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
