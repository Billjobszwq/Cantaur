#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from bridge_lifecycle_common import (
    append_jsonl,
    connect,
    ensure_lifecycle_dirs,
    now_iso,
    read_json,
    resolve_task_tree,
    runtime_paths,
    update_root_status,
    write_json,
)

MAIN_BRIDGE_GATE = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/scripts/main_bridge_gate.py")
BRIDGE_EXECUTOR = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/scripts/bridge_executor.py")
BRIDGE_TERMINATOR = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/scripts/bridge_terminator.py")
BRIDGE_TIMEOUT_WATCHER = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/scripts/bridge_timeout_watcher.py")
DEFAULT_GATE_CONFIG = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/config/main-bridge-gate.v1.json")


def parse_json_output(cmd: list[str]) -> dict[str, Any]:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError('subprocess returned empty stdout')
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


def task_id_from_gate_result(gate_result: dict[str, Any]) -> str:
    workflow_summary_json = gate_result.get('workflow_summary_json')
    if workflow_summary_json and Path(workflow_summary_json).exists():
        payload = read_json(Path(workflow_summary_json))
        task_id = payload.get('task_id')
        if task_id:
            return str(task_id)
    gate_run_record = gate_result.get('gate_run_record')
    if gate_run_record and Path(gate_run_record).exists():
        payload = read_json(Path(gate_run_record))
        wf = payload.get('workflow_summary_json')
        if wf and Path(str(wf)).exists():
            detail = read_json(Path(str(wf)))
            task_id = detail.get('task_id')
            if task_id:
                return str(task_id)
    return ''


def cmd_trigger(args: argparse.Namespace) -> int:
    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    gate_cmd = [
        'python3', MAIN_BRIDGE_GATE,
        '--config', args.config,
        'run',
        '--runtime', args.runtime,
        '--title', args.title,
        '--goal', args.goal,
        '--requested-by', args.requested_by,
        '--priority', args.priority,
        '--source-channel', args.source_channel,
        '--source-message-id', args.source_message_id,
        '--source-session-id', args.source_session_id,
        '--decision-note', args.decision_note,
        '--bridge-reason', args.bridge_reason,
        '--task-type', args.task_type,
    ]
    gate_result = parse_json_output(gate_cmd)
    task_id = task_id_from_gate_result(gate_result)

    payload = {
        'mode': 'main_bridge_lifecycle_trigger',
        'runtime': args.runtime,
        'task_id': task_id,
        'title': args.title,
        'goal': args.goal,
        'status': gate_result.get('status', 'unknown'),
        'requested_by': args.requested_by,
        'priority': args.priority,
        'source_channel': args.source_channel,
        'gate_result': gate_result,
        'created_at': now_iso(),
    }
    record = paths['runs_dir'] / f"trigger-{payload['created_at'].replace(':', '-')}-{task_id or 'unknown'}.json"
    write_json(record, payload)
    append_jsonl(paths['audit_dir'] / 'lifecycle-audit.jsonl', {
        'event': 'trigger',
        'runtime': args.runtime,
        'task_id': task_id,
        'status': payload['status'],
        'record': str(record),
        'created_at': now_iso(),
    })

    payload['record'] = str(record)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cmd = [
        'python3', BRIDGE_EXECUTOR,
        '--runtime', args.runtime,
        '--max-tasks', str(args.max_tasks),
        '--lock-ttl-seconds', str(args.lock_ttl_seconds),
        '--actor', args.actor,
    ]
    if args.task_id:
        cmd.extend(['--task-id', args.task_id])
    for agent in args.agent:
        cmd.extend(['--agent', agent])
    if args.skip_memory_refresh:
        cmd.append('--skip-memory-refresh')

    result = parse_json_output(cmd)
    print(json.dumps({**result, 'mode': 'main_bridge_lifecycle_run'}, ensure_ascii=False, indent=2))
    return 0


