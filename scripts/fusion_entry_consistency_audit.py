#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path.home() / ".openclaw"
WORKSPACE = ROOT / "workspace"
LIVE_LINK = WORKSPACE / "integration" / "claudecodex" / "live-link"
INTEGRATION = WORKSPACE / "integration" / "claudecodex"

FORMAL_VERSION = WORKSPACE / "scripts" / "formal_fusion_version.py"
FORMAL_LIVE = LIVE_LINK / "scripts" / "formal_fusion_live_entry.py"
RELEASE_CANDIDATE = LIVE_LINK / "scripts" / "real_version_release_candidate_entry.py"

OUT_DIR = INTEGRATION / "runtime" / "live" / "entry-consistency-audit"

OPTION_RE = re.compile(r"--[a-z0-9][a-z0-9-]*")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


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


def run_text(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(cmd)} :: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout or ""


def run_json(cmd: list[str]) -> dict[str, Any]:
    return parse_json_output(run_text(cmd))


def options_for_run_help(path: Path) -> set[str]:
    text = run_text(["python3", str(path), "run", "--help"])
    return set(OPTION_RE.findall(text))


def extract_result_keys(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    keys = set()
    for key in ("status", "task_id", "lifecycle_execution", "hermes_fusion", "workflow_summary_json", "workflow_summary_md"):
        if f"\"{key}\"" in text:
            keys.add(key)
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description="audit run-entry consistency across formal fusion entrypoints")
    parser.add_argument("--runtime", default="live")
    args = parser.parse_args()

    entries = {
        "formal_fusion_version": FORMAL_VERSION,
        "formal_fusion_live_entry": FORMAL_LIVE,
        "release_candidate_entry": RELEASE_CANDIDATE,
    }
    required_run_options = {
        "--title",
        "--goal",
        "--requested-by",
        "--priority",
        "--source-channel",
        "--task-type",
        "--sync-hermes",
        "--no-sync-hermes",
        "--hermes-knowledge-limit",
        "--hermes-review-limit",
        "--hermes-timeout-scan",
        "--hermes-apply-auto",
        "--hermes-autopilot-tier",
    }
    required_result_keys = {
        "status",
        "task_id",
        "lifecycle_execution",
        "hermes_fusion",
        "workflow_summary_json",
        "workflow_summary_md",
    }

    help_options: dict[str, list[str]] = {}
    option_checks: list[dict[str, Any]] = []
    for name, path in entries.items():
        opts = options_for_run_help(path)
        help_options[name] = sorted(opts)
        missing = sorted(required_run_options - opts)
        option_checks.append(
            {
                "entry": name,
                "path": str(path),
                "missing_required_options": missing,
                "pass": len(missing) == 0,
            }
        )

    status_checks: list[dict[str, Any]] = []
    for name, path in entries.items():
        payload = run_json(["python3", str(path), "status"])
        gate_cfg = payload.get("gate_config", {})
        status_checks.append(
            {
                "entry": name,
                "runtime": payload.get("runtime"),
                "gate_enabled": gate_cfg.get("enabled"),
                "gate_workflow_mode": gate_cfg.get("workflow_mode"),
                "gate_execution_mode": gate_cfg.get("execution_mode"),
                "active": bool(payload.get("active", True)),
            }
        )

    key_checks: list[dict[str, Any]] = []
    for name, path in entries.items():
        keys = extract_result_keys(path)
        missing = sorted(required_result_keys - keys)
        key_checks.append(
            {
                "entry": name,
                "path": str(path),
                "missing_result_keys_in_source": missing,
                "pass": len(missing) == 0,
            }
        )

    mandatory_active = {"formal_fusion_version", "formal_fusion_live_entry"}
    active_checks = []
    for item in status_checks:
        should_be_active = item["entry"] in mandatory_active
        active_checks.append(
            {
                "entry": item["entry"],
                "should_be_active": should_be_active,
                "active": item["active"],
                "pass": (not should_be_active) or bool(item["active"]),
            }
        )

    all_pass = (
        all(item["pass"] for item in option_checks)
        and all(item["pass"] for item in key_checks)
        and all(item["pass"] for item in active_checks)
    )

    generated_at = now_iso()
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"entry-consistency-audit-{stamp}.json"

    payload = {
        "mode": "fusion_entry_consistency_audit",
        "generated_at": generated_at,
        "runtime": args.runtime,
        "pass": all_pass,
        "required_run_options": sorted(required_run_options),
        "required_result_keys": sorted(required_result_keys),
        "help_options": help_options,
        "option_checks": option_checks,
        "status_checks": status_checks,
        "active_checks": active_checks,
        "result_key_checks": key_checks,
        "report_path": str(out_path),
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
