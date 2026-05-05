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

PROFILE_PATH = LIVE_LINK_ROOT / "config" / "real-version-limited-live-profile.v1.json"
GATE_CONFIG_PATH = LIVE_LINK_ROOT / "config" / "main-bridge-gate.v1.json"
MAIN_BRIDGE_GATE = LIVE_LINK_ROOT / "scripts" / "main_bridge_gate.py"


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


def ensure_runtime_dirs(runtime_name: str) -> dict[str, Path]:
    root = RUNTIME_BASE / runtime_name / "real-version"
    dirs = {
        "root": root,
        "activations": root / "activations",
        "runs": root / "runs",
        "audit": root / "audit",
        "surfaces": root / "surfaces",
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def render_surface_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {payload['title']} - Real Version Phase32 Default Surface",
        "",
        f"- `created_at`: `{payload['created_at']}`",
        f"- `task_id`: `{payload['task_id']}`",
        f"- `status`: `{payload['status']}`",
        f"- `workflow_mode`: `{payload['workflow_mode']}`",
        "",
        "## Default Surface",
        f"- `main_entry_doc`: `{payload['default_surface']['main_entry_doc']}`",
        f"- `loop_doc`: `{payload['default_surface']['loop_doc']}`",
        f"- `stable_signals_doc`: `{payload['default_surface']['stable_signals_doc']}`",
        "",
        "## Runtime Records",
        f"- `run_record`: `{payload['run_record']}`",
        f"- `gate_run_record`: `{payload['gate_run_record']}`",
        f"- `workflow_summary_json`: `{payload['workflow_summary_json']}`",
        f"- `workflow_summary_md`: `{payload['workflow_summary_md']}`",
        "",
        "## Governance",
        f"- `window_doc`: `{payload['governance']['window_doc']}`",
        f"- `checklist_doc`: `{payload['governance']['checklist_doc']}`",
        f"- `log_sheet_doc`: `{payload['governance']['log_sheet_doc']}`",
        f"- `execution_result_doc`: `{payload['governance']['execution_result_doc']}`",
        "",
        "## Boundaries",
    ]
    lines.extend(f"- {item}" for item in payload.get("boundaries", []))
    lines.extend([
        "",
        "## Summary",
    ])
    lines.extend(f"- {item}" for item in payload.get("summary_points", []))
    lines.append("")
    return "\n".join(lines)


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
        "gate_profile": gate_profile,
        "gate_config": gate_config,
        "active": active,
        "mode": "real_version_limited_live_status",
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
        "status": "activated",
    }
    record_path = dirs["activations"] / f"activation-{timestamp_slug()}.json"
    write_json(record_path, activation_record)
    append_jsonl(dirs["audit"] / "real-version-audit.jsonl", {"event": "activate", "record": str(record_path), "created_at": now_iso()})

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

    gate_cmd = [
        "python3",
        str(MAIN_BRIDGE_GATE),
        "--config",
        str(GATE_CONFIG_PATH),
        "run",
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
    gate_result = parse_json_output(gate_cmd)
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
        "gate_result": gate_result,
        "surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "boundaries": profile.get("boundaries", []),
        "status": gate_result.get("status"),
    }
    record_path = dirs["runs"] / f"run-{timestamp_slug()}.json"
    write_json(record_path, run_record)
    append_jsonl(dirs["audit"] / "real-version-audit.jsonl", {"event": "run", "record": str(record_path), "created_at": now_iso(), "status": gate_result.get("status")})

    task_id = gate_result.get("task_id", "unknown-task")
    surface_dir = dirs["surfaces"] / task_id
    surface_dir.mkdir(parents=True, exist_ok=True)
    surface_payload = {
        "created_at": now_iso(),
        "title": args.title,
        "task_id": task_id,
        "status": gate_result.get("status"),
        "workflow_mode": gate_result.get("workflow_mode"),
        "run_record": str(record_path),
        "gate_run_record": gate_result.get("gate_run_record"),
        "workflow_summary_json": gate_result.get("workflow_summary_json"),
        "workflow_summary_md": gate_result.get("workflow_summary_md"),
        "default_surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "boundaries": profile.get("boundaries", []),
        "summary_points": [
            "当前任务已通过真实版本受控融合入口进入 bridge workflow。",
            "当前任务默认绑定 Phase 32 作为长期工作面参考层。",
            "当前输出仍保持人工主控，不自动接管最终回复。",
        ],
    }
    surface_json = surface_dir / "phase32-default-surface.json"
    surface_md = surface_dir / "phase32-default-surface.md"
    write_json(surface_json, surface_payload)
    surface_md.write_text(render_surface_markdown(surface_payload), encoding="utf-8")

    print(json.dumps({
        "status": gate_result.get("status"),
        "runtime": profile.get("runtime"),
        "profile_id": profile.get("profile_id"),
        "run_record": str(record_path),
        "gate_run_record": gate_result.get("gate_run_record"),
        "workflow_summary_json": gate_result.get("workflow_summary_json"),
        "workflow_summary_md": gate_result.get("workflow_summary_md"),
        "surface_json": str(surface_json),
        "surface_md": str(surface_md),
        "default_surface": profile.get("default_surface"),
        "governance": profile.get("governance"),
        "mode": "real_version_limited_live_entry",
    }, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="controlled real-version limited-live entry using Phase 32 as default surface")
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
    p_run.add_argument("--task-type", default="report.cross_functional")
    p_run.add_argument("--decision-note", default="真实版本受控融合窗口内，允许当前任务进入 Phase 32 默认工作面。")
    p_run.add_argument("--bridge-reason", default="当前任务进入真实版本受控小流量执行窗口，使用 Phase 32 作为默认长期工作面。")
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
    raise SystemExit(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