def cmd_terminate(args: argparse.Namespace) -> int:
    cmd = [
        'python3', BRIDGE_TERMINATOR,
        '--runtime', args.runtime,
        '--task-id', args.task_id,
        '--actor', args.actor,
        '--reason', args.reason,
        '--scope', args.scope,
    ]
    if args.force:
        cmd.append('--force')
    result = parse_json_output(cmd)
    print(json.dumps({**result, 'mode': 'main_bridge_lifecycle_terminate'}, ensure_ascii=False, indent=2))
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    with connect(paths['task_db']) as task_conn, connect(paths['bus_db']) as bus_conn:
        root, children = resolve_task_tree(task_conn, args.task_id)
        ids = [str(root['task_id'])] + [str(item['task_id']) for item in children]

        reset_rows = []
        for row in [root, *children]:
            task_id = str(row['task_id'])
            old_status = str(row.get('status', 'queued'))
            if old_status in {'failed', 'timed_out', 'blocked', 'cancelled'}:
                task_conn.execute('UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?', ('queued', now_iso(), task_id))
                task_conn.execute(
                    '''
                    INSERT INTO task_events (task_id, state, progress, note, blockers_json, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (task_id, 'queued', 0.0, f'retry by {args.actor}: {args.reason}', '[]', args.actor, now_iso()),
                )
                reset_rows.append({'task_id': task_id, 'from': old_status, 'to': 'queued'})

        placeholders = ','.join('?' for _ in ids)
        bus_rows = bus_conn.execute(
            f"SELECT message_id, queue_status FROM bus_messages WHERE task_id IN ({placeholders}) AND message_type = 'TASK'",
            ids,
        ).fetchall()
        retried_messages = []
        for row in bus_rows:
            if str(row['queue_status']) in {'dead_letter', 'retry_wait'}:
                bus_conn.execute(
                    'UPDATE bus_messages SET queue_status = ?, available_at = ?, last_error = ? WHERE message_id = ?',
                    ('queued', now_iso(), '', row['message_id']),
                )
                retried_messages.append(str(row['message_id']))

        root_status = update_root_status(task_conn, str(root['task_id']))
        task_conn.commit()
        bus_conn.commit()

    payload = {
        'mode': 'main_bridge_lifecycle_retry',
        'runtime': args.runtime,
        'task_id': args.task_id,
        'root_task_id': str(root['task_id']),
        'reason': args.reason,
        'actor': args.actor,
        'reset_tasks': reset_rows,
        'retried_messages': retried_messages,
        'root_status': root_status,
        'updated_at': now_iso(),
    }
    record = paths['runs_dir'] / f"retry-{payload['updated_at'].replace(':', '-')}-{str(root['task_id'])}.json"
    write_json(record, payload)
    append_jsonl(paths['audit_dir'] / 'lifecycle-audit.jsonl', {
        'event': 'retry',
        'runtime': args.runtime,
        'task_id': str(root['task_id']),
        'record': str(record),
        'created_at': now_iso(),
    })
    payload['record'] = str(record)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    with connect(paths['task_db']) as task_conn:
        if args.task_id:
            root, children = resolve_task_tree(task_conn, args.task_id)
            payload = {
                'mode': 'main_bridge_lifecycle_status',
                'runtime': args.runtime,
                'root_task': root,
                'subtasks': children,
                'subtask_status_counts': {},
                'generated_at': now_iso(),
            }
            counts: dict[str, int] = {}
            for item in children:
                key = str(item.get('status', 'unknown'))
                counts[key] = counts.get(key, 0) + 1
            payload['subtask_status_counts'] = counts
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0

        rows = task_conn.execute(
            '''
            SELECT status, COUNT(1) AS count
            FROM tasks
            GROUP BY status
            ORDER BY status
            '''
        ).fetchall()
        roots = task_conn.execute(
            '''
            SELECT task_id, title, status, priority, updated_at
            FROM tasks
            WHERE parent_task_id IS NULL
            ORDER BY updated_at DESC
            LIMIT ?
            ''',
            (args.limit,),
        ).fetchall()

    payload = {
        'mode': 'main_bridge_lifecycle_status',
        'runtime': args.runtime,
        'status_counts': {str(row['status']): int(row['count']) for row in rows},
        'latest_root_tasks': [dict(row) for row in roots],
        'generated_at': now_iso(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_timeout_scan(args: argparse.Namespace) -> int:
    cmd = [
        'python3', BRIDGE_TIMEOUT_WATCHER,
        '--runtime', args.runtime,
        '--timeout-minutes', str(args.timeout_minutes),
        '--limit', str(args.limit),
        '--actor', args.actor,
    ]
    result = parse_json_output(cmd)
    print(json.dumps({**result, 'mode': 'main_bridge_lifecycle_timeout_scan'}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='unified lifecycle controller for main bridge trigger/run/terminate')
    parser.add_argument('--config', default=DEFAULT_GATE_CONFIG)
    sub = parser.add_subparsers(dest='cmd', required=True)

    p_trigger = sub.add_parser('trigger')
    p_trigger.add_argument('--runtime', default='live')
    p_trigger.add_argument('--title', required=True)
    p_trigger.add_argument('--goal', required=True)
    p_trigger.add_argument('--requested-by', default='main')
    p_trigger.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    p_trigger.add_argument('--source-channel', default='manual')
    p_trigger.add_argument('--source-message-id', default='')
    p_trigger.add_argument('--source-session-id', default='')
    p_trigger.add_argument('--decision-note', default='main lifecycle trigger')
    p_trigger.add_argument('--bridge-reason', default='unified lifecycle entry')
    p_trigger.add_argument('--task-type', default='report.cross_functional')
    p_trigger.set_defaults(func=cmd_trigger)

    p_run = sub.add_parser('run')
    p_run.add_argument('--runtime', default='live')
    p_run.add_argument('--task-id', default='')
    p_run.add_argument('--max-tasks', type=int, default=20)
    p_run.add_argument('--agent', action='append', default=[])
    p_run.add_argument('--lock-ttl-seconds', type=int, default=900)
    p_run.add_argument('--actor', default='main-bridge-lifecycle')
    p_run.add_argument('--skip-memory-refresh', action='store_true')
    p_run.set_defaults(func=cmd_run)

    p_term = sub.add_parser('terminate')
    p_term.add_argument('--runtime', default='live')
    p_term.add_argument('--task-id', required=True)
    p_term.add_argument('--scope', default='tree', choices=['tree', 'single'])
    p_term.add_argument('--reason', default='manual lifecycle terminate')
    p_term.add_argument('--actor', default='main')
    p_term.add_argument('--force', action='store_true')
    p_term.set_defaults(func=cmd_terminate)

    p_retry = sub.add_parser('retry')
    p_retry.add_argument('--runtime', default='live')
    p_retry.add_argument('--task-id', required=True)
    p_retry.add_argument('--reason', default='manual lifecycle retry')
    p_retry.add_argument('--actor', default='main')
    p_retry.set_defaults(func=cmd_retry)

    p_status = sub.add_parser('status')
    p_status.add_argument('--runtime', default='live')
    p_status.add_argument('--task-id', default='')
    p_status.add_argument('--limit', type=int, default=20)
    p_status.set_defaults(func=cmd_status)

    p_timeout = sub.add_parser('timeout-scan')
    p_timeout.add_argument('--runtime', default='live')
    p_timeout.add_argument('--timeout-minutes', type=int, default=30)
    p_timeout.add_argument('--limit', type=int, default=200)
    p_timeout.add_argument('--actor', default='main-bridge-timeout-watcher')
    p_timeout.set_defaults(func=cmd_timeout_scan)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
