#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

ROOT = Path.home() / ".openclaw"
OPENCLAW_CONFIG = ROOT / "openclaw.json"
OBSIDIAN_AGENT_DIR = Path.home() / "Documents" / "Obsidian Vault" / "Agent专用"

TEXT_SPLIT_RE = re.compile(r"[\n\r]+")
TASK_HINT_RE = re.compile(r"(待办|todo|下一步|需要|follow up|follow-up)", re.IGNORECASE)
DECISION_HINT_RE = re.compile(r"(决定|结论|建议|decision|recommend|应当|应该)", re.IGNORECASE)
CONFIRMED_DECISION_RE = re.compile(
    r"(先做|后置|采用|确定|定为|改为|统一为|保留|暂停|停止|推荐方案|当前采用|长期模式|最佳实践)",
    re.IGNORECASE,
)
ACTION_HINT_RE = re.compile(
    r"(写|做|检查|确认|联系|安排|推进|处理|修复|设计|部署|整理|发送|下载|回复|跟进|创建|更新|review|fix|check|prepare|draft|send|sync)",
    re.IGNORECASE,
)
PLANNING_ACTION_RE = re.compile(
    r"(写|做|检查|确认|联系|安排|推进|处理|修复|设计|部署|整理|跟进|创建|更新|review|fix|check|prepare|draft|sync)",
    re.IGNORECASE,
)
TASK_NOISE_RE = re.compile(
    r"(新邮件|候选邮件|新增登记|小计|发件人/主题|zip包已发送|已发送|下载完成|邮件下载完成|最新邮件下载完成)",
    re.IGNORECASE,
)
MAIL_SUBJECT_NOISE_RE = re.compile(r"^[\-\*\s]*[\u4e00-\u9fffA-Za-z0-9_]+[：:].*(回复：|re:)", re.IGNORECASE)
BUDGET_LINE_RE = re.compile(r"(设计费|手板费|样品|预算[\d\-~—至到万kK]*)", re.IGNORECASE)
DELIVERY_NOISE_RE = re.compile(r"(mediaurl|updating:|adding:|stored \d+%|deflated \d+%|\.zip|/users/|\\\\users\\\\|message:|failed|\.pdf)", re.IGNORECASE)
PROJECT_NOISE_RE = re.compile(
    r"(回复\s*billzhang|用于统筹项目|机器人|任务超时|部分工具遇到权限限制|继续用飞书文档测试|请稍等|自动在群里|自我介绍|目前系统里就我一个|subagent|conversation info|sender \(untrusted metadata\)|read heartbeat\.md|current time:)",
    re.IGNORECASE,
)
ROLE_LINE_RE = re.compile(r"^(角色|职责|顺序|请介绍|介绍模板|会议议程|agent)\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
CONTACT_NAME_LINE_RE = re.compile(r"^[\-\*\s]*([A-Za-z\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff·]{1,24})[：:]\s*(.+)$")
FEISHU_SENDER_RE = re.compile(r"sender[\"']?\s*:\s*[\"']?(ou_[A-Za-z0-9_]+)[\"']?", re.IGNORECASE)
CONTACT_NAME_NOISE_RE = re.compile(r"^(新邮件|新增登记|小计|设计费|手板费|语音模组样品|发件人/主题|预算|合计)$", re.IGNORECASE)
PREFERENCE_HINT_RE = re.compile(
    r"(倾向|偏好|希望|不要|优先|先做|后置|自己做|找当地合作|不需要|计划|预算内|不在预算内|保持|统一)",
    re.IGNORECASE,
)
REVIEW_RULE_HINT_RE = re.compile(
    r"(规则|SOP|流程|规范|协议|必须|统一|默认|以后|后续|长期|固定|标准|路由|优先级|评分|阈值|记忆维护|反馈机制|主控)",
    re.IGNORECASE,
)
INSTREET_HINT_RE = re.compile(
    r"(instreet|社区|帖子|评论|点赞|回复|方法论|符号囚禁|我的帖子|karma)",
    re.IGNORECASE,
)
REVIEW_NOISE_RE = re.compile(
    r"(\[toolresult\]|heartbeat-state\.json|heartbeat_ok|知识库已更新|obsidian 整理完成|验证流程闭环|请直接发送图片|接收到图片|session summary|source_session|^\#\#|mediaurl|stored \d+%|deflated \d+%|^\{.+\}$)",
    re.IGNORECASE,
)
INSTREET_LEARNING_VALUE_RE = re.compile(
    r"(方法论|经验|判断|回顾|机制|模式|复盘|策略|规则|协调|记忆|增量价值|优先级)",
    re.IGNORECASE,
)


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
    if args.cmd not in {"capture", "extract"}:
        return False
    if os.environ.get("OPENCLAW_ENABLE_LEGACY_CRON") == "1":
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
    def working_dir(self) -> Path:
        return self.memory_dir / "01-working"

    @property
    def working_sessions_dir(self) -> Path:
        return self.working_dir / "sessions"

    @property
    def working_tasks_dir(self) -> Path:
        return self.working_dir / "tasks"

    @property
    def working_index_file(self) -> Path:
        return self.working_dir / "index.json"

    @property
    def semantic_dir(self) -> Path:
        return self.memory_dir / "20-semantic"

    @property
    def semantic_inbox_dir(self) -> Path:
        return self.semantic_dir / "inbox"

    @property
    def semantic_project_updates_dir(self) -> Path:
        return self.semantic_dir / "project-updates"

    @property
    def semantic_decisions_dir(self) -> Path:
        return self.semantic_dir / "decisions"

    @property
    def semantic_lessons_dir(self) -> Path:
        return self.semantic_dir / "lessons"

    @property
    def semantic_preferences_dir(self) -> Path:
        return self.semantic_dir / "preferences"

    @property
    def semantic_review_queue_dir(self) -> Path:
        return self.semantic_dir / "review-queue"

    @property
    def semantic_review_reports_dir(self) -> Path:
        return self.semantic_dir / "review-reports"

    @property
    def structured_dir(self) -> Path:
        return self.memory_dir / "40-structured"

    @property
    def structured_db(self) -> Path:
        return self.structured_dir / "memory.db"

    @property
    def session_store_dir(self) -> Path:
        return ROOT / "agents" / self.agent_id / "sessions"

    @property
    def session_registry_file(self) -> Path:
        return self.session_store_dir / "sessions.json"


def read_json(path: Path) -> Dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def safe_slug(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", text.strip()).strip("-").lower()
    if not slug:
        slug = "item"
    return slug[:max_len].strip("-") or "item"


def ensure_file(path: Path, content: str) -> None:
    if not path.exists():
        write_text(path, content)


def discover_agents() -> List[Agent]:
    cfg = read_json(OPENCLAW_CONFIG)
    out: List[Agent] = []
    for item in cfg.get("agents", {}).get("list", []):
        agent_id = str(item.get("id", "")).strip()
        if not agent_id:
            continue
        name = str(item.get("identity", {}).get("name") or item.get("name") or agent_id).strip()
        workspace = Path(str(item.get("workspace", "")).strip())
        if not workspace:
            continue
        out.append(Agent(agent_id=agent_id, name=name, workspace=workspace))
    return out


def resolve_session_file(agent: Agent, raw: str, session_id: str = "") -> Path:
    raw = (raw or "").strip()
    if not raw:
        # Some registries may omit sessionFile. Fall back to conventional path.
        return agent.session_store_dir / f"{session_id}.jsonl" if session_id else agent.session_store_dir
    path = Path(raw)
    if path.is_absolute():
        return path
    return agent.session_store_dir / raw


def init_sqlite_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              slug TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              priority TEXT NOT NULL DEFAULT 'normal',
              owner_agent TEXT NOT NULL DEFAULT '',
              owner_human TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              goal TEXT NOT NULL DEFAULT '',
              constraints TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              start_date TEXT NOT NULL DEFAULT '',
              target_date TEXT NOT NULL DEFAULT '',
              closed_at TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tasks (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_slug TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'open',
              priority TEXT NOT NULL DEFAULT 'normal',
              task_type TEXT NOT NULL DEFAULT '',
              assignee_agent TEXT NOT NULL DEFAULT '',
              assignee_human TEXT NOT NULL DEFAULT '',
              blocking_reason TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              source_session TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              due_at TEXT NOT NULL DEFAULT '',
              completed_at TEXT NOT NULL DEFAULT '',
              UNIQUE(source_session, title)
            );

            CREATE TABLE IF NOT EXISTS contacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT '',
              org TEXT NOT NULL DEFAULT '',
              contact_type TEXT NOT NULL DEFAULT '',
              channel TEXT NOT NULL DEFAULT '',
              identifier TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              UNIQUE(name, org, channel, identifier)
            );

            CREATE TABLE IF NOT EXISTS decisions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              project_slug TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              summary TEXT NOT NULL,
              decision_type TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'accepted',
              decision_maker TEXT NOT NULL DEFAULT '',
              owner_agent TEXT NOT NULL DEFAULT '',
              reasoning TEXT NOT NULL DEFAULT '',
              impact_scope TEXT NOT NULL DEFAULT '',
              risks TEXT NOT NULL DEFAULT '',
              next_action TEXT NOT NULL DEFAULT '',
              source_session TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL,
              UNIQUE(source_session, title)
            );

            CREATE TABLE IF NOT EXISTS artifacts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              path TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL,
              title TEXT NOT NULL DEFAULT '',
              workspace TEXT NOT NULL DEFAULT '',
              agent_id TEXT NOT NULL DEFAULT '',
              project_slug TEXT NOT NULL DEFAULT '',
              summary TEXT NOT NULL DEFAULT '',
              tags TEXT NOT NULL DEFAULT '',
              content_hash TEXT NOT NULL DEFAULT '',
              source_session TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ingestion_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id TEXT NOT NULL DEFAULT '',
              source_session TEXT NOT NULL UNIQUE,
              session_hash TEXT NOT NULL,
              note_path TEXT NOT NULL,
              pipeline_version TEXT NOT NULL DEFAULT 'v1',
              status TEXT NOT NULL DEFAULT 'ok',
              error_message TEXT NOT NULL DEFAULT '',
              extracted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_queue (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              agent_id TEXT NOT NULL DEFAULT '',
              source_session TEXT NOT NULL DEFAULT '',
              project_slug TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              summary TEXT NOT NULL DEFAULT '',
              candidate_type TEXT NOT NULL DEFAULT '',
              proposed_target TEXT NOT NULL DEFAULT '',
              proposed_action TEXT NOT NULL DEFAULT '',
              evidence TEXT NOT NULL DEFAULT '',
              confidence REAL NOT NULL DEFAULT 0,
              priority INTEGER NOT NULL DEFAULT 1,
              status TEXT NOT NULL DEFAULT 'pending',
              reviewer TEXT NOT NULL DEFAULT '',
              review_note TEXT NOT NULL DEFAULT '',
              decision_ref TEXT NOT NULL DEFAULT '',
              source_note TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL DEFAULT '',
              updated_at TEXT NOT NULL DEFAULT '',
              reviewed_at TEXT NOT NULL DEFAULT '',
              UNIQUE(agent_id, source_session, candidate_type, title)
            );
            """
        )
        _migrate_existing_schema(conn)
        _ensure_indexes(conn)
        conn.commit()
    finally:
        conn.close()


def _table_columns(conn: sqlite3.Connection, table: str) -> Dict[str, str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]): str(row[2]) for row in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = _table_columns(conn, table)
    if column in cols:
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def _migrate_existing_schema(conn: sqlite3.Connection) -> None:
    wanted = {
        "projects": [
            ("priority", "TEXT NOT NULL DEFAULT 'normal'"),
            ("owner_agent", "TEXT NOT NULL DEFAULT ''"),
            ("owner_human", "TEXT NOT NULL DEFAULT ''"),
            ("summary", "TEXT NOT NULL DEFAULT ''"),
            ("goal", "TEXT NOT NULL DEFAULT ''"),
            ("constraints", "TEXT NOT NULL DEFAULT ''"),
            ("tags", "TEXT NOT NULL DEFAULT ''"),
            ("start_date", "TEXT NOT NULL DEFAULT ''"),
            ("target_date", "TEXT NOT NULL DEFAULT ''"),
            ("closed_at", "TEXT NOT NULL DEFAULT ''"),
            ("source_note", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("notes", "TEXT NOT NULL DEFAULT ''"),
        ],
        "tasks": [
            ("project_slug", "TEXT NOT NULL DEFAULT ''"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("task_type", "TEXT NOT NULL DEFAULT ''"),
            ("assignee_agent", "TEXT NOT NULL DEFAULT ''"),
            ("assignee_human", "TEXT NOT NULL DEFAULT ''"),
            ("blocking_reason", "TEXT NOT NULL DEFAULT ''"),
            ("tags", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("due_at", "TEXT NOT NULL DEFAULT ''"),
            ("completed_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "contacts": [
            ("contact_type", "TEXT NOT NULL DEFAULT ''"),
            ("channel", "TEXT NOT NULL DEFAULT ''"),
            ("identifier", "TEXT NOT NULL DEFAULT ''"),
            ("tags", "TEXT NOT NULL DEFAULT ''"),
            ("source_note", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "decisions": [
            ("project_slug", "TEXT NOT NULL DEFAULT ''"),
            ("decision_type", "TEXT NOT NULL DEFAULT ''"),
            ("status", "TEXT NOT NULL DEFAULT 'accepted'"),
            ("decision_maker", "TEXT NOT NULL DEFAULT ''"),
            ("owner_agent", "TEXT NOT NULL DEFAULT ''"),
            ("reasoning", "TEXT NOT NULL DEFAULT ''"),
            ("impact_scope", "TEXT NOT NULL DEFAULT ''"),
            ("risks", "TEXT NOT NULL DEFAULT ''"),
            ("next_action", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "artifacts": [
            ("workspace", "TEXT NOT NULL DEFAULT ''"),
            ("agent_id", "TEXT NOT NULL DEFAULT ''"),
            ("project_slug", "TEXT NOT NULL DEFAULT ''"),
            ("summary", "TEXT NOT NULL DEFAULT ''"),
            ("content_hash", "TEXT NOT NULL DEFAULT ''"),
            ("source_session", "TEXT NOT NULL DEFAULT ''"),
            ("source_note", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
        ],
        "ingestion_runs": [
            ("agent_id", "TEXT NOT NULL DEFAULT ''"),
            ("pipeline_version", "TEXT NOT NULL DEFAULT 'v1'"),
            ("status", "TEXT NOT NULL DEFAULT 'ok'"),
            ("error_message", "TEXT NOT NULL DEFAULT ''"),
        ],
        "review_queue": [
            ("agent_id", "TEXT NOT NULL DEFAULT ''"),
            ("source_session", "TEXT NOT NULL DEFAULT ''"),
            ("project_slug", "TEXT NOT NULL DEFAULT ''"),
            ("summary", "TEXT NOT NULL DEFAULT ''"),
            ("candidate_type", "TEXT NOT NULL DEFAULT ''"),
            ("proposed_target", "TEXT NOT NULL DEFAULT ''"),
            ("proposed_action", "TEXT NOT NULL DEFAULT ''"),
            ("evidence", "TEXT NOT NULL DEFAULT ''"),
            ("confidence", "REAL NOT NULL DEFAULT 0"),
            ("priority", "INTEGER NOT NULL DEFAULT 1"),
            ("status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("reviewer", "TEXT NOT NULL DEFAULT ''"),
            ("review_note", "TEXT NOT NULL DEFAULT ''"),
            ("decision_ref", "TEXT NOT NULL DEFAULT ''"),
            ("source_note", "TEXT NOT NULL DEFAULT ''"),
            ("created_at", "TEXT NOT NULL DEFAULT ''"),
            ("reviewed_at", "TEXT NOT NULL DEFAULT ''"),
        ],
    }
    for table, cols in wanted.items():
        for name, decl in cols:
            _ensure_column(conn, table, name, decl)

    now = dt.datetime.now().astimezone().isoformat()
    conn.execute("UPDATE projects SET created_at = updated_at WHERE created_at = '' AND updated_at != ''")
    conn.execute("UPDATE projects SET created_at = ? WHERE created_at = ''", (now,))
    conn.execute("UPDATE tasks SET created_at = updated_at WHERE created_at = '' AND updated_at != ''")
    conn.execute("UPDATE tasks SET created_at = ? WHERE created_at = ''", (now,))
    conn.execute("UPDATE contacts SET created_at = updated_at WHERE created_at = '' AND updated_at != ''")
    conn.execute("UPDATE contacts SET created_at = ? WHERE created_at = ''", (now,))
    conn.execute("UPDATE decisions SET created_at = updated_at WHERE created_at = '' AND updated_at != ''")
    conn.execute("UPDATE decisions SET created_at = ? WHERE created_at = ''", (now,))
    conn.execute("UPDATE artifacts SET created_at = updated_at WHERE created_at = '' AND updated_at != ''")
    conn.execute("UPDATE artifacts SET created_at = ? WHERE created_at = ''", (now,))


def _ensure_indexes(conn: sqlite3.Connection) -> None:
    stmts = [
        "CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)",
        "CREATE INDEX IF NOT EXISTS idx_projects_owner_agent ON projects(owner_agent)",
        "CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_project_slug ON tasks(project_slug)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_assignee_agent ON tasks(assignee_agent)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_due_at ON tasks(due_at)",
        "CREATE INDEX IF NOT EXISTS idx_tasks_updated_at ON tasks(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_org ON contacts(org)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_contact_type ON contacts(contact_type)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_updated_at ON contacts(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_project_slug ON decisions(project_slug)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_decision_type ON decisions(decision_type)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_owner_agent ON decisions(owner_agent)",
        "CREATE INDEX IF NOT EXISTS idx_decisions_updated_at ON decisions(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_kind ON artifacts(kind)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_agent_id ON artifacts(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_project_slug ON artifacts(project_slug)",
        "CREATE INDEX IF NOT EXISTS idx_artifacts_updated_at ON artifacts(updated_at)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_agent_id ON ingestion_runs(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_runs_extracted_at ON ingestion_runs(extracted_at)",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_agent_id ON review_queue(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_status ON review_queue(status)",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_candidate_type ON review_queue(candidate_type)",
        "CREATE INDEX IF NOT EXISTS idx_review_queue_updated_at ON review_queue(updated_at)",
    ]
    for sql in stmts:
        conn.execute(sql)


def init_agent(agent: Agent) -> None:
    agent.working_sessions_dir.mkdir(parents=True, exist_ok=True)
    agent.working_tasks_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_inbox_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_project_updates_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_decisions_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_lessons_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_preferences_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_review_queue_dir.mkdir(parents=True, exist_ok=True)
    agent.semantic_review_reports_dir.mkdir(parents=True, exist_ok=True)
    agent.structured_dir.mkdir(parents=True, exist_ok=True)

    ensure_file(
        agent.working_dir / "README.md",
        (
            "# Working Memory\n\n"
            "这一层只保存当前活跃会话与任务状态，不做永久仓库。\n\n"
            "- `sessions/<session_id>/state.md`: 会话状态快照\n"
            "- `sessions/<session_id>/summary.md`: 滚动摘要\n"
            "- `sessions/<session_id>/todo.md`: 待办与下一步\n"
            "- `tasks/`: 手工或自动整理的任务快照\n"
            "- `index.json`: 当前工作记忆索引\n"
        ),
    )
    ensure_file(
        agent.semantic_inbox_dir / "README.md",
        (
            "# Semantic Inbox\n\n"
            "这里存放从工作记忆中提炼出的语义记忆片段，供 embedding / RAG 使用。\n"
        ),
    )
    ensure_file(
        agent.semantic_project_updates_dir / "README.md",
        (
            "# Project Updates\n\n"
            "这里存放按项目整理的阶段更新，便于回看某个项目最近推进了什么。\n"
        ),
    )
    ensure_file(
        agent.semantic_decisions_dir / "README.md",
        (
            "# Semantic Decisions\n\n"
            "这里存放从会话中提炼出的决策语义片段，面向 RAG 检索。\n"
        ),
    )
    ensure_file(
        agent.semantic_lessons_dir / "README.md",
        (
            "# Lessons\n\n"
            "这里存放从会话中提炼出的经验教训片段，面向复盘与复用。\n"
        ),
    )
    ensure_file(
        agent.semantic_preferences_dir / "README.md",
        (
            "# Preferences\n\n"
            "这里存放用户或项目偏好片段，面向长期风格、约束与偏好检索。\n"
        ),
    )
    ensure_file(
        agent.semantic_review_queue_dir / "README.md",
        (
            "# Review Queue\n\n"
            "这里存放待人工确认的候选学习项。\n"
            "只有经过 review 才允许把社区学习沉淀为长期规则。\n"
        ),
    )
    ensure_file(
        agent.semantic_review_reports_dir / "README.md",
        (
            "# Review Reports\n\n"
            "这里存放周期性反馈报告，供用户决定候选学习项是采纳、观察还是排除。\n"
        ),
    )
    ensure_file(
        agent.structured_dir / "README.md",
        (
            "# Structured Long-Term Memory\n\n"
            "- `memory.db`: 本地 SQLite 长期记忆库\n"
            "- 只存高价值、可精确查询的数据，不存完整聊天原文\n"
            "- `review_queue`: 候选学习项审阅队列\n"
        ),
    )
    ensure_file(
        agent.working_index_file,
        json.dumps(
            {
                "agentId": agent.agent_id,
                "updatedAt": dt.datetime.now().isoformat(),
                "sessions": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    init_sqlite_schema(agent.structured_db)


def extract_text_from_content(content: List[Dict]) -> str:
    parts: List[str] = []
    for item in content or []:
        if item.get("type") == "text":
            text = str(item.get("text", "")).strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def load_session_messages(session_file: Path) -> List[Dict]:
    rows: List[Dict] = []
    if (not session_file.exists()) or (not session_file.is_file()):
        return rows
    try:
        raw_lines = session_file.read_text(encoding="utf-8").splitlines()
    except Exception:
        return rows
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if obj.get("type") != "message":
            continue
        msg = obj.get("message", {})
        role = msg.get("role")
        text = extract_text_from_content(msg.get("content", []))
        rows.append(
            {
                "timestamp": obj.get("timestamp") or msg.get("timestamp") or "",
                "role": role,
                "text": text,
            }
        )
    return rows


def normalize_lines(text: str) -> List[str]:
    return [ln.strip() for ln in TEXT_SPLIT_RE.split(text) if ln.strip()]


def normalize_markdown_text(text: str) -> str:
    out = text.strip()
    out = out.replace("**", "").replace("__", "").replace("`", "")
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def normalize_table_task(line: str) -> Optional[str]:
    raw = line.strip()
    if not (raw.startswith("|") and raw.endswith("|")):
        return None
    parts = [normalize_markdown_text(p) for p in raw.strip("|").split("|")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        return None

    title = parts[0]
    owner = parts[1] if len(parts) >= 2 else ""
    deliverable = parts[2] if len(parts) >= 3 else ""
    due = parts[3] if len(parts) >= 4 else ""

    suffix: List[str] = []
    if owner:
        suffix.append(f"负责人：{owner}")
    if due:
        suffix.append(f"截止：{due}")

    if deliverable:
        base = f"{title}：{deliverable}"
    else:
        base = title

    if suffix:
        return f"{base}（{'，'.join(suffix)}）"
    return base


def normalize_task_candidate(line: str) -> str:
    table_task = normalize_table_task(line)
    if table_task:
        return table_task
    text = line.strip()
    text = re.sub(r"^[-*]\s*", "", text)
    text = re.sub(r"^\d+\.\s*", "", text)
    text = normalize_markdown_text(text)
    return text.strip()


def is_meaningful_project_line(text: str) -> bool:
    line = normalize_markdown_text(text)
    if not line:
        return False
    if line in {"(none)", "Recent Highlights", "Open Items"}:
        return False
    if line in {"```", "{", "}", "json"}:
        return False
    if line.startswith(("##", "#", "---")):
        return False
    if line.startswith(('"', "`")):
        return False
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*:\s", line):
        return False
    if line.startswith(("[assistant]", "[user]", "- [assistant]", "- [user]")):
        return False
    if line.startswith(("- agent:", "- session", "- chat_type:", "- updated_at:", "- message_count:")):
        return False
    if line.startswith("System:"):
        return False
    if ROLE_LINE_RE.search(line):
        return False
    if PROJECT_NOISE_RE.search(line):
        return False
    if DELIVERY_NOISE_RE.search(line):
        return False
    return True


def clean_project_text(text: str) -> str:
    if not text:
        return ""
    parts: List[str] = []
    for raw in TEXT_SPLIT_RE.split(text):
        line = normalize_markdown_text(raw)
        if not is_meaningful_project_line(line):
            continue
        line = re.sub(r"^@\S+\s*", "", line)
        line = re.sub(r"^\s*Owner[:：]?\s*", "", line, flags=re.IGNORECASE)
        line = re.sub(r"^\s*QY\S+[:：]?\s*", "", line)
        line = line.strip(" -")
        if line and line not in parts:
            parts.append(line)
    merged = " ".join(parts).strip()
    if merged.startswith("json "):
        merged = merged[5:].strip()
    if len(merged) > 500:
        merged = merged[:497].rstrip() + "..."
    return merged


def derive_project_summary_goal(
    topic: str,
    latest_user: str,
    latest_assistant: str,
    summary_text: str,
    todo_items: List[str],
) -> Tuple[str, str]:
    cleaned_user = clean_project_text(latest_user)
    cleaned_assistant = clean_project_text(latest_assistant)

    summary = cleaned_user
    if not summary and todo_items:
        summary = "；".join(todo_items[:2])
    if not summary and cleaned_assistant:
        summary = cleaned_assistant
    if not summary:
        summary_lines: List[str] = []
        for line in summary_text.splitlines():
            cleaned = clean_project_text(line)
            if cleaned and cleaned not in summary_lines:
                summary_lines.append(cleaned)
            if len(" ".join(summary_lines)) >= 180:
                break
        summary = "；".join(summary_lines[:2])
    if not summary:
        summary = topic
    if topic == "产品规划" and todo_items:
        summary = "；".join(todo_items[:2])
    if topic == "邮件处理" and ("最新邮件下载完成" in summary or "新邮件：" in summary):
        summary = "处理邮件下载、附件归档与新增邮件登记"
    if len(summary) > 300:
        summary = summary[:297].rstrip() + "..."

    goal = cleaned_user
    if not goal and todo_items:
        goal = "推进：" + "；".join(todo_items[:3])
    if not goal and summary:
        goal = summary
    if topic == "产品规划" and todo_items:
        goal = "推进：" + "；".join(todo_items[:3])
    if topic == "邮件处理" and (not goal or "最新邮件下载完成" in goal or "新邮件：" in goal):
        goal = "按批次下载邮件、整理附件并登记新增邮件"
    if len(goal) > 500:
        goal = goal[:497].rstrip() + "..."
    return summary, goal


def should_keep_task_item(line: str) -> bool:
    text = normalize_task_candidate(line)
    if not text or text == "(none)":
        return False

    if TASK_NOISE_RE.search(text) and not ACTION_HINT_RE.search(text):
        return False

    if MAIL_SUBJECT_NOISE_RE.search(text):
        return False

    if DELIVERY_NOISE_RE.search(text):
        return False

    if BUDGET_LINE_RE.search(text) and not ACTION_HINT_RE.search(text.replace("设计费", "").replace("手板费", "")):
        return False

    if text.startswith(("收到。", "收到，", "收到。两个", "收到，两个")):
        return False

    if re.search(r"(完成|已发送|已完成)$", text) and not TASK_HINT_RE.search(text):
        return False

    if re.fullmatch(r"[*_：:（）()\-\d\s%.+万封行kK]+", text):
        return False

    if ("：" in text or ":" in text) and not ACTION_HINT_RE.search(text):
        return False

    if TASK_HINT_RE.search(text):
        return True

    if re.match(r"^\d+\.", line.strip()):
        return True

    if PLANNING_ACTION_RE.search(text):
        return True

    return False


def summarize_recent(messages: List[Dict], max_pairs: int = 3) -> Tuple[str, str, List[str]]:
    recent = [m for m in messages if m.get("text")][-12:]
    last_user = ""
    last_assistant = ""
    highlights: List[str] = []
    for msg in reversed(recent):
        if msg["role"] == "user" and not last_user:
            last_user = msg["text"]
        if msg["role"] == "assistant" and not last_assistant:
            last_assistant = msg["text"]
        if last_user and last_assistant:
            break

    pair_count = 0
    for msg in recent:
        text = msg.get("text", "")
        if not text:
            continue
        one_line = text.replace("\n", " / ")
        if len(one_line) > 180:
            one_line = one_line[:177] + "..."
        highlights.append(f"- [{msg['role']}] {one_line}")
        if msg["role"] == "assistant":
            pair_count += 1
        if pair_count >= max_pairs:
            break
    return last_user, last_assistant, highlights


def derive_todo_lines(messages: List[Dict], limit: int = 8) -> List[str]:
    todos: List[str] = []
    for msg in reversed(messages[-20:]):
        text = msg.get("text", "")
        if not text:
            continue
        for line in normalize_lines(text):
            if msg["role"] == "assistant" and line.startswith("System:"):
                continue
            if not should_keep_task_item(line):
                continue
            item = normalize_task_candidate(line)
            if len(item) > 180:
                item = item[:177] + "..."
            if item not in todos:
                todos.append(item)
            if len(todos) >= limit:
                return todos
    return todos


def write_working_task_snapshots(agent: Agent, session_id: str, session_key: str, todo_items: List[str]) -> int:
    prefix = f"{session_id}"
    for old in agent.working_tasks_dir.glob(f"{prefix}*.md"):
        old.unlink()
    if not todo_items:
        return 0

    now = dt.datetime.now().astimezone().isoformat()
    aggregate = [
        f"# Task Snapshot - {session_id}",
        "",
        f"- agent: `{agent.agent_id}` ({agent.name})",
        f"- session_id: `{session_id}`",
        f"- session_key: `{session_key}`",
        f"- updated_at: `{now}`",
        "",
        "## Open Tasks",
        "",
    ]
    aggregate.extend([f"{idx}. {item}" for idx, item in enumerate(todo_items, start=1)])
    aggregate.append("")
    write_text(agent.working_tasks_dir / f"{session_id}.md", "\n".join(aggregate))

    count = 1
    for idx, item in enumerate(todo_items, start=1):
        digest = hashlib.sha1(item.encode("utf-8")).hexdigest()[:8]
        slug = safe_slug(item, max_len=32)
        task_path = agent.working_tasks_dir / f"{session_id}--{idx:02d}--{slug}--{digest}.md"
        detail = [
            f"# Working Task - {item}",
            "",
            f"- agent: `{agent.agent_id}` ({agent.name})",
            f"- session_id: `{session_id}`",
            f"- session_key: `{session_key}`",
            f"- sequence: `{idx}`",
            f"- status: `open`",
            f"- updated_at: `{now}`",
            "",
            "## Task",
            "",
            item,
            "",
        ]
        write_text(task_path, "\n".join(detail))
        count += 1
    return count


def capture_agent(agent: Agent) -> int:
    init_agent(agent)
    if not agent.session_registry_file.exists():
        return 0
    registry = read_json(agent.session_registry_file)
    sessions_index: List[Dict] = []
    captured = 0

    for session_key, meta in registry.items():
        session_id = str(meta.get("sessionId", "")).strip()
        if not session_id:
            continue
        session_file = resolve_session_file(
            agent,
            str(meta.get("sessionFile", "")).strip(),
            session_id=session_id,
        )
        rows = load_session_messages(session_file)
        if not rows:
            continue
        updated_at_ms = int(meta.get("updatedAt") or 0)
        updated_iso = (
            dt.datetime.fromtimestamp(updated_at_ms / 1000, tz=dt.timezone.utc).astimezone().isoformat()
            if updated_at_ms
            else dt.datetime.now().astimezone().isoformat()
        )
        chat_type = str(meta.get("chatType", "")).strip() or "unknown"
        last_user, last_assistant, highlights = summarize_recent(rows)
        todos = derive_todo_lines(rows)

        sess_dir = agent.working_sessions_dir / session_id
        sess_dir.mkdir(parents=True, exist_ok=True)
        message_count = len(rows)

        state_md = [
            f"# Session State - {session_id}",
            "",
            f"- agent: `{agent.agent_id}` ({agent.name})",
            f"- session_key: `{session_key}`",
            f"- chat_type: `{chat_type}`",
            f"- updated_at: `{updated_iso}`",
            f"- message_count: `{message_count}`",
            "",
            "## Latest User Intent",
            "",
            last_user or "(none)",
            "",
            "## Latest Assistant Response",
            "",
            last_assistant or "(none)",
            "",
        ]
        write_text(sess_dir / "state.md", "\n".join(state_md).rstrip() + "\n")

        summary_md = [
            f"# Session Summary - {session_id}",
            "",
            "## Recent Highlights",
            "",
        ]
        summary_md.extend(highlights or ["- (none)"])
        summary_md.append("")
        write_text(sess_dir / "summary.md", "\n".join(summary_md).rstrip() + "\n")

        todo_md = [
            f"# Session TODO - {session_id}",
            "",
            "## Open Items",
            "",
        ]
        todo_md.extend([f"- {item}" for item in todos] or ["- (none)"])
        todo_md.append("")
        write_text(sess_dir / "todo.md", "\n".join(todo_md).rstrip() + "\n")

        meta_payload = {
            "agentId": agent.agent_id,
            "agentName": agent.name,
            "sessionId": session_id,
            "sessionKey": session_key,
            "sessionFile": str(session_file),
            "updatedAt": updated_iso,
            "chatType": chat_type,
            "messageCount": message_count,
            "lastUser": last_user,
            "lastAssistant": last_assistant,
            "capturedAt": dt.datetime.now().astimezone().isoformat(),
        }
        write_text(sess_dir / "meta.json", json.dumps(meta_payload, ensure_ascii=False, indent=2) + "\n")

        sessions_index.append(
            {
                "sessionId": session_id,
                "sessionKey": session_key,
                "chatType": chat_type,
                "updatedAt": updated_iso,
                "messageCount": message_count,
                "path": str(sess_dir),
            }
        )
        captured += 1

    sessions_index.sort(key=lambda x: x["updatedAt"], reverse=True)
    write_text(
        agent.working_index_file,
        json.dumps(
            {
                "agentId": agent.agent_id,
                "agentName": agent.name,
                "updatedAt": dt.datetime.now().astimezone().isoformat(),
                "sessions": sessions_index,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return captured


def compute_session_hash(meta: Dict, state_text: str, summary_text: str, todo_text: str) -> str:
    raw = json.dumps(
        {
            "updatedAt": meta.get("updatedAt", ""),
            "lastUser": meta.get("lastUser", ""),
            "lastAssistant": meta.get("lastAssistant", ""),
            "state": state_text,
            "summary": summary_text,
            "todo": todo_text,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def session_already_ingested(conn: sqlite3.Connection, session_id: str, session_hash: str) -> bool:
    row = conn.execute(
        "SELECT session_hash FROM ingestion_runs WHERE source_session = ?",
        (session_id,),
    ).fetchone()
    return bool(row and row[0] == session_hash)


def upsert_artifact(
    conn: sqlite3.Connection,
    *,
    path: str,
    kind: str,
    title: str,
    workspace: str,
    agent_id: str,
    project_slug: str,
    summary: str,
    tags: str,
    content_hash: str,
    source_session: str,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO artifacts(
          path, kind, title, workspace, agent_id, project_slug, summary, tags,
          content_hash, source_session, source_note, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
          kind=excluded.kind,
          title=excluded.title,
          workspace=excluded.workspace,
          agent_id=excluded.agent_id,
          project_slug=excluded.project_slug,
          summary=excluded.summary,
          tags=excluded.tags,
          content_hash=excluded.content_hash,
          source_session=excluded.source_session,
          source_note=excluded.source_note,
          updated_at=excluded.updated_at
        """,
        (
            path,
            kind,
            title,
            workspace,
            agent_id,
            project_slug,
            summary,
            tags,
            content_hash,
            source_session,
            source_note,
            now,
            now,
        ),
    )


