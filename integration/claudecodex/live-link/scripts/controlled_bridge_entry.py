#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

OPENCLAW_ROOT = Path.home() / ".openclaw"
INTEGRATION_ROOT = OPENCLAW_ROOT / 'workspace' / 'integration' / 'claudecodex'
LIVE_LINK_ROOT = INTEGRATION_ROOT / 'live-link'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'
MANUAL_REPORT = LIVE_LINK_ROOT / 'scripts' / 'manual_shadow_report.py'
REHEARSE_REPORT = LIVE_LINK_ROOT / 'scripts' / 'rehearse_shadow_report.py'

ALLOWED_TASK_TYPES = {'report.cross_functional'}
BLOCKED_TERMS = [
    '法律最终承诺', '财务最终承诺', '自动外发', '自动消息发送', '实时群聊联动',
    'destructive', '删除生产', '销毁生产', '自动回复用户', '自动发送给客户',
]
DEFAULT_CONSTRAINTS = ['走formal fusion mode', '不自动触发现网最终回复']
DEFAULT_REQUIRED_DOMAINS = 'research,ops,legal,finance,content'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def default_deadline() -> str:
    return (datetime.now().astimezone() + timedelta(days=7)).replace(microsecond=0).isoformat()


def slug(text: str) -> str:
    out = []
    for ch in text.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in {' ', '-', '_', '/'}:
            out.append('-')
    result = ''.join(out).strip('-')
    while '--' in result:
        result = result.replace('--', '-')
    return result or 'task'


