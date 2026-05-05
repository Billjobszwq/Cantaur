#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


CONTROL_ROOT = Path(__file__).resolve().parents[1]
BASE_ROOT = CONTROL_ROOT.parent
REGISTRY_DIR = BASE_ROOT / "registries"
TASK_BOARD_SCHEMA = BASE_ROOT / "state" / "task-board.schema.sql"
BUS_SCHEMA = BASE_ROOT / "bus" / "bus.schema.sql"
POLICY_PATH = CONTROL_ROOT / "coordinator-routing-policy.v1.json"


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    ensure(isinstance(data, dict), f"{path} must contain a JSON object")
    return data


def load_agent_registry() -> dict[str, Any]:
    return load_json(REGISTRY_DIR / "agent-registry.v1.json")


def load_skill_registry() -> dict[str, Any]:
    return load_json(REGISTRY_DIR / "skill-registry.v1.json")


def load_policy() -> dict[str, Any]:
    return load_json(POLICY_PATH)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_if_needed(db_path: Path, schema_path: Path) -> None:
    schema = schema_path.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema)


def ensure_bus_runtime(runtime_root: Path) -> None:
    for rel in ["inbox", "outbox", "dead-letter", "audit", "blackboard"]:
        (runtime_root / rel).mkdir(parents=True, exist_ok=True)


def build_registry_maps() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    agent_registry = load_agent_registry()
    skill_registry = load_skill_registry()
    agents = {a["id"]: a for a in agent_registry["agents"]}
    skills = {s["id"]: s for s in skill_registry["skills"]}
    return agents, skills


def route_agents(intake: dict[str, Any], agents: dict[str, dict[str, Any]], policy: dict[str, Any]) -> list[str]:
    selected: list[str] = []

    for agent_id in intake.get("required_agents", []):
        if agent_id in agents and agent_id not in selected:
            selected.append(agent_id)

    task_type = intake.get("task_type", "")
    for prefix, agent_ids in policy.get("task_type_routes", {}).items():
        if task_type.startswith(prefix):
            for agent_id in agent_ids:
                if agent_id in agents and agent_id not in selected:
                    selected.append(agent_id)

    for domain in intake.get("required_domains", []):
        agent_id = policy.get("domain_routes", {}).get(domain)
        if agent_id and agent_id in agents and agent_id not in selected:
            selected.append(agent_id)

    selected = [agent_id for agent_id in selected if agent_id != "main"]
    return selected


def choose_reviewers(agent_id: str, policy: dict[str, Any]) -> list[str]:
    return policy.get("review_defaults", {}).get(agent_id, ["main"])


def build_plan(intake: dict[str, Any]) -> dict[str, Any]:
    agents, _skills = build_registry_maps()
    policy = load_policy()
    coordinator = policy.get("default_coordinator", "main")
    selected_agents = route_agents(intake, agents, policy)

    subtasks = []
    for idx, agent_id in enumerate(selected_agents, start=1):
        agent = agents[agent_id]
        subtask_id = f"{intake['task_id']}::{agent_id}"
        subtasks.append(
            {
                "task_id": subtask_id,
                "parent_task_id": intake["task_id"],
                "trace_id": intake["trace_id"],
                "to_agent": agent_id,
                "title": f"{intake['title']} - {agent_id}",
                "goal": intake["goal"],
                "constraints": intake.get("constraints", []),
                "required_output": agent.get("default_output_modes", []),
                "review_by": choose_reviewers(agent_id, policy),
                "priority": intake["priority"],
                "deadline": intake["deadline"],
                "sequence": idx,
            }
        )

    return {
        "coordinator": coordinator,
        "root_task": {
            "task_id": intake["task_id"],
            "trace_id": intake["trace_id"],
            "title": intake["title"],
            "goal": intake["goal"],
            "requested_by": intake["requested_by"],
            "priority": intake["priority"],
            "deadline": intake["deadline"],
        },
        "selected_agents": selected_agents,
        "subtasks": subtasks,
    }


def insert_task(conn: sqlite3.Connection, payload: dict[str, Any], owner_agent: str, requested_by: str, status: str, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO tasks (
          task_id, parent_task_id, trace_id, title, goal, owner_agent,
          requested_by, status, priority, deadline, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["task_id"],
            payload.get("parent_task_id"),
            payload["trace_id"],
            payload["title"],
            payload["goal"],
            owner_agent,
            requested_by,
            status,
            payload["priority"],
            payload["deadline"],
            created_at,
            created_at,
        ),
    )


