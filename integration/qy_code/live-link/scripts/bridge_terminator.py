#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bridge_lifecycle_common import (
    append_jsonl,
    connect,
    ensure_lifecycle_dirs,
    now_iso,
    resolve_task_tree,
    runtime_paths,
    task_scope_ids,
    update_root_status,
    write_json,
)


IMMUTABLE = {'completed'}


def mark_cancelled(conn, task_id: str, actor: str, reason: str, force: bool) -> tuple[bool, str]:
    row = conn.execute('SELECT status FROM tasks WHERE task_id = ?', (task_id,)).fetchone()
    if row is None:
        return False, 'not_found'
    current = str(row['status'])
    if current in IMMUTABLE and not force:
        return False, f'skip_immutable:{current}'
    ts = now_iso()
    conn.execute(
        'UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?',
        ('cancelled', ts, task_id),
    )
    conn.execute(
        '''
        INSERT INTO task_events (task_id, state, progress, note, blockers_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (task_id, 'cancelled', 1.0, f'terminated by {actor}: {reason}', json.dumps([reason], ensure_ascii=False), actor, ts),
    )
    return True, current


def mark_bus_messages_terminated(conn, task_ids: list[str], actor: str, reason: str) -> int:
    if not task_ids:
        return 0
    placeholders = ','.join('?' for _ in task_ids)
    rows = conn.execute(
        f'''
        SELECT message_id, task_id, queue_status
        FROM bus_messages
        WHERE task_id IN ({placeholders}) AND queue_status IN ('queued', 'retry_wait')
        ''',
        task_ids,
    ).fetchall()
    ts = now_iso()
    for row in rows:
        conn.execute(
            'UPDATE bus_messages SET queue_status = ?, available_at = ?, last_error = ? WHERE message_id = ?',
            ('dead_letter', ts, f'terminated by {actor}: {reason}', row['message_id']),
        )
        conn.execute(
            '''
            INSERT INTO bus_audit (event_type, message_id, task_id, actor, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                'terminate',
                row['message_id'],
                row['task_id'],
                actor,
                'message moved to dead_letter by terminator',
                json.dumps({'reason': reason, 'old_status': row['queue_status']}, ensure_ascii=False),
                ts,
            ),
        )
    return len(rows)


def release_locks(conn, task_ids: list[str]) -> int:
    if not task_ids:
        return 0
    placeholders = ','.join('?' for _ in task_ids)
    cur = conn.execute(f'DELETE FROM task_locks WHERE task_id IN ({placeholders})', task_ids)
    return int(cur.rowcount)


def main() -> int:
    parser = argparse.ArgumentParser(description='terminate/cancel bridge task tree')
    parser.add_argument('--runtime', default='live')
    parser.add_argument('--task-id', required=True)
    parser.add_argument('--actor', default='main')
    parser.add_argument('--reason', default='manual terminate')
    parser.add_argument('--scope', default='tree', choices=['tree', 'single'])
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()

    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    with connect(paths['task_db']) as task_conn, connect(paths['bus_db']) as bus_conn:
        root, children = resolve_task_tree(task_conn, args.task_id)
        if args.scope == 'single':
            targets = [str(args.task_id)]
        else:
            targets = task_scope_ids(str(root['task_id']), children)

        cancelled = []
        skipped = []
        for tid in targets:
            changed, note = mark_cancelled(task_conn, tid, args.actor, args.reason, args.force)
            if changed:
                cancelled.append({'task_id': tid, 'previous_status': note})
            else:
                skipped.append({'task_id': tid, 'reason': note})

        root_status = update_root_status(task_conn, str(root['task_id']))
        task_conn.commit()

        terminated_messages = mark_bus_messages_terminated(bus_conn, targets, args.actor, args.reason)
        released_locks = release_locks(task_conn, targets)
        bus_conn.commit()
        task_conn.commit()

    payload = {
        'mode': 'bridge_terminator',
        'runtime': args.runtime,
        'task_id': args.task_id,
        'root_task_id': str(root['task_id']),
        'scope': args.scope,
        'actor': args.actor,
        'reason': args.reason,
        'cancelled_count': len(cancelled),
        'skipped_count': len(skipped),
        'terminated_message_count': terminated_messages,
        'released_lock_count': released_locks,
        'root_status': root_status,
        'cancelled': cancelled,
        'skipped': skipped,
        'terminated_at': now_iso(),
    }

    out_path = paths['terminations_dir'] / f"terminate-{str(root['task_id'])}-{payload['terminated_at'].replace(':', '-')}.json"
    write_json(out_path, payload)
    append_jsonl(paths['audit_dir'] / 'lifecycle-audit.jsonl', {
        'event': 'terminate',
        'task_id': str(root['task_id']),
        'record': str(out_path),
        'created_at': now_iso(),
    })

    payload['record'] = str(out_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
