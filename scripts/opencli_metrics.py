#!/usr/bin/env python3
"""OpenCLI invocation metrics for QYclaw."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path(str(Path.home() / ".qyclaw/logs/opencli-invocations.jsonl"))


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_float_ms(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return max((end - start).total_seconds() * 1000.0, 0.0)


def build_summary(hours: int = 24, log_path: Path = DEFAULT_LOG_PATH) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=max(hours, 1))

    rows: list[dict[str, Any]] = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            ts = _parse_iso(row.get("tsStart"))
            if ts and ts >= cutoff:
                rows.append(row)

    total = len(rows)
    ok_count = sum(1 for r in rows if r.get("ok") is True)
    fail_count = total - ok_count
    success_rate = (ok_count / total) if total else 0.0
    primary_ok = sum(1 for r in rows if r.get("primaryOk") is True)
    degraded_ok = sum(1 for r in rows if r.get("degraded") is True)

    site_counter = Counter()
    cmd_counter = Counter()
    code_counter = Counter()
    durations = []
    recent_failures = []

    for r in rows:
        site = str(r.get("site", ""))
        cmd = str(r.get("command", ""))
        site_counter[site] += 1
        cmd_counter[f"{site}/{cmd}"] += 1
        code_counter[str(r.get("code", ""))] += 1

        d = _safe_float_ms(_parse_iso(r.get("tsStart")), _parse_iso(r.get("tsEnd")))
        if d is not None:
            durations.append(d)

        if r.get("ok") is not True:
            recent_failures.append(
                {
                    "ts": r.get("tsStart"),
                    "site": site,
                    "command": cmd,
                    "code": r.get("code"),
                    "error": r.get("error", "")[:240],
                    "fallback": r.get("fallback"),
                }
            )

    avg_ms = (sum(durations) / len(durations)) if durations else 0.0
    p95_ms = 0.0
    if durations:
        ordered = sorted(durations)
        idx = int(0.95 * (len(ordered) - 1))
        p95_ms = ordered[idx]

    return {
        "windowHours": hours,
        "generatedAt": now.isoformat(),
        "sourceLog": str(log_path),
        "total": total,
        "success": ok_count,
        "failed": fail_count,
        "successRate": round(success_rate, 4),
        "primarySuccess": primary_ok,
        "primaryFailure": total - primary_ok,
        "degradedSuccess": degraded_ok,
        "avgLatencyMs": round(avg_ms, 2),
        "p95LatencyMs": round(p95_ms, 2),
        "topSites": [{"site": k, "count": v} for k, v in site_counter.most_common(10)],
        "topCommands": [{"command": k, "count": v} for k, v in cmd_counter.most_common(10)],
        "codes": [{"code": k, "count": v} for k, v in code_counter.most_common()],
        "recentFailures": recent_failures[-10:],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenCLI invocation metrics")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    args = parser.parse_args()
    summary = build_summary(hours=args.hours, log_path=Path(args.log_path))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