def bus_enqueue(bus_db: Path, runtime_root: Path, message: dict[str, Any]) -> None:
    ensure_bus_runtime(runtime_root)
    inbox_dir = runtime_root / "inbox" / message["to"]
    outbox_dir = runtime_root / "outbox" / message["from"]
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    temp_path = runtime_root / f"{message['message_id']}.json"
    temp_path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(temp_path, inbox_dir / f"{message['message_id']}.json")
    shutil.copy2(temp_path, outbox_dir / f"{message['message_id']}.json")
    temp_path.unlink(missing_ok=True)
    with connect(bus_db) as conn:
        conn.execute(
            """
            INSERT INTO bus_messages (
              message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
              message_type, body_json, queue_status, retry_count, available_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message["message_id"],
                message["task_id"],
                message["trace_id"],
                message["parent_task_id"],
                message["from"],
                message["to"],
                message["message_type"],
                json.dumps(message["body"], ensure_ascii=False),
                "queued",
                0,
                message["created_at"],
                message["created_at"],
            ),
        )
        conn.execute(
            """
            INSERT INTO bus_audit (event_type, message_id, task_id, actor, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "enqueue",
                message["message_id"],
                message["task_id"],
                message["from"],
                f"queued for {message['to']}",
                json.dumps(message, ensure_ascii=False),
                message["created_at"],
            ),
        )


def dispatch(task_db: Path, bus_db: Path, runtime_root: Path, intake: dict[str, Any]) -> dict[str, Any]:
    plan = build_plan(intake)
    init_if_needed(task_db, TASK_BOARD_SCHEMA)
    init_if_needed(bus_db, BUS_SCHEMA)
    ensure_bus_runtime(runtime_root)

    created_at = intake.get("created_at") or now_iso()
    with connect(task_db) as conn:
        insert_task(
            conn,
            {
                "task_id": plan["root_task"]["task_id"],
                "parent_task_id": None,
                "trace_id": plan["root_task"]["trace_id"],
                "title": plan["root_task"]["title"],
                "goal": plan["root_task"]["goal"],
                "priority": plan["root_task"]["priority"],
                "deadline": plan["root_task"]["deadline"],
            },
            owner_agent="main",
            requested_by=plan["root_task"]["requested_by"],
            status="queued",
            created_at=created_at,
        )

        for subtask in plan["subtasks"]:
            insert_task(
                conn,
                subtask,
                owner_agent=subtask["to_agent"],
                requested_by="main",
                status="queued",
                created_at=created_at,
            )
            conn.execute(
                """
                INSERT INTO task_dependencies (task_id, blocked_by_task_id, created_at)
                VALUES (?, ?, ?)
                """,
                (subtask["task_id"], plan["root_task"]["task_id"], created_at),
            )

    for index, subtask in enumerate(plan["subtasks"], start=1):
        message = {
            "protocol": "qyclaw-a2a/v1",
            "message_type": "TASK",
            "message_id": f"{intake['task_id']}-msg-{index:03d}",
            "task_id": subtask["task_id"],
            "trace_id": intake["trace_id"],
            "parent_task_id": intake["task_id"],
            "from": "main",
            "to": subtask["to_agent"],
            "created_at": created_at,
            "body": {
                "goal": subtask["goal"],
                "constraints": subtask["constraints"],
                "inputs": [],
                "required_output": subtask["required_output"],
                "priority": subtask["priority"],
                "deadline": subtask["deadline"],
                "handoff_to": "main"
            }
        }
        bus_enqueue(bus_db, runtime_root, message)

    return plan


def usage() -> int:
    print(
        "usage:\n"
        "  coordinator_cli.py plan <intake.json>\n"
        "  coordinator_cli.py dispatch <task_db> <bus_db> <bus_runtime> <intake.json>",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    if len(sys.argv) < 3:
        return usage()
    cmd = sys.argv[1]
    if cmd == "plan":
        ensure(len(sys.argv) == 3, "plan requires <intake.json>")
        intake = load_json(Path(sys.argv[2]).expanduser().resolve())
        print(json.dumps(build_plan(intake), ensure_ascii=False, indent=2))
        return 0
    if cmd == "dispatch":
        ensure(len(sys.argv) == 6, "dispatch requires <task_db> <bus_db> <bus_runtime> <intake.json>")
        task_db = Path(sys.argv[2]).expanduser().resolve()
        bus_db = Path(sys.argv[3]).expanduser().resolve()
        runtime_root = Path(sys.argv[4]).expanduser().resolve()
        intake = load_json(Path(sys.argv[5]).expanduser().resolve())
        plan = dispatch(task_db, bus_db, runtime_root, intake)
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    return usage()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
