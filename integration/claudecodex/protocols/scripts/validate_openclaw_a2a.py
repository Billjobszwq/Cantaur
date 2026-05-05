#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


MESSAGE_TYPES = {
    "TASK",
    "RESULT",
    "REVIEW",
    "CONSULT",
    "EVENT",
    "ESCALATE",
    "PERMISSION_REQUEST",
    "PERMISSION_RESPONSE",
    "KNOWLEDGE_CANDIDATE_CREATED",
    "KNOWLEDGE_PAGE_UPDATED",
    "MEMORY_CANDIDATE_PROMOTED",
    "PROCEDURE_PROMOTED",
    "REVIEW_DECISION_RECORDED",
    "KNOWLEDGE_ACTION_SUGGESTED",
}

EVENT_STATES = {
    "queued",
    "claimed",
    "in_progress",
    "blocked",
    "completed",
    "failed",
    "timed_out",
    "cancelled",
}

TASK_PRIORITIES = {"low", "medium", "high", "critical"}
RESULT_STATUSES = {"completed", "partial", "failed"}
PERMISSION_DECISIONS = {"approved", "rejected", "restricted"}
REQUESTED_SCOPES = {"single_task", "single_session", "persistent"}
CONSULT_RESPONSES = {"high-level opinion", "risk opinion", "numbers only", "approve/reject"}
RESOURCE_TYPES = {"tool", "skill", "channel", "session", "filesystem"}
KNOWLEDGE_SCOPES = {"source", "entity", "concept", "project", "comparison", "contradiction", "open-question", "overview"}
KNOWLEDGE_CANDIDATE_TYPES = {"project_update", "decision", "lesson", "preference", "rule", "fact"}
KNOWLEDGE_PAGE_OPERATIONS = {"create", "update", "merge", "link"}
PROMOTION_TARGETS = {
    "20-semantic/decisions",
    "20-semantic/preferences",
    "20-semantic/lessons",
    "20-semantic/project-updates",
    "30-procedures",
    "40-structured",
}
REVIEW_DECISIONS = {"adopt", "observe", "reject"}
REVIEW_STATUSES = {"adopted", "observed", "rejected"}
ACTION_PRIORITIES = {"low", "medium", "high"}


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def ensure_type(value: Any, expected_type: type | tuple[type, ...], field: str) -> None:
    ensure(isinstance(value, expected_type), f"{field} type invalid")


def ensure_non_empty_string(value: Any, field: str) -> None:
    ensure_type(value, str, field)
    ensure(bool(value.strip()), f"{field} cannot be empty")


def ensure_string_list(value: Any, field: str) -> None:
    ensure_type(value, list, field)
    for idx, item in enumerate(value):
        ensure_non_empty_string(item, f"{field}[{idx}]")


def ensure_iso8601(value: Any, field: str) -> None:
    ensure_non_empty_string(value, field)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise ValueError(f"{field} is not valid ISO8601") from exc


def validate_envelope(obj: dict[str, Any]) -> None:
    required = [
        "protocol",
        "message_type",
        "message_id",
        "task_id",
        "trace_id",
        "parent_task_id",
        "from",
        "to",
        "created_at",
        "body",
    ]
    for field in required:
        ensure(field in obj, f"missing field: {field}")

    ensure(obj["protocol"] == "openclaw-a2a/v1", "protocol must be openclaw-a2a/v1")
    ensure(obj["message_type"] in MESSAGE_TYPES, "message_type invalid")
    ensure_non_empty_string(obj["message_id"], "message_id")
    ensure_non_empty_string(obj["task_id"], "task_id")
    ensure_non_empty_string(obj["trace_id"], "trace_id")
    ensure(obj["parent_task_id"] is None or isinstance(obj["parent_task_id"], str), "parent_task_id invalid")
    ensure_non_empty_string(obj["from"], "from")
    ensure_non_empty_string(obj["to"], "to")
    ensure_iso8601(obj["created_at"], "created_at")
    ensure_type(obj["body"], dict, "body")