def parse_json_output(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError('bridge subprocess returned empty stdout')
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        last_obj = None
        length = len(stdout)
        while idx < length:
            while idx < length and stdout[idx].isspace():
                idx += 1
            if idx >= length:
                break
            obj, next_idx = decoder.raw_decode(stdout, idx)
            if isinstance(obj, dict):
                last_obj = obj
            idx = next_idx
        if last_obj is None:
            raise
        return last_obj


def ensure_runtime_dirs(runtime_root: Path) -> dict[str, Path]:
    bridge_root = runtime_root / 'bridge'
    mapping = {
        'bridge_root': bridge_root,
        'requests': bridge_root / 'requests',
        'responses': bridge_root / 'responses',
        'comparisons': bridge_root / 'comparisons',
        'audit': bridge_root / 'audit',
    }
    for path in mapping.values():
        path.mkdir(parents=True, exist_ok=True)
    return mapping


def contains_blocked_terms(title: str, goal: str, constraints: list[str]) -> list[str]:
    text = '\n'.join([title, goal, *constraints])
    markers = ['不', '非', '禁止', '不要', '无需', '不需要', '不做', '避免']
    hits: list[str] = []
    for term in BLOCKED_TERMS:
        start = 0
        blocked = False
        while True:
            idx = text.find(term, start)
            if idx == -1:
                break
            window = text[max(0, idx - 6):idx]
            if not any(marker in window for marker in markers):
                blocked = True
                break
            start = idx + len(term)
        if blocked:
            hits.append(term)
    return hits


def validate_policy(args, constraints: list[str]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not args.confirm_explicit_bridge:
        reasons.append('缺少显式触发确认：需要传入 --confirm-explicit-bridge')
    if args.bridge_mode != 'main_explicit_bridge':
        reasons.append(f'当前只允许 main_explicit_bridge，收到：{args.bridge_mode}')
    if args.task_type not in ALLOWED_TASK_TYPES:
        reasons.append(f'任务类型不在白名单：{args.task_type}')
    if not args.requires_multi_agent:
        reasons.append('桥接任务必须明确 requires_multi_agent=true')
    if not args.delivery_oriented:
        reasons.append('桥接任务必须明确 delivery_oriented=true')
    if args.contains_external_action:
        reasons.append('桥接任务禁止 contains_external_action=true')
    if args.contains_real_time_requirement:
        reasons.append('桥接任务禁止 contains_real_time_requirement=true')
    if args.risk_level in {'high', 'critical'}:
        reasons.append(f'桥接任务风险等级过高：{args.risk_level}')
    blocked_hits = contains_blocked_terms(args.title, args.goal, constraints)
    for hit in blocked_hits:
        reasons.append(f'命中禁止触发词：{hit}')
    return (len(reasons) == 0), reasons


def build_request(args, task_id: str | None, trace_id: str | None, constraints: list[str]) -> dict[str, Any]:
    return {
        'bridge_mode': args.bridge_mode,
        'task_type': args.task_type,
        'title': args.title,
        'goal': args.goal,
        'requested_by': args.requested_by,
        'priority': args.priority,
        'deadline': args.deadline,
        'source_channel': args.source_channel,
        'source_message_id': args.source_message_id,
        'source_session_id': args.source_session_id,
        'constraints': constraints,
        'required_domains': [item.strip() for item in args.required_domains.split(',') if item.strip()],
        'required_agents': [item.strip() for item in args.required_agents.split(',') if item.strip()],
        'requires_multi_agent': args.requires_multi_agent,
        'delivery_oriented': args.delivery_oriented,
        'risk_level': args.risk_level,
        'contains_external_action': args.contains_external_action,
        'contains_real_time_requirement': args.contains_real_time_requirement,
        'execution_mode': args.execution_mode,
        'requested_task_id': task_id,
        'requested_trace_id': trace_id,
        'created_at': now_iso(),
    }


def expected_paths(runtime_root: Path, task_id: str) -> tuple[str, str]:
    root_synthesis = runtime_root / 'artifacts' / 'main' / task_id / 'shadow-synthesis.md'
    memory_output = runtime_root / 'memory-fusion' / task_id
    return str(root_synthesis), str(memory_output)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + '\n')


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def write_comparison_note(path: Path, response: dict[str, Any], accepted: bool, reasons: list[str]) -> None:
    lines = [
        f"# {response.get('task_id') or 'bridge-request'} comparison note",
        '',
        f"- `bridge_mode`: `{response.get('bridge_mode', 'main_explicit_bridge')}`",
        f"- `accepted`: `{str(accepted).lower()}`",
        f"- `status`: `{response.get('status', 'rejected')}`",
        f"- `generated_at`: `{now_iso()}`",
        '',
    ]
    if accepted:
        lines.extend([
            '## Current Bridge Output',
            f"- `selected_agents`: `{', '.join(response.get('selected_agents', []))}`",
            f"- `root_synthesis`: `{response.get('root_synthesis', '')}`",
            f"- `memory_output`: `{response.get('memory_output', '')}`",
            '',
            '## Comparison Decision',
            '- 待 main 或操作者比较 sidecar 综合稿与现网主链输出。',
            '- 当前桥接结果作为正式融合辅助输出，由 main 做最终回复与最终动作判断。',
            '',
        ])
    else:
        lines.extend([
            '## Rejection Reasons',
            *[f'- {item}' for item in reasons],
            '',
            '## Comparison Decision',
            '- 本次桥接被拒绝，继续沿用现网主链。',
            '',
        ])
    path.write_text('\n'.join(lines), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='controlled bridge entry for main explicit bridge')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--bridge-mode', default='main_explicit_bridge')
    parser.add_argument('--task-type', default='report.cross_functional')
    parser.add_argument('--execution-mode', default='start-only', choices=['start-only', 'simulate'])
    parser.add_argument('--title', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--requested-by', default='main')
    parser.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--deadline', default=default_deadline())
    parser.add_argument('--constraint', dest='constraints', action='append')
    parser.add_argument('--required-domains', default=DEFAULT_REQUIRED_DOMAINS)
    parser.add_argument('--required-agents', default='')
    parser.add_argument('--task-id')
    parser.add_argument('--trace-id')
    parser.add_argument('--source-channel', default='main-explicit-bridge')
    parser.add_argument('--source-message-id', default='')
    parser.add_argument('--source-session-id', default='')
    parser.add_argument('--requires-multi-agent', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--delivery-oriented', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--contains-external-action', action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument('--contains-real-time-requirement', action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument('--risk-level', default='medium', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--confirm-explicit-bridge', action='store_true')
    parser.add_argument('--skip-sync', action='store_true')
    args = parser.parse_args()

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    task_id = args.task_id or f"task-{stamp}-{slug(args.title)[:24]}"
    trace_id = args.trace_id or f"trace-{stamp}"

    constraints = list(args.constraints or [])
    for item in DEFAULT_CONSTRAINTS:
        if item not in constraints:
            constraints.append(item)

    runtime_root = RUNTIME_BASE / args.runtime
    dirs = ensure_runtime_dirs(runtime_root)
    request_payload = build_request(args, task_id, trace_id, constraints)
    request_path = dirs['requests'] / f'{task_id}.bridge-request.json'
    write_json(request_path, request_payload)

    accepted, rejection_reasons = validate_policy(args, constraints)
    root_synthesis, memory_output = expected_paths(runtime_root, task_id)

    response_payload: dict[str, Any] = {
        'accepted': accepted,
        'bridge_mode': args.bridge_mode,
        'mode': 'formal-fusion-assisted',
        'task_id': task_id,
        'trace_id': trace_id,
        'selected_agents': [],
        'root_synthesis': root_synthesis,
        'root_synthesis_ready': False,
        'memory_output': memory_output,
        'memory_output_ready': False,
        'knowledge_output': '',
        'knowledge_output_ready': False,
        'status': 'rejected',
        'request_record': str(request_path),
        'created_at': now_iso(),
    }

    if accepted:
        bridge_script = MANUAL_REPORT if args.execution_mode == 'start-only' else REHEARSE_REPORT
        cmd = [
            'python3', str(bridge_script),
            '--runtime', args.runtime,
            '--title', args.title,
            '--goal', args.goal,
            '--requested-by', args.requested_by,
            '--priority', args.priority,
            '--deadline', args.deadline,
            '--required-domains', args.required_domains,
            '--required-agents', args.required_agents,
            '--task-id', task_id,
            '--trace-id', trace_id,
            '--source-channel', args.source_channel,
            '--source-message-id', args.source_message_id,
            '--source-session-id', args.source_session_id,
        ]
        if args.skip_sync:
            cmd.append('--skip-sync')
        for item in constraints:
            cmd.extend(['--constraint', item])
        result = parse_json_output(cmd)
        response_payload.update({
            'selected_agents': result.get('selected_agents', []),
            'root_synthesis': result.get('root_synthesis', root_synthesis),
            'root_synthesis_ready': bool(result.get('root_synthesis') and Path(result['root_synthesis']).exists()),
            'memory_output': result.get('memory_output', memory_output),
            'memory_output_ready': bool(result.get('memory_output') and Path(result['memory_output']).exists()),
            'knowledge_output': result.get('knowledge_output', ''),
            'knowledge_output_ready': bool(result.get('knowledge_output') and Path(result['knowledge_output']).exists()),
            'status': 'shadow_started' if args.execution_mode == 'start-only' else 'shadow_synthesized',
            'runtime': result.get('runtime', args.runtime),
            'selected_agent_count': len(result.get('selected_agents', [])),
            'bridge_result': result,
        })
    else:
        response_payload['rejection_reasons'] = rejection_reasons

    response_path = dirs['responses'] / f'{task_id}.bridge-response.json'
    write_json(response_path, response_payload)
    response_payload['response_record'] = str(response_path)
    write_json(response_path, response_payload)

    comparison_path = dirs['comparisons'] / f'{task_id}.comparison-note.md'
    write_comparison_note(comparison_path, response_payload, accepted, rejection_reasons)
    response_payload['comparison_note'] = str(comparison_path)
    write_json(response_path, response_payload)

    audit_payload = {
        'bridge_mode': args.bridge_mode,
        'task_id': task_id,
        'trace_id': trace_id,
        'task_type': args.task_type,
        'selected_agents': response_payload.get('selected_agents', []),
        'root_status': response_payload.get('status'),
        'root_synthesis_path': response_payload.get('root_synthesis'),
        'memory_output_path': response_payload.get('memory_output'),
        'knowledge_output_path': response_payload.get('knowledge_output'),
        'comparison_decision': 'pending_manual_compare' if accepted else 'bridge_rejected',
        'accepted': accepted,
        'created_at': now_iso(),
    }
    append_jsonl(dirs['audit'] / 'bridge-observability.jsonl', audit_payload)
    append_jsonl(dirs['audit'] / 'bridge-audit.jsonl', {
        'event': 'controlled_bridge_entry',
        'accepted': accepted,
        'request_record': str(request_path),
        'response_record': str(response_path),
        'comparison_note': str(comparison_path),
        'created_at': now_iso(),
    })

    print(json.dumps(response_payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
