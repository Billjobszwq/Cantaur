#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".openclaw"
WORKSPACE = ROOT / "workspace"
INTEGRATION = WORKSPACE / "integration" / "claudecodex"
LIVE_LINK = INTEGRATION / "live-link"
RUNTIME_BASE = INTEGRATION / "runtime"
LIFECYCLE_ENTRY = LIVE_LINK / "scripts" / "main_bridge_lifecycle.py"
POLICY_PATH = LIVE_LINK / "config" / "lifecycle-p1-governance.v1.json"


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


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def parse_json_output(stdout: str) -> dict[str, Any]:
    payload = stdout.strip()
    if not payload:
        raise RuntimeError("empty stdout")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        last_obj: dict[str, Any] | None = None
        while idx < len(payload):
            while idx < len(payload) and payload[idx].isspace():
                idx += 1
            if idx >= len(payload):
                break
            obj, idx = decoder.raw_decode(payload, idx)
            if isinstance(obj, dict):
                last_obj = obj
        if last_obj is None:
            raise
        return last_obj


def run_json(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)} :: {(proc.stderr or proc.stdout).strip()}"
        )
    return parse_json_output(proc.stdout or "")


def runtime_paths(runtime: str) -> dict[str, Path]:
    runtime_root = RUNTIME_BASE / runtime
    lifecycle_root = runtime_root / "bridge" / "lifecycle"
    return {
        "runtime_root": runtime_root,
        "task_db": runtime_root / "task-board.db",
        "lifecycle_root": lifecycle_root,
        "governance_dir": lifecycle_root / "governance",
    }