def validate_task(body: dict[str, Any]) -> None:
    for field in ["goal", "constraints", "inputs", "required_output", "priority", "deadline", "handoff_to"]:
        ensure(field in body, f"TASK missing field: {field}")
    ensure_non_empty_string(body["goal"], "body.goal")
    ensure_string_list(body["constraints"], "body.constraints")
    ensure_string_list(body["inputs"], "body.inputs")
    ensure_string_list(body["required_output"], "body.required_output")
    ensure(body["priority"] in TASK_PRIORITIES, "body.priority invalid")
    ensure_iso8601(body["deadline"], "body.deadline")
    ensure_non_empty_string(body["handoff_to"], "body.handoff_to")


def validate_result(body: dict[str, Any]) -> None:
    for field in ["status", "summary", "artifacts", "confidence", "needs_review", "review_by"]:
        ensure(field in body, f"RESULT missing field: {field}")
    ensure(body["status"] in RESULT_STATUSES, "body.status invalid")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_string_list(body["artifacts"], "body.artifacts")
    ensure_type(body["confidence"], (int, float), "body.confidence")
    ensure(0.0 <= float(body["confidence"]) <= 1.0, "body.confidence out of range")
    ensure_type(body["needs_review"], bool, "body.needs_review")
    ensure_string_list(body["review_by"], "body.review_by")


def validate_review(body: dict[str, Any]) -> None:
    for field in ["review_target", "review_scope", "review_note", "requested_by"]:
        ensure(field in body, f"REVIEW missing field: {field}")
    ensure_non_empty_string(body["review_target"], "body.review_target")
    ensure_string_list(body["review_scope"], "body.review_scope")
    ensure_non_empty_string(body["review_note"], "body.review_note")
    ensure_non_empty_string(body["requested_by"], "body.requested_by")


def validate_consult(body: dict[str, Any]) -> None:
    for field in ["consult_topic", "context_summary", "required_response", "handoff_back_to"]:
        ensure(field in body, f"CONSULT missing field: {field}")
    ensure_non_empty_string(body["consult_topic"], "body.consult_topic")
    ensure_non_empty_string(body["context_summary"], "body.context_summary")
    ensure(body["required_response"] in CONSULT_RESPONSES, "body.required_response invalid")
    ensure_non_empty_string(body["handoff_back_to"], "body.handoff_back_to")


def validate_event(body: dict[str, Any]) -> None:
    for field in ["state", "progress", "note", "blockers"]:
        ensure(field in body, f"EVENT missing field: {field}")
    ensure(body["state"] in EVENT_STATES, "body.state invalid")
    ensure_type(body["progress"], (int, float), "body.progress")
    ensure(0.0 <= float(body["progress"]) <= 1.0, "body.progress out of range")
    ensure_non_empty_string(body["note"], "body.note")
    ensure_string_list(body["blockers"], "body.blockers")


def validate_escalate(body: dict[str, Any]) -> None:
    for field in ["reason", "summary", "options", "escalate_to"]:
        ensure(field in body, f"ESCALATE missing field: {field}")
    ensure_non_empty_string(body["reason"], "body.reason")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_string_list(body["options"], "body.options")
    ensure_non_empty_string(body["escalate_to"], "body.escalate_to")


def validate_permission_request(body: dict[str, Any]) -> None:
    for field in ["resource_type", "resource_name", "reason", "requested_scope"]:
        ensure(field in body, f"PERMISSION_REQUEST missing field: {field}")
    ensure(body["resource_type"] in RESOURCE_TYPES, "body.resource_type invalid")
    ensure_non_empty_string(body["resource_name"], "body.resource_name")
    ensure_non_empty_string(body["reason"], "body.reason")
    ensure(body["requested_scope"] in REQUESTED_SCOPES, "body.requested_scope invalid")


