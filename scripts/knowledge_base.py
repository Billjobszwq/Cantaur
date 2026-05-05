#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Iterable

ROOT = Path.home() / ".qyclaw"
WORKSPACE_ROOT = ROOT / "workspace"
KNOWLEDGE_ROOT = WORKSPACE_ROOT / "knowledge"
MEMORY_ROOT = WORKSPACE_ROOT / "memory"
FUSION_OUTPUT_ROOT = WORKSPACE_ROOT / "integration" / "qy_code" / "memory-fusion" / "output"
BUS_SCRIPT = WORKSPACE_ROOT / "integration" / "qy_code" / "bus" / "scripts" / "bus_cli.py"
BUS_MESSAGE_CACHE = KNOWLEDGE_ROOT / "compiler" / "bus-messages"
SUBSCRIPTION_CONFIG = KNOWLEDGE_ROOT / "schemas" / "agent-knowledge-subscriptions.v1.json"

PAGE_DIRS = [
    "overview",
    "entities",
    "concepts",
    "projects",
    "comparisons",
    "sources",
    "contradictions",
    "open-questions",
    "schemas",
    "compiler",
]
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
REVIEW_QUEUE_INDEX_RE = re.compile(r"-(\d{3})-")


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat()


def safe_slug(text: str, max_len: int = 64) -> str:
    cleaned = []
    for ch in text.strip().lower():
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            cleaned.append(ch)
        else:
            cleaned.append("-")
    slug = "".join(cleaned)
    while "--" in slug:
        slug = slug.replace("--", "-")
    slug = slug.strip("-")
    return (slug[:max_len].strip("-") or "item")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def append_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + content, encoding="utf-8")


def cache_bus_message(payload: dict[str, object]) -> Path:
    BUS_MESSAGE_CACHE.mkdir(parents=True, exist_ok=True)
    message_id = str(payload.get("message_id", f"msg-{safe_slug(now_iso())}"))
    path = BUS_MESSAGE_CACHE / f"{message_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_subscription_config() -> dict:
    if SUBSCRIPTION_CONFIG.exists():
        return json.loads(SUBSCRIPTION_CONFIG.read_text(encoding="utf-8"))
    return {
        "default_subscribers": {},
        "candidate_type_routes": {},
        "keyword_routes": {},
    }


def ensure_layout() -> None:
    KNOWLEDGE_ROOT.mkdir(parents=True, exist_ok=True)
    for name in PAGE_DIRS:
        (KNOWLEDGE_ROOT / name).mkdir(parents=True, exist_ok=True)
    if not (KNOWLEDGE_ROOT / "index.md").exists():
        write_text(KNOWLEDGE_ROOT / "index.md", "# Knowledge Index\n")
    if not (KNOWLEDGE_ROOT / "log.md").exists():
        write_text(KNOWLEDGE_ROOT / "log.md", "# Knowledge Log\n")


def knowledge_status() -> dict:
    ensure_layout()
    source_pages = list((KNOWLEDGE_ROOT / "sources").rglob("*.md"))
    project_pages = list((KNOWLEDGE_ROOT / "projects").rglob("*.md"))
    concept_pages = list((KNOWLEDGE_ROOT / "concepts").rglob("*.md"))
    entity_pages = list((KNOWLEDGE_ROOT / "entities").rglob("*.md"))
    comparison_pages = list((KNOWLEDGE_ROOT / "comparisons").rglob("*.md"))
    review_queue = list((MEMORY_ROOT / "20-semantic" / "review-queue").rglob("*.md")) if (MEMORY_ROOT / "20-semantic" / "review-queue").exists() else []
    return {
        "knowledge_root": str(KNOWLEDGE_ROOT),
        "memory_root": str(MEMORY_ROOT),
        "source_pages": len(source_pages),
        "project_pages": len(project_pages),
        "concept_pages": len(concept_pages),
        "entity_pages": len(entity_pages),
        "comparison_pages": len(comparison_pages),
        "review_queue_docs": len(review_queue),
        "has_index": (KNOWLEDGE_ROOT / "index.md").exists(),
        "has_log": (KNOWLEDGE_ROOT / "log.md").exists(),
    }


def frontmatter(page_type: str, raw_sources: Iterable[str]) -> str:
    sources = list(raw_sources)
    return "\n".join(
        [
            "---",
            f"type: {page_type}",
            "status: active",
            f"updated_at: {now_iso()}",
            f"source_count: {len(sources)}",
            "confidence: 0.72",
            "related_pages: []",
            f"raw_sources: {json.dumps(sources, ensure_ascii=False)}",
            "---",
            "",
        ]
    )


def register_index(title: str, path: Path, summary: str) -> None:
    line = f"- [{title}]({path}) - {summary.strip()}"
    index_path = KNOWLEDGE_ROOT / "index.md"
    existing = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if line not in existing:
        append_text(index_path, line + "\n")


def append_log(event: str, title: str, note: str) -> None:
    append_text(
        KNOWLEDGE_ROOT / "log.md",
        f"\n## [{dt.date.today().isoformat()}] {event} | {title}\n- {note}\n",
    )


