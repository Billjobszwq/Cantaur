#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


OPENCLAW_ROOT = Path.home() / ".openclaw"
INTEGRATION_ROOT = OPENCLAW_ROOT / "workspace" / "integration" / "claudecodex"
RUNTIME_BASE = INTEGRATION_ROOT / "runtime"
BUS_SCRIPT = INTEGRATION_ROOT / "bus" / "scripts" / "bus_cli.py"
MEMORY_FUSION_SCRIPT = INTEGRATION_ROOT / "memory-fusion" / "scripts" / "memory_fusion_cli.py"
KNOWLEDGE_SCRIPT = OPENCLAW_ROOT / "workspace" / "scripts" / "knowledge_base.py"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def compile_knowledge(memory_output_dir: Path, project_title: str) -> tuple[str | None, str | None]:
    if not memory_output_dir.exists():
        return None, "memory output directory missing"
    try:
        payload = run_json(
            [
                "python3",
                str(KNOWLEDGE_SCRIPT),
                "compile-fusion",
                str(memory_output_dir),
                "--project",
                project_title,
            ]
        )
    except Exception as exc:
        return None, str(exc)
    return str(payload.get("page", "")) or None, None


def fetch_root_bundle(task_db: Path, root_task_id: str) -> tuple[dict, list[dict]]:
    with connect(task_db) as conn:
        root = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (root_task_id,)).fetchone()
        if root is None:
            raise ValueError(f"root task not found: {root_task_id}")
        subtasks = conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY task_id",
            (root_task_id,),
        ).fetchall()
    return dict(root), [dict(row) for row in subtasks]


def fetch_pending_results(bus_db: Path, root_task_id: str) -> list[dict]:
    with connect(bus_db) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM bus_messages
            WHERE parent_task_id = ?
              AND to_agent = 'main'
              AND message_type = 'RESULT'
            ORDER BY created_at ASC
            """,
            (root_task_id,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        result.append(item)
    return result


def summarize_root_status(subtasks: list[dict]) -> str:
    statuses = [item["status"] for item in subtasks]
    if statuses and all(status == "completed" for status in statuses):
        return "completed"
    if any(status in {"claimed", "in_progress", "completed"} for status in statuses):
        return "in_progress"
    if any(status in {"failed", "timed_out", "blocked"} for status in statuses):
        return "blocked"
    return "queued"


def add_artifact(task_db: Path, task_id: str, artifact_type: str, artifact_path: Path, produced_by: str) -> None:
    with connect(task_db) as conn:
        conn.execute(
            """
            INSERT INTO task_artifacts (task_id, artifact_type, artifact_path, produced_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id, artifact_type, str(artifact_path), produced_by, now_iso()),
        )


def add_review(task_db: Path, task_id: str, reviewer: str, scope: str, decision: str, note: str) -> None:
    with connect(task_db) as conn:
        conn.execute(
            """
            INSERT INTO task_reviews (task_id, reviewer, scope, decision, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id, reviewer, scope, decision, note, now_iso()),
        )


def add_event(task_db: Path, task_id: str, state: str, progress: float, note: str, created_by: str) -> None:
    ts = now_iso()
    with connect(task_db) as conn:
        conn.execute(
            """
            INSERT INTO task_events (task_id, state, progress, note, blockers_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (task_id, state, float(progress), note, "[]", created_by, ts),
        )
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (state, ts, task_id),
        )


def ack_result_messages(bus_db: Path, bus_runtime: Path, messages: list[dict]) -> list[str]:
    bus = load_module(BUS_SCRIPT, "shadow_main_bus_ack")
    acked = []
    for item in messages:
        if item["queue_status"] in {"queued", "retry_wait"}:
            bus.ack(bus_db, bus_runtime, item["message_id"], "main", now_iso())
            acked.append(item["message_id"])
    return acked


