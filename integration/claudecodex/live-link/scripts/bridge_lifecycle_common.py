#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPENCLAW_ROOT = Path.home() / ".openclaw"
INTEGRATION_ROOT = OPENCLAW_ROOT / 'workspace' / 'integration' / 'claudecodex'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def parse_iso(value: str) -> datetime:
    # Accept both offset-aware and naive timestamps from legacy records.
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def runtime_paths(runtime: str) -> dict[str, Path]:
    runtime_root = RUNTIME_BASE / runtime
    bridge_root = runtime_root / 'bridge'
    lifecycle_root = bridge_root / 'lifecycle'
    return {
        'runtime_root': runtime_root,
        'task_db': runtime_root / 'task-board.db',
        'bus_db': runtime_root / 'bus.db',
        'bus_runtime': runtime_root / 'bus-runtime',
        'bridge_root': bridge_root,
        'lifecycle_root': lifecycle_root,
        'runs_dir': lifecycle_root / 'runs',
        'audit_dir': lifecycle_root / 'audit',
        'terminations_dir': lifecycle_root / 'terminations',
        'timeouts_dir': lifecycle_root / 'timeouts',
    }


def ensure_lifecycle_dirs(paths: dict[str, Path]) -> None:
    for key in ('lifecycle_root', 'runs_dir', 'audit_dir', 'terminations_dir', 'timeouts_dir'):
        paths[key].mkdir(parents=True, exist_ok=True)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + '\n')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fetch_task(conn: sqlite3.Connection, task_id: str) -> dict[str, Any] | None:
    row = conn.execute('SELECT * FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
    return None if row is None else dict(row)


def fetch_children(conn: sqlite3.Connection, parent_task_id: str) -> list[dict[str, Any]]:
    rows = conn.execute('SELECT * FROM tasks WHERE parent_task_id = ? ORDER BY task_id', (parent_task_id,)).fetchall()
    return [dict(row) for row in rows]


def resolve_task_tree(conn: sqlite3.Connection, task_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = fetch_task(conn, task_id)
    if task is None:
        raise ValueError(f'task not found: {task_id}')
    if task.get('parent_task_id'):
        root = fetch_task(conn, str(task['parent_task_id']))
        if root is None:
            root = task
    else:
        root = task
    children = fetch_children(conn, str(root['task_id']))
    return root, children


def compute_root_status(subtasks: list[dict[str, Any]], current_root_status: str) -> str:
    if not subtasks:
        return current_root_status
    statuses = [str(item.get('status', 'queued')) for item in subtasks]
    if all(status == 'completed' for status in statuses):
        return 'completed'
    if any(status in {'failed', 'timed_out', 'blocked'} for status in statuses):
        return 'blocked'
    if any(status == 'cancelled' for status in statuses):
        if all(status in {'cancelled', 'completed'} for status in statuses):
            return 'cancelled'
        return 'blocked'
    if any(status in {'claimed', 'in_progress'} for status in statuses):
        return 'in_progress'
    return 'queued'


def update_root_status(conn: sqlite3.Connection, root_task_id: str) -> str:
    subtasks = fetch_children(conn, root_task_id)
    root = fetch_task(conn, root_task_id)
    if root is None:
        raise ValueError(f'root task not found: {root_task_id}')
    new_status = compute_root_status(subtasks, str(root.get('status', 'queued')))
    conn.execute('UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?', (new_status, now_iso(), root_task_id))
    return new_status


def task_scope_ids(root_task_id: str, children: list[dict[str, Any]]) -> list[str]:
    ids = [root_task_id]
    ids.extend(str(item['task_id']) for item in children)
    return ids
