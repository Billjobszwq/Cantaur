#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path.home() / ".qyclaw"
WORKSPACE_ROOT = ROOT / "workspace"
SCRIPTS_ROOT = WORKSPACE_ROOT / "scripts"
KNOWLEDGE_ROOT = WORKSPACE_ROOT / "knowledge"
REPORT_ROOT = KNOWLEDGE_ROOT / "overview" / "fusion-cycle-reports"

MEMORY_PIPELINE_SCRIPT = SCRIPTS_ROOT / "memory_pipeline.py"
KNOWLEDGE_BASE_SCRIPT = SCRIPTS_ROOT / "knowledge_base.py"
POLICY_PATH = KNOWLEDGE_ROOT / "schemas" / "unique-fusion-autopilot.v1.json"
ROLLOUT_POLICY_PATH = KNOWLEDGE_ROOT / "schemas" / "unique-fusion-rollout.v1.json"
LIFECYCLE_SCRIPT = WORKSPACE_ROOT / "integration" / "qy_code" / "live-link" / "scripts" / "main_bridge_lifecycle.py"


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def safe_slug(value: str, limit: int = 64) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    cleaned = cleaned.strip("-") or "item"
    return cleaned[:limit]


def contains_blocked(texts: list[str], blocked_keywords: list[str]) -> bool:
    if not blocked_keywords:
        return False
    haystack = "\n".join(texts).lower()
    return any(keyword.lower() in haystack for keyword in blocked_keywords)


def default_policy() -> dict[str, Any]:
    return {
        "version": "unique-fusion-autopilot/v1",
        "updated_at": now_iso(),
        "knowledge": {
            "enabled": True,
            "decision": "observe",
            "max_items_per_run": 8,
            "allowed_candidate_types": ["decision", "rule", "fact", "lesson", "preference"],
            "blocked_keywords": ["删除", "drop", "destructive", "高风险", "合规争议"],
            "reviewer": "main",
            "note": "auto triage by unique fusion orchestrator",
        },
        "memory": {
            "enabled": True,
            "status": "approved",
            "max_items_per_agent": 5,
            "max_items_total": 20,
            "days": 14,
            "limit": 200,
            "min_confidence": 0.78,
            "min_priority": 3,
            "allowed_candidate_types": ["decision", "lesson", "preference", "procedure", "rule", "fact"],
            "blocked_keywords": ["删除", "drop", "destructive", "高风险", "合规争议"],
            "reviewer": "main",
            "note": "auto triage by unique fusion orchestrator",
        },
        "bridge": {
            "enabled": True,
            "status_limit": 20,
            "autopilot_blocking_statuses": ["in_progress", "claimed"],
            "skip_autopilot_when_conflict": True,
            "evaluate_with_recent_roots": True,
            "conflict_window_days": 7,
            "max_blocked_roots_for_autopilot": 999999,
            "max_timed_out_roots_for_autopilot": 999999,
            "max_queued_roots_for_autopilot": 999999,
            "timeout_minutes": 30,
            "timeout_limit": 200,
        },
    }


def load_policy() -> dict[str, Any]:
    if not POLICY_PATH.exists():
        policy = default_policy()
        write_text(POLICY_PATH, json.dumps(policy, ensure_ascii=False, indent=2) + "\n")
        return policy
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid policy json: {POLICY_PATH}")
    return payload


def default_rollout_policy() -> dict[str, Any]:
    return {
        "version": "unique-fusion-rollout/v1",
        "updated_at": now_iso(),
        "active_tier": "low",
        "tiers": {
            "off": {"enabled": False},
            "low": {
                "enabled": True,
                "knowledge": {
                    "allowed_candidate_types": ["fact", "lesson", "preference"],
                    "max_items_per_run": 3,
                    "decision": "observe",
                },
                "memory": {
                    "allowed_candidate_types": ["lesson", "preference", "procedure"],
                    "min_confidence": 0.85,
                    "min_priority": 4,
                    "max_items_per_agent": 2,
                    "max_items_total": 6,
                    "status": "approved",
                },
            },
            "medium": {
                "enabled": True,
                "knowledge": {
                    "allowed_candidate_types": ["fact", "lesson", "preference", "rule"],
                    "max_items_per_run": 6,
                    "decision": "observe",
                },
                "memory": {
                    "allowed_candidate_types": ["decision", "lesson", "preference", "procedure", "rule", "fact"],
                    "min_confidence": 0.82,
                    "min_priority": 3,
                    "max_items_per_agent": 3,
                    "max_items_total": 12,
                    "status": "approved",
                },
            },
            "high": {
                "enabled": True,
                "knowledge": {
                    "allowed_candidate_types": ["decision", "rule", "fact", "lesson", "preference"],
                    "max_items_per_run": 8,
                    "decision": "observe",
                },
                "memory": {
                    "allowed_candidate_types": ["decision", "lesson", "preference", "procedure", "rule", "fact"],
                    "min_confidence": 0.78,
                    "min_priority": 3,
                    "max_items_per_agent": 5,
                    "max_items_total": 20,
                    "status": "approved",
                },
            },
        },
    }