def default_policy() -> dict[str, Any]:
    return {
        "version": "lifecycle-p1-governance/v1",
        "updated_at": now_iso(),
        "runtime": "live",
        "retry": {
            "enabled": True,
            "statuses": ["blocked", "timed_out"],
            "require_subtask_statuses": ["timed_out", "failed"],
            "min_created_age_hours": 0,
            "max_created_age_hours": 24,
            "max_roots_per_run": 8,
            "reason": "P1 governance retry for recent blocked/timed_out root",
        },
        "archive": {
            "enabled": True,
            "statuses": ["queued", "blocked", "timed_out"],
            "min_created_age_hours": 168,
            "max_roots_per_run": 30,
            "reason": "P1 governance archive stale root task tree",
        },
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


def fetch_root_candidates(task_db: Path) -> list[dict[str, Any]]:
    with sqlite3.connect(task_db) as conn:
        conn.row_factory = sqlite3.Row
        roots = conn.execute(
            """
            SELECT task_id, title, status, priority, created_at, updated_at
            FROM tasks
            WHERE parent_task_id IS NULL
            ORDER BY created_at ASC
            """
        ).fetchall()
        sub_rows = conn.execute(
            """
            SELECT parent_task_id, status, COUNT(1) AS count
            FROM tasks
            WHERE parent_task_id IS NOT NULL
            GROUP BY parent_task_id, status
            """
        ).fetchall()
    sub_map: dict[str, dict[str, int]] = {}
    for row in sub_rows:
        root_id = str(row["parent_task_id"])
        if root_id not in sub_map:
            sub_map[root_id] = {}
        sub_map[root_id][str(row["status"])] = int(row["count"])

    now = dt.datetime.now().astimezone()
    out: list[dict[str, Any]] = []
    for row in roots:
        created_at = parse_iso(str(row["created_at"]))
        updated_at = parse_iso(str(row["updated_at"]))
        created_age_hours = round((now - created_at).total_seconds() / 3600.0, 2)
        updated_age_hours = round((now - updated_at).total_seconds() / 3600.0, 2)
        task_id = str(row["task_id"])
        out.append(
            {
                "task_id": task_id,
                "title": str(row["title"]),
                "status": str(row["status"]),
                "priority": str(row["priority"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "created_age_hours": created_age_hours,
                "updated_age_hours": updated_age_hours,
                "subtask_status_counts": sub_map.get(task_id, {}),
            }
        )
    return out


def classify_actions(candidates: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    retry_cfg = dict(policy.get("retry", {}))
    archive_cfg = dict(policy.get("archive", {}))
    retry_statuses = set(str(v) for v in retry_cfg.get("statuses", []))
    retry_substatuses = set(str(v) for v in retry_cfg.get("require_subtask_statuses", []))
    retry_min_age = float(retry_cfg.get("min_created_age_hours", 0))
    retry_max_age = float(retry_cfg.get("max_created_age_hours", 24))
    archive_statuses = set(str(v) for v in archive_cfg.get("statuses", []))
    archive_min_age = float(archive_cfg.get("min_created_age_hours", 168))

    planned: list[dict[str, Any]] = []
    for row in candidates:
        status = str(row["status"])
        created_age = float(row["created_age_hours"])
        sub_counts = dict(row.get("subtask_status_counts", {}))
        sub_statuses = set(sub_counts.keys())
        action = "none"
        reason = "no_rule_matched"

        if retry_cfg.get("enabled", True):
            if (
                status in retry_statuses
                and created_age >= retry_min_age
                and created_age <= retry_max_age
                and (not retry_substatuses or bool(sub_statuses & retry_substatuses))
            ):
                action = "retry"
                reason = "retry_rule_matched"

        if action == "none" and archive_cfg.get("enabled", True):
            if status in archive_statuses and created_age >= archive_min_age:
                action = "archive"
                reason = "archive_rule_matched"

        planned.append({**row, "action": action, "action_reason": reason})
    return planned


def lifecycle_status(runtime: str, limit: int = 20) -> dict[str, Any]:
    return run_json(
        [
            "python3",
            str(LIFECYCLE_ENTRY),
            "status",
            "--runtime",
            runtime,
            "--limit",
            str(limit),
        ]
    )


def apply_plan(runtime: str, plan_rows: list[dict[str, Any]], policy: dict[str, Any], actor: str) -> list[dict[str, Any]]:
    retry_cfg = dict(policy.get("retry", {}))
    archive_cfg = dict(policy.get("archive", {}))
    retry_max = int(retry_cfg.get("max_roots_per_run", 8))
    archive_max = int(archive_cfg.get("max_roots_per_run", 30))
    retry_reason = str(retry_cfg.get("reason", "P1 governance retry"))
    archive_reason = str(archive_cfg.get("reason", "P1 governance archive"))
    retry_used = 0
    archive_used = 0
    outputs: list[dict[str, Any]] = []

    for row in plan_rows:
        action = str(row.get("action", "none"))
        task_id = str(row["task_id"])
        if action == "retry":
            if retry_used >= retry_max:
                outputs.append({"task_id": task_id, "action": action, "status": "skipped", "reason": "retry_limit_reached"})
                continue
            payload = run_json(
                [
                    "python3",
                    str(LIFECYCLE_ENTRY),
                    "retry",
                    "--runtime",
                    runtime,
                    "--task-id",
                    task_id,
                    "--reason",
                    retry_reason,
                    "--actor",
                    actor,
                ]
            )
            retry_used += 1
            outputs.append({"task_id": task_id, "action": action, "status": "applied", "result": payload})
            continue

        if action == "archive":
            if archive_used >= archive_max:
                outputs.append({"task_id": task_id, "action": action, "status": "skipped", "reason": "archive_limit_reached"})
                continue
            payload = run_json(
                [
                    "python3",
                    str(LIFECYCLE_ENTRY),
                    "terminate",
                    "--runtime",
                    runtime,
                    "--task-id",
                    task_id,
                    "--scope",
                    "tree",
                    "--reason",
                    archive_reason,
                    "--actor",
                    actor,
                ]
            )
            archive_used += 1
            outputs.append({"task_id": task_id, "action": action, "status": "applied", "result": payload})
            continue

        outputs.append({"task_id": task_id, "action": action, "status": "noop"})

    return outputs


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Lifecycle P1 Governance Report",
        "",
        f"- generated_at: `{report.get('generated_at', '')}`",
        f"- runtime: `{report.get('runtime', '')}`",
        f"- mode: `{report.get('mode', '')}`",
        f"- dry_run: `{bool(report.get('dry_run'))}`",
        "",
        "## Plan Summary",
        "",
    ]
    summary = report.get("plan_summary", {})
    lines.append(f"- total_roots: `{summary.get('total', 0)}`")
    lines.append(f"- retry: `{summary.get('retry', 0)}`")
    lines.append(f"- archive: `{summary.get('archive', 0)}`")
    lines.append(f"- none: `{summary.get('none', 0)}`")

    before = report.get("before_status", {})
    after = report.get("after_status", {})
    lines.extend(
        [
            "",
            "## Lifecycle Counts",
            "",
            f"- before: `{before.get('status_counts', {})}`",
            f"- after: `{after.get('status_counts', {})}`",
        ]
    )

    executed = report.get("executed", [])
    if executed:
        lines.extend(["", "## Executed Actions", ""])
        for row in executed:
            lines.append(f"- `{row.get('task_id', '')}` -> `{row.get('action', '')}` / `{row.get('status', '')}`")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="P1 lifecycle governance: classify stale roots and retry/archive them")
    parser.add_argument("--runtime", default="live")
    parser.add_argument("--policy", default=str(POLICY_PATH))
    parser.add_argument("--actor", default="p1-governance")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit-report-roots", type=int, default=200)
    args = parser.parse_args()

    runtime = str(args.runtime)
    paths = runtime_paths(runtime)
    policy = load_policy(Path(args.policy))
    candidates = fetch_root_candidates(paths["task_db"])
    planned = classify_actions(candidates, policy)
    plan_summary = {
        "total": len(planned),
        "retry": sum(1 for row in planned if row["action"] == "retry"),
        "archive": sum(1 for row in planned if row["action"] == "archive"),
        "none": sum(1 for row in planned if row["action"] == "none"),
    }

    before = lifecycle_status(runtime, limit=20)
    executed: list[dict[str, Any]] = []
    dry_run = not bool(args.apply)
    if args.apply:
        actionable = [row for row in planned if row["action"] in {"retry", "archive"}]
        executed = apply_plan(runtime, actionable, policy, actor=str(args.actor))
    after = lifecycle_status(runtime, limit=20)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = paths["governance_dir"] / "reports"
    json_path = out_dir / f"p1-governance-{ts}.json"
    md_path = out_dir / f"p1-governance-{ts}.md"

    report = {
        "mode": "lifecycle_p1_governance",
        "generated_at": now_iso(),
        "runtime": runtime,
        "dry_run": dry_run,
        "policy_path": str(Path(args.policy)),
        "plan_summary": plan_summary,
        "before_status": before,
        "after_status": after,
        "planned": planned[: max(1, int(args.limit_report_roots))],
        "executed": executed,
        "report_paths": {"json": str(json_path), "markdown": str(md_path)},
    }
    write_json(json_path, report)
    write_text(md_path, report_markdown(report))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