def parse_frontmatter_fields(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def update_frontmatter(path: Path, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"missing frontmatter: {path}")
    output: list[str] = ["---"]
    idx = 1
    seen: set[str] = set()
    while idx < len(lines):
        line = lines[idx]
        idx += 1
        if line.strip() == "---":
            break
        if ":" not in line:
            output.append(line)
            continue
        key, _ = line.split(":", 1)
        key = key.strip()
        if key in updates:
            output.append(f"{key}: {updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}: {value}")
    output.append("---")
    output.extend(lines[idx:])
    path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")


def markdown_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    capture = False
    collected: list[str] = []
    marker = f"## {heading}"
    for line in lines:
        if line.strip() == marker:
            capture = True
            continue
        if capture and line.startswith("## "):
            break
        if capture:
            collected.append(line)
    return "\n".join(collected).strip()


def upsert_markdown_section(path: Path, heading: str, body_lines: list[str]) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    marker = f"## {heading}"
    start = None
    end = None
    for idx, line in enumerate(lines):
        if line.strip() == marker:
            start = idx
            end = len(lines)
            for j in range(idx + 1, len(lines)):
                if lines[j].startswith("## "):
                    end = j
                    break
            break
    replacement = [marker, ""] + body_lines + [""]
    if start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(replacement)
    else:
        lines = lines[:start] + replacement + lines[end:]
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def runtime_context_for_task_dir(task_dir: Path, summary_payload: dict) -> dict | None:
    parts = list(task_dir.parts)
    if "runtime" not in parts:
        return None
    idx = parts.index("runtime")
    if idx + 1 >= len(parts):
        return None
    runtime_name = parts[idx + 1]
    runtime_root = Path(*parts[: idx + 2])
    bus_db = runtime_root / "bus.db"
    bus_runtime = runtime_root / "bus-runtime"
    if not bus_db.exists() or not bus_runtime.exists():
        return None
    task_info = summary_payload.get("task_bundle", {}).get("task", {})
    summary_info = summary_payload.get("summary", {})
    return {
        "runtime_root": runtime_root,
        "bus_db": bus_db,
        "bus_runtime": bus_runtime,
        "task_id": task_info.get("task_id") or summary_info.get("task_id") or task_dir.name,
        "trace_id": task_info.get("trace_id") or f"trace-{safe_slug(task_dir.name)}",
        "parent_task_id": task_info.get("parent_task_id"),
    }


def candidate_target(candidate_type: str) -> str:
    mapping = {
        "project_update": "20-semantic/project-updates",
        "decision_candidate": "20-semantic/decisions",
        "decision": "20-semantic/decisions",
        "lesson": "20-semantic/lessons",
        "preference": "20-semantic/preferences",
        "rule": "30-procedures",
        "fact": "40-structured",
    }
    return mapping.get(candidate_type, "20-semantic/project-updates")


def candidate_scope(candidate_type: str) -> str:
    mapping = {
        "project_update": "project",
        "decision_candidate": "project",
        "decision": "project",
        "lesson": "concept",
        "preference": "overview",
        "rule": "concept",
        "fact": "source",
    }
    return mapping.get(candidate_type, "project")


def normalized_candidate_type(candidate_type: str) -> str:
    return "decision" if candidate_type == "decision_candidate" else candidate_type


def blackboard_put_local(conn: sqlite3.Connection, bus_runtime: Path, task_id: str, entry_key: str, entry_value: dict | list, updated_by: str, updated_at: str, bus) -> None:
    conn.execute(
        """
        INSERT INTO blackboard_entries (task_id, entry_key, entry_value_json, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(task_id, entry_key) DO UPDATE SET
          entry_value_json = excluded.entry_value_json,
          updated_by = excluded.updated_by,
          updated_at = excluded.updated_at
        """,
        (
            task_id,
            entry_key,
            json.dumps(entry_value, ensure_ascii=False),
            updated_by,
            updated_at,
        ),
    )
    payload = {
        "task_id": task_id,
        "entry_key": entry_key,
        "entry_value": entry_value,
        "updated_by": updated_by,
        "updated_at": updated_at,
    }
    bus.audit(
        conn,
        bus_runtime,
        "blackboard_put",
        updated_by,
        "blackboard updated",
        payload,
        task_id=task_id,
        created_at=updated_at,
    )
    bb_dir = bus_runtime / "blackboard" / task_id
    bb_dir.mkdir(parents=True, exist_ok=True)
    (bb_dir / f"{entry_key}.json").write_text(json.dumps(entry_value, ensure_ascii=False, indent=2), encoding="utf-8")


def enqueue_protocol_message(conn: sqlite3.Connection, bus, bus_runtime: Path, payload: dict[str, object]) -> None:
    bus.ensure_runtime_dirs(bus_runtime)
    bus.validate_message(payload)
    existing = conn.execute(
        "SELECT message_id, queue_status, acked_at FROM bus_messages WHERE message_id = ?",
        (str(payload["message_id"]),),
    ).fetchone()
    if existing is not None:
        bus.audit(
            conn,
            bus_runtime,
            "enqueue_skipped_duplicate",
            str(payload["from"]),
            f"duplicate message skipped for {payload['to']}",
            payload,
            message_id=str(payload["message_id"]),
            task_id=str(payload["task_id"]),
            created_at=str(payload["created_at"]),
        )
        return
    message_path = cache_bus_message(payload)
    inbox_dir = bus_runtime / "inbox" / str(payload["to"])
    outbox_dir = bus_runtime / "outbox" / str(payload["from"])
    inbox_dir.mkdir(parents=True, exist_ok=True)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_target = inbox_dir / f"{payload['message_id']}.json"
    outbox_target = outbox_dir / f"{payload['message_id']}.json"
    shutil.copy2(message_path, inbox_target)
    shutil.copy2(message_path, outbox_target)
    conn.execute(
        """
        INSERT INTO bus_messages (
          message_id, task_id, trace_id, parent_task_id, from_agent, to_agent,
          message_type, body_json, queue_status, retry_count, available_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["message_id"],
            payload["task_id"],
            payload["trace_id"],
            payload["parent_task_id"],
            payload["from"],
            payload["to"],
            payload["message_type"],
            json.dumps(payload["body"], ensure_ascii=False),
            "queued",
            0,
            payload["created_at"],
            payload["created_at"],
        ),
    )
    bus.audit(
        conn,
        bus_runtime,
        "enqueue",
        str(payload["from"]),
        f"queued for {payload['to']}",
        payload,
        message_id=str(payload["message_id"]),
        task_id=str(payload["task_id"]),
        created_at=str(payload["created_at"]),
    )


def routing_text_for_payload(payload: dict[str, object]) -> str:
    body = payload.get("body", {})
    if not isinstance(body, dict):
        body = {}
    chunks = [
        str(payload.get("message_type", "")),
        str(body.get("title", "")),
        str(body.get("summary", "")),
        str(body.get("evidence", "")),
        str(body.get("note", "")),
        str(body.get("page_path", "")),
        str(body.get("target_path", "")),
        str(body.get("queue_path", "")),
        str(body.get("promotion_target", "")),
        str(body.get("adopted_target", "")),
        str(body.get("candidate_type", "")),
    ]
    return "\n".join(chunk for chunk in chunks if chunk)


def subscribed_agents_for_payload(payload: dict[str, object]) -> list[str]:
    config = load_subscription_config()
    body = payload.get("body", {})
    if not isinstance(body, dict):
        body = {}
    message_type = str(payload.get("message_type", ""))
    subscribers: list[str] = list(config.get("default_subscribers", {}).get(message_type, []))
    candidate_type = str(body.get("candidate_type", "") or "")
    if candidate_type:
        for agent in config.get("candidate_type_routes", {}).get(candidate_type, []):
            if agent not in subscribers:
                subscribers.append(agent)
    text = routing_text_for_payload(payload).lower()
    for agent, keywords in config.get("keyword_routes", {}).items():
        for keyword in keywords:
            if str(keyword).lower() in text:
                if agent not in subscribers:
                    subscribers.append(agent)
                break
    if "main" not in subscribers:
        subscribers.insert(0, "main")
    return subscribers


def clone_payload_for_target(payload: dict[str, object], target: str) -> dict[str, object]:
    cloned = json.loads(json.dumps(payload, ensure_ascii=False))
    original_message_id = str(payload["message_id"])
    if target == str(payload["to"]):
        return cloned
    cloned["to"] = target
    cloned["message_id"] = f"{original_message_id}-to-{safe_slug(target, 24)}"
    return cloned


def enqueue_protocol_message_to_subscribers(conn: sqlite3.Connection, bus, bus_runtime: Path, payload: dict[str, object]) -> list[str]:
    subscribers = subscribed_agents_for_payload(payload)
    message_ids: list[str] = []
    for target in subscribers:
        routed = clone_payload_for_target(payload, target)
        enqueue_protocol_message(conn, bus, bus_runtime, routed)
        message_ids.append(str(routed["message_id"]))
    return message_ids


def emit_knowledge_events(task_dir: Path, page_path: Path, project: str | None, summary_payload: dict, semantic_candidates: list[dict]) -> list[str]:
    context = runtime_context_for_task_dir(task_dir, summary_payload)
    if context is None:
        return []
    bus = load_module(BUS_SCRIPT, "knowledge_bus_cli")
    emitted: list[str] = []
    task_id = str(context["task_id"])
    trace_id = str(context["trace_id"])
    parent_task_id = context["parent_task_id"]
    bus_runtime = Path(context["bus_runtime"])
    bus_db = Path(context["bus_db"])
    task_title = summary_payload.get("summary", {}).get("title") or task_dir.name

    page_payload = {
        "protocol": "qyclaw-a2a/v1",
        "message_type": "KNOWLEDGE_PAGE_UPDATED",
        "message_id": f"{task_id}-knowledge-page-updated",
        "task_id": task_id,
        "trace_id": trace_id,
        "parent_task_id": parent_task_id,
        "from": "knowledge-compiler",
        "to": "main",
        "created_at": now_iso(),
        "body": {
            "page_type": "project" if project else "source",
            "page_path": str(page_path),
            "operation": "update",
            "summary": f"compiled knowledge page for {task_title}",
            "source_refs": [str(task_dir)],
            "related_pages": [str(page_path)],
            "compiler": "knowledge_base.py",
        },
    }
    with connect(bus_db) as conn:
        bus.audit(
            conn,
            bus_runtime,
            "knowledge_page_updated",
            "knowledge-compiler",
            "knowledge page updated",
            page_payload,
            message_id=page_payload["message_id"],
            task_id=task_id,
            created_at=page_payload["created_at"],
        )
        emitted.extend(enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, page_payload))

        candidate_summaries: list[dict] = []
        for idx, candidate in enumerate(semantic_candidates, start=1):
            candidate_type = str(candidate.get("type", "") or "project_update")
            normalized_type = normalized_candidate_type(candidate_type)
            review_queue_ref = review_queue_output_path(task_id, idx, str(candidate.get("title", f"{task_title} candidate {idx}")))
            candidate_payload = {
                "protocol": "qyclaw-a2a/v1",
                "message_type": "KNOWLEDGE_CANDIDATE_CREATED",
                "message_id": f"{task_id}-knowledge-candidate-{idx:03d}",
                "task_id": task_id,
                "trace_id": trace_id,
                "parent_task_id": parent_task_id,
                "from": "knowledge-compiler",
                "to": "main",
                "created_at": now_iso(),
                "body": {
                    "knowledge_scope": candidate_scope(candidate_type),
                    "candidate_type": normalized_type,
                    "title": candidate.get("title", f"{task_title} candidate {idx}"),
                    "summary": candidate.get("summary", ""),
                    "evidence": f"compiled from {task_dir}",
                    "proposed_target": candidate_target(candidate_type),
                    "source_refs": [str(task_dir), str(page_path)],
                    "related_pages": [str(page_path)],
                    "review_queue_ref": str(review_queue_ref),
                },
            }
            bus.audit(
                conn,
                bus_runtime,
                "knowledge_candidate_created",
                "knowledge-compiler",
                "knowledge candidate created",
                candidate_payload,
                message_id=candidate_payload["message_id"],
                task_id=task_id,
                created_at=candidate_payload["created_at"],
            )
            emitted.extend(enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, candidate_payload))
            candidate_summaries.append(
                {
                    "message_id": candidate_payload["message_id"],
                    "candidate_type": normalized_type,
                    "title": candidate_payload["body"]["title"],
                    "summary": candidate_payload["body"]["summary"],
                    "proposed_target": candidate_payload["body"]["proposed_target"],
                    "review_queue_ref": candidate_payload["body"]["review_queue_ref"],
                }
            )

        blackboard_put_local(
            conn,
            bus_runtime,
            task_id,
            "knowledge.page",
            {
                "message_id": page_payload["message_id"],
                "page_path": str(page_path),
                "page_type": page_payload["body"]["page_type"],
                "summary": page_payload["body"]["summary"],
                "project": project,
                "source_task_dir": str(task_dir),
            },
            "knowledge-compiler",
            page_payload["created_at"],
            bus,
        )
        blackboard_put_local(
            conn,
            bus_runtime,
            task_id,
            "knowledge.candidates",
            {
                "count": len(candidate_summaries),
                "items": candidate_summaries,
            },
            "knowledge-compiler",
            now_iso(),
            bus,
        )
    return emitted


def candidate_markdown(title: str, summary: str, source_task_id: str, adopted_by: str, candidate_type: str, target: str, source_ref: str) -> str:
    return "\n".join(
        [
            "---",
            f"type: {candidate_type}",
            "status: adopted",
            f"adopted_at: {now_iso()}",
            f"adopted_by: {adopted_by}",
            f"source_task_id: {source_task_id}",
            f"target: {target}",
            "---",
            "",
            f"# {title}",
            "",
            "## Summary",
            "",
            summary or "(empty)",
            "",
            "## Source",
            "",
            f"- source_task_id: `{source_task_id}`",
            f"- source_ref: `{source_ref}`",
            "",
        ]
    )


def structured_fact_payload(title: str, summary: str, source_task_id: str, adopted_by: str, candidate_type: str, target: str, source_ref: str) -> dict[str, object]:
    return {
        "type": candidate_type,
        "status": "adopted",
        "adopted_at": now_iso(),
        "adopted_by": adopted_by,
        "source_task_id": source_task_id,
        "target": target,
        "title": title,
        "summary": summary or "(empty)",
        "source_ref": source_ref,
    }


def review_queue_output_path(source_task_id: str, candidate_index: int, title: str) -> Path:
    month = dt.date.today().strftime("%Y-%m")
    slug = safe_slug(title)
    return MEMORY_ROOT / "20-semantic" / "review-queue" / month / f"{safe_slug(source_task_id, 48)}-{candidate_index:03d}-{slug}.md"


def review_queue_markdown(title: str, summary: str, source_task_id: str, candidate_type: str, target: str, source_ref: str, related_page: str) -> str:
    return "\n".join(
        [
            "---",
            f"type: {candidate_type}",
            "status: pending_review",
            f"queued_at: {now_iso()}",
            f"source_task_id: {source_task_id}",
            f"proposed_target: {target}",
            "---",
            "",
            f"# {title}",
            "",
            "## Summary",
            "",
            summary or "(empty)",
            "",
            "## Proposed Target",
            "",
            f"- target: `{target}`",
            "",
            "## Sources",
            "",
            f"- source_task_id: `{source_task_id}`",
            f"- source_ref: `{source_ref}`",
            f"- related_page: `{related_page}`",
            "",
            "## Review Note",
            "",
            "- 等待人工确认是否提升到长期蒸馏层。",
            "",
        ]
    )


def convergence_candidate_type(row: dict) -> str:
    text = f"{row.get('task_id', '')}\n{row.get('reason', '')}".lower()
    if "review_decision_candidate" in text:
        return "decision"
    if "review_rule_candidate" in text:
        return "rule"
    if "verify_fact_candidate" in text:
        return "fact"
    if "refine_lesson_candidate" in text:
        return "lesson"
    if "align_preference_candidate" in text:
        return "preference"
    return "decision"


def convergence_candidate_title(row: dict, candidate_type: str) -> str:
    label = {
        "decision": "decision convergence follow-up",
        "rule": "rule convergence follow-up",
        "fact": "fact convergence follow-up",
        "lesson": "lesson convergence follow-up",
        "preference": "preference convergence follow-up",
    }.get(candidate_type, "convergence follow-up")
    return f"{row.get('task_id', '(unknown task)')} {label}"


def convergence_candidate_summary(row: dict) -> str:
    lines = [str(row.get("reason", "")).strip() or "(no reason)"]
    steps = row.get("suggested_human_steps", [])
    if isinstance(steps, list):
        for step in steps[:3]:
            if step:
                lines.append(f"- {step}")
    return "\n".join(lines).strip()


def write_promoted_artifact(path: Path, target: str, title: str, summary: str, source_task_id: str, adopted_by: str, candidate_type: str, source_ref: str) -> None:
    if target == "40-structured":
        payload = structured_fact_payload(title, summary, source_task_id, adopted_by, candidate_type, target, source_ref)
        write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return
    content = candidate_markdown(title, summary, source_task_id, adopted_by, candidate_type, target, source_ref)
    write_text(path, content + "\n")


def ensure_review_queue_docs(task_id: str, task_dir: Path, page_path: Path, semantic_candidates: list[dict]) -> list[str]:
    created: list[str] = []
    for idx, candidate in enumerate(semantic_candidates, start=1):
        raw_type = str(candidate.get("type", "") or "project_update")
        normalized_type = normalized_candidate_type(raw_type)
        title = str(candidate.get("title", f"{task_id} candidate {idx}"))
        output = review_queue_output_path(task_id, idx, title)
        content = review_queue_markdown(
            title,
            str(candidate.get("summary", "")),
            str(candidate.get("source_task_id") or task_id),
            normalized_type,
            candidate_target(raw_type),
            str(task_dir),
            str(page_path),
        )
        write_text(output, content + "\n")
        created.append(str(output))
    return created


def collect_review_queue_docs(month_key: str, limit: int) -> list[dict]:
    queue_dir = MEMORY_ROOT / "20-semantic" / "review-queue" / month_key
    docs = sorted(queue_dir.glob("*.md"))
    items: list[dict] = []
    for idx, path in enumerate(docs[:limit], start=1):
        text = path.read_text(encoding="utf-8", errors="ignore")
        meta = parse_frontmatter_fields(text)
        items.append(
            {
                "id": f"RQ-{idx:03d}",
                "path": str(path),
                "path_obj": path,
                "title": markdown_heading(text) or path.stem,
                "candidate_type": meta.get("type", "unknown"),
                "status": meta.get("status", "pending_review"),
                "proposed_target": meta.get("proposed_target", ""),
                "source_task_id": meta.get("source_task_id", ""),
                "source_ref": next((line.split("`")[1] for line in markdown_section(text, "Sources").splitlines() if line.strip().startswith("- source_ref: `") and "`" in line), ""),
                "summary": markdown_section(text, "Summary") or "(empty)",
            }
        )
    return items


def bootstrap_review_queue_from_convergence(runtime_name: str, month_key: str, limit: int, review_limit: int) -> dict:
    rows = collect_convergence_summaries(runtime_name, limit)
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        if matched_review_docs_for_convergence(row, month_key, review_limit):
            skipped.append({"task_id": str(row.get("task_id", "")), "reason": "already_matched"})
            continue
        candidate_type = convergence_candidate_type(row)
        title = convergence_candidate_title(row, candidate_type)
        queue_path = review_queue_output_path(str(row.get("task_id", "")), 1, title)
        source_ref = str(row.get("_path", "") or row.get("page_path", "") or row.get("task_id", ""))
        related_page = str(row.get("page_path", "") or row.get("_path", ""))
        content = review_queue_markdown(
            title,
            convergence_candidate_summary(row),
            str(row.get("task_id", "")),
            candidate_type,
            candidate_target(candidate_type),
            source_ref,
            related_page,
        )
        write_text(queue_path, content + "\n")
        update_frontmatter(
            queue_path,
            {
                "status": "pending_review",
                "bootstrap_source": "convergence_workbench",
                "runtime": runtime_name,
            },
        )
        created.append(
            {
                "task_id": str(row.get("task_id", "")),
                "queue_path": str(queue_path),
                "candidate_type": candidate_type,
            }
        )
    append_log("convergence-bootstrap", runtime_name, f"created {len(created)} bootstrap review item(s) for `{month_key}`")
    return {
        "runtime": runtime_name,
        "month": month_key,
        "created_count": len(created),
        "created": created,
        "skipped": skipped,
    }


def build_review_report(month_key: str | None, limit: int) -> Path:
    ensure_layout()
    month = month_key or dt.date.today().strftime("%Y-%m")
    rows = collect_review_queue_docs(month, limit)
    report_dir = MEMORY_ROOT / "20-semantic" / "review-reports" / month
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{dt.date.today().isoformat()}-learning-review.md"
    lines = [
        "# Learning Review Report",
        "",
        "- agent: `main` / 大总管小云",
        f"- window: `{month}`",
        f"- generated_at: {now_iso()}",
        f"- pending_count: `{len(rows)}`",
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
    if not rows:
        lines.extend(["(none)", ""])
    for row in rows:
        lines.extend(
            [
                f"### {row['id']} · {row['title']}",
                "",
                f"- type: `{row['candidate_type']}`",
                f"- target: `{row['proposed_target']}`",
                f"- status: `{row['status']}`",
                f"- source_task_id: `{row['source_task_id']}`",
                f"- candidate_ref: `{row['path']}`",
                f"- summary: {row['summary']}",
                "",
            ]
        )
    lines.extend(
        [
            "## How To Decide",
            "",
            "示例回复：",
            "- `采纳 RQ-001`",
            "- `观察 RQ-002`",
            "- `排除 RQ-003，理由：不适用于当前阶段`",
            "",
        ]
    )
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    append_log("review-report", month, f"compiled review report at `{report_path}` with {len(rows)} pending item(s)")
    return report_path


def collect_convergence_summaries(runtime_name: str, limit: int) -> list[dict]:
    runtime_root = WORKSPACE_ROOT / "integration" / "qy_code" / "runtime" / runtime_name / "knowledge-convergence"
    if not runtime_root.exists():
        return []
    rows: list[dict] = []
    for path in sorted(runtime_root.rglob("*.json"), reverse=True):
        if len(rows) >= limit:
            break
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        payload["_path"] = str(path)
        rows.append(payload)
    rows.sort(key=lambda item: str(item.get("generated_at", "")), reverse=True)
    return rows[:limit]


def recommend_convergence_decision(row: dict) -> tuple[str, str]:
    task_id = str(row.get("task_id", ""))
    reason = str(row.get("reason", ""))
    text = f"{task_id}\n{reason}".lower()
    if "verify_fact_candidate" in text:
        return ("adopt", "事实类候选已经过补证链路，优先建议人工复核后一轮采纳。")
    if "review_rule_candidate" in text:
        return ("observe", "规则/边界类候选通常需要法务与运营共同复核，默认先观察收口更稳。")
    if "review_decision_candidate" in text:
        return ("observe", "决策类候选更适合先由 main 与 ops 收口，再决定是否固化。")
    if "align_preference_candidate" in text:
        return ("observe", "偏好/表达类候选建议先观察一轮，确认口径稳定后再采纳。")
    if "refine_lesson_candidate" in text:
        return ("observe", "经验教训类候选建议继续保留观察，避免把单次样本误写成长程规则。")
    return ("observe", "默认建议先观察，待更多证据后再决定是否采纳或排除。")


def suggested_review_command(runtime_name: str, decision: str) -> str:
    month = dt.date.today().strftime("%Y-%m")
    return (
        "python3 ${QYCLAW_HOME}/workspace/scripts/knowledge_base.py "
        f"review-decide --month {month} --id <RQ-ID> --decision {decision} --reviewer main --note \"依据 convergence-report 处理\""
    )


def suggested_review_command_for_id(month_key: str, review_id: str, decision: str, note: str | None = None) -> str:
    review_note = note or "依据 convergence-workbench 处理"
    return (
        "python3 ${QYCLAW_HOME}/workspace/scripts/knowledge_base.py "
        f"review-decide --month {month_key} --id {review_id} --decision {decision} "
        f"--reviewer main --note \"{review_note}\""
    )


def suggested_review_batch_command(month_key: str, review_ids: list[str], decision: str, note: str | None = None) -> str:
    review_note = note or "依据 convergence-workbench 批量处理"
    args = " ".join(f"--id {review_id}" for review_id in review_ids)
    return (
        "python3 ${QYCLAW_HOME}/workspace/scripts/knowledge_base.py "
        f"review-decide-batch --month {month_key} {args} --decision {decision} "
        f"--reviewer main --note \"{review_note}\" --report-limit 200"
    )


def review_doc_matches_convergence(row: dict, doc: dict) -> bool:
    task_id = str(row.get("task_id", "")).strip()
    if not task_id:
        return False
    if doc.get("source_task_id") == task_id:
        return True
    path = str(doc.get("path", ""))
    if task_id in path:
        return True
    summary = str(doc.get("summary", ""))
    title = str(doc.get("title", ""))
    for fragment in [task_id, task_id[:48], safe_slug(task_id, 48)]:
        if fragment and (fragment in path or fragment in summary or fragment in title):
            return True
    page_path = str(row.get("page_path", "") or "")
    if page_path and page_path in str(doc.get("source_ref", "")):
        return True
    return False


def matched_review_docs_for_convergence(row: dict, month_key: str, review_limit: int = 10000) -> list[dict]:
    docs = collect_review_queue_docs(month_key, review_limit)
    return [doc for doc in docs if review_doc_matches_convergence(row, doc)]


def build_convergence_workbench(runtime_name: str, month_key: str, limit: int, review_limit: int) -> Path:
    ensure_layout()
    rows = collect_convergence_summaries(runtime_name, limit)
    report_dir = KNOWLEDGE_ROOT / "overview" / "convergence-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    workbench_path = report_dir / f"{dt.date.today().isoformat()}-{safe_slug(runtime_name, 24)}-convergence-workbench.md"
    decisions: dict[str, list[str]] = {
        "adopt": [],
        "observe": [],
        "reject": [],
    }
    matched_count = 0
    unmatched_count = 0
    lines = [
        "# Knowledge Convergence Workbench",
        "",
        f"- runtime: `{runtime_name}`",
        f"- month: `{month_key}`",
        f"- generated_at: {now_iso()}",
        f"- item_count: `{len(rows)}`",
        "",
        "## Purpose",
        "",
        "把 `human_review_only` 收敛项直接映射到可执行的 review 决策面，减少人工在 runtime / review-queue 之间来回查找。",
        "",
        "## Items",
        "",
    ]
    if not rows:
        lines.extend(["(none)", ""])
    for row in rows:
        recommended_decision, recommendation_reason = recommend_convergence_decision(row)
        matched_docs = matched_review_docs_for_convergence(row, month_key, review_limit)
        matched_ids = [str(doc["id"]) for doc in matched_docs]
        if matched_ids:
            matched_count += 1
            decisions[recommended_decision].extend(matched_ids)
        else:
            unmatched_count += 1
        lines.extend(
            [
                f"### {row.get('task_id', '(unknown task)')}",
                "",
                f"- recommended_decision: `{recommended_decision}`",
                f"- recommendation_reason: {recommendation_reason}",
                f"- summary_ref: `{row.get('_path', '')}`",
                f"- page_path: `{row.get('page_path', '')}`" if row.get("page_path") else "- page_path: `(none)`",
                f"- matched_review_ids: `{', '.join(matched_ids)}`" if matched_ids else "- matched_review_ids: `(none)`",
                "",
            ]
        )
        if matched_docs:
            lines.extend(["#### Matched Review Queue Items", ""])
            for doc in matched_docs:
                lines.extend(
                    [
                        f"- `{doc['id']}` · `{doc['candidate_type']}` · `{doc['status']}`",
                        f"  - title: {doc['title']}",
                        f"  - candidate_ref: `{doc['path']}`",
                    ]
                )
            lines.append("")
            lines.extend(["#### Ready Commands", ""])
            for review_id in matched_ids:
                lines.append(f"- `{suggested_review_command_for_id(month_key, review_id, recommended_decision)}`")
            lines.append("")
        else:
            lines.extend(
                [
                    "#### Manual Follow-up",
                    "",
                    "- 当前没有自动匹配到对应 `review-queue` 项。",
                    "- 先查看 `summary_ref` / `page_path`，必要时手动补建 `review-queue`，再执行 review-decide。",
                    f"- 参考命令：`{suggested_review_command(runtime_name, recommended_decision)}`",
                    "",
                ]
            )
    for key in decisions:
        deduped: list[str] = []
        seen: set[str] = set()
        for review_id in decisions[key]:
            if review_id not in seen:
                deduped.append(review_id)
                seen.add(review_id)
        decisions[key] = deduped
    lines.extend(
        [
            "## Batch Commands",
            "",
            f"- matched_items: `{matched_count}`",
            f"- unmatched_items: `{unmatched_count}`",
            "",
        ]
    )
    for decision, ids in decisions.items():
        if not ids:
            continue
        lines.extend(
            [
                f"### {decision}",
                "",
                f"- review_ids: `{', '.join(ids)}`",
                f"- batch_command: `{suggested_review_batch_command(month_key, ids, decision)}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Operator Flow",
            "",
            "1. 先看每条 item 的 `matched_review_ids`，确认系统是否已经找到对应待审候选。",
            "2. 对能自动匹配的项，优先使用 `Ready Commands` 或 `Batch Commands` 处理。",
            "3. 对无法匹配的项，先补 review-queue，再做人工采纳/观察/排除。",
            "",
        ]
    )
    write_text(workbench_path, "\n".join(lines).rstrip() + "\n")
    register_index(f"Convergence Workbench {runtime_name}", workbench_path, "actionable human convergence workbench")
    append_log("convergence-workbench", runtime_name, f"compiled convergence workbench at `{workbench_path}` with {len(rows)} item(s)")
    return workbench_path


def build_convergence_report(runtime_name: str, limit: int) -> Path:
    ensure_layout()
    rows = collect_convergence_summaries(runtime_name, limit)
    report_dir = KNOWLEDGE_ROOT / "overview" / "convergence-reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{dt.date.today().isoformat()}-{safe_slug(runtime_name, 24)}-convergence-review.md"
    lines = [
        "# Knowledge Convergence Review",
        "",
        f"- runtime: `{runtime_name}`",
        f"- generated_at: {now_iso()}",
        f"- item_count: `{len(rows)}`",
        "",
        "## Purpose",
        "",
        "把已经进入 `human_review_only` 的知识循环收口项汇总成一份人工决策面，避免逐个 task 翻 runtime 目录。",
        "",
        "## Items",
        "",
    ]
    if not rows:
        lines.extend(["(none)", ""])
    for row in rows:
        recommended_decision, recommendation_reason = recommend_convergence_decision(row)
        lines.extend(
            [
                f"### {row.get('task_id', '(unknown task)')}",
                "",
                f"- actor: `{row.get('actor', '')}`",
                f"- mode: `{row.get('mode', '')}`",
                f"- generated_at: `{row.get('generated_at', '')}`",
                f"- reason: {row.get('reason', '(none)')}",
                f"- recommended_decision: `{recommended_decision}`",
                f"- recommendation_reason: {recommendation_reason}",
                f"- summary_ref: `{row.get('_path', '')}`",
                f"- page_path: `{row.get('page_path', '')}`" if row.get("page_path") else "- page_path: `(none)`",
                "",
            ]
        )
        blocked = row.get("blocked_actions", [])
        if isinstance(blocked, list) and blocked:
            lines.extend(["#### Blocked Actions", ""])
            for item in blocked:
                if not isinstance(item, dict):
                    continue
                lines.append(f"- `{item.get('action_type', '')}` · {item.get('title', '')} · {item.get('reason', '')}")
            lines.append("")
        steps = row.get("suggested_human_steps", [])
        if isinstance(steps, list) and steps:
            lines.extend(["#### Suggested Human Steps", ""])
            for step in steps:
                lines.append(f"- {step}")
            lines.append("")
        lines.extend(
            [
                "#### Suggested Review Command",
                "",
                f"- `{suggested_review_command(runtime_name, recommended_decision)}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Suggested Operator Flow",
            "",
            "1. 先看 `reason`，判断这条链为什么停止自动扩圈。",
            "2. 再看 `page_path` / `summary_ref`，决定是补证据、采纳、观察还是排除。",
            "3. 如需继续推进，优先用人工方式发起下一轮，不直接放开自动扩圈。",
            "",
        ]
    )
    write_text(report_path, "\n".join(lines).rstrip() + "\n")
    register_index(f"Convergence Review {runtime_name}", report_path, "human convergence dashboard")
    append_log("convergence-report", runtime_name, f"compiled convergence review at `{report_path}` with {len(rows)} item(s)")
    return report_path


def infer_candidate_index(path: Path) -> int:
    match = REVIEW_QUEUE_INDEX_RE.search(path.name)
    if not match:
        raise ValueError(f"cannot infer candidate index from {path.name}")
    return int(match.group(1))


def normalize_review_ids(ids: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in ids:
        for part in item.split(","):
            value = part.strip()
            if value:
                normalized.append(value)
    return normalized


def emit_review_decision(task_dir: Path, review_id: str, decision: str, reviewer: str, note: str, queue_path: Path, promoted_path: str | None) -> str | None:
    summary_payload = {}
    summary_json = task_dir / "summary.json"
    if summary_json.exists():
        summary_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    context = runtime_context_for_task_dir(task_dir, summary_payload)
    if context is None:
        return None
    bus = load_module(BUS_SCRIPT, "knowledge_bus_cli_review")
    bus_runtime = Path(context["bus_runtime"])
    bus_db = Path(context["bus_db"])
    task_id = str(context["task_id"])
    status_after = {
        "adopt": "adopted",
        "observe": "observed",
        "reject": "rejected",
    }[decision]
    note_value = note or "(none)"
    payload = {
        "review_id": review_id,
        "decision": decision,
        "status_after": status_after,
        "reviewer": reviewer,
        "note": note_value,
        "queue_path": str(queue_path),
        "promoted_path": promoted_path or "",
        "created_at": now_iso(),
    }
    with connect(bus_db) as conn:
        bus.audit(
            conn,
            bus_runtime,
            "review_decision_recorded",
            reviewer,
            "review decision recorded",
            payload,
            task_id=task_id,
            created_at=payload["created_at"],
        )
        message_payload = {
            "protocol": "qyclaw-a2a/v1",
            "message_type": "REVIEW_DECISION_RECORDED",
            "message_id": f"{task_id}-review-decision-recorded-{review_id.lower()}",
            "task_id": task_id,
            "trace_id": str(context["trace_id"]),
            "parent_task_id": context["parent_task_id"],
            "from": reviewer,
            "to": "main",
            "created_at": payload["created_at"],
            "body": {
                "review_id": review_id,
                "decision": decision,
                "status_after": status_after,
                "reviewer": reviewer,
                "note": note_value,
                "queue_path": str(queue_path),
                "promoted_path": promoted_path or "",
            },
        }
        enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, message_payload)
        blackboard_put_local(
            conn,
            bus_runtime,
            task_id,
            "knowledge.latest_review_decision",
            payload,
            reviewer,
            payload["created_at"],
            bus,
        )
    return payload["created_at"]


def review_decide(month_key: str, review_id: str, decision: str, reviewer: str, note: str) -> dict:
    rows = collect_review_queue_docs(month_key, 10000)
    selected = next((row for row in rows if row["id"] == review_id), None)
    if selected is None:
        raise ValueError(f"review id not found: {review_id}")
    queue_path: Path = selected["path_obj"]
    status_map = {
        "adopt": "adopted",
        "observe": "observed",
        "reject": "rejected",
    }
    new_status = status_map[decision]
    decision_time = now_iso()
    update_frontmatter(
        queue_path,
        {
            "status": new_status,
            "reviewed_at": decision_time,
            "reviewer": reviewer,
            "decision": decision,
        },
    )
    decision_lines = [
        f"- decision: `{decision}`",
        f"- reviewer: `{reviewer}`",
        f"- reviewed_at: `{decision_time}`",
        f"- note: {note or '(none)'}",
    ]
    upsert_markdown_section(queue_path, "Review Decision", decision_lines)

    promoted = None
    if decision == "adopt":
        source_ref = selected.get("source_ref", "")
        if not source_ref:
            promoted = promote_review_queue_item(selected, reviewer)
        else:
            source_path = Path(source_ref)
            if source_path.is_dir() and (source_path / "semantic-candidates.json").exists():
                promoted = promote_candidate(source_path, infer_candidate_index(queue_path), reviewer)
            else:
                promoted = promote_review_queue_item(selected, reviewer)

    source_ref = selected.get("source_ref", "")
    if source_ref:
        emit_review_decision(Path(source_ref), review_id, decision, reviewer, note, queue_path, promoted["promoted_path"] if promoted else None)

    append_log("review-decide", review_id, f"{decision} by `{reviewer}` for `{queue_path}`")
    return {
        "review_id": review_id,
        "queue_path": str(queue_path),
        "decision": decision,
        "status": new_status,
        "promoted": promoted,
    }


def review_decide_batch(month_key: str, review_ids: list[str], decision: str, reviewer: str, note: str, report_limit: int = 200) -> dict:
    ids = normalize_review_ids(review_ids)
    if not ids:
        raise ValueError("no review ids provided")
    results = [review_decide(month_key, review_id, decision, reviewer, note) for review_id in ids]
    report_path = build_review_report(month_key, report_limit)
    append_log("review-decide-batch", month_key, f"{decision} applied to {len(results)} item(s)")
    return {
        "month": month_key,
        "decision": decision,
        "count": len(results),
        "results": results,
        "report": str(report_path),
    }


def promotion_output_path(target: str, title: str) -> Path:
    month = dt.date.today().strftime("%Y-%m")
    slug = safe_slug(title)
    if target.startswith("20-semantic/"):
        return MEMORY_ROOT / target / month / f"{slug}.md"
    if target == "30-procedures":
        return MEMORY_ROOT / "30-procedures" / month / f"{slug}.md"
    if target == "40-structured":
        return MEMORY_ROOT / "40-structured" / month / f"{slug}.json"
    raise ValueError(f"unsupported promotion target: {target}")


def promote_review_queue_item(selected: dict, adopted_by: str) -> dict:
    candidate_type = normalized_candidate_type(str(selected.get("candidate_type", "") or "decision"))
    target = str(selected.get("proposed_target", "") or candidate_target(candidate_type))
    title = str(selected.get("title", "review candidate"))
    summary = str(selected.get("summary", ""))
    source_task_id = str(selected.get("source_task_id", ""))
    source_ref = str(selected.get("source_ref", ""))
    promoted_path = promotion_output_path(target, title)
    write_promoted_artifact(promoted_path, target, title, summary, source_task_id, adopted_by, candidate_type, source_ref)
    event_type = "PROCEDURE_PROMOTED" if target == "30-procedures" else "MEMORY_CANDIDATE_PROMOTED"
    append_log("promote-candidate", title, f"promoted review queue item into `{promoted_path}`")
    return {
        "promoted_path": str(promoted_path),
        "event_type": event_type,
        "message_id": None,
        "target": target,
        "candidate_type": candidate_type,
    }


def emit_promotion_event(task_dir: Path, promoted_path: Path, message_type: str, title: str, summary: str, target: str, candidate_type: str, adopted_by: str, source_task_id: str) -> str | None:
    summary_payload = {}
    summary_json = task_dir / "summary.json"
    if summary_json.exists():
        summary_payload = json.loads(summary_json.read_text(encoding="utf-8"))
    context = runtime_context_for_task_dir(task_dir, summary_payload)
    if context is None:
        return None
    bus = load_module(BUS_SCRIPT, "knowledge_bus_cli_promote")
    task_id = str(context["task_id"])
    trace_id = str(context["trace_id"])
    parent_task_id = context["parent_task_id"]
    bus_runtime = Path(context["bus_runtime"])
    bus_db = Path(context["bus_db"])
    payload = {
        "protocol": "qyclaw-a2a/v1",
        "message_type": message_type,
        "message_id": f"{task_id}-{safe_slug(message_type)}-{safe_slug(title, 24)}",
        "task_id": task_id,
        "trace_id": trace_id,
        "parent_task_id": parent_task_id,
        "from": "knowledge-compiler",
        "to": "main",
        "created_at": now_iso(),
        "body": {
            "title": title,
            "summary": summary,
            "source_task_id": source_task_id,
            "target_path": str(promoted_path),
            "promotion_target": target,
            "candidate_type": candidate_type,
            "adopted_by": adopted_by,
        },
    }
    with connect(bus_db) as conn:
        bus.audit(
            conn,
            bus_runtime,
            message_type.lower(),
            "knowledge-compiler",
            f"{message_type.lower()} emitted",
            payload,
            message_id=payload["message_id"],
            task_id=task_id,
            created_at=payload["created_at"],
        )
        enqueue_protocol_message_to_subscribers(conn, bus, bus_runtime, payload)
        blackboard_key = "knowledge.latest_memory_promotion" if message_type == "MEMORY_CANDIDATE_PROMOTED" else "knowledge.latest_procedure_promotion"
        blackboard_put_local(
            conn,
            bus_runtime,
            task_id,
            blackboard_key,
            payload["body"],
            "knowledge-compiler",
            payload["created_at"],
            bus,
        )
    return payload["message_id"]


def promote_candidate(task_dir: Path, candidate_index: int, adopted_by: str, force_target: str | None = None) -> dict:
    semantic_json = task_dir / "semantic-candidates.json"
    if not semantic_json.exists():
        raise FileNotFoundError(f"semantic candidates missing: {semantic_json}")
    candidates = json.loads(semantic_json.read_text(encoding="utf-8"))
    if candidate_index < 1 or candidate_index > len(candidates):
        raise IndexError(f"candidate index out of range: {candidate_index}")
    candidate = candidates[candidate_index - 1]
    raw_type = str(candidate.get("type", "") or "project_update")
    candidate_type = normalized_candidate_type(raw_type)
    target = force_target or candidate_target(raw_type)
    if target not in {"30-procedures", "40-structured"} and not target.startswith("20-semantic/"):
        raise ValueError(f"promotion target not yet supported by command: {target}")
    promoted_path = promotion_output_path(target, str(candidate.get("title", f"candidate-{candidate_index:03d}")))
    source_task_id = str(candidate.get("source_task_id") or task_dir.name)
    write_promoted_artifact(
        promoted_path,
        target,
        str(candidate.get("title", f"candidate-{candidate_index:03d}")),
        str(candidate.get("summary", "")),
        source_task_id,
        adopted_by,
        candidate_type,
        str(task_dir),
    )
    event_type = "PROCEDURE_PROMOTED" if target == "30-procedures" else "MEMORY_CANDIDATE_PROMOTED"
    message_id = emit_promotion_event(
        task_dir,
        promoted_path,
        event_type,
        str(candidate.get("title", f"candidate-{candidate_index:03d}")),
        str(candidate.get("summary", "")),
        target,
        candidate_type,
        adopted_by,
        source_task_id,
    )
    append_log("promote-candidate", str(candidate.get("title", f"candidate-{candidate_index:03d}")), f"promoted into `{promoted_path}`")
    return {
        "promoted_path": str(promoted_path),
        "event_type": event_type,
        "message_id": message_id,
        "target": target,
        "candidate_type": candidate_type,
    }


def ingest_source(source: Path, title: str, category: str, project: str | None, copy_raw: bool) -> Path:
    ensure_layout()
    slug = safe_slug(title)
    month_dir = KNOWLEDGE_ROOT / "sources" / dt.date.today().strftime("%Y-%m")
    page_path = month_dir / f"{slug}.md"
    raw_path = source
    if copy_raw:
        raw_dir = KNOWLEDGE_ROOT / "compiler" / "raw-cache" / dt.date.today().strftime("%Y-%m")
        raw_dir.mkdir(parents=True, exist_ok=True)
        copied = raw_dir / source.name
        shutil.copy2(source, copied)
        raw_path = copied
    preview = source.read_text(encoding="utf-8", errors="ignore")[:1200].strip()
    summary = preview.splitlines()[0][:120] if preview else "raw source ingested"
    content = [
        frontmatter("source", [str(raw_path)]),
        f"# {title}",
        "",
        f"- category: `{category}`",
        f"- project: `{project or ''}`",
        f"- ingested_at: `{now_iso()}`",
        f"- raw_source: `{raw_path}`",
        "",
        "## Summary",
        "",
        summary or "(empty)",
        "",
        "## Preview",
        "",
        "```text",
        preview or "(empty)",
        "```",
        "",
    ]
    write_text(page_path, "\n".join(content))
    register_index(title, page_path, f"source / {category}")
    append_log("ingest", title, f"compiled source page at `{page_path}`")
    if project:
        ensure_project_page(project, title, page_path)
    return page_path


def ensure_project_page(project: str, title: str, source_page: Path) -> Path:
    slug = safe_slug(project)
    page = KNOWLEDGE_ROOT / "projects" / f"{slug}.md"
    if not page.exists():
        content = [
            frontmatter("project", []),
            f"# {project}",
            "",
            "## Current Synthesis",
            "",
            "(to be compiled)",
            "",
            "## Related Sources",
            "",
        ]
        write_text(page, "\n".join(content))
        register_index(project, page, "project page")
    line = f"- [{title}]({source_page})"
    existing = page.read_text(encoding="utf-8")
    if line not in existing:
        append_text(page, line + "\n")
    return page


def compile_fusion(task_dir: Path, project: str | None) -> Path:
    ensure_layout()
    summary_md = task_dir / "summary.md"
    summary_json = task_dir / "summary.json"
    semantic_json = task_dir / "semantic-candidates.json"
    title = task_dir.name
    page = KNOWLEDGE_ROOT / "sources" / dt.date.today().strftime("%Y-%m") / f"{safe_slug(task_dir.name)}-fusion.md"
    md_preview = summary_md.read_text(encoding="utf-8", errors="ignore")[:3000] if summary_md.exists() else ""
    json_summary = {}
    semantic_candidates: list[dict] = []
    if summary_json.exists():
        json_summary = json.loads(summary_json.read_text(encoding="utf-8"))
    if semantic_json.exists():
        semantic_candidates = json.loads(semantic_json.read_text(encoding="utf-8"))
    semantic_preview = semantic_json.read_text(encoding="utf-8", errors="ignore")[:2000] if semantic_json.exists() else ""
    content = [
        frontmatter("source", [str(task_dir)]),
        f"# {title}",
        "",
        f"- source_type: `memory-fusion`",
        f"- compiled_at: `{now_iso()}`",
        f"- task_dir: `{task_dir}`",
        "",
        "## Summary Markdown",
        "",
        md_preview or "(missing summary.md)",
        "",
        "## Summary JSON",
        "",
        "```json",
        json.dumps(json_summary, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Semantic Candidates",
        "",
        "```json",
        semantic_preview or "{}",
        "```",
        "",
    ]
    write_text(page, "\n".join(content))
    register_index(title, page, "memory-fusion compiled source")
    append_log("compile-fusion", title, f"compiled memory-fusion package into `{page}`")
    if project:
        ensure_project_page(project, title, page)
    review_queue_docs = ensure_review_queue_docs(task_dir.name, task_dir, page, semantic_candidates)
    emit_knowledge_events(task_dir, page, project, json_summary, semantic_candidates)
    append_log("review-queue", title, f"generated {len(review_queue_docs)} review queue docs")
    return page


def lint_knowledge() -> dict:
    ensure_layout()
    md_files = [p for p in KNOWLEDGE_ROOT.rglob("*.md") if p.name not in {"index.md", "log.md", "README.md", "PAGE-SCHEMA.md"}]
    linked_paths: set[str] = set()
    for page in KNOWLEDGE_ROOT.rglob("*.md"):
        text = page.read_text(encoding="utf-8", errors="ignore")
        for match in MARKDOWN_LINK_RE.findall(text):
            if match.startswith(str(Path.home() / ".qyclaw/workspace/")):
                linked_paths.add(match)
    orphans = [str(p) for p in md_files if str(p) not in linked_paths]
    empty_pages = [str(p) for p in md_files if len(p.read_text(encoding="utf-8").strip()) < 40]
    return {
        "orphans": orphans,
        "empty_pages": empty_pages,
        "page_count": len(md_files),
    }


def collect_markdown_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted([p for p in root.rglob("*.md") if p.is_file()])


def relative_to_workspace(path: Path) -> str:
    try:
        return str(path.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(path)


def backfill_continuity() -> dict:
    ensure_layout()

    instreet_root = KNOWLEDGE_ROOT / "instreet"
    semantic_root = MEMORY_ROOT / "20-semantic"

    instreet_files = collect_markdown_files(instreet_root)
    semantic_files = collect_markdown_files(semantic_root)

    instreet_page = KNOWLEDGE_ROOT / "overview" / "instreet-domain-map.md"
    semantic_page = KNOWLEDGE_ROOT / "overview" / "legacy-memory-semantic-map.md"
    manifest_path = KNOWLEDGE_ROOT / "compiler" / "continuity-manifest.json"

    instreet_lines = [
        frontmatter("overview", [str(instreet_root)]),
        "# InStreet Domain Map",
        "",
        f"- generated_at: `{now_iso()}`",
        f"- root: `{instreet_root}`",
        f"- page_count: `{len(instreet_files)}`",
        "",
        "## Purpose",
        "",
        "把历史 `knowledge/instreet/` 资产纳入统一知识主根索引，避免 V2 构建后成为孤岛。",
        "",
        "## Files",
        "",
    ]
    for path in instreet_files:
        if path.name == "README.md":
            continue
        instreet_lines.append(f"- [{path.stem}](${QYCLAW_HOME}/workspace/{relative_to_workspace(path)})")
    write_text(instreet_page, "\n".join(instreet_lines).rstrip() + "\n")

    categories = {
        "inbox": collect_markdown_files(semantic_root / "inbox"),
        "project_updates": collect_markdown_files(semantic_root / "project-updates"),
        "decisions": collect_markdown_files(semantic_root / "decisions"),
        "lessons": collect_markdown_files(semantic_root / "lessons"),
        "preferences": collect_markdown_files(semantic_root / "preferences"),
        "review_queue": collect_markdown_files(semantic_root / "review-queue"),
        "review_reports": collect_markdown_files(semantic_root / "review-reports"),
    }
    semantic_lines = [
        frontmatter("overview", [str(semantic_root)]),
        "# Legacy Memory Semantic Map",
        "",
        f"- generated_at: `{now_iso()}`",
        f"- root: `{semantic_root}`",
        f"- page_count: `{len(semantic_files)}`",
        "",
        "## Purpose",
        "",
        "保留 `memory/20-semantic/` 作为 V2 兼容蒸馏层，并把历史语义资产接入统一知识索引。",
        "",
        "## Category Counts",
        "",
    ]
    for name, files in categories.items():
        semantic_lines.append(f"- `{name}`: `{len(files)}`")
    semantic_lines.extend(
        [
            "",
            "## Canonical Rule",
            "",
            "- 历史语义文件继续保留，不做破坏性迁移",
            "- 新的主题知识优先进入 `${QYCLAW_HOME}/workspace/knowledge/`",
            "- 旧 `20-semantic/` 作为蒸馏输出与兼容层继续可用",
            "",
            "## Key Entry Files",
            "",
            f"- [MEMORY semantic index](${QYCLAW_HOME}/workspace/{relative_to_workspace(semantic_root / 'MEMORY.md')})",
            f"- [decisions index](${QYCLAW_HOME}/workspace/{relative_to_workspace(semantic_root / 'decisions.md')})",
            f"- [lessons index](${QYCLAW_HOME}/workspace/{relative_to_workspace(semantic_root / 'lessons.md')})",
        ]
    )
    write_text(semantic_page, "\n".join(semantic_lines).rstrip() + "\n")

    manifest = {
        "generated_at": now_iso(),
        "knowledge_root": str(KNOWLEDGE_ROOT),
        "legacy_sources": {
            "instreet_root": str(instreet_root),
            "instreet_page_count": len(instreet_files),
            "semantic_root": str(semantic_root),
            "semantic_page_count": len(semantic_files),
            "semantic_categories": {name: len(files) for name, files in categories.items()},
        },
        "bridge_pages": {
            "instreet_domain_map": str(instreet_page),
            "legacy_memory_semantic_map": str(semantic_page),
        },
        "strategy": "bridge-not-break",
    }
    write_text(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    register_index("InStreet Domain Map", instreet_page, "legacy knowledge domain bridge")
    register_index("Legacy Memory Semantic Map", semantic_page, "compatibility bridge for 20-semantic")
    append_log("backfill-continuity", "legacy knowledge bridge", "registered instreet and memory semantic assets into unified knowledge index")

    return {
        "instreet_page": str(instreet_page),
        "semantic_page": str(semantic_page),
        "manifest": str(manifest_path),
        "instreet_page_count": len(instreet_files),
        "semantic_page_count": len(semantic_files),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified QYclaw knowledge system entry")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")
    sub.add_parser("status")

    ingest = sub.add_parser("ingest-source")
    ingest.add_argument("source")
    ingest.add_argument("--title", required=True)
    ingest.add_argument("--category", default="general")
    ingest.add_argument("--project")
    ingest.add_argument("--copy-raw", action="store_true")

    fusion = sub.add_parser("compile-fusion")
    fusion.add_argument("task_dir")
    fusion.add_argument("--project")

    promote = sub.add_parser("promote-candidate")
    promote.add_argument("task_dir")
    promote.add_argument("--candidate-index", type=int, required=True)
    promote.add_argument("--adopted-by", default="main")
    promote.add_argument("--target")

    review_report = sub.add_parser("review-report")
    review_report.add_argument("--month")
    review_report.add_argument("--limit", type=int, default=50)

    review_decide_cmd = sub.add_parser("review-decide")
    review_decide_cmd.add_argument("--month", default=dt.date.today().strftime("%Y-%m"))
    review_decide_cmd.add_argument("--id", required=True)
    review_decide_cmd.add_argument("--decision", required=True, choices=["adopt", "observe", "reject"])
    review_decide_cmd.add_argument("--reviewer", default="main")
    review_decide_cmd.add_argument("--note", default="")

    review_decide_batch_cmd = sub.add_parser("review-decide-batch")
    review_decide_batch_cmd.add_argument("--month", default=dt.date.today().strftime("%Y-%m"))
    review_decide_batch_cmd.add_argument("--id", action="append", required=True)
    review_decide_batch_cmd.add_argument("--decision", required=True, choices=["adopt", "observe", "reject"])
    review_decide_batch_cmd.add_argument("--reviewer", default="main")
    review_decide_batch_cmd.add_argument("--note", default="")
    review_decide_batch_cmd.add_argument("--report-limit", type=int, default=200)

    convergence_report = sub.add_parser("convergence-report")
    convergence_report.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    convergence_report.add_argument("--limit", type=int, default=50)

    convergence_workbench = sub.add_parser("convergence-workbench")
    convergence_workbench.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    convergence_workbench.add_argument("--month", default=dt.date.today().strftime("%Y-%m"))
    convergence_workbench.add_argument("--limit", type=int, default=50)
    convergence_workbench.add_argument("--review-limit", type=int, default=10000)

    convergence_bootstrap = sub.add_parser("convergence-bootstrap")
    convergence_bootstrap.add_argument("--runtime", default="live", choices=["live", "shadow-live"])
    convergence_bootstrap.add_argument("--month", default=dt.date.today().strftime("%Y-%m"))
    convergence_bootstrap.add_argument("--limit", type=int, default=50)
    convergence_bootstrap.add_argument("--review-limit", type=int, default=10000)

    sub.add_parser("lint")
    sub.add_parser("backfill-continuity")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.cmd == "init":
        ensure_layout()
        print(json.dumps({"status": "initialized", **knowledge_status()}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "status":
        print(json.dumps({"status": "ok", **knowledge_status()}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "ingest-source":
        page = ingest_source(Path(args.source), args.title, args.category, args.project, args.copy_raw)
        print(json.dumps({"status": "ingested", "page": str(page)}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "compile-fusion":
        page = compile_fusion(Path(args.task_dir), args.project)
        print(json.dumps({"status": "compiled", "page": str(page)}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "promote-candidate":
        result = promote_candidate(Path(args.task_dir), args.candidate_index, args.adopted_by, args.target)
        print(json.dumps({"status": "promoted", **result}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "review-report":
        report = build_review_report(args.month, args.limit)
        print(json.dumps({"status": "review_report_built", "report": str(report)}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "review-decide":
        result = review_decide(args.month, args.id, args.decision, args.reviewer, args.note)
        print(json.dumps({"status": "review_decision_recorded", **result}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "review-decide-batch":
        result = review_decide_batch(args.month, args.id, args.decision, args.reviewer, args.note, args.report_limit)
        print(json.dumps({"status": "review_decision_batch_recorded", **result}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "convergence-report":
        report = build_convergence_report(args.runtime, args.limit)
        print(json.dumps({"status": "convergence_report_built", "report": str(report)}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "convergence-workbench":
        report = build_convergence_workbench(args.runtime, args.month, args.limit, args.review_limit)
        print(json.dumps({"status": "convergence_workbench_built", "report": str(report)}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "convergence-bootstrap":
        result = bootstrap_review_queue_from_convergence(args.runtime, args.month, args.limit, args.review_limit)
        print(json.dumps({"status": "convergence_bootstrapped", **result}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "lint":
        print(json.dumps({"status": "linted", **lint_knowledge()}, ensure_ascii=False, indent=2))
        return
    if args.cmd == "backfill-continuity":
        print(json.dumps({"status": "backfilled", **backfill_continuity()}, ensure_ascii=False, indent=2))
        return
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    main()
