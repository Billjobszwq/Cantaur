#!/usr/bin/env python3
"""Translate natural-language task into OpenCLI guarded execution."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GUARD_PATH = Path(str(Path.home() / ".qyclaw/workspace/scripts/opencli_guard.py"))

SITE_ALIASES: dict[str, list[str]] = {
    "twitter": ["x", "twitter", "推特", "推文"],
    "xiaohongshu": ["小红书", "xhs", "rednote"],
    "weibo": ["微博"],
    "zhihu": ["知乎"],
    "reddit": ["reddit"],
    "hackernews": ["hackernews", "hn"],
    "google": ["google", "谷歌"],
    "36kr": ["36kr", "36氪"],
    "v2ex": ["v2ex"],
    "jike": ["即刻", "jike"],
    "douyin": ["抖音", "douyin"],
    "bilibili": ["bilibili", "b站", "哔哩哔哩"],
}

HOT_WORDS = ("热点", "热门", "趋势", "热榜", "爆款", "trending", "hot", "top", "latest")
SEARCH_WORDS = ("搜索", "查找", "寻找", "搜", "search", "look up", "find")
NEWS_WORDS = ("新闻", "news", "资讯", "快讯", "报道")
WRITE_WORDS = ("发布", "发", "发帖", "写", "回复", "评论", "转发", "点赞", "post", "publish", "reply")
USER_WORDS = ("用户", "账号", "博主", "作者", "profile", "user")


@dataclass
class CommandChoice:
    site: str
    command: str
    strategy: str
    score: float
    reasons: list[str]
    row: dict[str, Any]


def run_json(cmd: list[str]) -> tuple[int, dict[str, Any]]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    payload: dict[str, Any] = {}
    out = (proc.stdout or "").strip()
    if out:
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            payload = {"ok": False, "raw": out[:1200]}
    elif proc.stderr:
        payload = {"ok": False, "stderr": proc.stderr[:1200]}
    return proc.returncode, payload


def load_manifest() -> list[dict[str, Any]]:
    proc = subprocess.run(["opencli", "list", "-f", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "opencli list failed")
    return json.loads(proc.stdout)


def normalize(text: str) -> str:
    return " ".join(text.strip().lower().split())


def detect_site(text: str) -> tuple[str | None, list[str]]:
    lowered = normalize(text)
    reasons: list[str] = []
    for site, aliases in SITE_ALIASES.items():
        for alias in aliases:
            a = alias.lower()
            if re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", lowered):
                reasons.append(f"matched site alias `{alias}` -> `{site}`")
                return site, reasons
    return None, reasons


def extract_limit(text: str, default: int = 10) -> int:
    patterns = [
        r"(\d+)\s*(条|个|篇|条新闻|results?)",
        r"(top|前)\s*(\d+)",
        r"limit\s*=?\s*(\d+)",
    ]
    for p in patterns:
        m = re.search(p, text, flags=re.IGNORECASE)
        if not m:
            continue
        for g in m.groups()[::-1]:
            if g and g.isdigit():
                return max(1, min(int(g), 100))
    return default


def extract_query(text: str) -> str:
    stripped = text.strip()
    quoted = re.findall(r"[\"“](.+?)[\"”]", stripped)
    if quoted:
        return quoted[0].strip()

    patterns = [
        r"(?:关于|有关)\s*([^，。,.；;]+)",
        r"(?:搜索|查找|寻找|搜)\s*([^，。,.；;]+)",
        r"(?:for|about)\s+([^,.;]+)",
    ]
    for p in patterns:
        m = re.search(p, stripped, flags=re.IGNORECASE)
        if not m:
            continue
        q = m.group(1).strip()
        q = re.sub(r"(今日|今天|最新|热点|热门|趋势|新闻|资讯)$", "", q).strip()
        q = re.sub(r"(的热点|的热门|的趋势|的新闻|的资讯)$", "", q).strip()
        if q:
            return q

    fallback = stripped
    filler = [
        "帮我",
        "请",
        "一下",
        "从",
        "上",
        "去",
        "寻找",
        "查找",
        "搜索",
        "热点",
        "热门",
        "趋势",
        "新闻",
        "资讯",
        "今日",
        "今天",
        "最新",
    ]
    for token in filler:
        fallback = fallback.replace(token, " ")
    fallback = re.sub(r"\s+", " ", fallback).strip()
    return fallback or "AI"


def command_score(text: str, row: dict[str, Any], write_intent: bool, query: str) -> tuple[float, list[str]]:
    cmd = str(row.get("name", "")).lower()
    score = 0.0
    reasons: list[str] = []

    if cmd in normalize(text):
        score += 5
        reasons.append("command name mentioned explicitly")

    has_search_intent = any(w in text.lower() for w in SEARCH_WORDS)
    has_hot_intent = any(w in text.lower() for w in HOT_WORDS)
    has_news_intent = any(w in text.lower() for w in NEWS_WORDS)
    has_user_intent = any(w in text.lower() for w in USER_WORDS)

    if has_search_intent and cmd == "search":
        score += 4
        reasons.append("search intent -> search command")
    if has_hot_intent and cmd in {"hot", "trending", "top", "latest", "feed", "frontpage", "timeline"}:
        score += 4
        reasons.append("hot intent alignment")
        if cmd in {"trending", "hot", "top", "latest"}:
            score += 2
            reasons.append("prefer explicit hot/trending commands")
    if has_news_intent and cmd in {"news", "hot", "trending", "search"}:
        score += 3
        reasons.append("news intent alignment")
        if cmd == "news":
            score += 2
            reasons.append("prefer dedicated news command")
    if has_user_intent and cmd in {"user", "profile", "creator-profile"}:
        score += 3
        reasons.append("user intent alignment")

    is_write_cmd = cmd in {"post", "publish", "reply", "comment", "like", "follow", "delete", "create", "update"}
    if write_intent:
        if is_write_cmd:
            score += 3
            reasons.append("write intent alignment")
        else:
            score -= 1
    else:
        if is_write_cmd:
            score -= 4
            reasons.append("avoid write command for read intent")
        else:
            score += 1

    args = row.get("args") or []
    required_count = len([a for a in args if a.get("required")])
    if cmd == "search" and query:
        score += 2
        reasons.append("query extracted for search")
    if required_count == 0:
        score += 0.5
    return score, reasons


def choose_command(text: str, site: str | None, rows: list[dict[str, Any]], query: str) -> CommandChoice:
    write_intent = any(w in text.lower() for w in WRITE_WORDS)
    candidates = [r for r in rows if (site is None or r.get("site") == site)]
    if not candidates:
        raise RuntimeError("no command candidates")

    best: CommandChoice | None = None
    for row in candidates:
        score, reasons = command_score(text, row, write_intent=write_intent, query=query)
        choice = CommandChoice(
            site=str(row.get("site")),
            command=str(row.get("name")),
            strategy=str(row.get("strategy", "")),
            score=score,
            reasons=reasons,
            row=row,
        )
        if best is None or choice.score > best.score:
            best = choice
    assert best is not None
    return best


def build_args(row: dict[str, Any], query: str, text: str, overrides: dict[str, str]) -> tuple[list[str], list[str], dict[str, str]]:
    args_schema = row.get("args") or []
    unresolved: list[str] = []
    cli_args: list[str] = []
    resolved: dict[str, str] = {}
    limit = str(extract_limit(text, default=10))

    for arg in args_schema:
        name = str(arg.get("name"))
        required = bool(arg.get("required"))
        positional = bool(arg.get("positional"))

        value = overrides.get(name)
        if value is None and name in {"limit", "max", "count", "size"}:
            value = limit
        if value is None and name in {"query", "keyword", "q", "topic", "text"}:
            value = query

        if value is None and required:
            unresolved.append(name)
            continue
        if value is None:
            continue

        resolved[name] = value
        if positional:
            cli_args.append(value)
        else:
            cli_args.extend([f"--{name}", value])

    return cli_args, unresolved, resolved


def parse_overrides(raw: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        if k.strip():
            result[k.strip()] = v.strip()
    return result


def default_site_for_intent(text: str) -> tuple[str, str]:
    low = text.lower()
    if any(w in low for w in NEWS_WORDS):
        return "google", "fallback to google for news intent"
    if any(w in low for w in HOT_WORDS):
        return "twitter", "fallback to twitter for hot/trending intent"
    if any(w in low for w in SEARCH_WORDS):
        return "google", "fallback to google for generic search intent"
    return "google", "fallback default site"


def make_guard_cmd(agent: str, site: str, command: str, cli_args: list[str], allow_write: bool, ignore_circuit: bool) -> list[str]:
    cmd = [
        "python3",
        str(GUARD_PATH),
        "run",
        "--agent",
        agent,
        "--site",
        site,
        "--command",
        command,
    ]
    if allow_write:
        cmd.append("--allow-write")
    if ignore_circuit:
        cmd.append("--ignore-circuit")
    cmd.append("--")
    cmd.extend(cli_args)
    return cmd


def confidence_from_score(score: float) -> float:
    return max(0.0, min(1.0, (score + 1.0) / 10.0))


def cmd_translate(args: argparse.Namespace) -> None:
    rows = load_manifest()
    site, site_reasons = detect_site(args.text)
    fallback_reason = None
    if site is None:
        site, fallback_reason = default_site_for_intent(args.text)

    query = extract_query(args.text)
    choice = choose_command(args.text, site, rows, query=query)
    overrides = parse_overrides(args.arg or [])
    cli_args, unresolved, resolved = build_args(choice.row, query=query, text=args.text, overrides=overrides)
    confidence = confidence_from_score(choice.score)
    guard_cmd = make_guard_cmd(
        agent=args.agent,
        site=choice.site,
        command=choice.command,
        cli_args=cli_args,
        allow_write=args.allow_write,
        ignore_circuit=args.ignore_circuit,
    )

    result: dict[str, Any] = {
        "ok": True,
        "input": {"text": args.text, "agent": args.agent},
        "resolution": {
            "site": choice.site,
            "command": choice.command,
            "strategy": choice.strategy,
            "confidence": round(confidence, 4),
            "query": query,
            "reasons": site_reasons + choice.reasons + ([fallback_reason] if fallback_reason else []),
        },
        "args": {
            "resolved": resolved,
            "unresolvedRequired": unresolved,
        },
        "preview": {
            "guardCommand": guard_cmd,
            "shell": " ".join([f'"{x}"' if " " in x else x for x in guard_cmd]),
            "canRun": len(unresolved) == 0,
        },
    }

    if args.action == "translate":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if unresolved:
            sys.exit(2)
        return

    if unresolved:
        result["ok"] = False
        result["error"] = "missing_required_args"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(2)

    if confidence < args.min_confidence and not args.force:
        result["ok"] = False
        result["error"] = "low_confidence"
        result["hint"] = "Use --force to execute anyway or provide explicit --arg name=value."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(3)

    code, run_out = run_json(guard_cmd)
    result["execution"] = {"exit": code, "result": run_out}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if code != 0:
        sys.exit(code)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Translate NL instructions to OpenCLI commands")
    sub = p.add_subparsers(dest="action", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--text", required=True, help="Natural language instruction")
    common.add_argument("--agent", default="main", help="Agent id")
    common.add_argument("--arg", action="append", help="Override argument, e.g. --arg query=AI")
    common.add_argument("--allow-write", action="store_true", help="Pass --allow-write to guard")
    common.add_argument("--ignore-circuit", action="store_true")

    t = sub.add_parser("translate", parents=[common], help="Translate only, do not execute")
    t.set_defaults(func=cmd_translate)

    r = sub.add_parser("run", parents=[common], help="Translate and execute")
    r.add_argument("--min-confidence", type=float, default=0.35)
    r.add_argument("--force", action="store_true", help="Execute even when confidence is low")
    r.set_defaults(func=cmd_translate)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
