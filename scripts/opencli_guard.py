#!/usr/bin/env python3
"""Policy guard + circuit breaker + auto fallback executor for OpenCLI in QYclaw."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POLICY_PATH = Path(str(Path.home() / ".qyclaw/workspace/config/opencli-policy.json"))
ROUTER_PATH = Path(str(Path.home() / ".qyclaw/workspace/scripts/opencli_router.py"))
TAVILY_SCRIPT = Path(str(Path.home() / ".qyclaw/workspace/skills/tavily-search/scripts/tavily_search.py"))
LOG_PATH = Path(str(Path.home() / ".qyclaw/logs/opencli-invocations.jsonl"))
STATE_PATH = Path(str(Path.home() / ".qyclaw/state/opencli-circuit.json"))
ALERT_LOG = Path(str(Path.home() / ".qyclaw/logs/maintenance-alert.log"))

WRITE_VERBS = (
    "post",
    "publish",
    "send",
    "comment",
    "reply",
    "like",
    "follow",
    "unfollow",
    "delete",
    "remove",
    "create",
    "update",
    "edit",
    "submit",
)

BRIDGE_ERROR_PATTERNS = (
    "BROWSER_CONNECT",
    "Debugger is not attached",
    "fetch failed",
    "extension not connected",
    "Browser Bridge",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def load_policy() -> dict[str, Any]:
    if not POLICY_PATH.exists():
        return {
            "version": 1,
            "defaultDenyWrite": True,
            "blockedSites": [],
            "blockedCommands": [],
            "circuitBreaker": {
                "enabled": True,
                "failureThreshold": 3,
                "windowSeconds": 900,
                "cooldownSeconds": 600,
            },
            "agentAllowSites": {"main": ["*"]},
        }
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"version": 1, "updatedAt": now_iso(), "commands": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid state")
        data.setdefault("version", 1)
        data.setdefault("updatedAt", now_iso())
        data.setdefault("commands", {})
        return data
    except Exception:
        return {"version": 1, "updatedAt": now_iso(), "commands": {}}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state["updatedAt"] = now_iso()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_alert(msg: str) -> None:
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}] ALERT [opencli] {msg}\n")


def run_cmd(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parsed = None
    if out:
        try:
            parsed = json.loads(out)
        except Exception:
            parsed = None
    return {
        "ok": proc.returncode == 0,
        "code": proc.returncode,
        "cmd": cmd,
        "stdout": out,
        "stderr": err,
        "json": parsed,
    }


def load_manifest() -> list[dict[str, Any]]:
    result = run_cmd(["python3", str(ROUTER_PATH), "discover"])
    if not result["ok"]:
        raise RuntimeError(result["stderr"] or "discover failed")
    rows = (result["json"] or {}).get("json")
    if not isinstance(rows, list):
        return []
    return rows


def find_command(rows: list[dict[str, Any]], site: str, command: str) -> dict[str, Any] | None:
    s = site.lower().strip()
    c = command.lower().strip()
    for row in rows:
        if str(row.get("site", "")).lower() == s and str(row.get("name", "")).lower() == c:
            return row
    return None


def is_write_command(command: str) -> bool:
    c = command.lower()
    return any(v in c for v in WRITE_VERBS)


def site_allowed(policy: dict[str, Any], agent: str, site: str) -> bool:
    allow_map = policy.get("agentAllowSites", {})
    allow_list = allow_map.get(agent) or allow_map.get("main") or []
    if "*" in allow_list:
        return True
    return site in allow_list


def blocked_by_policy(policy: dict[str, Any], site: str, command: str) -> str | None:
    if site in (policy.get("blockedSites") or []):
        return f"site_blocked:{site}"
    for item in policy.get("blockedCommands") or []:
        if item.get("site") == site and item.get("command") == command:
            return f"command_blocked:{site}/{command}"
    return None


def key_of(site: str, command: str) -> str:
    return f"{site.lower().strip()}/{command.lower().strip()}"


def get_circuit_cfg(policy: dict[str, Any]) -> dict[str, Any]:
    cfg = policy.get("circuitBreaker") or {}
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "failureThreshold": int(cfg.get("failureThreshold", 3)),
        "windowSeconds": int(cfg.get("windowSeconds", 900)),
        "cooldownSeconds": int(cfg.get("cooldownSeconds", 600)),
    }


def circuit_status(state: dict[str, Any], key: str, now: int) -> tuple[bool, int]:
    cmd_state = (state.get("commands") or {}).get(key) or {}
    open_until = int(cmd_state.get("openUntil", 0) or 0)
    return (open_until > now, open_until)


def update_circuit(
    state: dict[str, Any],
    key: str,
    ok: bool,
    code: int,
    error: str,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    commands = state.setdefault("commands", {})
    row = commands.setdefault(key, {})
    now = now_ts()
    history = [int(x) for x in row.get("failures", []) if isinstance(x, int) or str(x).isdigit()]
    cutoff = now - cfg["windowSeconds"]
    history = [x for x in history if x >= cutoff]

    if ok:
        row["failures"] = []
        row["openUntil"] = 0
        row["lastCode"] = code
        row["lastError"] = ""
        row["lastSuccessAt"] = now
        return row

    history.append(now)
    row["failures"] = history
    row["lastCode"] = code
    row["lastError"] = error[:500]
    row["lastFailureAt"] = now

    if len(history) >= cfg["failureThreshold"]:
        row["openUntil"] = now + cfg["cooldownSeconds"]
    else:
        row["openUntil"] = int(row.get("openUntil", 0) or 0)
    return row


def is_bridge_error(text: str) -> bool:
    source = (text or "").lower()
    return any(p.lower() in source for p in BRIDGE_ERROR_PATTERNS)


def extract_limit(argv: list[str], default: int = 5) -> int:
    for i, token in enumerate(argv):
        if token in ("--limit", "-l") and i + 1 < len(argv):
            try:
                return max(1, min(int(argv[i + 1]), 10))
            except Exception:
                return default
        m = re.match(r"^--limit=(\d+)$", token)
        if m:
            try:
                return max(1, min(int(m.group(1)), 10))
            except Exception:
                return default
    return default


def build_fallback_query(site: str, command: str, argv: list[str]) -> str:
    terms = [site, command]
    for token in argv:
        if token.startswith("-"):
            continue
        terms.append(token)
    return " ".join(terms).strip()


def auto_bridge_recover_and_retry(
    exec_cmd: list[str],
    domain: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    recovery_steps = []
    recovery_steps.append(run_cmd(["opencli", "daemon", "stop"]))
    probe_url = f"https://{domain}" if domain else "https://www.baidu.com"
    recovery_steps.append(run_cmd(["opencli", "browser", "open", probe_url]))
    retry = run_cmd(exec_cmd)
    return retry, {"steps": recovery_steps, "probeUrl": probe_url}


def auto_tavily_fallback(site: str, command: str, argv: list[str]) -> dict[str, Any]:
    if not TAVILY_SCRIPT.exists():
        return {"ok": False, "reason": "tavily_script_missing"}
    query = build_fallback_query(site, command, argv)
    limit = extract_limit(argv, default=5)
    result = run_cmd(
        [
            "python3",
            str(TAVILY_SCRIPT),
            query,
            "--max-results",
            str(limit),
            "--raw",
        ]
    )
    if result["ok"]:
        return {
            "ok": True,
            "engine": "tavily-search",
            "query": query,
            "maxResults": limit,
            "result": result,
        }
    return {
        "ok": False,
        "engine": "tavily-search",
        "query": query,
        "maxResults": limit,
        "error": result.get("stderr", "")[:500],
    }


def fallback_recommendation(strategy: str) -> dict[str, str]:
    s = (strategy or "").lower()
    if s in ("cookie", "intercept", "ui", "header"):
        return {
            "engine": "playwright-cli",
            "skill": "playwright-cli",
            "reason": "browser/login-sensitive path failed",
        }
    return {
        "engine": "tavily-search",
        "skill": "tavily-search",
        "reason": "public/local adapter failed",
    }


def log_invocation(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def print_json(data: dict[str, Any], exit_code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def cmd_route(args: argparse.Namespace) -> None:
    policy = load_policy()
    rows = load_manifest()
    row = find_command(rows, args.site, args.command)
    if row is None:
        print_json(
            {
                "ok": False,
                "reason": "command_not_found",
                "site": args.site,
                "command": args.command,
                "engine": "none",
            },
            2,
        )

    blocked = blocked_by_policy(policy, args.site, args.command)
    if blocked:
        print_json(
            {
                "ok": False,
                "reason": blocked,
                "site": args.site,
                "command": args.command,
                "engine": "blocked",
            },
            3,
        )

    if not site_allowed(policy, args.agent, args.site):
        print_json(
            {
                "ok": False,
                "reason": "agent_site_not_allowed",
                "agent": args.agent,
                "site": args.site,
                "command": args.command,
                "engine": "blocked",
            },
            4,
        )

    write_like = is_write_command(args.command)
    deny_write = bool(policy.get("defaultDenyWrite", True))
    if write_like and deny_write and not args.allow_write:
        print_json(
            {
                "ok": False,
                "reason": "write_denied_by_default",
                "agent": args.agent,
                "site": args.site,
                "command": args.command,
                "strategy": row.get("strategy"),
                "engine": "blocked",
                "hint": "re-run with --allow-write if explicitly approved",
            },
            5,
        )

    cfg = get_circuit_cfg(policy)
    if cfg["enabled"] and not args.ignore_circuit:
        state = load_state()
        key = key_of(args.site, args.command)
        opened, open_until = circuit_status(state, key, now_ts())
        if opened:
            print_json(
                {
                    "ok": False,
                    "reason": "circuit_open",
                    "agent": args.agent,
                    "site": args.site,
                    "command": args.command,
                    "strategy": row.get("strategy"),
                    "engine": "blocked",
                    "retryAt": datetime.fromtimestamp(open_until, tz=timezone.utc).isoformat(),
                },
                6,
            )

    print_json(
        {
            "ok": True,
            "agent": args.agent,
            "site": args.site,
            "command": args.command,
            "strategy": row.get("strategy"),
            "domain": row.get("domain"),
            "engine": "opencli",
            "writeLike": write_like,
            "circuitIgnored": bool(args.ignore_circuit),
        },
        0,
    )


def cmd_run(args: argparse.Namespace) -> None:
    route_cmd = [
        "python3",
        str(Path(__file__).resolve()),
        "route",
        "--agent",
        args.agent,
        "--site",
        args.site,
        "--command",
        args.command,
    ]
    if args.allow_write:
        route_cmd.append("--allow-write")
    if args.ignore_circuit:
        route_cmd.append("--ignore-circuit")

    route_proc = run_cmd(route_cmd)
    route_out = route_proc.get("stdout", "")
    route_data: dict[str, Any] = {}
    if route_out:
        try:
            route_data = json.loads(route_out)
        except Exception:
            route_data = {"ok": False, "reason": "route_parse_failed", "raw": route_out}

    if route_proc["code"] != 0:
        print(route_out or route_proc.get("stderr", ""))
        sys.exit(route_proc["code"])

    raw_args = list(args.args)
    if raw_args[:1] == ["--"]:
        raw_args = raw_args[1:]

    exec_cmd = [
        "python3",
        str(ROUTER_PATH),
        "run",
        "--format",
        args.format,
        args.site,
        args.command,
    ] + raw_args

    started = now_iso()
    primary = run_cmd(exec_cmd)
    primary_ok = bool(primary["ok"])
    retry_info = None

    # Auto bridge recover + retry once for bridge-like failures.
    if (not primary_ok) and (not args.no_auto_fallback):
        combined_err = f"{primary.get('stderr','')}\n{primary.get('stdout','')}"
        if is_bridge_error(combined_err):
            retried, recover_meta = auto_bridge_recover_and_retry(exec_cmd, route_data.get("domain"))
            retry_info = {"recovery": recover_meta, "retry": retried}
            if retried["ok"]:
                primary = retried
                primary_ok = True

    fallback = None
    degraded = False
    final_ok = primary_ok
    final_code = int(primary.get("code", 1))

    # Auto fallback to Tavily for read tasks when primary still failed.
    if (not primary_ok) and (not args.no_auto_fallback) and (route_data.get("writeLike") is False):
        fallback = auto_tavily_fallback(args.site, args.command, raw_args)
        if fallback.get("ok") is True:
            degraded = True
            final_ok = True
            final_code = 0
        else:
            # keep recommendation for operator if fallback itself fails
            fallback = {
                "ok": False,
                "attempt": fallback,
                "recommendation": fallback_recommendation(str(route_data.get("strategy", ""))),
            }

    ended = now_iso()

    policy = load_policy()
    cfg = get_circuit_cfg(policy)
    state = load_state()
    ckey = key_of(args.site, args.command)
    circuit_after = {}
    if cfg["enabled"]:
        circuit_row = update_circuit(
            state=state,
            key=ckey,
            ok=primary_ok,
            code=int(primary.get("code", 1)),
            error=str(primary.get("stderr", "")),
            cfg=cfg,
        )
        save_state(state)
        circuit_after = {
            "failuresInWindow": len(circuit_row.get("failures", [])),
            "openUntil": int(circuit_row.get("openUntil", 0) or 0),
        }
        if circuit_after["openUntil"] > now_ts():
            append_alert(
                f"circuit open for {ckey}; cooldown={cfg['cooldownSeconds']}s; "
                f"failures={circuit_after['failuresInWindow']}"
            )

    log_invocation(
        {
            "tsStart": started,
            "tsEnd": ended,
            "agent": args.agent,
            "site": args.site,
            "command": args.command,
            "argv": raw_args,
            "allowWrite": args.allow_write,
            "ignoreCircuit": args.ignore_circuit,
            "autoFallback": not args.no_auto_fallback,
            "route": route_data,
            "ok": final_ok,
            "primaryOk": primary_ok,
            "degraded": degraded,
            "code": final_code,
            "primaryCode": int(primary.get("code", 1)),
            "error": str(primary.get("stderr", ""))[:2000],
            "circuit": circuit_after,
            "retryInfo": retry_info,
            "fallback": fallback,
        }
    )

    output = {
        "ok": final_ok,
        "primaryOk": primary_ok,
        "degraded": degraded,
        "route": route_data,
        "result": primary,
        "retryInfo": retry_info,
        "circuit": circuit_after,
        "fallback": fallback,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not final_ok:
        sys.exit(final_code)


def cmd_metrics(args: argparse.Namespace) -> None:
    from opencli_metrics import build_summary

    summary = build_summary(hours=args.hours, log_path=LOG_PATH)
    print_json(summary, 0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenCLI policy guard and executor")
    sub = p.add_subparsers(dest="action", required=True)

    route = sub.add_parser("route", help="Evaluate route and policy for a command")
    route.add_argument("--agent", default=os.environ.get("QYCLAW_AGENT_ID", "main"))
    route.add_argument("--site", required=True)
    route.add_argument("--command", required=True)
    route.add_argument("--allow-write", action="store_true")
    route.add_argument("--ignore-circuit", action="store_true")
    route.set_defaults(func=cmd_route)

    run = sub.add_parser("run", help="Run command with policy guard")
    run.add_argument("--agent", default=os.environ.get("QYCLAW_AGENT_ID", "main"))
    run.add_argument("--site", required=True)
    run.add_argument("--command", required=True)
    run.add_argument("--format", default="json", choices=["json", "yaml", "table", "plain", "md", "csv"])
    run.add_argument("--allow-write", action="store_true")
    run.add_argument("--ignore-circuit", action="store_true")
    run.add_argument("--no-auto-fallback", action="store_true")
    run.add_argument("args", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)

    metrics = sub.add_parser("metrics", help="Show invocation summary from logs")
    metrics.add_argument("--hours", type=int, default=24)
    metrics.set_defaults(func=cmd_metrics)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

