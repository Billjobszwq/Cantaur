#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".qyclaw"
WORKSPACE = ROOT / "workspace"
INTEGRATION = WORKSPACE / "integration" / "qy_code"
LIVE_LINK = INTEGRATION / "live-link"
RUNTIME_BASE = INTEGRATION / "runtime"
LOG_ROOT = ROOT / "logs"
POLICY_PATH = LIVE_LINK / "config" / "p4-soak-release-gate.v1.json"
ROLLOUT_POLICY_PATH = WORKSPACE / "knowledge" / "schemas" / "unique-fusion-rollout.v1.json"


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


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def default_policy() -> dict[str, Any]:
    return {
        "version": "p4-soak-release-gate/v1",
        "updated_at": now_iso(),
        "runtime": "live",
        "window_days": 7,
        "thresholds": {
            "max_health_failures": 0,
            "max_p3_failures": 0,
            "max_executor_failure_rate": 0.03,
            "min_executor_runs": 500,
            "max_alert_lines": 3,
        },
        "release_controls": {
            "auto_defer_rollout_on_fail": True,
            "defer_target_tier": "low",
            "apply_rollout_tier_update": False,
            "clear_hold_flag_on_pass": True,
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


def runtime_paths(runtime: str) -> dict[str, Path]:
    runtime_root = RUNTIME_BASE / runtime
    lifecycle_root = runtime_root / "bridge" / "lifecycle"
    release_root = runtime_root / "bridge" / "release-gate"
    return {
        "runtime_root": runtime_root,
        "health_dir": lifecycle_root / "health",
        "executor_dir": lifecycle_root / "runs",
        "p3_report_dir": runtime_root / "p3-distillation" / "reports",
        "release_root": release_root,
        "release_report_dir": release_root / "reports",
        "hold_flag": release_root / "defer-rollout.hold",
        "latest_status": release_root / "latest-status.json",
        "maintenance_alert_log": LOG_ROOT / "maintenance-alert.log",
    }


def in_window(ts: dt.datetime, start_at: dt.datetime, end_at: dt.datetime) -> bool:
    return start_at <= ts <= end_at


def day_key(ts: dt.datetime) -> str:
    return ts.astimezone().date().isoformat()


def load_health_records(health_dir: Path, start_at: dt.datetime, end_at: dt.datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not health_dir.exists():
        return records
    for path in sorted(health_dir.glob("health-gate-*.json")):
        payload = read_json(path)
        generated_at = str(payload.get("generated_at", "")).strip()
        if not generated_at:
            continue
        ts = parse_iso(generated_at)
        if not in_window(ts, start_at, end_at):
            continue
        status = str(payload.get("evaluation", {}).get("status", "unknown"))
        records.append(
            {
                "file": str(path),
                "generated_at": generated_at,
                "date": day_key(ts),
                "status": status,
                "is_pass": status == "pass",
            }
        )
    return records


def load_p3_records(p3_report_dir: Path, start_at: dt.datetime, end_at: dt.datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not p3_report_dir.exists():
        return records
    for path in sorted(p3_report_dir.glob("p3-distillation-*.json")):
        payload = read_json(path)
        finished_at = str(payload.get("finished_at") or payload.get("started_at") or "").strip()
        if not finished_at:
            continue
        ts = parse_iso(finished_at)
        if not in_window(ts, start_at, end_at):
            continue
        success = bool(payload.get("success", False))
        audit = dict(payload.get("audit", {})) if isinstance(payload.get("audit", {}), dict) else {}
        records.append(
            {
                "file": str(path),
                "finished_at": finished_at,
                "date": day_key(ts),
                "success": success,
                "audit_exit_code": int(audit.get("exit_code", 1 if not success else 0)),
                "audit_invalid_count": int(audit.get("invalid_count", 0)),
            }
        )
    return records


def load_executor_records(executor_dir: Path, start_at: dt.datetime, end_at: dt.datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not executor_dir.exists():
        return records
    for path in sorted(executor_dir.glob("executor-*.json")):
        payload = read_json(path)
        executed_at = str(payload.get("executed_at", "")).strip()
        if not executed_at:
            continue
        ts = parse_iso(executed_at)
        if not in_window(ts, start_at, end_at):
            continue
        processed = int(payload.get("processed", 0))
        failed = int(payload.get("failed", 0))
        records.append(
            {
                "file": str(path),
                "executed_at": executed_at,
                "date": day_key(ts),
                "processed": processed,
                "failed": failed,
            }
        )
    return records


def load_alert_records(alert_log: Path, start_at: dt.datetime, end_at: dt.datetime) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not alert_log.exists():
        return records
    pattern = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<line>.*)$")
    for line in alert_log.read_text(encoding="utf-8", errors="replace").splitlines():
        matched = pattern.match(line.strip())
        if not matched:
            continue
        ts_raw = matched.group("ts")
        try:
            ts = dt.datetime.strptime(ts_raw, "%Y-%m-%d %H:%M:%S %z")
        except ValueError:
            continue
        if not in_window(ts, start_at, end_at):
            continue
        records.append({"timestamp": ts.isoformat(), "date": day_key(ts), "line": matched.group("line")})
    return records


def evaluate_gate(
    window_days: int,
    health_records: list[dict[str, Any]],
    p3_records: list[dict[str, Any]],
    executor_records: list[dict[str, Any]],
    alert_records: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    health_days = {row["date"] for row in health_records}
    p3_days = {row["date"] for row in p3_records}
    executor_days = {row["date"] for row in executor_records}
    health_failures = sum(1 for row in health_records if not row["is_pass"])
    p3_failures = sum(1 for row in p3_records if not row["success"])
    total_processed = sum(int(row["processed"]) for row in executor_records)
    total_failed = sum(int(row["failed"]) for row in executor_records)
    executor_runs = len(executor_records)
    executor_failure_rate = 0.0 if total_processed <= 0 else round(total_failed / float(total_processed), 6)
    alert_lines = len(alert_records)

    checks = [
        {
            "name": "health_daily_coverage",
            "value": len(health_days),
            "limit": window_days,
            "comparator": ">=",
            "pass": len(health_days) >= window_days,
        },
        {
            "name": "p3_daily_coverage",
            "value": len(p3_days),
            "limit": window_days,
            "comparator": ">=",
            "pass": len(p3_days) >= window_days,
        },
        {
            "name": "executor_daily_coverage",
            "value": len(executor_days),
            "limit": window_days,
            "comparator": ">=",
            "pass": len(executor_days) >= window_days,
        },
        {
            "name": "health_failures",
            "value": health_failures,
            "limit": int(thresholds.get("max_health_failures", 0)),
            "comparator": "<=",
            "pass": health_failures <= int(thresholds.get("max_health_failures", 0)),
        },
        {
            "name": "p3_failures",
            "value": p3_failures,
            "limit": int(thresholds.get("max_p3_failures", 0)),
            "comparator": "<=",
            "pass": p3_failures <= int(thresholds.get("max_p3_failures", 0)),
        },
        {
            "name": "executor_runs",
            "value": executor_runs,
            "limit": int(thresholds.get("min_executor_runs", 500)),
            "comparator": ">=",
            "pass": executor_runs >= int(thresholds.get("min_executor_runs", 500)),
        },
        {
            "name": "executor_failure_rate",
            "value": executor_failure_rate,
            "limit": float(thresholds.get("max_executor_failure_rate", 0.03)),
            "comparator": "<=",
            "pass": executor_failure_rate <= float(thresholds.get("max_executor_failure_rate", 0.03)),
        },
        {
            "name": "alert_lines",
            "value": alert_lines,
            "limit": int(thresholds.get("max_alert_lines", 3)),
            "comparator": "<=",
            "pass": alert_lines <= int(thresholds.get("max_alert_lines", 3)),
        },
    ]
    failed_checks = [row for row in checks if not row["pass"]]
    decision = "approve" if not failed_checks else "defer"
    return {
        "decision": decision,
        "checks": checks,
        "failed_checks": failed_checks,
        "stats": {
            "health_records": len(health_records),
            "p3_records": len(p3_records),
            "executor_records": executor_runs,
            "alerts": alert_lines,
            "executor_processed_total": total_processed,
            "executor_failed_total": total_failed,
            "executor_failure_rate": executor_failure_rate,
            "health_days": sorted(health_days),
            "p3_days": sorted(p3_days),
            "executor_days": sorted(executor_days),
        },
    }


def apply_release_controls(
    decision: str,
    controls: dict[str, Any],
    rollout_policy_path: Path,
    hold_flag: Path,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    auto_defer = bool(controls.get("auto_defer_rollout_on_fail", True))
    clear_on_pass = bool(controls.get("clear_hold_flag_on_pass", True))
    apply_rollout_tier_update = bool(controls.get("apply_rollout_tier_update", False))
    defer_target_tier = str(controls.get("defer_target_tier", "low"))

    if decision == "defer" and auto_defer:
        write_text(hold_flag, f"defer at {now_iso()}\n")
        actions.append({"name": "write_hold_flag", "status": "applied", "file": str(hold_flag)})
        if apply_rollout_tier_update and rollout_policy_path.exists():
            payload = read_json(rollout_policy_path)
            before_tier = str(payload.get("active_tier", ""))
            payload["active_tier"] = defer_target_tier
            payload["updated_at"] = now_iso()
            write_json(rollout_policy_path, payload)
            actions.append(
                {
                    "name": "set_rollout_tier",
                    "status": "applied",
                    "before_tier": before_tier,
                    "after_tier": defer_target_tier,
                    "file": str(rollout_policy_path),
                }
            )

    if decision == "approve" and clear_on_pass and hold_flag.exists():
        hold_flag.unlink()
        actions.append({"name": "clear_hold_flag", "status": "applied", "file": str(hold_flag)})

    return {"actions": actions, "hold_flag_exists": hold_flag.exists()}


def report_markdown(payload: dict[str, Any]) -> str:
    gate = payload["gate"]
    lines = [
        "# P4 Soak Release Gate Report",
        "",
        f"- generated_at: `{payload['generated_at']}`",
        f"- runtime: `{payload['runtime']}`",
        f"- window_days: `{payload['window_days']}`",
        f"- decision: `{gate['decision']}`",
        "",
        "## Checks",
        "",
        "| Check | Value | Comparator | Limit | Result |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in gate.get("checks", []):
        result = "PASS" if check["pass"] else "FAIL"
        lines.append(
            f"| {check['name']} | `{check['value']}` | `{check['comparator']}` | `{check['limit']}` | `{result}` |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- health_records: `{gate['stats']['health_records']}`",
            f"- p3_records: `{gate['stats']['p3_records']}`",
            f"- executor_records: `{gate['stats']['executor_records']}`",
            f"- executor_failure_rate: `{gate['stats']['executor_failure_rate']}`",
            f"- alert_lines: `{gate['stats']['alerts']}`",
            "",
            "## Release Control Actions",
            "",
        ]
    )
    actions = payload.get("release_controls", {}).get("actions", [])
    if not actions:
        lines.append("- (none)")
    else:
        for row in actions:
            lines.append(f"- `{row['name']}` -> `{row['status']}`")
    lines.append("")
    return "\n".join(lines)


def run_gate(runtime: str, policy_path: Path) -> dict[str, Any]:
    policy = load_policy(policy_path)
    paths = runtime_paths(runtime)
    window_days = int(policy.get("window_days", 7))
    now = dt.datetime.now().astimezone()
    start_at = (now - dt.timedelta(days=window_days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_at = now

    health_records = load_health_records(paths["health_dir"], start_at, end_at)
    p3_records = load_p3_records(paths["p3_report_dir"], start_at, end_at)
    executor_records = load_executor_records(paths["executor_dir"], start_at, end_at)
    alert_records = load_alert_records(paths["maintenance_alert_log"], start_at, end_at)

    gate = evaluate_gate(
        window_days=window_days,
        health_records=health_records,
        p3_records=p3_records,
        executor_records=executor_records,
        alert_records=alert_records,
        thresholds=dict(policy.get("thresholds", {})),
    )

    control_result = apply_release_controls(
        decision=gate["decision"],
        controls=dict(policy.get("release_controls", {})),
        rollout_policy_path=ROLLOUT_POLICY_PATH,
        hold_flag=paths["hold_flag"],
    )

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    report_json = paths["release_report_dir"] / f"p4-soak-gate-{ts}.json"
    report_md = paths["release_report_dir"] / f"p4-soak-gate-{ts}.md"
    payload = {
        "mode": "p4_soak_release_gate",
        "generated_at": now_iso(),
        "runtime": runtime,
        "window_days": window_days,
        "window_start": start_at.isoformat(timespec="seconds"),
        "window_end": end_at.isoformat(timespec="seconds"),
        "policy_path": str(policy_path),
        "gate": gate,
        "release_controls": control_result,
        "sources": {
            "health_dir": str(paths["health_dir"]),
            "p3_report_dir": str(paths["p3_report_dir"]),
            "executor_dir": str(paths["executor_dir"]),
            "maintenance_alert_log": str(paths["maintenance_alert_log"]),
        },
        "samples": {
            "health": health_records[-20:],
            "p3": p3_records[-20:],
            "executor": executor_records[-20:],
            "alerts": alert_records[-20:],
        },
        "report_json": str(report_json),
        "report_md": str(report_md),
    }
    write_json(report_json, payload)
    write_text(report_md, report_markdown(payload))
    write_json(paths["latest_status"], payload)
    return payload


def status(runtime: str, limit: int) -> dict[str, Any]:
    paths = runtime_paths(runtime)
    report_dir = paths["release_report_dir"]
    files = sorted(report_dir.glob("p4-soak-gate-*.json")) if report_dir.exists() else []
    selected = files[-limit:] if limit > 0 else files
    items: list[dict[str, Any]] = []
    for path in selected:
        payload = read_json(path)
        gate = payload.get("gate", {})
        stats = gate.get("stats", {})
        items.append(
            {
                "file": str(path),
                "generated_at": payload.get("generated_at"),
                "decision": gate.get("decision"),
                "failed_checks": len(gate.get("failed_checks", [])),
                "health_records": stats.get("health_records", 0),
                "p3_records": stats.get("p3_records", 0),
                "executor_records": stats.get("executor_records", 0),
                "hold_flag_exists": bool(payload.get("release_controls", {}).get("hold_flag_exists", False)),
            }
        )
    return {
        "mode": "status",
        "runtime": runtime,
        "report_dir": str(report_dir),
        "hold_flag": str(paths["hold_flag"]),
        "hold_flag_exists": paths["hold_flag"].exists(),
        "count": len(items),
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P4 soak gate: 7-day stability gate + production release review")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    run_cmd.add_argument("--policy", default=str(POLICY_PATH))
    run_cmd.add_argument("--strict-defer", action="store_true", help="return non-zero exit code when decision is defer")

    status_cmd = sub.add_parser("status")
    status_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    status_cmd.add_argument("--limit", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.cmd == "run":
        payload = run_gate(runtime=str(args.runtime), policy_path=Path(args.policy))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        decision = str(payload["gate"]["decision"])
        if decision == "approve":
            return 0
        if decision == "defer":
            return 1 if bool(args.strict_defer) else 0
        return 1
    if args.cmd == "status":
        print(json.dumps(status(runtime=str(args.runtime), limit=int(args.limit)), ensure_ascii=False, indent=2))
        return 0
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
