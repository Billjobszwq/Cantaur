#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path.home() / ".qyclaw"
WORKSPACE_ROOT = ROOT / "workspace"
QYCLAW_CONFIG = ROOT / "qyclaw.json"
SYSTEM_DIR = WORKSPACE_ROOT / "memory-system"
REGISTRY_PATH = SYSTEM_DIR / "agents.json"
ROUTER_RULES_PATH = SYSTEM_DIR / "router-rules.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")
TAG_RE = re.compile(r"#([0-9A-Za-z_\-\u4e00-\u9fff]+)")


def process_command(pid: int) -> str:
    try:
        out = subprocess.check_output(["ps", "-o", "command=", "-p", str(pid)], text=True)
    except Exception:
        return ""
    return out.strip().lower()


def process_parent_pid(pid: int) -> Optional[int]:
    try:
        out = subprocess.check_output(["ps", "-o", "ppid=", "-p", str(pid)], text=True)
        return int(out.strip())
    except Exception:
        return None


def running_under_cron(max_depth: int = 6) -> bool:
    pid = os.getppid()
    for _ in range(max_depth):
        if pid is None or pid <= 1:
            return False
        command = process_command(pid)
        if "cron" in command:
            return True
        pid = process_parent_pid(pid)
    return False


def should_skip_legacy_cron(args: argparse.Namespace) -> bool:
    if args.cmd != "maintain":
        return False
    if os.environ.get("QYCLAW_ENABLE_LEGACY_CRON") == "1":
        return False
    if args.force:
        return False
    if args.interval_days < 7:
        return False
    return running_under_cron()


