#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = ROOT / "output"


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_task_bundle(task_db: Path, task_id: str) -> dict:
    with connect(task_db) as conn:
        task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        ensure(task is not None, f"task not found: {task_id}")
        subtasks = conn.execute(
            "SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY task_id",
            (task_id,),
        ).fetchall()
        artifacts = conn.execute(
            """
            SELECT * FROM task_artifacts
            WHERE task_id = ? OR task_id IN (SELECT task_id FROM tasks WHERE parent_task_id = ?)
            ORDER BY id
            """,
            (task_id, task_id),
        ).fetchall()
        reviews = conn.execute(
            """
            SELECT * FROM task_reviews
            WHERE task_id = ? OR task_id IN (SELECT task_id FROM tasks WHERE parent_task_id = ?)
            ORDER BY id
            """,
            (task_id, task_id),
        ).fetchall()
        events = conn.execute(
            """
            SELECT * FROM task_events
            WHERE task_id = ? OR task_id IN (SELECT task_id FROM tasks WHERE parent_task_id = ?)
            ORDER BY id
            """,
            (task_id, task_id),
        ).fetchall()
        deps = conn.execute(
            """
            SELECT * FROM task_dependencies
            WHERE task_id = ? OR task_id IN (SELECT task_id FROM tasks WHERE parent_task_id = ?)
            ORDER BY id
            """,
            (task_id, task_id),
        ).fetchall()
    return {
        "task": dict(task),
        "subtasks": [dict(r) for r in subtasks],
        "artifacts": [dict(r) for r in artifacts],
        "reviews": [dict(r) for r in reviews],
        "events": [dict(r) for r in events],
        "dependencies": [dict(r) for r in deps],
    }


def fetch_bus_bundle(bus_db: Path, task_id: str) -> dict:
    with connect(bus_db) as conn:
        messages = conn.execute(
            "SELECT * FROM bus_messages WHERE task_id = ? OR parent_task_id = ? ORDER BY created_at",
            (task_id, task_id),
        ).fetchall()
        audits = conn.execute(
            "SELECT * FROM bus_audit WHERE task_id = ? ORDER BY created_at, id",
            (task_id,),
        ).fetchall()
        blackboard = conn.execute(
            "SELECT * FROM blackboard_entries WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
    out_messages = []
    for row in messages:
        item = dict(row)
        item["body"] = json.loads(item.pop("body_json"))
        out_messages.append(item)
    out_audits = []
    for row in audits:
        item = dict(row)
        item["payload"] = json.loads(item.pop("payload_json"))
        out_audits.append(item)
    out_blackboard = []
    for row in blackboard:
        item = dict(row)
        item["entry_value"] = json.loads(item.pop("entry_value_json"))
        out_blackboard.append(item)
    return {
        "messages": out_messages,
        "audit": out_audits,
        "blackboard": out_blackboard,
    }


def build_summary(task_bundle: dict, bus_bundle: dict) -> dict:
    task = task_bundle["task"]
    subtasks = task_bundle["subtasks"]
    summary = {
        "task_id": task["task_id"],
        "title": task["title"],
        "goal": task["goal"],
        "status": task["status"],
        "owner_agent": task["owner_agent"],
        "subtask_agents": [item["owner_agent"] for item in subtasks],
        "subtask_count": len(subtasks),
        "artifact_count": len(task_bundle["artifacts"]),
        "review_count": len(task_bundle["reviews"]),
        "event_count": len(task_bundle["events"]),
        "message_count": len(bus_bundle["messages"]),
        "audit_count": len(bus_bundle["audit"]),
        "blackboard_keys": [item["entry_key"] for item in bus_bundle["blackboard"]],
    }
    return summary


def build_semantic_candidates(task_bundle: dict, bus_bundle: dict) -> list[dict]:
    task = task_bundle["task"]
    candidates = [
        {
            "type": "project_update",
            "title": task["title"],
            "summary": f"已建立 root task 与 {len(task_bundle['subtasks'])} 个 subtasks。",
            "source_task_id": task["task_id"],
        }
    ]
    if task_bundle["reviews"]:
        candidates.append(
            {
                "type": "lesson",
                "title": f"{task['title']} - review signals",
                "summary": f"当前已记录 {len(task_bundle['reviews'])} 条 review，可用于后续复盘。",
                "source_task_id": task["task_id"],
            }
        )
    if bus_bundle["blackboard"]:
        candidates.append(
            {
                "type": "decision_candidate",
                "title": f"{task['title']} - blackboard state",
                "summary": "协作过程中已形成共享事实板，可作为后续决策上下文。",
                "source_task_id": task["task_id"],
            }
        )
    return candidates


def render_markdown(task_bundle: dict, bus_bundle: dict, summary: dict, semantic_candidates: list[dict]) -> str:
    task = task_bundle["task"]
    lines = [
        f"# {task['title']} 记忆融合摘要",
        "",
        f"- `task_id`: `{task['task_id']}`",
        f"- `status`: `{task['status']}`",
        f"- `owner_agent`: `{task['owner_agent']}`",
        f"- `subtask_count`: `{summary['subtask_count']}`",
        f"- `artifact_count`: `{summary['artifact_count']}`",
        f"- `review_count`: `{summary['review_count']}`",
        f"- `message_count`: `{summary['message_count']}`",
        "",
        "## Goal",
        task["goal"],
        "",
        "## Subtasks",
    ]
    for item in task_bundle["subtasks"]:
        lines.append(f"- `{item['owner_agent']}`: `{item['task_id']}` / `{item['status']}`")
    lines.extend(["", "## Blackboard"])
    for item in bus_bundle["blackboard"]:
        lines.append(f"- `{item['entry_key']}`: {json.dumps(item['entry_value'], ensure_ascii=False)}")
    lines.extend(["", "## Semantic Candidates"])
    for item in semantic_candidates:
        lines.append(f"- `{item['type']}`: {item['summary']}")
    lines.append("")
    return "\n".join(lines)


def summarize(task_db: Path, bus_db: Path, task_id: str) -> Path:
    task_bundle = fetch_task_bundle(task_db, task_id)
    bus_bundle = fetch_bus_bundle(bus_db, task_id)
    summary = build_summary(task_bundle, bus_bundle)
    semantic_candidates = build_semantic_candidates(task_bundle, bus_bundle)

    out_dir = OUTPUT_ROOT / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "task_bundle": task_bundle,
                "bus_bundle": bus_bundle,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "semantic-candidates.json").write_text(
        json.dumps(semantic_candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "summary.md").write_text(
        render_markdown(task_bundle, bus_bundle, summary, semantic_candidates),
        encoding="utf-8",
    )
    return out_dir


def usage() -> int:
    print(
        "usage:\n"
        "  memory_fusion_cli.py summarize <task_db> <bus_db> <task_id>",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    if len(sys.argv) != 5:
        return usage()
    cmd = sys.argv[1]
    if cmd != "summarize":
        return usage()
    out_dir = summarize(
        Path(sys.argv[2]).expanduser().resolve(),
        Path(sys.argv[3]).expanduser().resolve(),
        sys.argv[4],
    )
    print(f"[OK] memory package created at {out_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
