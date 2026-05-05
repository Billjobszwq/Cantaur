#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
LIVE_LINK_ROOT = INTEGRATION_ROOT / 'live-link'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'
WORKFLOW = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_workflow.py'
DEFAULT_CONFIG = LIVE_LINK_ROOT / 'config' / 'main-bridge-gate.v1.json'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def parse_json_output(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError('gate subprocess returned empty stdout')
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


def ensure_gate_dirs(runtime_root: Path) -> dict[str, Path]:
    gate_root = runtime_root / 'bridge' / 'gate'
    dirs = {
        'root': gate_root,
        'runs': gate_root / 'runs',
        'audit': gate_root / 'audit',
    }
    for path in dirs.values():
        path.mkdir(parents=True, exist_ok=True)
    return dirs


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + '\n')


def cmd_status(config_path: Path) -> int:
    config = read_json(config_path)
    result = {
        'config_path': str(config_path),
        'enabled': config.get('enabled', False),
        'workflow_mode': config.get('workflow_mode'),
        'execution_mode': config.get('execution_mode'),
        'require_classifier_allow_bridge': config.get('require_classifier_allow_bridge', False),
        'allowed_task_types': config.get('allowed_task_types', []),
        'allowed_source_channels': config.get('allowed_source_channels', []),
        'updated_at': config.get('updated_at'),
        'mode': 'main_bridge_gate_status',
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_enable(config_path: Path, workflow_mode: str | None, execution_mode: str | None, require_classifier_allow_bridge: bool | None) -> int:
    config = read_json(config_path)
    config['enabled'] = True
    if workflow_mode:
        config['workflow_mode'] = workflow_mode
    if execution_mode:
        config['execution_mode'] = execution_mode
    if require_classifier_allow_bridge is not None:
        config['require_classifier_allow_bridge'] = require_classifier_allow_bridge
    config['updated_at'] = now_iso()
    write_json(config_path, config)
    return cmd_status(config_path)


def cmd_disable(config_path: Path) -> int:
    config = read_json(config_path)
    config['enabled'] = False
    config['updated_at'] = now_iso()
    write_json(config_path, config)
    return cmd_status(config_path)


def cmd_run(config_path: Path, title: str, goal: str, requested_by: str, priority: str, source_channel: str, source_message_id: str, source_session_id: str, decision_note: str, bridge_reason: str, task_type: str, runtime_override: str | None) -> int:
    config = read_json(config_path)
    runtime_name = runtime_override or config.get('runtime', 'shadow-live')
    runtime_root = RUNTIME_BASE / runtime_name
    dirs = ensure_gate_dirs(runtime_root)

    gate_record = {
        'title': title,
        'goal': goal,
        'requested_by': requested_by,
        'priority': priority,
        'source_channel': source_channel,
        'source_message_id': source_message_id,
        'source_session_id': source_session_id,
        'decision_note': decision_note,
        'bridge_reason': bridge_reason,
        'task_type': task_type,
        'config_snapshot': config,
        'created_at': now_iso(),
    }

    if not config.get('enabled', False):
        gate_record['status'] = 'gate_closed'
        run_path = dirs['runs'] / f"gate-{datetime.now().strftime('%Y%m%d-%H%M%S')}-closed.json"
        write_json(run_path, gate_record)
        append_jsonl(dirs['audit'] / 'gate-audit.jsonl', {'event': 'gate_closed', 'run_record': str(run_path), 'created_at': now_iso()})
        print(json.dumps({'status': 'gate_closed', 'run_record': str(run_path), 'mode': 'main_bridge_gate'}, ensure_ascii=False, indent=2))
        return 0

    if task_type not in config.get('allowed_task_types', []):
        gate_record['status'] = 'task_type_rejected'
        gate_record['rejection_reason'] = f'task_type not allowed: {task_type}'
        run_path = dirs['runs'] / f"gate-{datetime.now().strftime('%Y%m%d-%H%M%S')}-task-type-rejected.json"
        write_json(run_path, gate_record)
        append_jsonl(dirs['audit'] / 'gate-audit.jsonl', {'event': 'task_type_rejected', 'run_record': str(run_path), 'created_at': now_iso()})
        print(json.dumps({'status': 'task_type_rejected', 'run_record': str(run_path), 'mode': 'main_bridge_gate'}, ensure_ascii=False, indent=2))
        return 0

    if source_channel not in config.get('allowed_source_channels', []):
        gate_record['status'] = 'source_channel_rejected'
        gate_record['rejection_reason'] = f'source_channel not allowed: {source_channel}'
        run_path = dirs['runs'] / f"gate-{datetime.now().strftime('%Y%m%d-%H%M%S')}-channel-rejected.json"
        write_json(run_path, gate_record)
        append_jsonl(dirs['audit'] / 'gate-audit.jsonl', {'event': 'source_channel_rejected', 'run_record': str(run_path), 'created_at': now_iso()})
        print(json.dumps({'status': 'source_channel_rejected', 'run_record': str(run_path), 'mode': 'main_bridge_gate'}, ensure_ascii=False, indent=2))
        return 0

    workflow_cmd = [
        'python3', str(WORKFLOW),
        '--runtime', runtime_name,
        '--workflow-mode', config.get('workflow_mode', 'assist'),
        '--execution-mode', config.get('execution_mode', 'simulate'),
        '--title', title,
        '--goal', goal,
        '--requested-by', requested_by,
        '--priority', priority,
        '--source-channel', source_channel,
        '--source-message-id', source_message_id,
        '--source-session-id', source_session_id,
        '--decision-note', decision_note,
        '--bridge-reason', bridge_reason,
    ]
    if config.get('require_classifier_allow_bridge', False):
        workflow_cmd.extend(['--workflow-mode', 'enforce'])
    workflow_result = parse_json_output(workflow_cmd)

    gate_record.update({
        'status': workflow_result.get('status'),
        'workflow_summary_json': workflow_result.get('workflow_summary_json'),
        'workflow_summary_md': workflow_result.get('workflow_summary_md'),
        'updated_at': now_iso(),
    })
    run_path = dirs['runs'] / f"{workflow_result.get('task_id', 'unknown')}.gate-run.json"
    write_json(run_path, gate_record)
    append_jsonl(dirs['audit'] / 'gate-audit.jsonl', {'event': 'workflow_run', 'run_record': str(run_path), 'status': gate_record['status'], 'created_at': now_iso()})
    print(json.dumps({**workflow_result, 'gate_run_record': str(run_path), 'mode': 'main_bridge_gate'}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='manual gate for real-main bridge experiments')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    sub = parser.add_subparsers(dest='cmd', required=True)

    sub.add_parser('status')

    p_enable = sub.add_parser('enable')
    p_enable.add_argument('--workflow-mode', choices=['off', 'assist', 'enforce'])
    p_enable.add_argument('--execution-mode', choices=['start-only', 'simulate'])
    p_enable.add_argument('--require-classifier-allow-bridge', action=argparse.BooleanOptionalAction, default=None)

    sub.add_parser('disable')

    p_run = sub.add_parser('run')
    p_run.add_argument('--runtime')
    p_run.add_argument('--title', required=True)
    p_run.add_argument('--goal', required=True)
    p_run.add_argument('--requested-by', default='main')
    p_run.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    p_run.add_argument('--source-channel', default='manual')
    p_run.add_argument('--source-message-id', default='')
    p_run.add_argument('--source-session-id', default='')
    p_run.add_argument('--decision-note', default='main 判断该任务需要桥接辅助。')
    p_run.add_argument('--bridge-reason', default='当前任务适合进入 main bridge workflow。')
    p_run.add_argument('--task-type', default='report.cross_functional')

    args = parser.parse_args()
    config_path = Path(args.config)

    if args.cmd == 'status':
        return cmd_status(config_path)
    if args.cmd == 'enable':
        return cmd_enable(config_path, args.workflow_mode, args.execution_mode, args.require_classifier_allow_bridge)
    if args.cmd == 'disable':
        return cmd_disable(config_path)
    if args.cmd == 'run':
        return cmd_run(config_path, args.title, args.goal, args.requested_by, args.priority, args.source_channel, args.source_message_id, args.source_session_id, args.decision_note, args.bridge_reason, args.task_type, args.runtime)
    raise SystemExit(f'unknown cmd: {args.cmd}')


if __name__ == '__main__':
    raise SystemExit(main())