def validate_permission_response(body: dict[str, Any]) -> None:
    for field in ["decision", "granted_scope", "constraints", "note"]:
        ensure(field in body, f"PERMISSION_RESPONSE missing field: {field}")
    ensure(body["decision"] in PERMISSION_DECISIONS, "body.decision invalid")
    ensure(body["granted_scope"] is None or body["granted_scope"] in REQUESTED_SCOPES, "body.granted_scope invalid")
    ensure_string_list(body["constraints"], "body.constraints")
    ensure_non_empty_string(body["note"], "body.note")


def validate_knowledge_candidate_created(body: dict[str, Any]) -> None:
    for field in [
        "knowledge_scope",
        "candidate_type",
        "title",
        "summary",
        "evidence",
        "proposed_target",
        "source_refs",
        "related_pages",
        "review_queue_ref",
    ]:
        ensure(field in body, f"KNOWLEDGE_CANDIDATE_CREATED missing field: {field}")
    ensure(body["knowledge_scope"] in KNOWLEDGE_SCOPES, "body.knowledge_scope invalid")
    ensure(body["candidate_type"] in KNOWLEDGE_CANDIDATE_TYPES, "body.candidate_type invalid")
    ensure_non_empty_string(body["title"], "body.title")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_non_empty_string(body["evidence"], "body.evidence")
    ensure(body["proposed_target"] in PROMOTION_TARGETS, "body.proposed_target invalid")
    ensure_string_list(body["source_refs"], "body.source_refs")
    ensure_string_list(body["related_pages"], "body.related_pages")
    ensure_non_empty_string(body["review_queue_ref"], "body.review_queue_ref")


def validate_knowledge_page_updated(body: dict[str, Any]) -> None:
    for field in [
        "page_type",
        "page_path",
        "operation",
        "summary",
        "source_refs",
        "related_pages",
        "compiler",
    ]:
        ensure(field in body, f"KNOWLEDGE_PAGE_UPDATED missing field: {field}")
    ensure(body["page_type"] in KNOWLEDGE_SCOPES, "body.page_type invalid")
    ensure_non_empty_string(body["page_path"], "body.page_path")
    ensure(body["operation"] in KNOWLEDGE_PAGE_OPERATIONS, "body.operation invalid")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_string_list(body["source_refs"], "body.source_refs")
    ensure_string_list(body["related_pages"], "body.related_pages")
    ensure_non_empty_string(body["compiler"], "body.compiler")


def validate_memory_candidate_promoted(body: dict[str, Any]) -> None:
    for field in [
        "candidate_ref",
        "adopted_target",
        "title",
        "summary",
        "adopted_by",
        "decision_note",
    ]:
        ensure(field in body, f"MEMORY_CANDIDATE_PROMOTED missing field: {field}")
    ensure_non_empty_string(body["candidate_ref"], "body.candidate_ref")
    ensure(body["adopted_target"] in PROMOTION_TARGETS, "body.adopted_target invalid")
    ensure_non_empty_string(body["title"], "body.title")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_non_empty_string(body["adopted_by"], "body.adopted_by")
    ensure_non_empty_string(body["decision_note"], "body.decision_note")


def validate_procedure_promoted(body: dict[str, Any]) -> None:
    for field in [
        "procedure_path",
        "title",
        "summary",
        "promoted_from",
        "approved_by",
        "rollout_scope",
    ]:
        ensure(field in body, f"PROCEDURE_PROMOTED missing field: {field}")
    ensure_non_empty_string(body["procedure_path"], "body.procedure_path")
    ensure_non_empty_string(body["title"], "body.title")
    ensure_non_empty_string(body["summary"], "body.summary")
    ensure_non_empty_string(body["promoted_from"], "body.promoted_from")
    ensure_non_empty_string(body["approved_by"], "body.approved_by")
    ensure_non_empty_string(body["rollout_scope"], "body.rollout_scope")


