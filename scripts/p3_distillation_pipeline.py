#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".openclaw"
WORKSPACE_ROOT = ROOT / "workspace"
RUNTIME_BASE = WORKSPACE_ROOT / "integration" / "claudecodex" / "runtime"
KNOWLEDGE_CONSUMER = WORKSPACE_ROOT / "scripts" / "main_knowledge_message_consumer.py"
TASK_RESULT_BRIDGE = WORKSPACE_ROOT / "scripts" / "agent_task_result_bridge.py"
RESULT_FEEDBACK_CONSUMER = WORKSPACE_ROOT / "scripts" / "main_result_feedback_consumer.py"
PROTOCOL_VALIDATOR = WORKSPACE_ROOT / "integration" / "claudecodex" / "protocols" / "scripts" / "validate_openclaw_a2a.py"
SUBSCRIPTION_CONFIG = WORKSPACE_ROOT / "knowledge" / "schemas" / "agent-knowledge-subscriptions.v1.json"
DEFAULT_TARGET_AGENTS = ["dev", "content", "ops", "law", "finance", "research"]


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def runtime_root(runtime_name: str) -> Path:
    return RUNTIME_BASE / runtime_name


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        name = item.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def load_target_agents(explicit_agents: list[str]) -> list[str]:
    if explicit_agents:
        return unique(explicit_agents)
    if not SUBSCRIPTION_CONFIG.exists():
        return list(DEFAULT_TARGET_AGENTS)
    data = json.loads(SUBSCRIPTION_CONFIG.read_text(encoding="utf-8"))
    action_routes = data.get("action_routes", {}) or {}
    candidates: list[str] = []
    for values in action_routes.values():
        if isinstance(values, list):
            candidates.extend([str(item) for item in values])
    agents = [item for item in unique(candidates) if item != "main"]
    return agents or list(DEFAULT_TARGET_AGENTS)


