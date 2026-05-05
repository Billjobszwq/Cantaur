#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path


QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / "workspace" / "integration" / "qy_code"
RUNTIME_BASE = INTEGRATION_ROOT / "runtime"
BUS_SCRIPT = INTEGRATION_ROOT / "bus" / "scripts" / "bus_cli.py"
MEMORY_FUSION_SCRIPT = INTEGRATION_ROOT / "memory-fusion" / "scripts" / "memory_fusion_cli.py"
KNOWLEDGE_SCRIPT = QYCLAW_ROOT / "workspace" / "scripts" / "knowledge_base.py"


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


def ensure_task_owned(task_db: Path, task_id: str, agent: str) -> dict:
    with connect(task_db) as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND owner_agent = ?",
            (task_id, agent),
        ).fetchone()
    if row is None:
        raise ValueError(f"task not found for agent {agent}: {task_id}")
    return dict(row)


def pick_next_task(task_db: Path, agent: str) -> str:
    with connect(task_db) as conn:
        row = conn.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE owner_agent = ? AND status IN ('queued', 'claimed', 'in_progress')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (agent,),
        ).fetchone()
    if row is None:
        raise ValueError(f"no pending task for agent: {agent}")
    return str(row["task_id"])


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


def refresh_parent_status(task_db: Path, parent_task_id: str | None) -> None:
    if not parent_task_id:
        return
    with connect(task_db) as conn:
        rows = conn.execute(
            "SELECT status FROM tasks WHERE parent_task_id = ? ORDER BY task_id",
            (parent_task_id,),
        ).fetchall()
        if not rows:
            return
        statuses = [str(row["status"]) for row in rows]
        if all(status == "completed" for status in statuses):
            parent_status = "completed"
        elif any(status in {"claimed", "in_progress", "completed"} for status in statuses):
            parent_status = "in_progress"
        elif any(status in {"failed", "timed_out"} for status in statuses):
            parent_status = "blocked"
        else:
            parent_status = "queued"
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
            (parent_status, now_iso(), parent_task_id),
        )


