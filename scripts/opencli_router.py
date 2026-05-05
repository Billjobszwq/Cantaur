#!/usr/bin/env python3
"""OpenCLI router wrapper for QYclaw agents.

Provides a stable JSON envelope around opencli invocations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any


def _run(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    parsed: Any = None
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


def _ensure_opencli() -> None:
    if shutil.which("opencli"):
        return
    print(
        json.dumps(
            {
                "ok": False,
                "code": 127,
                "error": "opencli_not_found",
                "hint": "Install with: npm install -g @jackwener/opencli@1.7.8",
            },
            ensure_ascii=False,
        )
    )
    sys.exit(127)


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok", False):
        sys.exit(int(result.get("code", 1)))


def main() -> None:
    parser = argparse.ArgumentParser(description="QYclaw OpenCLI router")
    sub = parser.add_subparsers(dest="action", required=True)

    discover = sub.add_parser("discover", help="List available opencli commands")
    discover.add_argument("--site", help="Filter by site")

    doctor = sub.add_parser("doctor", help="Run opencli doctor")
    doctor.add_argument("--strict", action="store_true", help="Fail if connectivity not green")

    run = sub.add_parser("run", help="Run opencli <site> <command>")
    run.add_argument("site")
    run.add_argument("command")
    run.add_argument("--format", default="json", choices=["json", "yaml", "table", "plain", "md", "csv"])
    run.add_argument("args", nargs=argparse.REMAINDER)

    browser = sub.add_parser("browser", help="Run opencli browser <...>")
    browser.add_argument("args", nargs=argparse.REMAINDER)

    passthrough = sub.add_parser("passthrough", help="Run raw opencli command")
    passthrough.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    _ensure_opencli()

    if args.action == "discover":
        result = _run(["opencli", "list", "-f", "json"])
        if result.get("ok") and args.site and isinstance(result.get("json"), list):
            site = args.site.strip().lower()
            rows = [row for row in result["json"] if str(row.get("site", "")).lower() == site]
            result["json"] = rows
            result["stdout"] = json.dumps(rows, ensure_ascii=False, indent=2)
        _print_result(result)
        return

    if args.action == "doctor":
        result = _run(["opencli", "doctor"])
        if args.strict and "Connectivity: failed" in result.get("stdout", "") + "\n" + result.get("stderr", ""):
            result["ok"] = False
            result["code"] = 3
            result["error"] = "connectivity_failed"
        _print_result(result)
        return

    if args.action == "run":
        raw_args = list(args.args)
        if raw_args[:1] == ["--"]:
            raw_args = raw_args[1:]
        # Guard against duplicated format flags sneaking in via remainder args.
        cleaned: list[str] = []
        i = 0
        while i < len(raw_args):
            token = raw_args[i]
            if token in ("--format", "-f"):
                i += 2
                continue
            cleaned.append(token)
            i += 1
        raw_args = cleaned
        cmd = ["opencli", args.site, args.command, "-f", args.format] + raw_args
        _print_result(_run(cmd))
        return

    if args.action == "browser":
        raw_args = list(args.args)
        if raw_args[:1] == ["--"]:
            raw_args = raw_args[1:]
        _print_result(_run(["opencli", "browser"] + raw_args))
        return

    if args.action == "passthrough":
        raw_args = list(args.args)
        if raw_args[:1] == ["--"]:
            raw_args = raw_args[1:]
        _print_result(_run(["opencli"] + raw_args))
        return


if __name__ == "__main__":
    main()