def parse_json_from_output(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
        return None
    except json.JSONDecodeError:
        return None


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def run_cmd(cmd: list[str], env: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
    finished = time.time()
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    return {
        "cmd": cmd,
        "exit_code": proc.returncode,
        "duration_seconds": round(finished - started, 3),
        "stdout_tail": "\n".join(stdout.splitlines()[-80:]) if stdout else "",
        "stderr_tail": "\n".join(stderr.splitlines()[-80:]) if stderr else "",
        "json": parse_json_from_output(stdout),
    }


def protocol_delta_audit(runtime_name: str, started_at: str) -> dict[str, Any]:
    rt_root = runtime_root(runtime_name)
    bus_db = rt_root / "bus.db"
    validator = load_module(PROTOCOL_VALIDATOR, "validate_openclaw_a2a_for_p3")
    invalid: list[dict[str, Any]] = []
    checked = 0
    started = time.time()
    query = """
        SELECT
          message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
          message_type, body_json, created_at
        FROM bus_messages
        WHERE created_at >= ?
        ORDER BY created_at ASC, message_id ASC
    """
    with connect(bus_db) as conn:
        rows = conn.execute(query, (started_at,)).fetchall()
    for row in rows:
        payload = {
            "protocol": "openclaw-a2a/v1",
            "message_type": row["message_type"],
            "message_id": row["message_id"],
            "task_id": row["task_id"],
            "trace_id": row["trace_id"],
            "parent_task_id": row["parent_task_id"],
            "from": row["from_agent"],
            "to": row["to_agent"],
            "created_at": row["created_at"],
            "body": json.loads(row["body_json"]),
        }
        checked += 1
        try:
            validator.validate_message(payload)
        except Exception as exc:
            invalid.append(
                {
                    "message_id": row["message_id"],
                    "message_type": row["message_type"],
                    "error": str(exc),
                }
            )
    finished = time.time()
    return {
        "mode": "delta_schema_audit",
        "runtime": runtime_name,
        "started_at": started_at,
        "checked": checked,
        "invalid_count": len(invalid),
        "invalid_samples": invalid[:50],
        "duration_seconds": round(finished - started, 3),
        "exit_code": 0 if not invalid else 1,
    }


def step_markdown(step: dict[str, Any]) -> str:
    status = "OK" if int(step["exit_code"]) == 0 else "FAIL"
    return f"| {step['name']} | {status} | `{step['duration_seconds']}`s |"


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# P3 Distillation Pipeline Report",
        "",
        f"- runtime: `{report['runtime']}`",
        f"- started_at: `{report['started_at']}`",
        f"- finished_at: `{report['finished_at']}`",
        f"- success: `{report['success']}`",
        f"- agents: `{', '.join(report['agents'])}`",
        "",
        "## Steps",
        "",
        "| Step | Status | Duration |",
        "| --- | --- | --- |",
    ]
    for step in report.get("steps", []):
        lines.append(step_markdown(step))
    audit = report.get("audit")
    if audit:
        lines.extend(
            [
                "",
                "## Protocol Audit",
                "",
                f"- exit_code: `{audit['exit_code']}`",
                f"- duration_seconds: `{audit['duration_seconds']}`",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def run_pipeline(runtime_name: str, limit: int, explicit_agents: list[str], run_audit: bool) -> dict[str, Any]:
    rt_root = runtime_root(runtime_name)
    if not rt_root.exists():
        raise FileNotFoundError(f"runtime not found: {rt_root}")

    started_at = now_iso()
    started_ts = time.time()
    agents = load_target_agents(explicit_agents)
    steps: list[dict[str, Any]] = []

    main_step = run_cmd(
        [
            "python3",
            str(KNOWLEDGE_CONSUMER),
            "consume",
            "--runtime",
            runtime_name,
            "--to-agent",
            "main",
            "--limit",
            str(limit),
            "--ack",
            "--emit-actions",
        ]
    )
    steps.append({"name": "main.consume_knowledge_emit_actions", **main_step})

    for agent in agents:
        task_step = run_cmd(
            [
                "python3",
                str(KNOWLEDGE_CONSUMER),
                "consume",
                "--runtime",
                runtime_name,
                "--to-agent",
                agent,
                "--limit",
                str(limit),
                "--ack",
                "--emit-tasks",
            ]
        )
        steps.append({"name": f"{agent}.consume_knowledge_emit_tasks", **task_step})

    for agent in agents:
        result_step = run_cmd(
            [
                "python3",
                str(TASK_RESULT_BRIDGE),
                "consume",
                "--runtime",
                runtime_name,
                "--to-agent",
                agent,
                "--limit",
                str(limit),
                "--ack",
                "--emit-results",
            ]
        )
        steps.append({"name": f"{agent}.consume_tasks_emit_results", **result_step})

    feedback_step = run_cmd(
        [
            "python3",
            str(RESULT_FEEDBACK_CONSUMER),
            "consume",
            "--runtime",
            runtime_name,
            "--to-agent",
            "main",
            "--limit",
            str(limit),
            "--ack",
            "--emit-knowledge",
        ]
    )
    steps.append({"name": "main.consume_results_emit_knowledge", **feedback_step})

    audit_result: dict[str, Any] | None = None
    if run_audit:
        audit_result = protocol_delta_audit(runtime_name, started_at)

    success = all(int(step["exit_code"]) == 0 for step in steps)
    if audit_result:
        success = success and int(audit_result["exit_code"]) == 0

    finished_at = now_iso()
    finished_ts = time.time()
    report = {
        "mode": "p3_distillation_pipeline",
        "runtime": runtime_name,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": round(finished_ts - started_ts, 3),
        "limit": limit,
        "agents": agents,
        "steps": steps,
        "audit": audit_result,
        "success": success,
    }
    out_dir = rt_root / "p3-distillation" / "reports"
    ts_key = dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"p3-distillation-{ts_key}.json"
    md_path = out_dir / f"p3-distillation-{ts_key}.md"
    write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(md_path, report_markdown(report))
    report["report_json"] = str(json_path)
    report["report_md"] = str(md_path)
    return report


def status(runtime_name: str, limit: int) -> dict[str, Any]:
    report_dir = runtime_root(runtime_name) / "p3-distillation" / "reports"
    files = sorted(report_dir.glob("p3-distillation-*.json")) if report_dir.exists() else []
    latest = files[-limit:] if limit > 0 else files
    summaries: list[dict[str, Any]] = []
    for path in latest:
        data = json.loads(path.read_text(encoding="utf-8"))
        summaries.append(
            {
                "file": str(path),
                "finished_at": data.get("finished_at"),
                "success": bool(data.get("success")),
                "duration_seconds": data.get("duration_seconds"),
                "step_count": len(data.get("steps", [])),
                "agent_count": len(data.get("agents", [])),
            }
        )
    return {
        "mode": "status",
        "runtime": runtime_name,
        "report_dir": str(report_dir),
        "count": len(summaries),
        "items": summaries,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P3 distillation pipeline orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_cmd_parser = sub.add_parser("run")
    run_cmd_parser.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    run_cmd_parser.add_argument("--limit", type=int, default=80)
    run_cmd_parser.add_argument("--agent", action="append", default=[])
    run_cmd_parser.add_argument("--no-audit", action="store_true")

    status_parser = sub.add_parser("status")
    status_parser.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    status_parser.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "run":
        report = run_pipeline(
            runtime_name=args.runtime,
            limit=args.limit,
            explicit_agents=list(args.agent),
            run_audit=not args.no_audit,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["success"] else 1
    if args.cmd == "status":
        print(json.dumps(status(args.runtime, args.limit), ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