def find_queued_task_message(bus_db: Path, task_id: str, agent: str) -> str | None:
    with connect(bus_db) as conn:
        row = conn.execute(
            """
            SELECT message_id
            FROM bus_messages
            WHERE task_id = ? AND to_agent = ? AND message_type = 'TASK' AND queue_status IN ('queued', 'retry_wait')
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (task_id, agent),
        ).fetchone()
    return None if row is None else str(row["message_id"])


def enqueue_result(bus_db: Path, bus_runtime: Path, task: dict, agent: str, summary: str, artifact_paths: list[str], review_by: list[str]) -> str:
    bus = load_module(BUS_SCRIPT, "shadow_worker_bus_cli")
    message_id = f"{task['task_id']}-result-{agent}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    payload = {
        "protocol": "qyclaw-a2a/v1",
        "message_type": "RESULT",
        "message_id": message_id,
        "task_id": task["task_id"],
        "trace_id": task["trace_id"],
        "parent_task_id": task["parent_task_id"],
        "from": agent,
        "to": "main",
        "created_at": now_iso(),
        "body": {
            "status": "completed",
            "summary": summary,
            "artifacts": artifact_paths,
            "confidence": 0.76,
            "needs_review": True,
            "review_by": review_by or ["main"],
        },
    }
    temp_path = bus_runtime / f"{message_id}.json"
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        bus.enqueue(bus_db, bus_runtime, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)
    return message_id


def blackboard_put(bus_db: Path, bus_runtime: Path, task_id: str, agent: str, summary: str, artifact_paths: list[str]) -> None:
    bus = load_module(BUS_SCRIPT, "shadow_worker_bus_cli_blackboard")
    payload = {
        "task_id": task_id,
        "entry_key": f"result.{agent}",
        "entry_value": {
            "summary": summary,
            "artifacts": artifact_paths,
            "updated_at": now_iso(),
        },
        "updated_by": agent,
        "updated_at": now_iso(),
    }
    temp_path = bus_runtime / f"{task_id}-{agent}-blackboard.json"
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        bus.blackboard_put(bus_db, bus_runtime, temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def refresh_memory(runtime_root: Path, task_id: str) -> Path:
    memory_fusion = load_module(MEMORY_FUSION_SCRIPT, "shadow_worker_memory_fusion")
    out_dir = memory_fusion.summarize(runtime_root / "task-board.db", runtime_root / "bus.db", task_id)
    runtime_memory_dir = runtime_root / "memory-fusion" / task_id
    if runtime_memory_dir.exists():
        import shutil
        shutil.rmtree(runtime_memory_dir)
    import shutil
    shutil.copytree(out_dir, runtime_memory_dir)
    return runtime_memory_dir


def render_summary(task: dict, agent: str, result_summary: str) -> str:
    return "\n".join(
        [
            f"# {task['title']} - {agent} 影子输出",
            "",
            f"- `task_id`: `{task['task_id']}`",
            f"- `owner_agent`: `{agent}`",
            f"- `generated_at`: `{now_iso()}`",
            "",
            "## Goal",
            task["goal"],
            "",
            "## Summary",
            result_summary,
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="simulate a worker writeback in formal fusion mode")
    parser.add_argument("--runtime", default="shadow-live")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--task-id")
    parser.add_argument("--summary", default="已完成影子任务处理并准备主协调复核。")
    parser.add_argument("--review-note", default="影子回写已完成，建议由 main 做统一复核。")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--skip-memory-refresh", action="store_true")
    args = parser.parse_args()

    runtime_root = RUNTIME_BASE / args.runtime
    task_db = runtime_root / "task-board.db"
    bus_db = runtime_root / "bus.db"
    bus_runtime = runtime_root / "bus-runtime"

    task_id = args.task_id or pick_next_task(task_db, args.agent)
    task = ensure_task_owned(task_db, task_id, args.agent)

    queued_message_id = find_queued_task_message(bus_db, task_id, args.agent)
    bus = load_module(BUS_SCRIPT, "shadow_worker_bus_cli_ack")
    if queued_message_id:
        bus.ack(bus_db, bus_runtime, queued_message_id, args.agent, now_iso())

    add_event(task_db, task_id, "claimed", 0.1, "worker 已认领 shadow task", args.agent)
    add_event(task_db, task_id, "in_progress", 0.5, "worker 正在生成影子产物", args.agent)

    artifacts_root = runtime_root / "artifacts" / args.agent / task_id
    artifacts_root.mkdir(parents=True, exist_ok=True)
    summary_md = artifacts_root / "summary.md"
    result_json = artifacts_root / "result.json"
    summary_md.write_text(render_summary(task, args.agent, args.summary), encoding="utf-8")
    result_payload = {
        "task_id": task_id,
        "agent": args.agent,
        "summary": args.summary,
        "generated_at": now_iso(),
        "mode": "shadow",
    }
    result_json.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    add_artifact(task_db, task_id, "shadow_summary", summary_md, args.agent)
    add_artifact(task_db, task_id, "shadow_result_json", result_json, args.agent)

    artifact_paths = [str(summary_md), str(result_json)]

    if not args.skip_review:
        review_md = artifacts_root / "review-note.md"
        review_md.write_text(
            "\n".join(
                [
                    f"# {task['title']} - {args.agent} review note",
                    "",
                    args.review_note,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        add_artifact(task_db, task_id, "shadow_review_note", review_md, args.agent)
        add_review(task_db, task_id, args.agent, "task_output", "ready_for_main_review", args.review_note)
        artifact_paths.append(str(review_md))

    add_event(task_db, task_id, "completed", 1.0, "worker 已完成 shadow 回写", args.agent)
    refresh_parent_status(task_db, task.get("parent_task_id"))
    result_message_id = enqueue_result(task_db.parent / "bus.db", bus_runtime, task, args.agent, args.summary, artifact_paths, ["main"])
    blackboard_put(bus_db, bus_runtime, task_id, args.agent, args.summary, artifact_paths)

    memory_output = None
    knowledge_output = None
    knowledge_error = None
    if not args.skip_memory_refresh:
        memory_output = str(refresh_memory(runtime_root, task["parent_task_id"] or task_id))
        knowledge_output, knowledge_error = compile_knowledge(Path(memory_output), task["title"])

    payload = {
        "runtime": args.runtime,
        "agent": args.agent,
        "task_id": task_id,
        "acked_task_message": queued_message_id,
        "result_message_id": result_message_id,
        "artifacts": artifact_paths,
        "memory_output": memory_output,
        "knowledge_output": knowledge_output,
        "knowledge_error": knowledge_error,
        "mode": "shadow",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
