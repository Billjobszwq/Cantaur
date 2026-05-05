#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any


QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / "workspace" / "integration" / "qy_code"
LIVE_LINK_ROOT = INTEGRATION_ROOT / "live-link"
RUNTIME_BASE = INTEGRATION_ROOT / "runtime"

PROFILE_PATH = LIVE_LINK_ROOT / "config" / "formal-fusion-live.v1.json"
GATE_CONFIG_PATH = LIVE_LINK_ROOT / "config" / "main-bridge-gate.v1.json"
MAIN_BRIDGE_GATE = LIVE_LINK_ROOT / "scripts" / "main_bridge_gate.py"
LIFECYCLE_ENTRY = LIVE_LINK_ROOT / "scripts" / "main_bridge_lifecycle.py"
HERMES_FUSION_ENTRY = QYCLAW_ROOT / "workspace" / "scripts" / "unique_fusion_orchestrator.py"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_json_output(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError("subprocess returned empty stdout")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        last_obj = None
        while idx < len(stdout):
            while idx < len(stdout) and stdout[idx].isspace():
                idx += 1
            if idx >= len(stdout):
                break
            obj, next_idx = decoder.raw_decode(stdout, idx)
            if isinstance(obj, dict):
                last_obj = obj
            idx = next_idx
        if last_obj is None:
            raise
        return last_obj


def run_unique_sync(
    *,
    runtime: str,
    knowledge_limit: int,
    review_limit: int,
    with_timeout_scan: bool,
    apply_auto: bool,
    autopilot_tier: str,
) -> dict[str, Any]:
    cmd = [
        "python3",
        str(HERMES_FUSION_ENTRY),
        "run",
        "--runtime",
        runtime,
        "--days",
        "14",
        "--knowledge-limit",
        str(knowledge_limit),
        "--review-limit",
        str(review_limit),
        "--with-bridge-guard",
    ]
    if with_timeout_scan:
        cmd.extend(["--bridge-timeout-scan", "--bridge-timeout-minutes", "30", "--bridge-timeout-limit", "200"])
    if apply_auto:
        cmd.extend(["--apply-auto", "--max-knowledge-auto", "8", "--max-memory-auto", "20"])
        if autopilot_tier:
            cmd.extend(["--autopilot-tier", autopilot_tier])
    try:
        payload = parse_json_output(cmd)
        return {
            "enabled": True,
            "status": "ok",
            "runtime": runtime,
            "command": cmd,
            "result": payload,
        }
    except Exception as exc:
        return {
            "enabled": True,
            "status": "failed",
            "runtime": runtime,
            "command": cmd,
            "error": str(exc),
        }


def ensure_runtime_dirs(runtime_name: str) -> dict[str, Path]:
    root = RUNTIME_BASE / runtime_name / "formal-fusion-live"
    dirs = {
        "root": root,
        "activations": root / "activations",
        "runs": root / "runs",
        "audit": root / "audit",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def profile_status() -> dict[str, Any]:
    profile = read_json(PROFILE_PATH)
    gate_config = read_json(GATE_CONFIG_PATH)
    gate_profile = profile["gate_profile"]
    active = (
        gate_config.get("enabled") == gate_profile.get("enabled")
        and gate_config.get("workflow_mode") == gate_profile.get("workflow_mode")
        and gate_config.get("execution_mode") == gate_profile.get("execution_mode")
        and gate_config.get("require_classifier_allow_bridge") == gate_profile.get("require_classifier_allow_bridge")
        and gate_config.get("allowed_task_types") == gate_profile.get("allowed_task_types")
        and gate_config.get("allowed_source_channels") == gate_profile.get("allowed_source_channels")
    )
    return {
        "profile_path": str(PROFILE_PATH),
        "gate_config_path": str(GATE_CONFIG_PATH),
        "profile_id": profile.get("profile_id"),
        "profile_label": profile.get("profile_label"),
        "runtime": profile.get("runtime"),
        "surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "boundaries": profile.get("boundaries", []),
        "testing_positioning": profile.get("testing_positioning", []),
        "gate_profile": gate_profile,
        "gate_config": gate_config,
        "active": active,
        "mode": "formal_fusion_live_status",
    }


def cmd_status() -> int:
    print(json.dumps(profile_status(), ensure_ascii=False, indent=2))
    return 0


def cmd_activate(actor: str) -> int:
    profile = read_json(PROFILE_PATH)
    gate_profile = profile["gate_profile"]
    gate_config = read_json(GATE_CONFIG_PATH)
    gate_config.update(gate_profile)
    gate_config["updated_at"] = now_iso()
    gate_config["activated_by_profile"] = profile.get("profile_id")
    write_json(GATE_CONFIG_PATH, gate_config)

    dirs = ensure_runtime_dirs(profile["runtime"])
    activation_record = {
        "activated_at": now_iso(),
        "activated_by": actor,
        "profile_id": profile.get("profile_id"),
        "profile_label": profile.get("profile_label"),
        "runtime": profile.get("runtime"),
        "surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "gate_profile": gate_profile,
        "boundaries": profile.get("boundaries", []),
        "testing_positioning": profile.get("testing_positioning", []),
        "status": "activated",
    }
    record_path = dirs["activations"] / f"activation-{timestamp_slug()}.json"
    write_json(record_path, activation_record)
    append_jsonl(dirs["audit"] / "formal-fusion-live-audit.jsonl", {"event": "activate", "record": str(record_path), "created_at": now_iso()})

    result = profile_status()
    result.update({
        "status": "activated",
        "activation_record": str(record_path),
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    profile = read_json(PROFILE_PATH)
    dirs = ensure_runtime_dirs(profile["runtime"])

    trigger_cmd = [
        "python3",
        str(LIFECYCLE_ENTRY),
        "trigger",
        "--config",
        str(GATE_CONFIG_PATH),
        "--runtime",
        profile["runtime"],
        "--title",
        args.title,
        "--goal",
        args.goal,
        "--requested-by",
        args.requested_by,
        "--priority",
        args.priority,
        "--source-channel",
        args.source_channel,
        "--source-message-id",
        args.source_message_id,
        "--source-session-id",
        args.source_session_id,
        "--decision-note",
        args.decision_note,
        "--bridge-reason",
        args.bridge_reason,
        "--task-type",
        args.task_type,
    ]
    trigger_result = parse_json_output(trigger_cmd)

    task_id = str(trigger_result.get("task_id", "") or "")
    accepted_statuses = {"bridge_absorbed", "shadow_started", "shadow_synthesized", "triggered"}
    execution_result: dict[str, Any] = {"skipped": True, "reason": f"trigger_status={trigger_result.get('status')}"}
    if task_id and str(trigger_result.get("status", "")) in accepted_statuses:
        run_cmd = [
            "python3",
            str(LIFECYCLE_ENTRY),
            "run",
            "--runtime",
            profile["runtime"],
            "--max-tasks",
            "20",
            "--lock-ttl-seconds",
            "900",
            "--task-id",
            task_id,
        ]
        execution_result = parse_json_output(run_cmd)

    unique_sync = {
        "enabled": bool(args.sync_unique),
        "status": "skipped",
        "reason": "disabled_by_flag",
    }
    if args.sync_unique:
        unique_sync = run_unique_sync(
            runtime=profile["runtime"],
            knowledge_limit=args.unique_knowledge_limit,
            review_limit=args.unique_review_limit,
            with_timeout_scan=args.unique_timeout_scan,
            apply_auto=args.unique_apply_auto,
            autopilot_tier=args.unique_autopilot_tier,
        )

    run_record = {
        "created_at": now_iso(),
        "profile_id": profile.get("profile_id"),
        "profile_label": profile.get("profile_label"),
        "runtime": profile.get("runtime"),
        "title": args.title,
        "goal": args.goal,
        "requested_by": args.requested_by,
        "priority": args.priority,
        "source_channel": args.source_channel,
        "trigger_result": trigger_result,
        "execution_result": execution_result,
        "unique_fusion": unique_sync,
        "surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "boundaries": profile.get("boundaries", []),
        "testing_positioning": profile.get("testing_positioning", []),
        "status": trigger_result.get("status"),
    }
    record_path = dirs["runs"] / f"run-{timestamp_slug()}.json"
    write_json(record_path, run_record)
    append_jsonl(
        dirs["audit"] / "formal-fusion-live-audit.jsonl",
        {"event": "run", "record": str(record_path), "created_at": now_iso(), "status": trigger_result.get("status")},
    )
    result = {
        "status": trigger_result.get("status"),
        "runtime": profile.get("runtime"),
        "profile_id": profile.get("profile_id"),
        "task_id": task_id,
        "run_record": str(record_path),
        "trigger_record": trigger_result.get("record"),
        "gate_run_record": trigger_result.get("gate_result", {}).get("gate_run_record"),
        "workflow_summary_json": trigger_result.get("gate_result", {}).get("workflow_summary_json"),
        "workflow_summary_md": trigger_result.get("gate_result", {}).get("workflow_summary_md"),
        "lifecycle_execution": execution_result,
        "unique_fusion": unique_sync,
        "mode": "formal_fusion_live_entry",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="formal fusion live entry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")
    p_activate = sub.add_parser("activate")
    p_activate.add_argument("--actor", default="main")

    p_run = sub.add_parser("run")
    p_run.add_argument("--title", required=True)
    p_run.add_argument("--goal", required=True)
    p_run.add_argument("--requested-by", default="main")
    p_run.add_argument("--priority", default="high")
    p_run.add_argument("--source-channel", default="manual")
    p_run.add_argument("--source-message-id", default="")
    p_run.add_argument("--source-session-id", default="")
    p_run.add_argument("--decision-note", default="正式协同上线版下，允许当前任务进入 Phase 32 默认工作面。")
    p_run.add_argument("--bridge-reason", default="当前任务进入正式协同上线版，使用 Phase 32 作为默认长期工作面。")
    p_run.add_argument("--task-type", default="report.cross_functional")
    p_run.add_argument("--sync-unique", action=argparse.BooleanOptionalAction, default=True)
    p_run.add_argument("--unique-knowledge-limit", type=int, default=80)
    p_run.add_argument("--unique-review-limit", type=int, default=10000)
    p_run.add_argument("--unique-timeout-scan", action="store_true")
    p_run.add_argument("--unique-apply-auto", action="store_true")
    p_run.add_argument("--unique-autopilot-tier", default="low", choices=["off", "low", "medium", "high"])
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "activate":
        return cmd_activate(args.actor)
    if args.cmd == "run":
        return cmd_run(args)
    raise ValueError(f"unsupported cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
