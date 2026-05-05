#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import importlib.util
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "bus.schema.sql"
PROTOCOL_VALIDATOR_PATH = ROOT.parent / "protocols" / "scripts" / "validate_qyclaw_a2a.py"
VALID_MESSAGE_TYPES = {
    "TASK",
    "RESULT",
    "REVIEW",
    "CONSULT",
    "EVENT",
    "ESCALATE",
    "PERMISSION_REQUEST",
    "PERMISSION_RESPONSE",
    "KNOWLEDGE_CANDIDATE_CREATED",
    "KNOWLEDGE_PAGE_UPDATED",
    "MEMORY_CANDIDATE_PROMOTED",
    "PROCEDURE_PROMOTED",
    "REVIEW_DECISION_RECORDED",
    "KNOWLEDGE_ACTION_SUGGESTED",
}
QUEUE_STATUSES = {"queued", "acked", "retry_wait", "dead_letter"}
_PROTOCOL_VALIDATOR = None


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def ensure_type(value: Any, expected: type | tuple[type, ...], field: str) -> None:
    ensure(isinstance(value, expected), f"{field} type invalid")


def strict_schema_enabled() -> bool:
    raw = os.getenv("QYCLAW_A2A_STRICT_SCHEMA", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def protocol_validator_module():
    global _PROTOCOL_VALIDATOR
    if _PROTOCOL_VALIDATOR is not None:
        return _PROTOCOL_VALIDATOR
    ensure(PROTOCOL_VALIDATOR_PATH.exists(), f"protocol validator missing: {PROTOCOL_VALIDATOR_PATH}")
    spec = importlib.util.spec_from_file_location("validate_qyclaw_a2a", str(PROTOCOL_VALIDATOR_PATH))
    ensure(spec is not None and spec.loader is not None, "failed to load protocol validator spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _PROTOCOL_VALIDATOR = module
    return _PROTOCOL_VALIDATOR


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    ensure_type(data, dict, str(path))
    return data


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_runtime_dirs(runtime_root: Path) -> None:
    for rel in ["inbox", "outbox", "dead-letter", "audit", "blackboard"]:
        (runtime_root / rel).mkdir(parents=True, exist_ok=True)


def init_bus(db_path: Path, runtime_root: Path) -> None:
    ensure_runtime_dirs(runtime_root)
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(schema)


def validate_message(msg: dict[str, Any]) -> None:
    required = [
        "protocol",
        "message_type",
        "message_id",
        "task_id",
        "trace_id",
        "parent_task_id",
        "from",
        "to",
        "created_at",
        "body",
    ]
    for field in required:
        ensure(field in msg, f"missing field: {field}")
    ensure(msg["protocol"] == "qyclaw-a2a/v1", "protocol invalid")
    ensure(msg["message_type"] in VALID_MESSAGE_TYPES, "message_type invalid")
    for field in ["message_id", "task_id", "trace_id", "from", "to", "created_at"]:
        ensure_type(msg[field], str, field)
        ensure(bool(msg[field]), f"{field} cannot be empty")
    ensure(msg["parent_task_id"] is None or isinstance(msg["parent_task_id"], str), "parent_task_id invalid")
    ensure_type(msg["body"], dict, "body")
    if strict_schema_enabled():
        validator = protocol_validator_module()
        validator.validate_message(msg)


def audit(conn: sqlite3.Connection, runtime_root: Path, event_type: str, actor: str, note: str, payload: dict[str, Any], message_id: str | None = None, task_id: str | None = None, created_at: str | None = None) -> None:
    ts = created_at or payload.get("created_at") or ""
    conn.execute(
        """
        INSERT INTO bus_audit (event_type, message_id, task_id, actor, note, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_type,
            message_id,
            task_id,
            actor,
            note,
            json.dumps(payload, ensure_ascii=False),
            ts,
        ),
    )
    audit_path = runtime_root / "audit" / f"{ts or 'unknown'}-{event_type}-{message_id or 'na'}.json"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue(db_path: Path, runtime_root: Path, message_path: Path) -> None:
    ensure_runtime_dirs(runtime_root)
    msg = load_json(message_path)
    validate_message(msg)
    inbox_dir = runtime_root / "inbox" / msg["to"]
    outbox_dir = runtime_root / "outbox" / msg["from"]
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)

    inbox_target = inbox_dir / f"{msg['message_id']}.json"
    outbox_target = outbox_dir / f"{msg['message_id']}.json"
    shutil.copy2(message_path, inbox_target)
    shutil.copy2(message_path, outbox_target)

    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO bus_messages (
              message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
              message_type, body_json, queue_status, retry_count, available_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                msg["message_id"],
                msg["task_id"],
                msg["trace_id"],
                msg["parent_task_id"],
                msg["from"],
                msg["to"],
                msg["message_type"],
                json.dumps(msg["body"], ensure_ascii=False),
                "queued",
                0,
                msg["created_at"],
                msg["created_at"],
            ),
        )
        audit(
            conn,
            runtime_root,
            "enqueue",
            msg["from"],
            f"queued for {msg['to']}",
            msg,
            message_id=msg["message_id"],
            task_id=msg["task_id"],
            created_at=msg["created_at"],
        )


def list_inbox(db_path: Path, to_agent: str) -> None:
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT message_id, task_id, trace_id, from_agent, to_agent, message_type, queue_status, retry_count, created_at
            FROM bus_messages
            WHERE to_agent = ? AND queue_status IN ('queued', 'retry_wait')
            ORDER BY created_at ASC
            """,
            (to_agent,),
        ).fetchall()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))


