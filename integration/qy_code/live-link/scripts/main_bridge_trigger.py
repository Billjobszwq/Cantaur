#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
LIVE_LINK_ROOT = INTEGRATION_ROOT / 'live-link'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'
CONTROLLED_BRIDGE = LIVE_LINK_ROOT / 'scripts' / 'controlled_bridge_entry.py'
CLASSIFIER = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_classifier.py'
DEFAULT_REQUIRED_DOMAINS = 'research,ops,legal,finance,content'
DEFAULT_CONSTRAINTS = ['走formal fusion mode', '不自动触发现网最终回复']


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
        raise RuntimeError('main bridge subprocess returned empty stdout')
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        idx = 0
        last_obj = None
        while idx < len(stdout):
            while idx < len(stdout) and stdout[idx].isspace():
                idx += 1
            if idx >= len(stdout):
                break
            obj, next_idx = decoder.raw_decode(stdout, idx)
            if isinstance(obj, dict):
                last_obj = obj
            idx = next_idx
        if last_obj is None:
            raise
        return last_obj


def ensure_trigger_dir(runtime_root: Path) -> Path:
    path = runtime_root / 'bridge' / 'triggers'
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description='main-facing wrapper for controlled bridge entry')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--title', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--requested-by', default='main')
    parser.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--deadline', default=default_deadline())
    parser.add_argument('--source-channel', default='main-explicit-bridge')
    parser.add_argument('--source-message-id', default='')
    parser.add_argument('--source-session-id', default='')
    parser.add_argument('--required-domains', default=DEFAULT_REQUIRED_DOMAINS)
    parser.add_argument('--required-agents', default='')
    parser.add_argument('--constraint', dest='constraints', action='append')
    parser.add_argument('--decision-note', default='main 判断该任务适合走跨职能 formal-fusion 协作。')
    parser.add_argument('--bridge-reason', default='需要 research/ops/law/finance/content 多方协作，且当前适合走正式协同桥接。')
    parser.add_argument('--expected-deliverable', action='append')
    parser.add_argument('--execution-mode', default='simulate', choices=['start-only', 'simulate'])
    parser.add_argument('--risk-level', default='medium', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--run-classifier', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--require-allow-bridge', action='store_true')
    parser.add_argument('--task-id')
    parser.add_argument('--trace-id')
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
    trigger_dir = ensure_trigger_dir(runtime_root)
    trigger_path = trigger_dir / f'{task_id}.main-trigger.json'
    trigger_payload = {
        'task_id': task_id,
        'trace_id': trace_id,
        'bridge_mode': 'main_explicit_bridge',
        'task_type': 'report.cross_functional',
        'title': args.title,
        'goal': args.goal,
        'requested_by': args.requested_by,
        'priority': args.priority,
        'deadline': args.deadline,
        'source_channel': args.source_channel,
        'source_message_id': args.source_message_id,
        'source_session_id': args.source_session_id,
        'required_domains': [item.strip() for item in args.required_domains.split(',') if item.strip()],
        'required_agents': [item.strip() for item in args.required_agents.split(',') if item.strip()],
        'constraints': constraints,
        'decision_note': args.decision_note,
        'bridge_reason': args.bridge_reason,
        'expected_deliverables': args.expected_deliverable or ['shadow_synthesis', 'memory_package', 'comparison_note'],
        'risk_level': args.risk_level,
        'requires_multi_agent': True,
        'delivery_oriented': True,
        'contains_external_action': False,
        'contains_real_time_requirement': False,
        'execution_mode': args.execution_mode,
        'classifier_enabled': args.run_classifier,
        'require_allow_bridge': args.require_allow_bridge,
        'created_at': now_iso(),
        'status': 'prepared',
    }
    write_json(trigger_path, trigger_payload)

    classifier_result = None
    classifier_record = None
    if args.run_classifier:
        classifier_cmd = [
            'python3', str(CLASSIFIER),
            '--runtime', args.runtime,
            '--task-id', task_id,
            '--title', args.title,
            '--goal', args.goal,
            '--decision-note', args.decision_note,
            '--bridge-reason', args.bridge_reason,
        ]
        classifier_result = parse_json_output(classifier_cmd)
        classifier_record = classifier_result.get('classifier_record')
        trigger_payload['classifier_result'] = classifier_result
        trigger_payload['classifier_record'] = classifier_record
        trigger_payload['classifier_allow_bridge'] = classifier_result.get('allow_bridge')
        write_json(trigger_path, trigger_payload)

        if args.require_allow_bridge and not classifier_result.get('allow_bridge'):
            trigger_payload.update({
                'status': 'classifier_rejected',
                'updated_at': now_iso(),
            })
            write_json(trigger_path, trigger_payload)
            output = {
                'accepted': False,
                'status': 'classifier_rejected',
                'task_id': task_id,
                'trace_id': trace_id,
                'main_trigger_record': str(trigger_path),
                'classifier_record': classifier_record,
                'classifier_allow_bridge': classifier_result.get('allow_bridge'),
                'classifier_reasons': classifier_result.get('reasons', []),
                'decision_note': args.decision_note,
                'bridge_reason': args.bridge_reason,
                'mode': 'main_bridge_trigger',
            }
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0

    cmd = [
        'python3', str(CONTROLLED_BRIDGE),
        '--runtime', args.runtime,
        '--bridge-mode', 'main_explicit_bridge',
        '--task-type', 'report.cross_functional',
        '--execution-mode', args.execution_mode,
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
        '--risk-level', args.risk_level,
        '--confirm-explicit-bridge',
    ]
    if args.skip_sync:
        cmd.append('--skip-sync')
    for item in constraints:
        cmd.extend(['--constraint', item])

    bridge_response = parse_json_output(cmd)
    trigger_payload.update({
        'status': 'triggered' if bridge_response.get('accepted') else 'rejected',
        'bridge_response_record': bridge_response.get('response_record'),
        'bridge_request_record': bridge_response.get('request_record'),
        'comparison_note': bridge_response.get('comparison_note'),
        'selected_agents': bridge_response.get('selected_agents', []),
        'root_synthesis': bridge_response.get('root_synthesis'),
        'memory_output': bridge_response.get('memory_output'),
        'updated_at': now_iso(),
    })
    write_json(trigger_path, trigger_payload)

    output = dict(bridge_response)
    output['main_trigger_record'] = str(trigger_path)
    output['classifier_record'] = classifier_record
    output['classifier_allow_bridge'] = None if classifier_result is None else classifier_result.get('allow_bridge')
    output['classifier_reasons'] = [] if classifier_result is None else classifier_result.get('reasons', [])
    output['decision_note'] = args.decision_note
    output['bridge_reason'] = args.bridge_reason
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
