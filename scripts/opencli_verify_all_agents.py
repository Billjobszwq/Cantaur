#!/usr/bin/env python3
"""Verify OpenCLI availability and auto-degrade behavior for all QYclaw agents."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GUARD = Path(str(Path.home() / ".qyclaw/workspace/scripts/opencli_guard.py"))


@dataclass(frozen=True)
class AgentCase:
    agent: str
    site: str
    command: str


CASES = [
    AgentCase("main", "hackernews", "top"),
    AgentCase("dev", "v2ex", "latest"),
    AgentCase("content", "36kr", "news"),
    AgentCase("ops", "devto", "top"),
    AgentCase("law", "gitee", "trending"),
    AgentCase("finance", "google", "news"),
    AgentCase("research", "wikipedia", "trending"),
]


def run_guard(args: list[str]) -> tuple[int, dict]:
    proc = subprocess.run(args, capture_output=True, text=True)
    payload = {}
    if proc.stdout.strip():
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            payload = {"ok": False, "raw": proc.stdout[:800]}
    if not payload and proc.stderr.strip():
        payload = {"ok": False, "stderr": proc.stderr[:800]}
    return proc.returncode, payload


def verify_primary(case: AgentCase) -> dict:
    code, out = run_guard(
        [
            "python3",
            str(GUARD),
            "run",
            "--agent",
            case.agent,
            "--site",
            case.site,
            "--command",
            case.command,
            "--",
            "--limit",
            "1",
        ]
    )
    return {
        "agent": case.agent,
        "site": case.site,
        "command": case.command,
        "exit": code,
        "ok": bool(out.get("ok")),
        "primaryOk": bool(out.get("primaryOk")),
        "degraded": bool(out.get("degraded")),
    }


def verify_degrade(case: AgentCase) -> dict:
    code, out = run_guard(
        [
            "python3",
            str(GUARD),
            "run",
            "--agent",
            case.agent,
            "--site",
            case.site,
            "--command",
            case.command,
            "--ignore-circuit",
            "--",
            "--badflag",
        ]
    )
    fb = out.get("fallback") if isinstance(out, dict) else {}
    return {
        "agent": case.agent,
        "site": case.site,
        "command": case.command,
        "exit": code,
        "ok": bool(out.get("ok")),
        "primaryOk": bool(out.get("primaryOk")),
        "degraded": bool(out.get("degraded")),
        "fallbackEngine": (fb or {}).get("engine"),
        "fallbackOk": (fb or {}).get("ok"),
    }


def print_section(title: str, rows: list[dict]) -> None:
    print(title)
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    print("")


def main() -> int:
    primary_rows = [verify_primary(c) for c in CASES]
    degrade_rows = [verify_degrade(c) for c in CASES]

    print_section("PRIMARY", primary_rows)
    print_section("DEGRADE", degrade_rows)

    primary_pass = all(r["exit"] == 0 and r["ok"] and r["primaryOk"] for r in primary_rows)
    degrade_pass = all(
        r["exit"] == 0
        and r["ok"]
        and (not r["primaryOk"])
        and r["degraded"]
        and r["fallbackEngine"] == "tavily-search"
        and r["fallbackOk"] is True
        for r in degrade_rows
    )

    summary = {"primaryPass": primary_pass, "degradePass": degrade_pass, "allPass": primary_pass and degrade_pass}
    print("SUMMARY")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["allPass"] else 1


if __name__ == "__main__":
    sys.exit(main())