def ack(db_path: Path, runtime_root: Path, message_id: str, actor: str, acked_at: str) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM bus_messages WHERE message_id = ?", (message_id,)).fetchone()
        ensure(row is not None, f"message not found: {message_id}")
        conn.execute(
            "UPDATE bus_messages SET queue_status = 'acked', acked_at = ? WHERE message_id = ?",
            (acked_at, message_id),
        )
        payload = dict(row)
        payload["acked_at"] = acked_at
        audit(conn, runtime_root, "ack", actor, "message acknowledged", payload, message_id=message_id, task_id=row["task_id"], created_at=acked_at)


def retry(db_path: Path, runtime_root: Path, message_id: str, actor: str, retry_at: str, last_error: str, dead_letter_after: int = 3) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM bus_messages WHERE message_id = ?", (message_id,)).fetchone()
        ensure(row is not None, f"message not found: {message_id}")
        retry_count = int(row["retry_count"]) + 1
        new_status = "dead_letter" if retry_count >= dead_letter_after else "retry_wait"
        conn.execute(
            """
            UPDATE bus_messages
            SET queue_status = ?, retry_count = ?, available_at = ?, last_error = ?
            WHERE message_id = ?
            """,
            (new_status, retry_count, retry_at, last_error, message_id),
        )
        payload = dict(row)
        payload["retry_count"] = retry_count
        payload["queue_status"] = new_status
        payload["last_error"] = last_error
        audit(conn, runtime_root, "retry", actor, f"message moved to {new_status}", payload, message_id=message_id, task_id=row["task_id"], created_at=retry_at)
        if new_status == "dead_letter":
            dead_letter_dir = runtime_root / "dead-letter" / row["to_agent"]
            dead_letter_dir.mkdir(parents=True, exist_ok=True)
            msg_path = runtime_root / "inbox" / row["to_agent"] / f"{message_id}.json"
            if msg_path.exists():
                shutil.copy2(msg_path, dead_letter_dir / f"{message_id}.json")


