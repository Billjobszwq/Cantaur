#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import timedelta

from bridge_lifecycle_common import (
    append_jsonl,
    connect,
    ensure_lifecycle_dirs,
    now_iso,
    parse_iso,
    resolve_task_tree,
    runtime_paths,
    update_root_status,
    write_json,
)

SHADOW_WORKER = str(Path.home() / ".qyclaw/workspace/integration/qy_code/live-link/scripts/shadow_worker_writeback.py")


def acquire_lock(conn, task_id: str, locker: str, ttl_seconds: int) -> bool:
    now = parse_iso(now_iso())
    expires = now + timedelta(seconds=ttl_seconds)
    conn.execute('DELETE FROM task_locks WHERE task_id = ? AND expires_at <= ?', (task_id, now.isoformat(timespec='seconds')))
    try:
        conn.execute(
            'INSERT INTO task_locks (task_id, locked_by, locked_at, expires_at) VALUES (?, ?, ?, ?)',
            (task_id, locker, now.isoformat(timespec='seconds'), expires.isoformat(timespec='seconds')),
        )
        return True
    except Exception:
        return False


def release_lock(conn, task_id: str, locker: str) -> None:
    conn.execute('DELETE FROM task_locks WHERE task_id = ? AND locked_by = ?', (task_id, locker))


def pick_tasks(conn, root_task_id: str | None, max_tasks: int, agents: set[str]) -> list[dict]:
    params: list[object] = []
    clauses = ["status IN ('queued', 'claimed', 'in_progress')", "parent_task_id IS NOT NULL"]
    if root_task_id:
        clauses.append('parent_task_id = ?')
        params.append(root_task_id)
    if agents:
        placeholders = ','.join('?' for _ in agents)
        clauses.append(f'owner_agent IN ({placeholders})')
        params.extend(sorted(agents))
    where_clause = ' AND '.join(clauses)
    query = f'''
        SELECT task_id, parent_task_id, owner_agent, status, updated_at, priority
        FROM tasks
        WHERE {where_clause}
        ORDER BY updated_at ASC, task_id ASC
        LIMIT ?
    '''
    params.append(max_tasks)
    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def mark_failed(conn, task_id: str, actor: str, reason: str) -> None:
    ts = now_iso()
    conn.execute('UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?', ('failed', ts, task_id))
    conn.execute(
        '''
        INSERT INTO task_events (task_id, state, progress, note, blockers_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (task_id, 'failed', 1.0, f'executor failed: {reason}', json.dumps([reason], ensure_ascii=False), actor, ts),
    )


def run_worker(runtime: str, task_id: str, agent: str, skip_memory_refresh: bool) -> dict:
    cmd = [
        'python3', SHADOW_WORKER,
        '--runtime', runtime,
        '--agent', agent,
        '--task-id', task_id,
    ]
    if skip_memory_refresh:
        cmd.append('--skip-memory-refresh')
    proc = subprocess.run(cmd, capture_output=True, text=True)
    stdout = (proc.stdout or '').strip()
    stderr = (proc.stderr or '').strip()
    payload = {
        'exit_code': proc.returncode,
        'stdout_tail': '\n'.join(stdout.splitlines()[-40:]) if stdout else '',
        'stderr_tail': '\n'.join(stderr.splitlines()[-40:]) if stderr else '',
    }
    if proc.returncode == 0 and stdout:
        try:
            payload['result'] = json.loads(stdout)
        except json.JSONDecodeError:
            payload['result'] = None
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description='execute queued bridge subtasks with lock/timeout-safe behavior')
    parser.add_argument('--runtime', default='live')
    parser.add_argument('--task-id', default='')
    parser.add_argument('--max-tasks', type=int, default=20)
    parser.add_argument('--agent', action='append', default=[])
    parser.add_argument('--lock-ttl-seconds', type=int, default=900)
    parser.add_argument('--actor', default='main-bridge-executor')
    parser.add_argument('--skip-memory-refresh', action='store_true')
    args = parser.parse_args()

    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    agents = {item.strip() for item in args.agent if item.strip()}

    with connect(paths['task_db']) as task_conn:
        if args.task_id:
            root, _ = resolve_task_tree(task_conn, args.task_id)
            root_task_id = str(root['task_id'])
        else:
            root_task_id = None
        tasks = pick_tasks(task_conn, root_task_id, args.max_tasks, agents)

    processed = []
    failed = []
    skipped = []

    for task in tasks:
        task_id = str(task['task_id'])
        owner = str(task['owner_agent'])

        with connect(paths['task_db']) as task_conn:
            if not acquire_lock(task_conn, task_id, args.actor, args.lock_ttl_seconds):
                task_conn.commit()
                skipped.append({'task_id': task_id, 'reason': 'lock_not_acquired'})
                continue
            task_conn.commit()

        worker = run_worker(args.runtime, task_id, owner, args.skip_memory_refresh)

        with connect(paths['task_db']) as task_conn:
            if worker['exit_code'] != 0:
                reason = worker['stderr_tail'] or worker['stdout_tail'] or 'worker_failed'
                mark_failed(task_conn, task_id, args.actor, reason[:400])
                failed.append({'task_id': task_id, 'agent': owner, 'reason': reason[:400]})
            try:
                row = task_conn.execute('SELECT parent_task_id FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
                if row and row['parent_task_id']:
                    update_root_status(task_conn, str(row['parent_task_id']))
            finally:
                release_lock(task_conn, task_id, args.actor)
            task_conn.commit()

        processed.append({'task_id': task_id, 'agent': owner, 'worker': worker})

    summary = {
        'mode': 'bridge_executor',
        'runtime': args.runtime,
        'root_task_id': root_task_id,
        'picked': len(tasks),
        'processed': len(processed),
        'failed': len(failed),
        'skipped': len(skipped),
        'processed_items': processed,
        'failed_items': failed,
        'skipped_items': skipped,
        'executed_at': now_iso(),
    }

    record = paths['runs_dir'] / f"executor-{summary['executed_at'].replace(':', '-')}.json"
    write_json(record, summary)
    append_jsonl(paths['audit_dir'] / 'lifecycle-audit.jsonl', {
        'event': 'execute',
        'record': str(record),
        'runtime': args.runtime,
        'root_task_id': root_task_id,
        'created_at': now_iso(),
        'processed': len(processed),
        'failed': len(failed),
    })

    summary['record'] = str(record)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
