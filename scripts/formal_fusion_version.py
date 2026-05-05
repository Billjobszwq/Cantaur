#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


QYCLAW_ROOT = Path.home() / ".qyclaw"
LIVE_LINK_ROOT = QYCLAW_ROOT / "workspace" / "integration" / "qy_code" / "live-link"
RC_ENTRY = LIVE_LINK_ROOT / "scripts" / "formal_fusion_live_entry.py"
LIMITED_LIVE_ENTRY = LIVE_LINK_ROOT / "scripts" / "real_version_limited_live_entry.py"
LIFECYCLE_ENTRY = LIVE_LINK_ROOT / "scripts" / "main_bridge_lifecycle.py"
HERMES_FUSION_ENTRY = QYCLAW_ROOT / "workspace" / "scripts" / "unique_fusion_orchestrator.py"


def run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError("command returned empty stdout")
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
            obj, idx = decoder.raw_decode(stdout, idx)
            if isinstance(obj, dict):
                last_obj = obj
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
) -> dict:
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
        payload = run_json(cmd)
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


def cmd_status() -> int:
    result = run_json(["python3", str(RC_ENTRY), "status"])
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_status"
    result["current_default_version"] = True
    result["replacement_state"] = "completed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_activate(actor: str) -> int:
    result = run_json(["python3", str(RC_ENTRY), "activate", "--actor", actor])
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_activate"
    result["current_default_version"] = True
    result["replacement_state"] = "completed"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    trigger_cmd = [
        "python3",
        str(LIFECYCLE_ENTRY),
        "trigger",
        "--runtime",
        "live",
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
    trigger = run_json(trigger_cmd)

    task_id = str(trigger.get("task_id", "") or "")
    accepted_statuses = {"bridge_absorbed", "shadow_started", "shadow_synthesized", "triggered"}
    execution = {
        "skipped": True,
        "reason": f"trigger_status={trigger.get('status')}",
    }
    if task_id and str(trigger.get("status", "")) in accepted_statuses:
        run_cmd = [
            "python3",
            str(LIFECYCLE_ENTRY),
            "run",
            "--runtime",
            "live",
            "--max-tasks",
            str(args.max_tasks),
            "--lock-ttl-seconds",
            str(args.lock_ttl_seconds),
            "--task-id",
            task_id,
        ]
        if args.skip_memory_refresh:
            run_cmd.append("--skip-memory-refresh")
        execution = run_json(run_cmd)

    unique_sync = {
        "enabled": bool(args.sync_unique),
        "status": "skipped",
        "reason": "disabled_by_flag",
    }
    if args.sync_unique:
        unique_sync = run_unique_sync(
            runtime="live",
            knowledge_limit=args.unique_knowledge_limit,
            review_limit=args.unique_review_limit,
            with_timeout_scan=args.unique_timeout_scan,
            apply_auto=args.unique_apply_auto,
            autopilot_tier=args.unique_autopilot_tier,
        )

    result = {
        "status": trigger.get("status"),
        "task_id": task_id,
        "trigger_record": trigger.get("record"),
        "gate_run_record": trigger.get("gate_result", {}).get("gate_run_record"),
        "workflow_summary_json": trigger.get("gate_result", {}).get("workflow_summary_json"),
        "workflow_summary_md": trigger.get("gate_result", {}).get("workflow_summary_md"),
        "lifecycle_execution": execution,
        "unique_fusion": unique_sync,
    }
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_run"
    result["current_default_version"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_smoke(actor: str) -> int:
    trigger = run_json(
        [
            "python3",
            str(LIFECYCLE_ENTRY),
            "trigger",
            "--runtime",
            "live",
            "--title",
            "示例行业巡检系统正式融合版完整测试综合稿",
            "--goal",
            "输出综合稿、summary 和 deliverable，生成跨 research ops finance law content 的 report.cross_functional 交付输入，用于正式融合版本完整测试。",
            "--requested-by",
            actor,
            "--priority",
            "high",
            "--source-channel",
            "manual",
            "--task-type",
            "report.cross_functional",
        ]
    )
    task_id = str(trigger.get("task_id", "") or "")
    accepted_statuses = {"bridge_absorbed", "shadow_started", "shadow_synthesized", "triggered"}
    execution = {
        "skipped": True,
        "reason": f"trigger_status={trigger.get('status')}",
    }
    if task_id and str(trigger.get("status", "")) in accepted_statuses:
        run_cmd = [
            "python3",
            str(LIFECYCLE_ENTRY),
            "run",
            "--runtime",
            "live",
            "--max-tasks",
            "20",
            "--lock-ttl-seconds",
            "900",
            "--task-id",
            task_id,
        ]
        execution = run_json(run_cmd)
    unique_sync = run_unique_sync(
        runtime="live",
        knowledge_limit=80,
        review_limit=10000,
        with_timeout_scan=False,
        apply_auto=False,
        autopilot_tier="low",
    )
    result = {
        "status": trigger.get("status"),
        "task_id": task_id,
        "trigger_record": trigger.get("record"),
        "gate_run_record": trigger.get("gate_result", {}).get("gate_run_record"),
        "workflow_summary_json": trigger.get("gate_result", {}).get("workflow_summary_json"),
        "workflow_summary_md": trigger.get("gate_result", {}).get("workflow_summary_md"),
        "lifecycle_execution": execution,
        "unique_fusion": unique_sync,
    }
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_smoke"
    result["current_default_version"] = True
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_rollback(actor: str) -> int:
    result = run_json(["python3", str(LIMITED_LIVE_ENTRY), "activate", "--actor", actor])
    result["workspace_entry"] = str(Path(__file__))
    result["rollback_target"] = str(LIMITED_LIVE_ENTRY)
    result["mode"] = "formal_fusion_version_rollback"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_lifecycle_status(runtime: str, task_id: str, limit: int) -> int:
    cmd = [
        "python3",
        str(LIFECYCLE_ENTRY),
        "status",
        "--runtime",
        runtime,
        "--limit",
        str(limit),
    ]
    if task_id:
        cmd.extend(["--task-id", task_id])
    result = run_json(cmd)
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_lifecycle_status"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_terminate(runtime: str, task_id: str, scope: str, reason: str, actor: str, force: bool) -> int:
    cmd = [
        "python3",
        str(LIFECYCLE_ENTRY),
        "terminate",
        "--runtime",
        runtime,
        "--task-id",
        task_id,
        "--scope",
        scope,
        "--reason",
        reason,
        "--actor",
        actor,
    ]
    if force:
        cmd.append("--force")
    result = run_json(cmd)
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_terminate"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_retry(runtime: str, task_id: str, reason: str, actor: str) -> int:
    result = run_json(
        [
            "python3",
            str(LIFECYCLE_ENTRY),
            "retry",
            "--runtime",
            runtime,
            "--task-id",
            task_id,
            "--reason",
            reason,
            "--actor",
            actor,
        ]
    )
    result["workspace_entry"] = str(Path(__file__))
    result["mode"] = "formal_fusion_version_retry"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="workspace-facing formal fusion version entry for current live fusion operation"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    p_activate = sub.add_parser("activate")
    p_activate.add_argument("--actor", default="main")

    p_run = sub.add_parser("run")
    p_run.add_argument("--title", required=True)
    p_run.add_argument("--goal", required=True)
    p_run.add_argument("--requested-by", default="main")
    p_run.add_argument("--priority", default="high", choices=["low", "medium", "high", "critical"])
    p_run.add_argument("--source-channel", default="manual")
    p_run.add_argument("--source-message-id", default="")
    p_run.add_argument("--source-session-id", default="")
    p_run.add_argument("--decision-note", default="正式融合上线版下，允许当前任务进入 Phase 32 默认工作面。")
    p_run.add_argument("--bridge-reason", default="当前任务通过工作区正式融合版入口进入 formal-fusion-live，使用 Phase 32 作为默认长期工作面。")
    p_run.add_argument("--task-type", default="report.cross_functional")
    p_run.add_argument("--max-tasks", type=int, default=20)
    p_run.add_argument("--lock-ttl-seconds", type=int, default=900)
    p_run.add_argument("--skip-memory-refresh", action="store_true")
    p_run.add_argument("--sync-unique", action=argparse.BooleanOptionalAction, default=True)
    p_run.add_argument("--unique-knowledge-limit", type=int, default=80)
    p_run.add_argument("--unique-review-limit", type=int, default=10000)
    p_run.add_argument("--unique-timeout-scan", action="store_true")
    p_run.add_argument("--unique-apply-auto", action="store_true")
    p_run.add_argument("--unique-autopilot-tier", default="low", choices=["off", "low", "medium", "high"])

    p_smoke = sub.add_parser("smoke")
    p_smoke.add_argument("--actor", default="main")

    p_rollback = sub.add_parser("rollback")
    p_rollback.add_argument("--actor", default="main")

    p_lifecycle_status = sub.add_parser("lifecycle-status")
    p_lifecycle_status.add_argument("--runtime", default="live")
    p_lifecycle_status.add_argument("--task-id", default="")
    p_lifecycle_status.add_argument("--limit", type=int, default=20)

    p_terminate = sub.add_parser("terminate")
    p_terminate.add_argument("--runtime", default="live")
    p_terminate.add_argument("--task-id", required=True)
    p_terminate.add_argument("--scope", default="tree", choices=["tree", "single"])
    p_terminate.add_argument("--reason", default="manual stop from formal_fusion_version")
    p_terminate.add_argument("--actor", default="main")
    p_terminate.add_argument("--force", action="store_true")

    p_retry = sub.add_parser("retry")
    p_retry.add_argument("--runtime", default="live")
    p_retry.add_argument("--task-id", required=True)
    p_retry.add_argument("--reason", default="manual retry from formal_fusion_version")
    p_retry.add_argument("--actor", default="main")
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
    if args.cmd == "smoke":
        return cmd_smoke(args.actor)
    if args.cmd == "rollback":
        return cmd_rollback(args.actor)
    if args.cmd == "lifecycle-status":
        return cmd_lifecycle_status(args.runtime, args.task_id, args.limit)
    if args.cmd == "terminate":
        return cmd_terminate(args.runtime, args.task_id, args.scope, args.reason, args.actor, args.force)
    if args.cmd == "retry":
        return cmd_retry(args.runtime, args.task_id, args.reason, args.actor)
    raise SystemExit(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