def blackboard_put(db_path: Path, runtime_root: Path, payload_path: Path) -> None:
    payload = load_json(payload_path)
    for field in ["task_id", "entry_key", "entry_value", "updated_by", "updated_at"]:
        ensure(field in payload, f"missing field: {field}")
    with connect(db_path) as conn:
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
                payload["task_id"],
                payload["entry_key"],
                json.dumps(payload["entry_value"], ensure_ascii=False),
                payload["updated_by"],
                payload["updated_at"],
            ),
        )
        audit(conn, runtime_root, "blackboard_put", payload["updated_by"], "blackboard updated", payload, task_id=payload["task_id"], created_at=payload["updated_at"])
    bb_dir = runtime_root / "blackboard" / payload["task_id"]
    bb_dir.mkdir(parents=True, exist_ok=True)
    (bb_dir / f"{payload['entry_key']}.json").write_text(json.dumps(payload["entry_value"], ensure_ascii=False, indent=2), encoding="utf-8")


def blackboard_get(db_path: Path, task_id: str) -> None:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT task_id, entry_key, entry_value_json, updated_by, updated_at FROM blackboard_entries WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["entry_value"] = json.loads(item.pop("entry_value_json"))
        out.append(item)
    print(json.dumps(out, ensure_ascii=False, indent=2))


def show_message(db_path: Path, message_id: str) -> None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM bus_messages WHERE message_id = ?", (message_id,)).fetchone()
        ensure(row is not None, f"message not found: {message_id}")
    payload = dict(row)
    payload["body"] = json.loads(payload.pop("body_json"))
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def usage() -> int:
    print(
        "usage:\n"
        "  bus_cli.py init <db> <runtime_root>\n"
        "  bus_cli.py enqueue <db> <runtime_root> <message.json>\n"
        "  bus_cli.py list-inbox <db> <to_agent>\n"
        "  bus_cli.py ack <db> <runtime_root> <message_id> <actor> <acked_at>\n"
        "  bus_cli.py retry <db> <runtime_root> <message_id> <actor> <retry_at> <last_error>\n"
        "  bus_cli.py blackboard-put <db> <runtime_root> <payload.json>\n"
        "  bus_cli.py blackboard-get <db> <task_id>\n"
        "  bus_cli.py show-message <db> <message_id>",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    if len(sys.argv) < 2:
        return usage()
    cmd = sys.argv[1]
    if cmd == "init":
        ensure(len(sys.argv) == 4, "init requires <db> <runtime_root>")
        init_bus(Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve())
        print(f"[OK] initialized bus")
        return 0
    if cmd == "enqueue":
        ensure(len(sys.argv) == 5, "enqueue requires <db> <runtime_root> <message.json>")
        enqueue(Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve(), Path(sys.argv[4]).expanduser().resolve())
        print("[OK] message enqueued")
        return 0
    if cmd == "list-inbox":
        ensure(len(sys.argv) == 4, "list-inbox requires <db> <to_agent>")
        list_inbox(Path(sys.argv[2]).expanduser().resolve(), sys.argv[3])
        return 0
    if cmd == "ack":
        ensure(len(sys.argv) == 7, "ack requires <db> <runtime_root> <message_id> <actor> <acked_at>")
        ack(Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve(), sys.argv[4], sys.argv[5], sys.argv[6])
        print("[OK] message acknowledged")
        return 0
    if cmd == "retry":
        ensure(len(sys.argv) == 8, "retry requires <db> <runtime_root> <message_id> <actor> <retry_at> <last_error>")
        retry(Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve(), sys.argv[4], sys.argv[5], sys.argv[6], sys.argv[7])
        print("[OK] message retried")
        return 0
    if cmd == "blackboard-put":
        ensure(len(sys.argv) == 5, "blackboard-put requires <db> <runtime_root> <payload.json>")
        blackboard_put(Path(sys.argv[2]).expanduser().resolve(), Path(sys.argv[3]).expanduser().resolve(), Path(sys.argv[4]).expanduser().resolve())
        print("[OK] blackboard updated")
        return 0
    if cmd == "blackboard-get":
        ensure(len(sys.argv) == 4, "blackboard-get requires <db> <task_id>")
        blackboard_get(Path(sys.argv[2]).expanduser().resolve(), sys.argv[3])
        return 0
    if cmd == "show-message":
        ensure(len(sys.argv) == 4, "show-message requires <db> <message_id>")
        show_message(Path(sys.argv[2]).expanduser().resolve(), sys.argv[3])
        return 0
    return usage()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