def validate_review_decision_recorded(body: dict[str, Any]) -> None:
    for field in [
        "review_id",
        "decision",
        "status_after",
        "reviewer",
        "note",
        "queue_path",
        "promoted_path",
    ]:
        ensure(field in body, f"REVIEW_DECISION_RECORDED missing field: {field}")
    ensure_non_empty_string(body["review_id"], "body.review_id")
    ensure(body["decision"] in REVIEW_DECISIONS, "body.decision invalid")
    ensure(body["status_after"] in REVIEW_STATUSES, "body.status_after invalid")
    ensure_non_empty_string(body["reviewer"], "body.reviewer")
    ensure_type(body["note"], str, "body.note")
    ensure_non_empty_string(body["queue_path"], "body.queue_path")
    ensure_type(body["promoted_path"], str, "body.promoted_path")


def validate_knowledge_action_suggested(body: dict[str, Any]) -> None:
    for field in [
        "action_type",
        "title",
        "reason",
        "priority",
        "target_agent",
        "ref",
        "recommended_command",
        "source_message_ids",
        "suggested_by",
    ]:
        ensure(field in body, f"KNOWLEDGE_ACTION_SUGGESTED missing field: {field}")
    ensure_non_empty_string(body["action_type"], "body.action_type")
    ensure_non_empty_string(body["title"], "body.title")
    ensure_non_empty_string(body["reason"], "body.reason")
    ensure(body["priority"] in ACTION_PRIORITIES, "body.priority invalid")
    ensure_non_empty_string(body["target_agent"], "body.target_agent")
    ensure_type(body["ref"], str, "body.ref")
    ensure_type(body["recommended_command"], str, "body.recommended_command")
    ensure_string_list(body["source_message_ids"], "body.source_message_ids")
    ensure_non_empty_string(body["suggested_by"], "body.suggested_by")


def validate_message(obj: dict[str, Any]) -> None:
    validate_envelope(obj)
    body = obj["body"]
    message_type = obj["message_type"]
    if message_type == "TASK":
        validate_task(body)
    elif message_type == "RESULT":
        validate_result(body)
    elif message_type == "REVIEW":
        validate_review(body)
    elif message_type == "CONSULT":
        validate_consult(body)
    elif message_type == "EVENT":
        validate_event(body)
    elif message_type == "ESCALATE":
        validate_escalate(body)
    elif message_type == "PERMISSION_REQUEST":
        validate_permission_request(body)
    elif message_type == "PERMISSION_RESPONSE":
        validate_permission_response(body)
    elif message_type == "KNOWLEDGE_CANDIDATE_CREATED":
        validate_knowledge_candidate_created(body)
    elif message_type == "KNOWLEDGE_PAGE_UPDATED":
        validate_knowledge_page_updated(body)
    elif message_type == "MEMORY_CANDIDATE_PROMOTED":
        validate_memory_candidate_promoted(body)
    elif message_type == "PROCEDURE_PROMOTED":
        validate_procedure_promoted(body)
    elif message_type == "REVIEW_DECISION_RECORDED":
        validate_review_decision_recorded(body)
    elif message_type == "KNOWLEDGE_ACTION_SUGGESTED":
        validate_knowledge_action_suggested(body)
    else:
        raise ValueError(f"unknown message_type: {message_type}")


def validate_file(path: Path) -> tuple[bool, str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            obj = json.load(fh)
        ensure_type(obj, dict, "root")
        validate_message(obj)
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


def iter_json_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.json") if p.is_file())


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_openclaw_a2a.py <json-file-or-directory>", file=sys.stderr)
        return 2

    target = Path(sys.argv[1]).expanduser().resolve()
    ensure(target.exists(), "target does not exist")
    files = iter_json_files(target)
    ensure(files, "no json files found")

    failed = 0
    for file_path in files:
        ok, message = validate_file(file_path)
        status = "OK" if ok else "FAIL"
        print(f"[{status}] {file_path}")
        if not ok:
            print(f"  -> {message}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        raise SystemExit(1)
