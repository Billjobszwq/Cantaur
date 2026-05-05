#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".qyclaw"
WORKSPACE = ROOT / "workspace"
INTEGRATION = WORKSPACE / "integration" / "qy_code"
LIVE_LINK = INTEGRATION / "live-link"
RUNTIME_BASE = INTEGRATION / "runtime"
POLICY_PATH = LIVE_LINK / "config" / "lifecycle-health-gate.v1.json"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_iso(raw: str) -> dt.datetime:
    value = raw.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def runtime_paths(runtime: str) -> dict[str, Path]:
    runtime_root = RUNTIME_BASE / runtime
    lifecycle_root = runtime_root / "bridge" / "lifecycle"
    return {
        "runtime_root": runtime_root,
        "task_db": runtime_root / "task-board.db",
        "lifecycle_root": lifecycle_root,
        "health_dir": lifecycle_root / "health",
    }


def default_policy() -> dict[str, Any]:
    return {
        "version": "lifecycle-health-gate/v1",
        "updated_at": now_iso(),
        "runtime": "live",
        "thresholds": {
            "max_root_blocked": 6,
            "max_subtask_active": 120,
            "max_subtask_timed_out": 60,
            "max_oldest_active_subtask_minutes": 180,
            "max_recent_timeout_events_6h": 40,
        },
        "exit_nonzero_on_warn": False,
    }


def load_policy(path: Path) -> dict[str, Any]:
    if not path.exists():
        payload = default_policy()
        write_json(path, payload)
        return payload
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid policy: {path}")
    return payload


def fetch_metrics(task_db: Path) -> dict[str, Any]:
    now = dt.datetime.now().astimezone()
    with sqlite3.connect(task_db) as conn:
        conn.row_factory = sqlite3.Row
        root_rows = conn.execute(
            """
            SELECT status, COUNT(1) AS count
            FROM tasks
            WHERE parent_task_id IS NULL
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        sub_rows = conn.execute(
            """
            SELECT status, COUNT(1) AS count
            FROM tasks
            WHERE parent_task_id IS NOT NULL
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        active_rows = conn.execute(
            """
            SELECT task_id, status, updated_at
            FROM tasks
            WHERE parent_task_id IS NOT NULL
              AND status IN ('queued', 'claimed', 'in_progress')
            ORDER BY updated_at ASC
            """
        ).fetchall()
        timeout_rows = conn.execute(
            """
            SELECT COUNT(1) AS count
            FROM task_events
            WHERE state = 'timed_out'
              AND created_at >= datetime('now', '-6 hours')
            """
        ).fetchone()

    root_counts = {str(row["status"]): int(row["count"]) for row in root_rows}
    sub_counts = {str(row["status"]): int(row["count"]) for row in sub_rows}
    active = [dict(row) for row in active_rows]
    oldest_minutes = 0.0
    oldest_task_id = ""
    if active:
        first = active[0]
        updated = parse_iso(str(first["updated_at"]))
        oldest_minutes = round((now - updated).total_seconds() / 60.0, 2)
        oldest_task_id = str(first["task_id"])

    return {
        "root_counts": root_counts,
        "sub_counts": sub_counts,
        "subtask_active_total": int(sub_counts.get("queued", 0)) + int(sub_counts.get("claimed", 0)) + int(sub_counts.get("in_progress", 0)),
        "subtask_timed_out_total": int(sub_counts.get("timed_out", 0)),
        "oldest_active_subtask_minutes": oldest_minutes,
        "oldest_active_subtask_id": oldest_task_id,
        "recent_timeout_events_6h": int(timeout_rows["count"] if timeout_rows else 0),
    }


def evaluate(metrics: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    th = dict(policy.get("thresholds", {}))
    checks: list[dict[str, Any]] = []

    def add_check(name: str, value: float, limit: float) -> None:
        level = "pass"
        if value > limit:
            level = "fail"
        checks.append({"name": name, "value": value, "limit": limit, "level": level})

    add_check("root_blocked", float(metrics["root_counts"].get("blocked", 0)), float(th.get("max_root_blocked", 6)))
    add_check("subtask_active_total", float(metrics["subtask_active_total"]), float(th.get("max_subtask_active", 120)))
    add_check("subtask_timed_out_total", float(metrics["subtask_timed_out_total"]), float(th.get("max_subtask_timed_out", 60)))
    add_check(
        "oldest_active_subtask_minutes",
        float(metrics["oldest_active_subtask_minutes"]),
        float(th.get("max_oldest_active_subtask_minutes", 180)),
    )
    add_check(
        "recent_timeout_events_6h",
        float(metrics["recent_timeout_events_6h"]),
        float(th.get("max_recent_timeout_events_6h", 40)),
    )

    failed = [c for c in checks if c["level"] == "fail"]
    status = "pass" if not failed else "fail"
    return {"status": status, "checks": checks, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="lifecycle health gate for P1 stability")
    parser.add_argument("--runtime", default="live")
    parser.add_argument("--policy", default=str(POLICY_PATH))
    parser.add_argument("--strict-warn", action="store_true")
    args = parser.parse_args()

    runtime = str(args.runtime)
    policy = load_policy(Path(args.policy))
    paths = runtime_paths(runtime)
    metrics = fetch_metrics(paths["task_db"])
    result = evaluate(metrics, policy)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = paths["health_dir"] / f"health-gate-{ts}.json"
    payload = {
        "mode": "lifecycle_health_gate",
        "generated_at": now_iso(),
        "runtime": runtime,
        "policy_path": str(Path(args.policy)),
        "metrics": metrics,
        "evaluation": result,
        "report_path": str(out_path),
    }
    write_json(out_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))

    if result["status"] == "fail":
        return 1
    if result["status"] == "warn" and (args.strict_warn or bool(policy.get("exit_nonzero_on_warn", False))):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