def blackboard_put(bus_db: Path, bus_runtime: Path, root_task_id: str, synthesis: dict) -> None:
    bus = load_module(BUS_SCRIPT, "shadow_main_bus_blackboard")
    payload = {
        "task_id": root_task_id,
        "entry_key": "main.synthesis",
        "entry_value": synthesis,
        "updated_by": "main",
        "updated_at": now_iso(),
    }
    temp_path = bus_runtime / f"{root_task_id}-main-synthesis.json"
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        bus.blackboard_put(bus_db, bus_runtime, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def render_markdown(root: dict, subtasks: list[dict], result_messages: list[dict], note: str, root_status: str) -> str:
    lines = [
        f"# {root['title']} - main 融合汇总",
        "",
        f"- `task_id`: `{root['task_id']}`",
        f"- `trace_id`: `{root['trace_id']}`",
        f"- `status`: `{root_status}`",
        f"- `generated_at`: `{now_iso()}`",
        "",
        "## Goal",
        root["goal"],
        "",
        "## Coordination Note",
        note,
        "",
        "## Subtask Status",
    ]
    for item in subtasks:
        lines.append(f"- `{item['owner_agent']}`: `{item['status']}`")
    lines.extend(["", "## Returned Results"])
    for item in result_messages:
        lines.append(f"- `{item['from_agent']}`: {item['body'].get('summary', '')}")
    lines.append("")
    return "\n".join(lines)


def refresh_memory(runtime_root: Path, root_task_id: str) -> Path:
    memory_fusion = load_module(MEMORY_FUSION_SCRIPT, "shadow_main_memory_fusion")
    out_dir = memory_fusion.summarize(runtime_root / "task-board.db", runtime_root / "bus.db", root_task_id)
    runtime_memory_dir = runtime_root / "memory-fusion" / root_task_id
    if runtime_memory_dir.exists():
        import shutil
        shutil.rmtree(runtime_memory_dir)
    import shutil
    shutil.copytree(out_dir, runtime_memory_dir)
    return runtime_memory_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="summarize fusion worker results from main perspective")
    parser.add_argument("--runtime", default="shadow-live")
    parser.add_argument("--root-task-id", required=True)
    parser.add_argument("--note", default="main 已读取影子 worker 结果，并生成统一汇总。")
    parser.add_argument("--skip-memory-refresh", action="store_true")
    args = parser.parse_args()

    runtime_root = RUNTIME_BASE / args.runtime
    task_db = runtime_root / "task-board.db"
    bus_db = runtime_root / "bus.db"
    bus_runtime = runtime_root / "bus-runtime"

    root, subtasks = fetch_root_bundle(task_db, args.root_task_id)
    result_messages = fetch_pending_results(bus_db, args.root_task_id)
    root_status = summarize_root_status(subtasks)

    artifacts_root = runtime_root / "artifacts" / "main" / args.root_task_id
    artifacts_root.mkdir(parents=True, exist_ok=True)
    summary_md = artifacts_root / "shadow-synthesis.md"
    summary_json = artifacts_root / "shadow-synthesis.json"

    synthesis_payload = {
        "root_task_id": args.root_task_id,
        "root_status": root_status,
        "subtask_status": [{"agent": item["owner_agent"], "status": item["status"], "task_id": item["task_id"]} for item in subtasks],
        "results": [
            {
                "message_id": item["message_id"],
                "from_agent": item["from_agent"],
                "summary": item["body"].get("summary", ""),
                "artifacts": item["body"].get("artifacts", []),
                "confidence": item["body"].get("confidence"),
                "needs_review": item["body"].get("needs_review"),
            }
            for item in result_messages
        ],
        "note": args.note,
        "generated_at": now_iso(),
        "mode": "fusion-live" if args.runtime == "live" else "shadow",
    }

    summary_md.write_text(render_markdown(root, subtasks, result_messages, args.note, root_status), encoding="utf-8")
    summary_json.write_text(json.dumps(synthesis_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    add_artifact(task_db, args.root_task_id, "shadow_main_synthesis_md", summary_md, "main")
    add_artifact(task_db, args.root_task_id, "shadow_main_synthesis_json", summary_json, "main")
    add_review(
        task_db,
        args.root_task_id,
        "main",
        "root_synthesis",
        "shadow_synthesized",
        args.note,
    )
    add_event(task_db, args.root_task_id, root_status, 1.0 if root_status == "completed" else 0.7, "main 已完成融合汇总", "main")
    acked_messages = ack_result_messages(bus_db, bus_runtime, result_messages)
    blackboard_put(bus_db, bus_runtime, args.root_task_id, synthesis_payload)

    memory_output = None
    knowledge_output = None
    knowledge_error = None
    if not args.skip_memory_refresh:
        memory_output = str(refresh_memory(runtime_root, args.root_task_id))
        knowledge_output, knowledge_error = compile_knowledge(Path(memory_output), root["title"])

    result = {
        "runtime": args.runtime,
        "root_task_id": args.root_task_id,
        "root_status": root_status,
        "result_message_count": len(result_messages),
        "acked_messages": acked_messages,
        "artifacts": [str(summary_md), str(summary_json)],
        "memory_output": memory_output,
        "knowledge_output": knowledge_output,
        "knowledge_error": knowledge_error,
        "mode": "fusion-live" if args.runtime == "live" else "shadow",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