@dataclass
class Agent:
    agent_id: str
    name: str
    workspace: Path

    @property
    def memory_dir(self) -> Path:
        return self.workspace / "memory"

    @property
    def inbox_dir(self) -> Path:
        return self.memory_dir / "00-inbox"

    @property
    def episodic_daily_dir(self) -> Path:
        return self.memory_dir / "10-episodic" / "daily"

    @property
    def semantic_dir(self) -> Path:
        return self.memory_dir / "20-semantic"

    @property
    def procedures_dir(self) -> Path:
        return self.memory_dir / "30-procedures"

    @property
    def archive_daily_dir(self) -> Path:
        return self.memory_dir / "90-archive" / "daily"

    @property
    def heartbeat_state_file(self) -> Path:
        return self.memory_dir / "heartbeat-state.json"


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_file(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def discover_agents(config_path: Path = QYCLAW_CONFIG) -> List[Agent]:
    cfg = read_json(config_path)
    agents: List[Agent] = []
    for item in cfg.get("agents", {}).get("list", []):
        agent_id = str(item.get("id", "")).strip()
        if not agent_id:
            continue
        name = str(item.get("identity", {}).get("name") or item.get("name") or agent_id).strip()
        workspace = Path(str(item.get("workspace", "")).strip())
        if not workspace:
            continue
        agents.append(Agent(agent_id=agent_id, name=name, workspace=workspace))
    return agents


def write_registry(agents: List[Agent]) -> None:
    SYSTEM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "updatedAt": dt.datetime.now().isoformat(),
        "agents": [
            {
                "id": a.agent_id,
                "name": a.name,
                "workspace": str(a.workspace),
                "memoryDir": str(a.memory_dir),
            }
            for a in agents
        ],
    }
    REGISTRY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_router_rules(agents: List[Agent]) -> None:
    if ROUTER_RULES_PATH.exists():
        return
    defaults = {
        "main": ["总管", "统筹", "规划", "协调", "全局", "项目", "任务分配", "邮件"],
        "dev": ["开发", "代码", "脚本", "数据库", "接口", "爬虫", "bug", "调试"],
        "content": ["内容", "文案", "营销", "内容运营", "选题", "公众号", "社媒", "热点"],
        "ops": ["运营", "执行", "排期", "流程", "落地", "监控", "交付", "SOP"],
        "law": ["法务", "合同", "合规", "风险", "条款", "隐私", "监管", "仲裁"],
        "finance": ["架构", "财务", "预算", "成本", "ROI", "现金流", "模型", "系统设计"],
    }
    payload = {
        "updatedAt": dt.datetime.now().isoformat(),
        "rules": [
            {
                "agentId": a.agent_id,
                "agentName": a.name,
                "keywords": defaults.get(a.agent_id, [a.name, a.agent_id]),
            }
            for a in agents
        ],
    }
    ROUTER_RULES_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def init_memory_layers(agents: List[Agent]) -> None:
    for a in agents:
        dirs = [
            a.inbox_dir,
            a.episodic_daily_dir,
            a.semantic_dir,
            a.procedures_dir,
            a.archive_daily_dir,
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)

        ensure_file(
            a.memory_dir / "README.md",
            (
                "# Memory Layout\n\n"
                "- `00-inbox/`: 待整理原始记录\n"
                "- `10-episodic/daily/`: 每日事件日志（YYYY-MM-DD.md）\n"
                "- `20-semantic/MEMORY.md`: 提炼后的长期事实与决策\n"
                "- `20-semantic/projects.md`: 项目状态与关键决策\n"
                "- `20-semantic/lessons.md`: 问题解决经验库\n"
                "- `20-semantic/decisions.md`: 决策索引\n"
                "- `20-semantic/recent-summary.md`: 近期摘要（维护任务生成）\n"
                "- `30-procedures/SOP.md`: 可复用流程和技能调用规范\n"
                "- `90-archive/daily/`: 历史归档\n"
            ),
        )
        ensure_file(
            a.semantic_dir / "MEMORY.md",
            (
                "# Long-term Memory\n\n"
                "## Stable Facts\n\n"
                "## Decisions\n\n"
                "## Preferences\n\n"
                "## Constraints / Risks\n\n"
            ),
        )
        ensure_file(a.semantic_dir / "projects.md", "# Projects\n\n")
        ensure_file(a.semantic_dir / "lessons.md", "# Lessons\n\n")
        ensure_file(a.semantic_dir / "decisions.md", "# Decisions Index\n\n")
        ensure_file(a.semantic_dir / "recent-summary.md", "# Recent Summary\n\n")
        ensure_file(
            a.procedures_dir / "SOP.md",
            (
                "# SOP\n\n"
                "记录可复用的执行流程、命令模板、回滚方法。\n"
            ),
        )
        ensure_file(
            a.inbox_dir / "LOG_TEMPLATE.md",
            (
                "### [项目:<project>] <主题标题>\n\n"
                "- **结果**：\n"
                "- **相关文件**：\n"
                "- **经验教训**：\n"
                "- **下一步**：\n"
                "- **检索标签**：#tag1 #tag2\n"
            ),
        )
        ensure_file(
            a.heartbeat_state_file,
            json.dumps(
                {
                    "lastMemoryMaintenance": "",
                    "lastDailySummaryDate": "",
                    "lastUpdatedAt": dt.datetime.now().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )


def migrate_legacy_daily(agent: Agent) -> int:
    moved = 0
    for f in agent.memory_dir.glob("*.md"):
        if f.name in {"README.md"}:
            continue
        if DATE_RE.match(f.name):
            target = agent.episodic_daily_dir / f.name
            if not target.exists():
                target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
                f.unlink()
                moved += 1
    return moved


def summarize_file(path: Path, max_lines: int = 6) -> List[str]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    picks: List[str] = []
    for ln in lines:
        if ln.startswith("#") or ln.startswith("- ") or ln.startswith("1. ") or len(ln) > 8:
            picks.append(ln)
        if len(picks) >= max_lines:
            break
    return picks if picks else lines[:max_lines]


def load_heartbeat_state(agent: Agent) -> Dict:
    if not agent.heartbeat_state_file.exists():
        return {}
    try:
        return read_json(agent.heartbeat_state_file)
    except Exception:
        return {}


def save_heartbeat_state(agent: Agent, state: Dict) -> None:
    state["lastUpdatedAt"] = dt.datetime.now().isoformat()
    agent.heartbeat_state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_iso_date(raw: str) -> Optional[dt.date]:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except Exception:
        return None


def ensure_daily_stub(agent: Agent, day: dt.date) -> None:
    path = agent.episodic_daily_dir / f"{day.isoformat()}.md"
    if path.exists():
        return
    path.write_text(
        (
            f"# {day.isoformat()}\n\n"
            "## Summary\n"
            "- Auto-generated placeholder to keep episodic daily continuity.\n\n"
            "## Events\n"
            "- Pending capture.\n\n"
            "## Decisions\n"
            "- None recorded yet.\n\n"
            "## Follow-ups\n"
            "- Pending backfill.\n"
        ),
        encoding="utf-8",
    )


def upsert_line(path: Path, text: str) -> bool:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if text in existing:
        return False
    path.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + text + "\n", encoding="utf-8")
    return True


def maintain_agent(
    agent: Agent,
    keep_days: int,
    summary_files: int,
    maintenance_interval_days: int,
    force: bool = False,
) -> Dict[str, int]:
    migrate_count = migrate_legacy_daily(agent)
    today = dt.date.today()
    archived = 0
    compacted = 0
    extracted = 0

    state = load_heartbeat_state(agent)
    last_maintenance = parse_iso_date(state.get("lastMemoryMaintenance", ""))
    should_run = force or last_maintenance is None or (today - last_maintenance).days >= maintenance_interval_days

    # Keep at least yesterday/today files present so downstream reads do not fail.
    ensure_daily_stub(agent, today - dt.timedelta(days=1))
    ensure_daily_stub(agent, today)

    # Daily summary is cheap and runs every time.
    recent_files = sorted(
        [p for p in agent.episodic_daily_dir.glob("*.md") if DATE_RE.match(p.name)],
        reverse=True,
    )[:summary_files]
    summary_lines = [f"# Recent Summary ({today.isoformat()})", ""]
    for rf in recent_files:
        summary_lines.append(f"## {rf.stem}")
        for item in summarize_file(rf):
            summary_lines.append(f"- {item}")
        summary_lines.append("")
    (agent.semantic_dir / "recent-summary.md").write_text("\n".join(summary_lines).rstrip() + "\n", encoding="utf-8")
    state["lastDailySummaryDate"] = today.isoformat()

    if should_run:
        daily_files = sorted([p for p in agent.episodic_daily_dir.glob("*.md") if DATE_RE.match(p.name)])
        for f in daily_files:
            day = dt.date.fromisoformat(f.stem)
            if (today - day).days > keep_days:
                target = agent.archive_daily_dir / f.name
                if not target.exists():
                    target.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
                    f.unlink()
                    archived += 1

        # Light extraction: promote high-value lines to semantic memory files.
        for rf in recent_files:
            if not rf.exists():
                continue
            lines = [ln.strip() for ln in rf.read_text(encoding="utf-8").splitlines() if ln.strip()]
            for ln in lines:
                low = ln.lower()
                if "决策" in ln or "decision" in low:
                    if upsert_line(agent.semantic_dir / "decisions.md", f"- {rf.stem}: {ln}"):
                        extracted += 1
                if "经验" in ln or "教训" in ln or "lesson" in low:
                    if upsert_line(agent.semantic_dir / "lessons.md", f"- {rf.stem}: {ln}"):
                        extracted += 1
                if "[项目:" in ln or "项目" in ln:
                    if upsert_line(agent.semantic_dir / "projects.md", f"- {rf.stem}: {ln}"):
                        extracted += 1

        memory_md = agent.semantic_dir / "MEMORY.md"
        text = memory_md.read_text(encoding="utf-8") if memory_md.exists() else ""
        max_chars = 12000
        if len(text) > max_chars:
            memory_md.write_text(text[:max_chars] + "\n\n[TRUNCATED_BY_MAINTENANCE]\n", encoding="utf-8")
            compacted = 1

        state["lastMemoryMaintenance"] = today.isoformat()

    save_heartbeat_state(agent, state)

    index_payload = {
        "updatedAt": dt.datetime.now().isoformat(),
        "agentId": agent.agent_id,
        "workspace": str(agent.workspace),
        "counts": {
            "inbox": len(list(agent.inbox_dir.glob("*"))),
            "daily": len(list(agent.episodic_daily_dir.glob("*.md"))),
            "archive_daily": len(list(agent.archive_daily_dir.glob("*.md"))),
            "semantic_files": len(list(agent.semantic_dir.glob("*.md"))),
            "procedure_files": len(list(agent.procedures_dir.glob("*.md"))),
        },
        "latestDaily": [p.name for p in sorted(agent.episodic_daily_dir.glob("*.md"), reverse=True)[:10]],
        "heartbeatState": state,
    }
    (agent.memory_dir / "index.json").write_text(json.dumps(index_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "migrated": migrate_count,
        "archived": archived,
        "compacted": compacted,
        "extracted": extracted,
        "skippedWeekly": 0 if should_run else 1,
    }


def tokenize_query(query: str) -> List[str]:
    raw = re.split(r"[\s,，。;；]+", query.strip().lower())
    return [t for t in raw if t]


def score_line(line: str, file_path: Path, query: str, tokens: List[str]) -> float:
    low = line.lower()
    score = 0.0
    if query in low:
        score += 3.0 + low.count(query) * 1.5
    for tk in tokens:
        if tk in low:
            score += 0.8

    if line.startswith("#"):
        score += 1.2

    tags = [m.group(1).lower() for m in TAG_RE.finditer(line)]
    for tk in tokens:
        if tk in tags:
            score += 2.0

    p = str(file_path)
    if "20-semantic" in p:
        score += 1.0
    if "30-procedures" in p:
        score += 0.8
    if "10-episodic/daily" in p:
        score += 0.5
    if "90-archive" in p:
        score -= 0.2
    return score


def search_agent(agent: Agent, query: str, limit: int) -> List[Tuple[float, Path, int, str]]:
    q = query.lower().strip()
    if not q:
        return []
    tokens = tokenize_query(q)

    files: List[Path] = [
        agent.semantic_dir / "MEMORY.md",
        agent.semantic_dir / "recent-summary.md",
        agent.semantic_dir / "projects.md",
        agent.semantic_dir / "lessons.md",
        agent.semantic_dir / "decisions.md",
        agent.procedures_dir / "SOP.md",
    ]
    files.extend(sorted(agent.episodic_daily_dir.glob("*.md"), reverse=True)[:28])

    results: List[Tuple[float, Path, int, str]] = []
    for f in files:
        if not f.exists():
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            score = score_line(line, f, q, tokens)
            if score > 1.0:
                results.append((score, f, i, line.strip()))

    results.sort(key=lambda x: x[0], reverse=True)
    return results[:limit]


def recommend_agents(agents: List[Agent], query: str) -> List[Tuple[float, Agent, List[str]]]:
    if not ROUTER_RULES_PATH.exists():
        ensure_router_rules(agents)
    rules = read_json(ROUTER_RULES_PATH).get("rules", [])
    by_id = {a.agent_id: a for a in agents}
    q = query.lower()
    out: List[Tuple[float, Agent, List[str]]] = []
    for rule in rules:
        aid = rule.get("agentId")
        agent = by_id.get(aid)
        if not agent:
            continue
        hits: List[str] = []
        score = 0.0
        for kw in rule.get("keywords", []):
            kw_text = str(kw).strip()
            if not kw_text:
                continue
            if kw_text.lower() in q:
                score += 2.0
                hits.append(kw_text)
        if agent.name.lower() in q or agent.agent_id.lower() in q:
            score += 1.5
        if score > 0:
            out.append((score, agent, hits))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def cmd_init(_: argparse.Namespace) -> None:
    agents = discover_agents()
    if not agents:
        raise SystemExit("No agents found from qyclaw.json")
    write_registry(agents)
    ensure_router_rules(agents)
    init_memory_layers(agents)
    print(f"initialized memory layers for {len(agents)} agents")


def cmd_maintain(args: argparse.Namespace) -> None:
    agents = discover_agents()
    write_registry(agents)
    init_memory_layers(agents)
    total = {"migrated": 0, "archived": 0, "compacted": 0, "extracted": 0, "skippedWeekly": 0}
    for a in agents:
        stat = maintain_agent(
            a,
            keep_days=args.keep_days,
            summary_files=args.summary_files,
            maintenance_interval_days=args.interval_days,
            force=args.force,
        )
        total = {k: total[k] + stat[k] for k in total}
        print(
            f"[{a.agent_id}] migrated={stat['migrated']} archived={stat['archived']} "
            f"extracted={stat['extracted']} compacted={stat['compacted']} skippedWeekly={stat['skippedWeekly']}"
        )
    print(
        f"done migrated={total['migrated']} archived={total['archived']} extracted={total['extracted']} "
        f"compacted={total['compacted']} skippedWeekly={total['skippedWeekly']}"
    )


def cmd_search(args: argparse.Namespace) -> None:
    agents = discover_agents()
    by_id = {a.agent_id: a for a in agents}
    target_agents: Iterable[Agent]
    if args.agent == "all":
        target_agents = agents
    else:
        agent = by_id.get(args.agent)
        if not agent:
            raise SystemExit(f"agent not found: {args.agent}")
        target_agents = [agent]

    found = 0
    for a in target_agents:
        rows = search_agent(a, args.query, args.limit)
        if not rows:
            continue
        print(f"\n=== {a.agent_id} ({a.name}) ===")
        for score, path, line_no, text in rows:
            print(f"[{score:.1f}] {path}:{line_no}  {text}")
            found += 1
    if found == 0:
        print("no matches")


def cmd_recommend(args: argparse.Namespace) -> None:
    agents = discover_agents()
    ranking = recommend_agents(agents, args.query)
    if not ranking:
        print("no recommendation by keyword rules")
        return
    for score, agent, hits in ranking[: args.limit]:
        print(f"[{score:.1f}] {agent.agent_id} ({agent.name}) hits={','.join(hits) if hits else '-'}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Layered memory + recommendation system for QYclaw multi-agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize layered memory structure for all agents")
    p_init.set_defaults(func=cmd_init)

    p_maintain = sub.add_parser("maintain", help="run memory maintenance for all agents")
    p_maintain.add_argument("--keep-days", type=int, default=14, help="keep daily logs in active layer for N days")
    p_maintain.add_argument("--summary-files", type=int, default=7, help="how many recent daily files to summarize")
    p_maintain.add_argument("--interval-days", type=int, default=7, help="weekly maintenance interval in days")
    p_maintain.add_argument("--force", action="store_true", help="force weekly maintenance now")
    p_maintain.set_defaults(func=cmd_maintain)

    p_search = sub.add_parser("search", help="search layered memory")
    p_search.add_argument("--agent", default="all", help="agent id or 'all'")
    p_search.add_argument("--query", required=True, help="search query")
    p_search.add_argument("--limit", type=int, default=15, help="max lines per agent")
    p_search.set_defaults(func=cmd_search)

    p_recommend = sub.add_parser("recommend", help="recommend target agent(s) by query")
    p_recommend.add_argument("--query", required=True, help="user request")
    p_recommend.add_argument("--limit", type=int, default=5, help="max agent recommendations")
    p_recommend.set_defaults(func=cmd_recommend)
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if should_skip_legacy_cron(args):
        print("skip legacy cron maintain; launchd scheduler is active")
        return
    args.func(args)


if __name__ == "__main__":
    main()