def load_rollout_policy() -> dict[str, Any]:
    if not ROLLOUT_POLICY_PATH.exists():
        payload = default_rollout_policy()
        write_text(ROLLOUT_POLICY_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return payload
    payload = json.loads(ROLLOUT_POLICY_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid rollout policy: {ROLLOUT_POLICY_PATH}")
    return payload


def resolve_rollout_profile(rollout_policy: dict[str, Any], tier_override: str) -> dict[str, Any]:
    tiers = rollout_policy.get("tiers", {})
    if not isinstance(tiers, dict):
        tiers = {}
    active_tier = str(rollout_policy.get("active_tier", "low")).strip() or "low"
    selected_tier = str(tier_override).strip() or active_tier
    if selected_tier not in tiers:
        selected_tier = active_tier if active_tier in tiers else "off"
    profile = tiers.get(selected_tier, {"enabled": False})
    if not isinstance(profile, dict):
        profile = {"enabled": False}
    return {
        "selected_tier": selected_tier,
        "active_tier": active_tier,
        "enabled": bool(profile.get("enabled", False)),
        "knowledge": dict(profile.get("knowledge", {})) if isinstance(profile.get("knowledge", {}), dict) else {},
        "memory": dict(profile.get("memory", {})) if isinstance(profile.get("memory", {}), dict) else {},
    }


def merge_cfg(base_cfg: dict[str, Any], override_cfg: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_cfg)
    for key, value in override_cfg.items():
        if value is None:
            continue
        merged[key] = value
    return merged


def run_step(command: list[str]) -> dict[str, Any]:
    started_at = now_iso()
    proc = subprocess.run(command, text=True, capture_output=True)
    ended_at = now_iso()
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    return {
        "command": command,
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_code": int(proc.returncode),
        "stdout_tail": "\n".join(out.splitlines()[-20:]) if out else "",
        "stderr_tail": "\n".join(err.splitlines()[-20:]) if err else "",
    }


def parse_json_output(stdout: str) -> dict[str, Any]:
    payload = stdout.strip()
    if not payload:
        raise ValueError("empty stdout")
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


def run_json(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(command, text=True, capture_output=True)
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(command)} :: {stderr or stdout}")
    return parse_json_output(stdout)


def bridge_policy(policy: dict[str, Any], enabled: bool) -> dict[str, Any]:
    cfg = dict(policy.get("bridge", {}))
    cfg.setdefault("enabled", enabled)
    cfg.setdefault("status_limit", 20)
    cfg.setdefault("autopilot_blocking_statuses", ["in_progress", "claimed"])
    cfg.setdefault("skip_autopilot_when_conflict", True)
    cfg.setdefault("evaluate_with_recent_roots", True)
    cfg.setdefault("conflict_window_days", 7)
    cfg.setdefault("max_blocked_roots_for_autopilot", 999999)
    cfg.setdefault("max_timed_out_roots_for_autopilot", 999999)
    cfg.setdefault("max_queued_roots_for_autopilot", 999999)
    cfg.setdefault("timeout_minutes", 30)
    cfg.setdefault("timeout_limit", 200)
    return cfg


def lifecycle_status(runtime: str, limit: int) -> dict[str, Any]:
    return run_json(
        [
            "/usr/bin/python3",
            str(LIFECYCLE_SCRIPT),
            "status",
            "--runtime",
            runtime,
            "--limit",
            str(limit),
        ]
    )


def lifecycle_timeout_scan(runtime: str, timeout_minutes: int, limit: int, actor: str = "unique-fusion") -> dict[str, Any]:
    return run_json(
        [
            "/usr/bin/python3",
            str(LIFECYCLE_SCRIPT),
            "timeout-scan",
            "--runtime",
            runtime,
            "--timeout-minutes",
            str(timeout_minutes),
            "--limit",
            str(limit),
            "--actor",
            actor,
        ]
    )


def recent_root_counts(snapshot: dict[str, Any], window_days: int) -> dict[str, int]:
    rows = snapshot.get("latest_root_tasks", [])
    if not isinstance(rows, list):
        return {}
    now = dt.datetime.now().astimezone()
    window = dt.timedelta(days=max(1, int(window_days)))
    counts: dict[str, int] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        raw_ts = str(item.get("updated_at", "")).strip()
        if not raw_ts:
            continue
        try:
            updated_at = dt.datetime.fromisoformat(raw_ts)
        except ValueError:
            continue
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=now.tzinfo)
        if now - updated_at > window:
            continue
        status = str(item.get("status", "unknown")).strip() or "unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def evaluate_bridge_conflict(snapshot: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    counts_raw = snapshot.get("status_counts", {})
    counts_all = {str(k): int(v) for k, v in counts_raw.items()} if isinstance(counts_raw, dict) else {}
    use_recent = bool(cfg.get("evaluate_with_recent_roots", True))
    window_days = int(cfg.get("conflict_window_days", 7))
    counts_recent = recent_root_counts(snapshot, window_days) if use_recent else {}
    counts = counts_recent if use_recent and counts_recent else counts_all
    scope = "recent_roots" if use_recent and counts_recent else "all_tasks"

    blocking_statuses = [str(v) for v in cfg.get("autopilot_blocking_statuses", [])]
    blocking_active_total = sum(int(counts.get(status, 0)) for status in blocking_statuses)
    blocked_total = int(counts.get("blocked", 0))
    timed_out_total = int(counts.get("timed_out", 0))
    queued_total = int(counts.get("queued", 0))
    reasons: list[str] = []

    if blocking_active_total > 0:
        reasons.append(f"active statuses present ({scope}): {blocking_statuses}={blocking_active_total}")
    if blocked_total > int(cfg.get("max_blocked_roots_for_autopilot", 999999)):
        reasons.append(f"blocked roots exceed threshold ({scope}): {blocked_total}")
    if timed_out_total > int(cfg.get("max_timed_out_roots_for_autopilot", 999999)):
        reasons.append(f"timed_out roots exceed threshold ({scope}): {timed_out_total}")
    if queued_total > int(cfg.get("max_queued_roots_for_autopilot", 999999)):
        reasons.append(f"queued roots exceed threshold ({scope}): {queued_total}")

    return {
        "has_conflict": bool(reasons),
        "reasons": reasons,
        "scope": scope,
        "counts_scope": counts,
        "counts_all": counts_all,
        "counts_recent_roots": counts_recent,
        "conflict_window_days": window_days,
        "blocking_statuses": blocking_statuses,
    }


def pending_status(month_key: str) -> dict[str, Any]:
    memory_pipeline = load_module(MEMORY_PIPELINE_SCRIPT, "memory_pipeline_status")
    knowledge_base = load_module(KNOWLEDGE_BASE_SCRIPT, "knowledge_base_status")

    memory_pending: dict[str, int] = {}
    for agent in memory_pipeline.iter_target_agents("all"):
        rows = memory_pipeline.list_review_candidates(agent, days=30, statuses=("pending", "deferred"), limit=10000)
        memory_pending[agent.agent_id] = len(rows)

    knowledge_rows = knowledge_base.collect_review_queue_docs(month_key, 10000)
    knowledge_pending = [row for row in knowledge_rows if str(row.get("status", "")).strip() == "pending_review"]

    return {
        "generated_at": now_iso(),
        "month": month_key,
        "memory_pending_by_agent": memory_pending,
        "memory_pending_total": sum(memory_pending.values()),
        "knowledge_pending_total": len(knowledge_pending),
    }


def apply_knowledge_autopilot(
    month_key: str,
    policy: dict[str, Any],
    rollout_profile: dict[str, Any],
    max_override: int | None = None,
) -> dict[str, Any]:
    cfg = dict(policy.get("knowledge", {}))
    cfg = merge_cfg(cfg, dict(rollout_profile.get("knowledge", {})))
    if not cfg.get("enabled", True):
        return {"enabled": False, "applied": []}

    knowledge_base = load_module(KNOWLEDGE_BASE_SCRIPT, "knowledge_base_autopilot")
    decision = str(cfg.get("decision", "observe"))
    reviewer = str(cfg.get("reviewer", "main"))
    note = str(cfg.get("note", "auto triage by unique fusion orchestrator"))
    allowed_types = set(str(v) for v in cfg.get("allowed_candidate_types", []))
    blocked_keywords = [str(v) for v in cfg.get("blocked_keywords", [])]
    max_items = int(cfg.get("max_items_per_run", 8))
    if max_override is not None:
        max_items = min(max_items, max_override)

    rows = knowledge_base.collect_review_queue_docs(month_key, 10000)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in rows:
        if len(applied) >= max_items:
            break
        if str(row.get("status", "")).strip() != "pending_review":
            continue
        candidate_type = str(row.get("candidate_type", "")).strip()
        if allowed_types and candidate_type not in allowed_types:
            skipped.append({"id": row.get("id"), "reason": "candidate_type_not_allowed"})
            continue
        title = str(row.get("title", ""))
        summary = str(row.get("summary", ""))
        if contains_blocked([title, summary], blocked_keywords):
            skipped.append({"id": row.get("id"), "reason": "blocked_keyword"})
            continue
        review_id = str(row["id"])
        result = knowledge_base.review_decide(month_key, review_id, decision, reviewer, note)
        applied.append(
            {
                "review_id": review_id,
                "title": title,
                "candidate_type": candidate_type,
                "decision": decision,
                "result": result,
            }
        )

    return {
        "enabled": True,
        "decision": decision,
        "max_items": max_items,
        "applied": applied,
        "skipped": skipped,
    }


def apply_memory_autopilot(
    policy: dict[str, Any],
    rollout_profile: dict[str, Any],
    max_override: int | None = None,
) -> dict[str, Any]:
    cfg = dict(policy.get("memory", {}))
    cfg = merge_cfg(cfg, dict(rollout_profile.get("memory", {})))
    if not cfg.get("enabled", True):
        return {"enabled": False, "applied": [], "applied_per_agent": {}}

    memory_pipeline = load_module(MEMORY_PIPELINE_SCRIPT, "memory_pipeline_autopilot")
    status = str(cfg.get("status", "approved"))
    reviewer = str(cfg.get("reviewer", "main"))
    note = str(cfg.get("note", "auto triage by unique fusion orchestrator"))
    days = int(cfg.get("days", 14))
    limit = int(cfg.get("limit", 200))
    min_confidence = float(cfg.get("min_confidence", 0.78))
    min_priority = int(cfg.get("min_priority", 3))
    allowed_types = set(str(v) for v in cfg.get("allowed_candidate_types", []))
    blocked_keywords = [str(v) for v in cfg.get("blocked_keywords", [])]
    max_per_agent = int(cfg.get("max_items_per_agent", 5))
    max_total = int(cfg.get("max_items_total", 20))
    if max_override is not None:
        max_total = min(max_total, max_override)

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    applied_per_agent: dict[str, int] = {}
    total = 0

    for agent in memory_pipeline.iter_target_agents("all"):
        if total >= max_total:
            break
        rows = memory_pipeline.list_review_candidates(agent, days=days, statuses=("pending", "deferred"), limit=limit)
        applied_per_agent[agent.agent_id] = 0
        for row in rows:
            if total >= max_total or applied_per_agent[agent.agent_id] >= max_per_agent:
                break
            candidate_type = str(row["candidate_type"])
            confidence = float(row["confidence"])
            priority = int(row["priority"])
            if allowed_types and candidate_type not in allowed_types:
                skipped.append({"agent": agent.agent_id, "id": int(row["id"]), "reason": "candidate_type_not_allowed"})
                continue
            if confidence < min_confidence:
                skipped.append({"agent": agent.agent_id, "id": int(row["id"]), "reason": "confidence_too_low"})
                continue
            if priority < min_priority:
                skipped.append({"agent": agent.agent_id, "id": int(row["id"]), "reason": "priority_too_low"})
                continue
            if contains_blocked([str(row["title"]), str(row["summary"]), str(row["evidence"])], blocked_keywords):
                skipped.append({"agent": agent.agent_id, "id": int(row["id"]), "reason": "blocked_keyword"})
                continue
            changed = memory_pipeline.update_review_status(agent, int(row["id"]), status, note, reviewer)
            if changed:
                applied.append(
                    {
                        "agent": agent.agent_id,
                        "review_id": int(row["id"]),
                        "status": status,
                        "title": str(row["title"]),
                        "candidate_type": candidate_type,
                        "confidence": confidence,
                        "priority": priority,
                    }
                )
                applied_per_agent[agent.agent_id] += 1
                total += 1

    applied_refs: dict[str, int] = {}
    for agent in memory_pipeline.iter_target_agents("all"):
        applied_refs[agent.agent_id] = memory_pipeline.apply_approved_reviews(agent)

    return {
        "enabled": True,
        "status": status,
        "max_total": max_total,
        "applied": applied,
        "applied_per_agent": applied_per_agent,
        "materialized_per_agent": applied_refs,
        "skipped": skipped,
    }


def run_cycle(
    runtime: str,
    month: str,
    days: int,
    knowledge_limit: int,
    review_limit: int,
    apply_auto: bool,
    max_knowledge_auto: int,
    max_memory_auto: int,
    with_bridge_guard: bool,
    bridge_timeout_scan_enabled: bool,
    bridge_timeout_minutes: int,
    bridge_timeout_limit: int,
    autopilot_tier: str,
) -> dict[str, Any]:
    policy = load_policy()
    rollout_policy = load_rollout_policy()
    rollout_profile = resolve_rollout_profile(rollout_policy, autopilot_tier)
    bridge_cfg = bridge_policy(policy, enabled=with_bridge_guard)

    steps: list[dict[str, Any]] = []
    python_bin = "/usr/bin/python3"
    bridge_guard: dict[str, Any] = {
        "enabled": bool(bridge_cfg.get("enabled", False)),
    }

    if bridge_guard["enabled"]:
        try:
            current_status = lifecycle_status(runtime, int(bridge_cfg.get("status_limit", 20)))
            bridge_guard["pre_status"] = current_status
            if bridge_timeout_scan_enabled:
                timeout_payload = lifecycle_timeout_scan(
                    runtime=runtime,
                    timeout_minutes=bridge_timeout_minutes or int(bridge_cfg.get("timeout_minutes", 30)),
                    limit=bridge_timeout_limit or int(bridge_cfg.get("timeout_limit", 200)),
                )
                bridge_guard["timeout_scan"] = timeout_payload
                current_status = lifecycle_status(runtime, int(bridge_cfg.get("status_limit", 20)))
                bridge_guard["post_timeout_status"] = current_status
            bridge_guard["conflict"] = evaluate_bridge_conflict(current_status, bridge_cfg)
        except Exception as exc:
            bridge_guard["error"] = str(exc)
            bridge_guard["conflict"] = {
                "has_conflict": True,
                "reasons": [f"bridge_guard_error: {exc}"],
                "counts": {},
                "blocking_statuses": [],
            }

    commands = [
        [python_bin, str(MEMORY_PIPELINE_SCRIPT), "capture", "--agent", "all"],
        [python_bin, str(MEMORY_PIPELINE_SCRIPT), "extract", "--agent", "all"],
        [python_bin, str(MEMORY_PIPELINE_SCRIPT), "review-report", "--agent", "all", "--days", str(days), "--limit", str(knowledge_limit)],
        [python_bin, str(KNOWLEDGE_BASE_SCRIPT), "convergence-bootstrap", "--runtime", runtime, "--month", month, "--limit", str(knowledge_limit), "--review-limit", str(review_limit)],
        [python_bin, str(KNOWLEDGE_BASE_SCRIPT), "convergence-report", "--runtime", runtime, "--limit", str(knowledge_limit)],
        [python_bin, str(KNOWLEDGE_BASE_SCRIPT), "convergence-workbench", "--runtime", runtime, "--month", month, "--limit", str(knowledge_limit), "--review-limit", str(review_limit)],
        [python_bin, str(KNOWLEDGE_BASE_SCRIPT), "review-report", "--month", month, "--limit", str(knowledge_limit)],
        [python_bin, str(KNOWLEDGE_BASE_SCRIPT), "lint"],
    ]

    for command in commands:
        step = run_step(command)
        steps.append(step)
        if step["exit_code"] != 0:
            return {
                "status": "failed",
                "generated_at": now_iso(),
                "runtime": runtime,
                "month": month,
                "policy": policy,
                "rollout_policy": rollout_policy,
                "rollout_profile": rollout_profile,
                "steps": steps,
                "bridge_guard": bridge_guard,
            }

    autopilot: dict[str, Any] = {"enabled": apply_auto}
    if apply_auto:
        conflict = bridge_guard.get("conflict", {})
        should_skip = bool(bridge_guard.get("enabled")) and bool(bridge_cfg.get("skip_autopilot_when_conflict", True)) and bool(conflict.get("has_conflict"))
        if should_skip:
            autopilot = {
                "enabled": False,
                "skipped": True,
                "reason": "bridge_guard_conflict",
                "conflict": conflict,
            }
        elif not bool(rollout_profile.get("enabled", False)):
            autopilot = {
                "enabled": False,
                "skipped": True,
                "reason": "autopilot_rollout_tier_disabled",
                "rollout_tier": rollout_profile.get("selected_tier", "off"),
            }
        else:
            autopilot = {
                "enabled": True,
                "rollout_tier": rollout_profile.get("selected_tier", "off"),
                "knowledge": apply_knowledge_autopilot(month, policy, rollout_profile, max_override=max_knowledge_auto),
                "memory": apply_memory_autopilot(policy, rollout_profile, max_override=max_memory_auto),
            }

    return {
        "status": "ok",
        "generated_at": now_iso(),
        "runtime": runtime,
        "month": month,
        "policy": policy,
        "rollout_policy": rollout_policy,
        "rollout_profile": rollout_profile,
        "steps": steps,
        "bridge_guard": bridge_guard,
        "autopilot": autopilot,
        "pending": pending_status(month),
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Unique Fusion Cycle Report",
        "",
        f"- status: `{report.get('status', 'unknown')}`",
        f"- generated_at: `{report.get('generated_at', '')}`",
        f"- runtime: `{report.get('runtime', '')}`",
        f"- month: `{report.get('month', '')}`",
        "",
        "## Pipeline Steps",
        "",
    ]

    for step in report.get("steps", []):
        cmd = " ".join(step.get("command", []))
        lines.extend(
            [
                f"- exit={step.get('exit_code')} :: `{cmd}`",
            ]
        )

    autopilot = report.get("autopilot", {})
    if autopilot.get("enabled"):
        lines.extend(["", "## Autopilot", ""])
        knowledge = autopilot.get("knowledge", {})
        memory = autopilot.get("memory", {})
        lines.append(f"- rollout_tier: `{autopilot.get('rollout_tier', 'unknown')}`")
        lines.append(f"- knowledge_applied: `{len(knowledge.get('applied', []))}`")
        lines.append(f"- memory_applied: `{len(memory.get('applied', []))}`")
    elif autopilot.get("skipped"):
        lines.extend(["", "## Autopilot", ""])
        lines.append("- skipped: `true`")
        lines.append(f"- reason: `{autopilot.get('reason', 'unknown')}`")

    bridge_guard = report.get("bridge_guard", {})
    if bridge_guard.get("enabled"):
        lines.extend(["", "## Bridge Guard", ""])
        conflict = bridge_guard.get("conflict", {})
        lines.append(f"- has_conflict: `{bool(conflict.get('has_conflict'))}`")
        reasons = conflict.get("reasons", [])
        if reasons:
            for reason in reasons:
                lines.append(f"- conflict_reason: `{reason}`")
        counts = conflict.get("counts", {})
        scope = str(conflict.get("scope", "all_tasks"))
        lines.append(f"- scope: `{scope}`")
        counts_scope = conflict.get("counts_scope", {})
        if isinstance(counts_scope, dict) and counts_scope:
            lines.append(f"- status_counts(scope): `{counts_scope}`")
        counts_all = conflict.get("counts_all", {})
        if isinstance(counts_all, dict) and counts_all:
            lines.append(f"- status_counts(all): `{counts_all}`")

    pending = report.get("pending", {})
    if pending:
        lines.extend(
            [
                "",
                "## Pending Snapshot",
                "",
                f"- knowledge_pending_total: `{pending.get('knowledge_pending_total', 0)}`",
                f"- memory_pending_total: `{pending.get('memory_pending_total', 0)}`",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def write_report(report: dict[str, Any]) -> dict[str, str]:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    stem = f"{stamp}-{safe_slug(str(report.get('runtime', 'live')))}"
    json_path = REPORT_ROOT / f"{stem}.json"
    md_path = REPORT_ROOT / f"{stem}.md"
    write_text(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write_text(md_path, report_markdown(report))
    try:
        knowledge_base = load_module(KNOWLEDGE_BASE_SCRIPT, "knowledge_base_report_index")
        register = getattr(knowledge_base, "register_index", None)
        if callable(register):
            for existing in sorted(REPORT_ROOT.glob("*.md")):
                title = f"Fusion Cycle Report {existing.stem}"
                register(title, existing, "unique fusion orchestration report")
    except Exception:
        # Report creation should not fail because of indexing issues.
        pass
    return {"json": str(json_path), "markdown": str(md_path)}


def cmd_status(args: argparse.Namespace) -> None:
    month = args.month or dt.date.today().strftime("%Y-%m")
    payload: dict[str, Any] = {"status": "ok", **pending_status(month)}
    rollout_policy = load_rollout_policy()
    payload["autopilot_rollout"] = {
        "active_tier": rollout_policy.get("active_tier", "low"),
        "available_tiers": sorted(list((rollout_policy.get("tiers", {}) or {}).keys())),
    }
    if args.with_bridge_guard:
        policy = load_policy()
        cfg = bridge_policy(policy, enabled=True)
        try:
            snapshot = lifecycle_status(args.runtime, int(cfg.get("status_limit", 20)))
            payload["bridge_guard"] = {
                "enabled": True,
                "status": snapshot,
                "conflict": evaluate_bridge_conflict(snapshot, cfg),
            }
        except Exception as exc:
            payload["bridge_guard"] = {"enabled": True, "error": str(exc)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    month = args.month or dt.date.today().strftime("%Y-%m")
    report = run_cycle(
        runtime=args.runtime,
        month=month,
        days=args.days,
        knowledge_limit=args.knowledge_limit,
        review_limit=args.review_limit,
        apply_auto=args.apply_auto,
        max_knowledge_auto=args.max_knowledge_auto,
        max_memory_auto=args.max_memory_auto,
        with_bridge_guard=args.with_bridge_guard,
        bridge_timeout_scan_enabled=args.bridge_timeout_scan,
        bridge_timeout_minutes=args.bridge_timeout_minutes,
        bridge_timeout_limit=args.bridge_timeout_limit,
        autopilot_tier=args.autopilot_tier,
    )
    report_paths = write_report(report)
    payload = {**report, "report_paths": report_paths}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if report.get("status") != "ok":
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unique-style orchestration layer for QYclaw memory+knowledge loop")
    sub = parser.add_subparsers(dest="cmd", required=True)

    status_cmd = sub.add_parser("status")
    status_cmd.add_argument("--month", default="")
    status_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    status_cmd.add_argument("--with-bridge-guard", action=argparse.BooleanOptionalAction, default=True)
    status_cmd.set_defaults(func=cmd_status)

    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    run_cmd.add_argument("--month", default="")
    run_cmd.add_argument("--days", type=int, default=14)
    run_cmd.add_argument("--knowledge-limit", type=int, default=80)
    run_cmd.add_argument("--review-limit", type=int, default=10000)
    run_cmd.add_argument("--apply-auto", action="store_true")
    run_cmd.add_argument("--max-knowledge-auto", type=int, default=8)
    run_cmd.add_argument("--max-memory-auto", type=int, default=20)
    run_cmd.add_argument("--with-bridge-guard", action=argparse.BooleanOptionalAction, default=True)
    run_cmd.add_argument("--bridge-timeout-scan", action="store_true")
    run_cmd.add_argument("--bridge-timeout-minutes", type=int, default=30)
    run_cmd.add_argument("--bridge-timeout-limit", type=int, default=200)
    run_cmd.add_argument("--autopilot-tier", default="", choices=["", "off", "low", "medium", "high"])
    run_cmd.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