def upsert_project(
    conn: sqlite3.Connection,
    *,
    slug: str,
    name: str,
    status: str,
    priority: str,
    owner_agent: str,
    owner_human: str,
    summary: str,
    goal: str,
    constraints: str,
    tags: str,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO projects(
          slug, name, status, priority, owner_agent, owner_human, summary, goal,
          constraints, tags, source_note, notes, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
          name=excluded.name,
          status=excluded.status,
          priority=excluded.priority,
          owner_agent=excluded.owner_agent,
          owner_human=excluded.owner_human,
          summary=excluded.summary,
          goal=excluded.goal,
          constraints=excluded.constraints,
          tags=excluded.tags,
          source_note=excluded.source_note,
          updated_at=excluded.updated_at
        """,
        (
            slug,
            name,
            status,
            priority,
            owner_agent,
            owner_human,
            summary,
            goal,
            constraints,
            tags,
            source_note,
            now,
            now,
        ),
    )


def upsert_contact(
    conn: sqlite3.Connection,
    *,
    name: str,
    role: str,
    org: str,
    contact_type: str,
    channel: str,
    identifier: str,
    notes: str,
    tags: str,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    row = conn.execute(
        "SELECT id FROM contacts WHERE name = ? AND org = ? AND channel = ? AND identifier = ?",
        (name, org, channel, identifier),
    ).fetchone()
    if row:
        conn.execute(
            """
            UPDATE contacts
            SET role = ?, contact_type = ?, notes = ?, tags = ?, source_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (role, contact_type, notes, tags, source_note, now, row[0]),
        )
        return

    legacy_row = conn.execute(
        "SELECT id FROM contacts WHERE name = ? AND org = ?",
        (name, org),
    ).fetchone()
    if legacy_row:
        conn.execute(
            """
            UPDATE contacts
            SET role = ?, contact_type = ?, channel = ?, identifier = ?, notes = ?, tags = ?, source_note = ?, updated_at = ?
            WHERE id = ?
            """,
            (role, contact_type, channel, identifier, notes, tags, source_note, now, legacy_row[0]),
        )
        return

    conn.execute(
        """
        INSERT INTO contacts(
          name, role, org, contact_type, channel, identifier, notes, tags, source_note, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            role,
            org,
            contact_type,
            channel,
            identifier,
            notes,
            tags,
            source_note,
            now,
            now,
        ),
    )


def upsert_task(
    conn: sqlite3.Connection,
    *,
    project_slug: str,
    title: str,
    description: str,
    status: str,
    priority: str,
    task_type: str,
    assignee_agent: str,
    assignee_human: str,
    blocking_reason: str,
    tags: str,
    source_session: str,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO tasks(
          project_slug, title, description, status, priority, task_type, assignee_agent,
          assignee_human, blocking_reason, tags, source_session, source_note, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_session, title) DO UPDATE SET
          project_slug=excluded.project_slug,
          description=excluded.description,
          status=excluded.status,
          priority=excluded.priority,
          task_type=excluded.task_type,
          assignee_agent=excluded.assignee_agent,
          assignee_human=excluded.assignee_human,
          blocking_reason=excluded.blocking_reason,
          tags=excluded.tags,
          source_note=excluded.source_note,
          updated_at=excluded.updated_at
        """,
        (
            project_slug,
            title,
            description,
            status,
            priority,
            task_type,
            assignee_agent,
            assignee_human,
            blocking_reason,
            tags,
            source_session,
            source_note,
            now,
            now,
        ),
    )


def upsert_decision(
    conn: sqlite3.Connection,
    *,
    project_slug: str,
    title: str,
    summary: str,
    decision_type: str,
    status: str,
    decision_maker: str,
    owner_agent: str,
    reasoning: str,
    impact_scope: str,
    risks: str,
    next_action: str,
    source_session: str,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO decisions(
          project_slug, title, summary, decision_type, status, decision_maker, owner_agent,
          reasoning, impact_scope, risks, next_action, source_session, source_note, created_at, updated_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_session, title) DO UPDATE SET
          project_slug=excluded.project_slug,
          summary=excluded.summary,
          decision_type=excluded.decision_type,
          status=excluded.status,
          decision_maker=excluded.decision_maker,
          owner_agent=excluded.owner_agent,
          reasoning=excluded.reasoning,
          impact_scope=excluded.impact_scope,
          risks=excluded.risks,
          next_action=excluded.next_action,
          source_note=excluded.source_note,
          updated_at=excluded.updated_at
        """,
        (
            project_slug,
            title,
            summary,
            decision_type,
            status,
            decision_maker,
            owner_agent,
            reasoning,
            impact_scope,
            risks,
            next_action,
            source_session,
            source_note,
            now,
            now,
        ),
    )


def upsert_review_candidate(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    source_session: str,
    project_slug: str,
    title: str,
    summary: str,
    candidate_type: str,
    proposed_target: str,
    proposed_action: str,
    evidence: str,
    confidence: float,
    priority: int,
    source_note: str,
) -> None:
    now = dt.datetime.now().astimezone().isoformat()
    conn.execute(
        """
        INSERT INTO review_queue(
          agent_id, source_session, project_slug, title, summary, candidate_type, proposed_target,
          proposed_action, evidence, confidence, priority, status, reviewer, review_note,
          decision_ref, source_note, created_at, updated_at, reviewed_at
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', '', ?, ?, ?, '')
        ON CONFLICT(agent_id, source_session, candidate_type, title) DO UPDATE SET
          project_slug=excluded.project_slug,
          summary=excluded.summary,
          proposed_target=excluded.proposed_target,
          proposed_action=excluded.proposed_action,
          evidence=excluded.evidence,
          confidence=excluded.confidence,
          priority=excluded.priority,
          source_note=excluded.source_note,
          updated_at=excluded.updated_at
        """,
        (
            agent_id,
            source_session,
            project_slug,
            title,
            summary,
            candidate_type,
            proposed_target,
            proposed_action,
            evidence,
            confidence,
            priority,
            source_note,
            now,
            now,
        ),
    )


def strip_candidate_prefix(text: str) -> str:
    cleaned = normalize_markdown_text(text)
    for prefix in ("- lesson:", "- decision:", "- preference:", "- summary:", "- next:", "- project:", "- goal:"):
        if cleaned.lower().startswith(prefix):
            return cleaned[len(prefix) :].strip()
    if cleaned.startswith("- "):
        return cleaned[2:].strip()
    return cleaned


def review_title(text: str) -> str:
    cleaned = strip_candidate_prefix(text)
    return cleaned[:80].strip() or "untitled-candidate"


def review_summary(text: str, limit: int = 260) -> str:
    cleaned = strip_candidate_prefix(text)
    if len(cleaned) > limit:
        return cleaned[: limit - 3].rstrip() + "..."
    return cleaned


def should_generate_review_candidate(agent: Agent, text: str, context: str, candidate_type: str) -> bool:
    cleaned = review_summary(text)
    if not cleaned or cleaned in {"(none)", "邮件处理", "产品规划"}:
        return False
    if len(cleaned) < 8:
        return False
    if REVIEW_NOISE_RE.search(cleaned):
        return False
    if candidate_type == "preference":
        return bool(PREFERENCE_HINT_RE.search(cleaned))
    if candidate_type == "decision":
        return bool(CONFIRMED_DECISION_RE.search(cleaned) or REVIEW_RULE_HINT_RE.search(cleaned))
    if candidate_type == "lesson":
        if REVIEW_RULE_HINT_RE.search(cleaned):
            return True
        if not INSTREET_HINT_RE.search(context):
            return False
        if cleaned.startswith(("当前推进重点：", "当前确认方向：")):
            return False
        return bool(INSTREET_LEARNING_VALUE_RE.search(cleaned))
    return False


def derive_review_candidates(
    agent: Agent,
    meta: Dict,
    project_slug: str,
    project_summary: str,
    project_goal: str,
    candidate_decisions: List[str],
    lesson_lines: List[str],
    preference_lines: List[str],
) -> List[Dict[str, object]]:
    context = " ".join(
        [
            project_slug,
            project_summary,
            project_goal,
            str(meta.get("lastUser", "") or ""),
            str(meta.get("lastAssistant", "") or ""),
        ]
    )
    is_instreet = bool(INSTREET_HINT_RE.search(context))
    out: List[Dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(candidate_type: str, raw_text: str, proposed_target: str, confidence: float, priority: int) -> None:
        cleaned_title = review_title(raw_text)
        cleaned_summary = review_summary(raw_text)
        if not should_generate_review_candidate(agent, raw_text, context, candidate_type):
            return
        key = (candidate_type, cleaned_title)
        if key in seen:
            return
        seen.add(key)
        proposed_action = "adopt" if proposed_target == "30-procedures" else "observe"
        evidence = project_summary or project_goal or review_summary(str(meta.get("lastUser", "") or ""), 180)
        out.append(
            {
                "candidate_type": candidate_type,
                "title": cleaned_title,
                "summary": cleaned_summary,
                "proposed_target": proposed_target,
                "proposed_action": proposed_action,
                "confidence": round(confidence, 2),
                "priority": priority,
                "evidence": evidence[:260],
            }
        )

    for item in candidate_decisions:
        target = "30-procedures" if REVIEW_RULE_HINT_RE.search(item) else "20-semantic/decisions"
        add("decision", item, target, 0.9 if target == "30-procedures" else 0.78, 3 if target == "30-procedures" else 2)

    for line in lesson_lines:
        cleaned = strip_candidate_prefix(line)
        if cleaned in {"(none)", ""}:
            continue
        target = "30-procedures" if REVIEW_RULE_HINT_RE.search(cleaned) or is_instreet else "20-semantic/lessons"
        add("lesson", cleaned, target, 0.82 if target == "30-procedures" else 0.68, 3 if target == "30-procedures" else 2)

    for line in preference_lines:
        cleaned = strip_candidate_prefix(line)
        if cleaned in {"(none)", ""}:
            continue
        add("preference", cleaned, "20-semantic/preferences", 0.74, 2)

    return out


def review_row_label(row_id: int) -> str:
    return f"RQ-{row_id}"


def obsidian_review_dir(agent: Agent) -> Optional[Path]:
    mapping = {
        "main": OBSIDIAN_AGENT_DIR / "10-main-小云" / "InStreet" / "待审阅",
        "dev": OBSIDIAN_AGENT_DIR / "11-dev-开发与架构" / "待审阅",
        "content": OBSIDIAN_AGENT_DIR / "12-content-内容" / "待审阅",
        "ops": OBSIDIAN_AGENT_DIR / "13-ops-运营" / "待审阅",
        "law": OBSIDIAN_AGENT_DIR / "14-law-法务" / "待审阅",
        "finance": OBSIDIAN_AGENT_DIR / "15-finance-财务" / "待审阅",
    }
    return mapping.get(agent.agent_id)


def list_review_candidates(
    agent: Agent,
    *,
    days: int,
    statuses: Tuple[str, ...] = ("pending", "deferred"),
    limit: int = 50,
) -> List[sqlite3.Row]:
    conn = sqlite3.connect(str(agent.structured_db))
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (dt.datetime.now().astimezone() - dt.timedelta(days=days)).isoformat()
        placeholders = ",".join("?" for _ in statuses)
        sql = f"""
            SELECT id, agent_id, source_session, project_slug, title, summary, candidate_type,
                   proposed_target, proposed_action, evidence, confidence, priority, status,
                   reviewer, review_note, decision_ref, source_note, created_at, updated_at, reviewed_at
            FROM review_queue
            WHERE status IN ({placeholders}) AND updated_at >= ?
            ORDER BY priority DESC, confidence DESC, updated_at DESC
            LIMIT ?
        """
        params: List[object] = [*statuses, cutoff, limit]
        return list(conn.execute(sql, params).fetchall())
    finally:
        conn.close()


def write_review_report(agent: Agent, rows: List[sqlite3.Row], days: int) -> Optional[Path]:
    if not rows:
        return None
    month_key = dt.date.today().strftime("%Y-%m")
    report_dir = agent.semantic_review_reports_dir / month_key
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{dt.date.today().isoformat()}-learning-review.md"

    lines = [
        "# Learning Review Report",
        "",
        f"- agent: `{agent.agent_id}` / {agent.name}",
        f"- window: last {days} day(s)",
        f"- generated_at: {dt.datetime.now().astimezone().isoformat()}",
        "",
        "## Review Rule",
        "",
        "- `采纳`：允许后续写入长期规则或 procedure 层",
        "- `观察`：保留候选项，但暂不固化为规则",
        "- `排除`：明确不采用，避免以后反复提起",
        "",
        "## Pending Items",
        "",
    ]

    for row in rows:
        lines.extend(
            [
                f"### {review_row_label(int(row['id']))} · {row['title']}",
                "",
                f"- type: `{row['candidate_type']}`",
                f"- recommendation: `{row['proposed_action']}`",
                f"- target: `{row['proposed_target']}`",
                f"- confidence: `{row['confidence']}`",
                f"- priority: `{row['priority']}`",
                f"- status: `{row['status']}`",
                f"- project: `{row['project_slug']}`",
                f"- summary: {row['summary']}",
                f"- evidence: {row['evidence'] or '(none)'}",
                "",
            ]
        )

    lines.extend(
        [
            "## How To Decide",
            "",
            "示例回复：",
            "- `采纳 RQ-12`",
            "- `观察 RQ-18`",
            "- `排除 RQ-21，理由：不适用于当前阶段`",
            "",
        ]
    )
    write_text(report_path, "\n".join(lines).rstrip() + "\n")

    mirror_dir = obsidian_review_dir(agent)
    if mirror_dir is not None:
        mirror_dir.mkdir(parents=True, exist_ok=True)
        write_text(mirror_dir / report_path.name, report_path.read_text(encoding="utf-8"))
    return report_path


def update_review_status(agent: Agent, candidate_id: int, status: str, note: str, reviewer: str) -> int:
    now = dt.datetime.now().astimezone().isoformat()
    conn = sqlite3.connect(str(agent.structured_db))
    try:
        cur = conn.execute(
            """
            UPDATE review_queue
            SET status = ?, review_note = ?, reviewer = ?, reviewed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, note, reviewer, now, now, candidate_id),
        )
        conn.commit()
        return int(cur.rowcount)
    finally:
        conn.close()


def approved_target_path(agent: Agent, proposed_target: str) -> Path:
    month_key = dt.date.today().strftime("%Y-%m")
    if proposed_target == "30-procedures":
        return agent.memory_dir / "30-procedures" / "APPROVED-LEARNINGS.md"
    if proposed_target == "20-semantic/decisions":
        return agent.semantic_decisions_dir / month_key / "approved.md"
    if proposed_target == "20-semantic/preferences":
        return agent.semantic_preferences_dir / month_key / "approved.md"
    return agent.semantic_lessons_dir / month_key / "approved.md"


def apply_approved_reviews(agent: Agent) -> int:
    conn = sqlite3.connect(str(agent.structured_db))
    conn.row_factory = sqlite3.Row
    applied = 0
    try:
        rows = conn.execute(
            """
            SELECT id, title, summary, candidate_type, proposed_target, evidence, decision_ref
            FROM review_queue
            WHERE agent_id = ? AND status = 'approved' AND decision_ref = ''
            ORDER BY priority DESC, confidence DESC, updated_at ASC
            """,
            (agent.agent_id,),
        ).fetchall()
        for row in rows:
            target = approved_target_path(agent, str(row["proposed_target"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            entry = "\n".join(
                [
                    f"## {review_row_label(int(row['id']))} - {row['title']}",
                    "",
                    f"- type: `{row['candidate_type']}`",
                    f"- source: `{row['proposed_target']}`",
                    f"- summary: {row['summary']}",
                    f"- evidence: {row['evidence'] or '(none)'}",
                    "",
                ]
            )
            upsert_line(target, entry)
            now = dt.datetime.now().astimezone().isoformat()
            conn.execute(
                "UPDATE review_queue SET decision_ref = ?, updated_at = ? WHERE id = ?",
                (str(target), now, int(row["id"])),
            )
            applied += 1
        conn.commit()
    finally:
        conn.close()
    return applied


def derive_project_from_session(agent: Agent, meta: Dict, summary_text: str, todo_items: List[str]) -> Tuple[str, str, str, str]:
    session_id = str(meta.get("sessionId", "")).strip()
    latest_user = str(meta.get("lastUser", "")).strip()
    latest_assistant = str(meta.get("lastAssistant", "")).strip()
    chat_type = str(meta.get("chatType", "")).strip()

    topic = ""
    candidates = [latest_user, latest_assistant]
    for candidate in candidates:
        text = normalize_markdown_text(candidate)
        if not text:
            continue
        if "邮件" in text:
            topic = "邮件处理"
            break
        if any(k in text for k in ["产品", "PRD", "工业设计", "3C", "EAC", "B2B", "Prompt", "结构工程", "外观设计"]):
            topic = "产品规划"
            break
        if any(k in text for k in ["合同", "法务", "合规", "隐私"]):
            topic = "法务审查"
            break
        if any(k in text for k in ["预算", "成本", "ROI", "现金流", "财务"]):
            topic = "财务评估"
            break

    if not topic and todo_items:
        first = normalize_markdown_text(todo_items[0])
        if first:
            topic = first[:24]

    if not topic:
        topic = f"{agent.name}会话"

    base_slug = safe_slug(topic, max_len=24)
    if chat_type == "group":
        project_slug = f"{agent.agent_id}-group-{base_slug}"
    else:
        project_slug = f"{agent.agent_id}-{base_slug}"

    project_name = f"{agent.name} - {topic}"
    summary, goal = derive_project_summary_goal(topic, latest_user, latest_assistant, summary_text, todo_items)
    return project_slug, project_name, summary, goal


def derive_decision_candidates(meta: Dict, summary_text: str, todo_items: List[str]) -> List[str]:
    candidates: List[str] = []

    for line in summary_text.splitlines():
        clean = line.strip()
        if not clean.startswith("- "):
            continue
        item = normalize_markdown_text(clean[2:].strip())
        if not item:
            continue
        if item.startswith("[assistant]") or item.startswith("[user]"):
            continue
        if CONFIRMED_DECISION_RE.search(item):
            if item not in candidates:
                candidates.append(item)
                continue
        if DECISION_HINT_RE.search(item) and item not in candidates:
            candidates.append(item)

    # Some task items are actually explicit decisions (for example "先做3C", "EAC后置").
    for item in todo_items:
        normalized = normalize_markdown_text(item)
        if not normalized:
            continue
        if CONFIRMED_DECISION_RE.search(normalized) and normalized not in candidates:
            candidates.append(normalized)

    return candidates[:5]


def derive_contact_candidates(meta: Dict, summary_text: str) -> List[Dict[str, str]]:
    candidates: List[Dict[str, str]] = []
    seen: set[Tuple[str, str, str, str]] = set()

    def add(name: str, role: str, org: str, contact_type: str, channel: str, identifier: str, notes: str, tags: str) -> None:
        clean_name = normalize_markdown_text(name)
        clean_org = normalize_markdown_text(org)
        clean_identifier = normalize_markdown_text(identifier)
        clean_notes = normalize_markdown_text(notes)
        if not clean_name:
            return
        key = (clean_name.lower(), clean_org.lower(), channel.lower(), clean_identifier.lower())
        if key in seen:
            return
        seen.add(key)
        candidates.append(
            {
                "name": clean_name[:80],
                "role": normalize_markdown_text(role)[:80],
                "org": clean_org[:160],
                "contact_type": contact_type[:40],
                "channel": channel[:40],
                "identifier": clean_identifier[:160],
                "notes": clean_notes[:300],
                "tags": tags[:160],
            }
        )

    last_user = str(meta.get("lastUser", "") or "")
    sender_name = ""
    sender_label = ""
    sender_id = ""
    sender_name_match = re.search(r'"name"\s*:\s*"([^"]+)"', last_user)
    if sender_name_match:
        sender_name = sender_name_match.group(1).strip()
    sender_label_match = re.search(r'"label"\s*:\s*"([^"]+)"', last_user)
    if sender_label_match:
        sender_label = sender_label_match.group(1).strip()
    sender_id_match = FEISHU_SENDER_RE.search(last_user)
    if sender_id_match:
        sender_id = sender_id_match.group(1).strip()
    chosen_sender = sender_label or sender_name
    if chosen_sender and chosen_sender.lower() not in {"assistant", "system"}:
        add(
            chosen_sender,
            "sender",
            "",
            "person",
            "feishu",
            sender_id or chosen_sender,
            "会话发送者",
            "feishu,sender",
        )

    for email in EMAIL_RE.findall(last_user + "\n" + summary_text):
        local = email.split("@", 1)[0]
        inferred_name = re.sub(r"[._\\-]+", " ", local).strip() or local
        add(
            inferred_name,
            "email",
            "",
            "person",
            "email",
            email,
            "会话中提及邮箱",
            "email",
        )

    def ingest_contact_line(content: str, channel: str, tag: str) -> None:
        normalized_content = normalize_markdown_text(content)
        if not normalized_content or ("：" not in normalized_content and ":" not in normalized_content):
            return
        match = CONTACT_NAME_LINE_RE.match(normalized_content)
        if not match:
            return
        name = match.group(1).strip()
        detail = normalize_markdown_text(match.group(2))
        if not name or len(name) > 24:
            return
        if CONTACT_NAME_NOISE_RE.search(name):
            return
        if re.search(r"\d", name):
            return
        if MAIL_SUBJECT_NOISE_RE.search(normalized_content):
            add(
                name,
                "email-sender",
                "",
                "person",
                "email",
                "",
                detail,
                f"mail-sender,{tag}",
            )
            return
        org = ""
        if any(token in detail for token in ["有限公司", "公司", "集团", "科技", "贸易", "供应", "中心", "银行", "大学"]):
            org = detail.split("（", 1)[0].split("(", 1)[0][:160]
        add(
            name,
            "email-sender" if channel == "email" else "",
            org,
            "person",
            channel,
            "",
            detail,
            tag,
        )

    for raw in summary_text.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        ingest_contact_line(line[2:].strip(), "text", "derived")

    last_assistant = str(meta.get("lastAssistant", "") or "")
    for raw in last_assistant.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        ingest_contact_line(line[2:].strip(), "email", "mail-derived")

    return candidates[:10]


def write_semantic_category_note(
    base_dir: Path,
    month_key: str,
    session_id: str,
    title: str,
    kind: str,
    body_lines: List[str],
    meta: Dict,
    agent: Agent,
    project_slug: str,
) -> Optional[Path]:
    if not body_lines:
        return None
    month_dir = base_dir / month_key
    month_dir.mkdir(parents=True, exist_ok=True)
    path = month_dir / f"{session_id}.md"
    note_lines = [
        "---",
        f"id: {agent.agent_id}-{kind}-{session_id}",
        f"agent: {agent.agent_id}",
        f"agent_name: {agent.name}",
        f"source_session: {session_id}",
        f"project_slug: {project_slug}",
        f"chat_type: {meta.get('chatType', '')}",
        f"updated_at: {meta.get('updatedAt', '')}",
        f"extracted_at: {dt.datetime.now().astimezone().isoformat()}",
        f"kind: {kind}",
        "---",
        "",
        f"# {title}",
        "",
        *body_lines,
        "",
    ]
    write_text(path, "\n".join(note_lines).rstrip() + "\n")
    return path


def derive_lesson_candidates(todo_items: List[str], candidate_decisions: List[str], project_summary: str) -> List[str]:
    lessons: List[str] = []
    if todo_items:
        lessons.append(f"- 当前推进重点：{'；'.join(todo_items[:2])}")
    if candidate_decisions:
        lessons.append(f"- 当前确认方向：{'；'.join(candidate_decisions[:2])}")
    if project_summary and project_summary not in {"邮件处理", "产品规划"}:
        lessons.append(f"- 会话摘要：{project_summary}")
    return lessons[:3]


def derive_preference_candidates(meta: Dict, project_goal: str, candidate_decisions: List[str]) -> List[str]:
    preferences: List[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        cleaned = normalize_markdown_text(text)
        if not cleaned:
            return
        if len(cleaned) > 220:
            cleaned = cleaned[:217].rstrip() + "..."
        if cleaned in seen:
            return
        seen.add(cleaned)
        preferences.append(f"- preference: {cleaned}")

    last_user = clean_project_text(str(meta.get("lastUser", "") or ""))
    if last_user:
        parts = re.split(r"\s(?=\d+\.)", last_user)
        if len(parts) == 1:
            parts = [last_user]
        for part in parts:
            candidate = normalize_markdown_text(part)
            if PREFERENCE_HINT_RE.search(candidate):
                add(candidate)

    for item in candidate_decisions:
        if PREFERENCE_HINT_RE.search(item):
            add(item)

    if project_goal and PREFERENCE_HINT_RE.search(project_goal):
        add(project_goal)

    return preferences[:4]


def extract_agent(agent: Agent, force: bool = False) -> Dict[str, int]:
    init_agent(agent)
    session_dirs = sorted([p for p in agent.working_sessions_dir.iterdir() if p.is_dir()])
    conn = sqlite3.connect(str(agent.structured_db))
    extracted = 0
    tasks = 0
    decisions = 0
    contacts = 0
    review_candidates = 0
    try:
        for sess_dir in session_dirs:
            meta_file = sess_dir / "meta.json"
            state_file = sess_dir / "state.md"
            summary_file = sess_dir / "summary.md"
            todo_file = sess_dir / "todo.md"
            if not meta_file.exists():
                continue
            meta = read_json(meta_file)
            state_text = state_file.read_text(encoding="utf-8") if state_file.exists() else ""
            summary_text = summary_file.read_text(encoding="utf-8") if summary_file.exists() else ""
            todo_text = todo_file.read_text(encoding="utf-8") if todo_file.exists() else ""
            session_hash = compute_session_hash(meta, state_text, summary_text, todo_text)
            session_id = str(meta.get("sessionId", "")).strip()
            session_key = str(meta.get("sessionKey", "")).strip()
            if not session_id:
                continue
            if not force and session_already_ingested(conn, session_id, session_hash):
                continue

            month_dir = agent.semantic_inbox_dir / dt.date.today().strftime("%Y-%m")
            month_key = dt.date.today().strftime("%Y-%m")
            month_dir.mkdir(parents=True, exist_ok=True)
            note_path = month_dir / f"{session_id}.md"

            note_lines = [
                "---",
                f"id: {agent.agent_id}-{session_id}",
                f"agent: {agent.agent_id}",
                f"agent_name: {agent.name}",
                f"source_session: {session_id}",
                f"chat_type: {meta.get('chatType', '')}",
                f"updated_at: {meta.get('updatedAt', '')}",
                f"captured_at: {meta.get('capturedAt', '')}",
                f"extracted_at: {dt.datetime.now().astimezone().isoformat()}",
                "kind: session-summary",
                "---",
                "",
                f"# Session Summary - {session_id}",
                "",
                "## Latest User Intent",
                "",
                meta.get("lastUser", "") or "(none)",
                "",
                "## Latest Assistant Response",
                "",
                meta.get("lastAssistant", "") or "(none)",
                "",
                "## Working Summary",
                "",
                *(summary_text.strip().splitlines() if summary_text.strip() else ["(none)"]),
                "",
                "## Open Items",
                "",
                *(todo_text.strip().splitlines() if todo_text.strip() else ["(none)"]),
                "",
            ]
            write_text(note_path, "\n".join(note_lines).rstrip() + "\n")

            todo_items = []
            for line in todo_text.splitlines():
                clean = line.strip()
                if clean.startswith("- "):
                    if not should_keep_task_item(clean):
                        continue
                    item = normalize_task_candidate(clean)
                    if item and item != "(none)":
                        todo_items.append(item)
            project_slug, project_name, project_summary, project_goal = derive_project_from_session(agent, meta, summary_text, todo_items)
            upsert_project(
                conn,
                slug=project_slug,
                name=project_name,
                status="active",
                priority="normal",
                owner_agent=agent.agent_id,
                owner_human="Owner",
                summary=project_summary,
                goal=project_goal,
                constraints="",
                tags=agent.agent_id,
                source_note=str(note_path),
            )

            note_hash = hashlib.sha256(note_path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            upsert_artifact(
                conn,
                path=str(note_path),
                kind="semantic-note",
                title=f"Session {session_id}",
                workspace=str(agent.workspace),
                agent_id=agent.agent_id,
                project_slug=project_slug,
                summary=(meta.get("lastUser", "") or meta.get("lastAssistant", "") or "")[:500],
                tags=f"{agent.agent_id},session-summary",
                content_hash=note_hash,
                source_session=session_id,
                source_note=str(note_path),
            )
            write_working_task_snapshots(agent, session_id, session_key, todo_items)
            conn.execute("DELETE FROM tasks WHERE source_session = ?", (session_id,))
            for item in todo_items:
                upsert_task(
                    conn,
                    project_slug=project_slug,
                    title=item,
                    description=item,
                    status="open",
                    priority="normal",
                    task_type=agent.agent_id,
                    assignee_agent=agent.agent_id,
                    assignee_human="Owner",
                    blocking_reason="",
                    tags=agent.agent_id,
                    source_session=session_id,
                    source_note=str(note_path),
                )
                tasks += 1

            candidate_decisions = derive_decision_candidates(meta, summary_text, todo_items)
            write_semantic_category_note(
                agent.semantic_project_updates_dir,
                month_key,
                session_id,
                f"Project Update - {project_name}",
                "project-update",
                [
                    f"- project: `{project_slug}`",
                    f"- summary: {project_summary}",
                    f"- goal: {project_goal}",
                    *([f"- next: {item}" for item in todo_items[:3]] or ["- next: (none)"]),
                ],
                meta,
                agent,
                project_slug,
            )
            write_semantic_category_note(
                agent.semantic_decisions_dir,
                month_key,
                session_id,
                f"Decision Note - {project_name}",
                "decision-note",
                ([f"- decision: {item}" for item in candidate_decisions[:5]] or ["- decision: (none)"]),
                meta,
                agent,
                project_slug,
            )
            lesson_lines = derive_lesson_candidates(todo_items, candidate_decisions, project_summary)
            write_semantic_category_note(
                agent.semantic_lessons_dir,
                month_key,
                session_id,
                f"Lesson Note - {project_name}",
                "lesson-note",
                lesson_lines or ["- lesson: (none)"],
                meta,
                agent,
                project_slug,
            )
            preference_lines = derive_preference_candidates(meta, project_goal, candidate_decisions)
            write_semantic_category_note(
                agent.semantic_preferences_dir,
                month_key,
                session_id,
                f"Preference Note - {project_name}",
                "preference-note",
                preference_lines or ["- preference: (none)"],
                meta,
                agent,
                project_slug,
            )
            conn.execute(
                "DELETE FROM review_queue WHERE agent_id = ? AND source_session = ? AND status IN ('pending', 'deferred')",
                (agent.agent_id, session_id),
            )
            for candidate in derive_review_candidates(
                agent,
                meta,
                project_slug,
                project_summary,
                project_goal,
                candidate_decisions,
                lesson_lines,
                preference_lines,
            ):
                upsert_review_candidate(
                    conn,
                    agent_id=agent.agent_id,
                    source_session=session_id,
                    project_slug=project_slug,
                    title=str(candidate["title"]),
                    summary=str(candidate["summary"]),
                    candidate_type=str(candidate["candidate_type"]),
                    proposed_target=str(candidate["proposed_target"]),
                    proposed_action=str(candidate["proposed_action"]),
                    evidence=str(candidate["evidence"]),
                    confidence=float(candidate["confidence"]),
                    priority=int(candidate["priority"]),
                    source_note=str(note_path),
                )
                review_candidates += 1

            conn.execute("DELETE FROM decisions WHERE source_session = ?", (session_id,))
            if candidate_decisions:
                upsert_decision(
                    conn,
                    project_slug=project_slug,
                    title=f"Session {session_id}",
                    summary=" | ".join(candidate_decisions[:3]),
                    decision_type=agent.agent_id,
                    status="accepted",
                    decision_maker="Owner",
                    owner_agent=agent.agent_id,
                    reasoning=(meta.get("lastUser", "") or "")[:500],
                    impact_scope="session",
                    risks="",
                    next_action=todo_items[0] if todo_items else "",
                    source_session=session_id,
                    source_note=str(note_path),
                )
                decisions += 1

            contact_candidates = derive_contact_candidates(meta, summary_text)
            conn.execute("DELETE FROM contacts WHERE source_note = ?", (str(note_path),))
            for contact in contact_candidates:
                upsert_contact(
                    conn,
                    name=contact["name"],
                    role=contact["role"],
                    org=contact["org"],
                    contact_type=contact["contact_type"],
                    channel=contact["channel"],
                    identifier=contact["identifier"],
                    notes=contact["notes"],
                    tags=contact["tags"],
                    source_note=str(note_path),
                )
                contacts += 1

            # Final sync: ensure the project row exists even if earlier extraction branches change.
            upsert_project(
                conn,
                slug=project_slug,
                name=project_name,
                status="active",
                priority="normal",
                owner_agent=agent.agent_id,
                owner_human="Owner",
                summary=project_summary,
                goal=project_goal,
                constraints="",
                tags=agent.agent_id,
                source_note=str(note_path),
            )

            now = dt.datetime.now().astimezone().isoformat()
            conn.execute(
                """
                INSERT INTO ingestion_runs(agent_id, source_session, session_hash, note_path, pipeline_version, status, error_message, extracted_at)
                VALUES(?, ?, ?, ?, 'v1', 'ok', '', ?)
                ON CONFLICT(source_session) DO UPDATE SET
                  agent_id=excluded.agent_id,
                  session_hash=excluded.session_hash,
                  note_path=excluded.note_path,
                  pipeline_version=excluded.pipeline_version,
                  status=excluded.status,
                  error_message=excluded.error_message,
                  extracted_at=excluded.extracted_at
                """,
                (agent.agent_id, session_id, session_hash, str(note_path), now),
            )
            extracted += 1

        conn.commit()
    finally:
        conn.close()
    return {
        "extracted": extracted,
        "tasks": tasks,
        "decisions": decisions,
        "contacts": contacts,
        "review_candidates": review_candidates,
    }


def search_working(agent: Agent, query: str, limit: int) -> List[Tuple[Path, int, str]]:
    q = query.lower().strip()
    if not q:
        return []
    hits: List[Tuple[Path, int, str]] = []
    for path in sorted(agent.working_sessions_dir.glob("**/*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, start=1):
            if q in line.lower():
                hits.append((path, i, line.strip()))
                if len(hits) >= limit:
                    return hits
    return hits


def search_semantic(agent: Agent, query: str, limit: int) -> List[Tuple[Path, int, str]]:
    q = query.lower().strip()
    if not q:
        return []
    files: List[Path] = []
    files.extend(sorted(agent.semantic_inbox_dir.glob("**/*.md"), reverse=True))
    files.extend(sorted(agent.semantic_project_updates_dir.glob("**/*.md"), reverse=True))
    files.extend(sorted(agent.semantic_decisions_dir.glob("**/*.md"), reverse=True))
    files.extend(sorted(agent.semantic_lessons_dir.glob("**/*.md"), reverse=True))
    files.extend(sorted(agent.semantic_preferences_dir.glob("**/*.md"), reverse=True))
    files.extend(
        [
            agent.semantic_dir / "MEMORY.md",
            agent.semantic_dir / "projects.md",
            agent.semantic_dir / "lessons.md",
            agent.semantic_dir / "decisions.md",
            agent.semantic_dir / "recent-summary.md",
        ]
    )
    seen: set[str] = set()
    hits: List[Tuple[Path, int, str]] = []
    for path in files:
        if not path.exists():
            continue
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, start=1):
            if q in line.lower():
                hits.append((path, i, line.strip()))
                if len(hits) >= limit:
                    return hits
    return hits


def search_structured(agent: Agent, query: str, limit: int) -> List[Tuple[str, str, str]]:
    q = query.strip()
    if not q:
        return []
    like = f"%{q}%"
    conn = sqlite3.connect(str(agent.structured_db))
    out: List[Tuple[str, str, str]] = []
    try:
        queries = [
            (
                "projects",
                "SELECT 'projects', name, slug || ' | ' || summary FROM projects WHERE slug LIKE ? OR name LIKE ? OR summary LIKE ? OR goal LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
            (
                "tasks",
                "SELECT 'tasks', title, status || ' | ' || project_slug || ' | ' || source_session FROM tasks WHERE title LIKE ? OR description LIKE ? OR project_slug LIKE ? OR source_note LIKE ? OR tags LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
            (
                "decisions",
                "SELECT 'decisions', title, project_slug || ' | ' || summary FROM decisions WHERE title LIKE ? OR summary LIKE ? OR project_slug LIKE ? OR next_action LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
            (
                "artifacts",
                "SELECT 'artifacts', title, project_slug || ' | ' || path FROM artifacts WHERE title LIKE ? OR path LIKE ? OR tags LIKE ? OR project_slug LIKE ? OR summary LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
            (
                "contacts",
                "SELECT 'contacts', name, org || ' | ' || channel || ' | ' || COALESCE(identifier, '') || ' | ' || notes FROM contacts WHERE name LIKE ? OR role LIKE ? OR org LIKE ? OR identifier LIKE ? OR notes LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
            (
                "review_queue",
                "SELECT 'review_queue', title, status || ' | ' || candidate_type || ' | ' || proposed_target || ' | ' || summary FROM review_queue WHERE title LIKE ? OR summary LIKE ? OR evidence LIKE ? OR proposed_target LIKE ? OR review_note LIKE ? ORDER BY updated_at DESC LIMIT ?",
            ),
        ]
        for kind, sql in queries:
            if kind == "projects":
                rows = conn.execute(sql, (like, like, like, like, like, limit)).fetchall()
            elif kind == "tasks":
                rows = conn.execute(sql, (like, like, like, like, like, limit)).fetchall()
            elif kind == "decisions":
                rows = conn.execute(sql, (like, like, like, like, limit)).fetchall()
            elif kind == "artifacts":
                rows = conn.execute(sql, (like, like, like, like, like, limit)).fetchall()
            elif kind == "contacts":
                rows = conn.execute(sql, (like, like, like, like, like, limit)).fetchall()
            elif kind == "review_queue":
                rows = conn.execute(sql, (like, like, like, like, like, limit)).fetchall()
            else:
                rows = conn.execute(sql, (like, like, limit)).fetchall()
            for row in rows:
                out.append((row[0], row[1], row[2]))
                if len(out) >= limit:
                    return out
    finally:
        conn.close()
    return out


def _text_match_score(query: str, text: str) -> int:
    q = query.lower().strip()
    t = text.lower().strip()
    if not q or not t:
        return 0
    score = t.count(q) * 20
    if t.startswith(q):
        score += 25
    if q in t:
        score += 15
    if t == q:
        score += 60
    return score


def _working_path_bonus(path: Path) -> int:
    text = str(path).lower()
    if "/tasks/" in text:
        return 35
    if text.endswith("/todo.md"):
        return 30
    if text.endswith("/summary.md"):
        return 18
    if text.endswith("/state.md"):
        return 10
    return 0


def _semantic_path_bonus(path: Path) -> int:
    text = str(path).lower()
    if "/preferences/" in text:
        return 40
    if "/project-updates/" in text:
        return 34
    if "/decisions/" in text:
        return 32
    if "/lessons/" in text:
        return 26
    if "/inbox/" in text:
        return 18
    return 8


def _structured_kind_bonus(kind: str) -> int:
    return {
        "projects": 40,
        "tasks": 34,
        "decisions": 32,
        "contacts": 26,
        "artifacts": 18,
    }.get(kind, 10)


def search_ranked(agent: Agent, query: str, limit: int) -> List[Tuple[int, str, str, str]]:
    per_source_limit = max(3, limit * 3)
    scored: List[Tuple[int, str, str, str]] = []

    for path, line_no, text in search_working(agent, query, per_source_limit):
        score = 235 + _working_path_bonus(path) + _text_match_score(query, text)
        if text.startswith("System:"):
            score -= 90
        if len(text) > 220:
            score -= 20
        label = f"working:{path.name}:{line_no}"
        detail = f"{path}:{line_no}  {text}"
        scored.append((score, "working", label, detail))

    for path, line_no, text in search_semantic(agent, query, per_source_limit):
        score = 240 + _semantic_path_bonus(path) + _text_match_score(query, text)
        if "/preferences/" in str(path).lower() and text.strip().startswith("- preference:"):
            score += 30
        if text.strip().endswith("(none)"):
            score -= 40
        label = f"semantic:{path.parent.parent.name}/{path.parent.name}:{line_no}" if path.parent != agent.semantic_dir else f"semantic:{path.name}:{line_no}"
        detail = f"{path}:{line_no}  {text}"
        scored.append((score, "semantic", label, detail))

    for kind, title, detail in search_structured(agent, query, per_source_limit):
        score = 260 + _structured_kind_bonus(kind) + _text_match_score(query, title) + _text_match_score(query, detail)
        label = f"structured:{kind}"
        scored.append((score, "structured", label, f"[{kind}] {title}  {detail}"))

    scored.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    deduped: List[Tuple[int, str, str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for item in scored:
        dedupe_key = (item[1], item[3])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduped.append(item)
        if len(deduped) >= limit:
            break
    return deduped


def cmd_search_semantic(args: argparse.Namespace) -> None:
    found = 0
    for agent in iter_target_agents(args.agent):
        rows = search_semantic(agent, args.query, args.limit)
        if not rows:
            continue
        print(f"\n=== {agent.agent_id} ({agent.name}) semantic ===")
        for path, line_no, text in rows:
            print(f"{path}:{line_no}  {text}")
            found += 1
    if found == 0:
        print("no matches")


def cmd_search(args: argparse.Namespace) -> None:
    found = 0
    ranked_limit = max(1, args.limit)
    for agent in iter_target_agents(args.agent):
        ranked_rows = search_ranked(agent, args.query, ranked_limit)
        if not ranked_rows:
            continue
        print(f"\n=== {agent.agent_id} ({agent.name}) ===")
        for rank, row in enumerate(ranked_rows, start=1):
            score, layer, _label, detail = row
            print(f"[{rank}] {layer} score={score}  {detail}")
            found += 1
    if found == 0:
        print("no matches")


def iter_target_agents(agent_arg: str) -> Iterable[Agent]:
    agents = discover_agents()
    if agent_arg == "all":
        return agents
    for agent in agents:
        if agent.agent_id == agent_arg:
            return [agent]
    raise SystemExit(f"agent not found: {agent_arg}")


def cmd_init(args: argparse.Namespace) -> None:
    total = 0
    for agent in iter_target_agents(args.agent):
        init_agent(agent)
        print(f"[{agent.agent_id}] initialized {agent.memory_dir}")
        total += 1
    print(f"initialized pipeline memory layout for {total} agent(s)")


def cmd_capture(args: argparse.Namespace) -> None:
    total = 0
    for agent in iter_target_agents(args.agent):
        count = capture_agent(agent)
        print(f"[{agent.agent_id}] captured={count}")
        total += count
    print(f"capture done total_sessions={total}")


def cmd_extract(args: argparse.Namespace) -> None:
    total_extracted = 0
    total_tasks = 0
    total_decisions = 0
    total_contacts = 0
    total_review_candidates = 0
    for agent in iter_target_agents(args.agent):
        stat = extract_agent(agent, force=args.force)
        print(
            f"[{agent.agent_id}] extracted={stat['extracted']} tasks={stat['tasks']} decisions={stat['decisions']} contacts={stat['contacts']} review_candidates={stat['review_candidates']}"
        )
        total_extracted += stat["extracted"]
        total_tasks += stat["tasks"]
        total_decisions += stat["decisions"]
        total_contacts += stat["contacts"]
        total_review_candidates += stat["review_candidates"]
    print(
        f"extract done extracted={total_extracted} tasks={total_tasks} decisions={total_decisions} contacts={total_contacts} review_candidates={total_review_candidates}"
    )


def cmd_review_report(args: argparse.Namespace) -> None:
    for agent in iter_target_agents(args.agent):
        rows = list_review_candidates(agent, days=args.days, statuses=("pending", "deferred"), limit=args.limit)
        path = write_review_report(agent, rows, args.days)
        if path is None:
            print(f"[{agent.agent_id}] no pending review items in the last {args.days} day(s)")
            continue
        print(f"[{agent.agent_id}] review_report={path} items={len(rows)}")


def cmd_review_decide(args: argparse.Namespace) -> None:
    affected = 0
    for agent in iter_target_agents(args.agent):
        changed = update_review_status(agent, args.id, args.status, args.note, args.reviewer)
        if changed:
            print(f"[{agent.agent_id}] updated {review_row_label(args.id)} -> {args.status}")
            affected += changed
    if affected == 0:
        raise SystemExit(f"review candidate not found: {review_row_label(args.id)}")


def cmd_review_apply(args: argparse.Namespace) -> None:
    total = 0
    for agent in iter_target_agents(args.agent):
        applied = apply_approved_reviews(agent)
        print(f"[{agent.agent_id}] applied={applied}")
        total += applied
    print(f"review apply done total={total}")


def cmd_search_working(args: argparse.Namespace) -> None:
    found = 0
    for agent in iter_target_agents(args.agent):
        rows = search_working(agent, args.query, args.limit)
        if not rows:
            continue
        print(f"\n=== {agent.agent_id} ({agent.name}) working ===")
        for path, line_no, text in rows:
            print(f"{path}:{line_no}  {text}")
            found += 1
    if found == 0:
        print("no matches")


def cmd_search_structured(args: argparse.Namespace) -> None:
    found = 0
    for agent in iter_target_agents(args.agent):
        rows = search_structured(agent, args.query, args.limit)
        if not rows:
            continue
        print(f"\n=== {agent.agent_id} ({agent.name}) structured ===")
        for kind, title, detail in rows:
            print(f"[{kind}] {title}  {detail}")
            found += 1
    if found == 0:
        print("no matches")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Three-layer memory pipeline for isolated OpenClaw workspaces")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="initialize working / semantic / structured memory layout")
    p_init.add_argument("--agent", default="all", help="agent id or 'all'")
    p_init.set_defaults(func=cmd_init)

    p_capture = sub.add_parser("capture", help="capture current session state into working memory")
    p_capture.add_argument("--agent", default="all", help="agent id or 'all'")
    p_capture.set_defaults(func=cmd_capture)

    p_extract = sub.add_parser("extract", help="extract working memory into semantic notes and structured db")
    p_extract.add_argument("--agent", default="all", help="agent id or 'all'")
    p_extract.add_argument("--force", action="store_true", help="re-extract even if session hash is unchanged")
    p_extract.set_defaults(func=cmd_extract)

    p_rr = sub.add_parser("review-report", help="build a pending learning review report")
    p_rr.add_argument("--agent", default="all", help="agent id or 'all'")
    p_rr.add_argument("--days", type=int, default=7, help="lookback window for pending items")
    p_rr.add_argument("--limit", type=int, default=50, help="max items in the report")
    p_rr.set_defaults(func=cmd_review_report)

    p_rd = sub.add_parser("review-decide", help="update review queue status for one candidate")
    p_rd.add_argument("--agent", default="all", help="agent id or 'all'")
    p_rd.add_argument("--id", type=int, required=True, help="review queue numeric id")
    p_rd.add_argument(
        "--status",
        required=True,
        choices=["pending", "deferred", "approved", "rejected"],
        help="new review status",
    )
    p_rd.add_argument("--note", default="", help="short reviewer note")
    p_rd.add_argument("--reviewer", default="Owner", help="reviewer name")
    p_rd.set_defaults(func=cmd_review_decide)

    p_ra = sub.add_parser("review-apply", help="materialize approved review items into their target layer")
    p_ra.add_argument("--agent", default="all", help="agent id or 'all'")
    p_ra.set_defaults(func=cmd_review_apply)

    p_sw = sub.add_parser("search-working", help="search working memory")
    p_sw.add_argument("--agent", default="all", help="agent id or 'all'")
    p_sw.add_argument("--query", required=True, help="search query")
    p_sw.add_argument("--limit", type=int, default=20, help="max matches")
    p_sw.set_defaults(func=cmd_search_working)

    p_sem = sub.add_parser("search-semantic", help="search semantic memory")
    p_sem.add_argument("--agent", default="all", help="agent id or 'all'")
    p_sem.add_argument("--query", required=True, help="search query")
    p_sem.add_argument("--limit", type=int, default=20, help="max matches")
    p_sem.set_defaults(func=cmd_search_semantic)

    p_ss = sub.add_parser("search-structured", help="search structured memory")
    p_ss.add_argument("--agent", default="all", help="agent id or 'all'")
    p_ss.add_argument("--query", required=True, help="search query")
    p_ss.add_argument("--limit", type=int, default=20, help="max matches")
    p_ss.set_defaults(func=cmd_search_structured)

    p_s = sub.add_parser("search", help="search all three layers in priority order")
    p_s.add_argument("--agent", default="all", help="agent id or 'all'")
    p_s.add_argument("--query", required=True, help="search query")
    p_s.add_argument("--limit", type=int, default=5, help="max matches per layer")
    p_s.set_defaults(func=cmd_search)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if should_skip_legacy_cron(args):
        print(f"skip legacy cron command={args.cmd}; launchd scheduler is active")
        return
    args.func(args)


if __name__ == "__main__":
    main()
