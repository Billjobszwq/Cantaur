#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import timedelta

from bridge_lifecycle_common import (
    append_jsonl,
    connect,
    ensure_lifecycle_dirs,
    now_iso,
    parse_iso,
    runtime_paths,
    update_root_status,
    write_json,
)


def find_overdue(conn, timeout_minutes: int, limit: int) -> list[dict]:
    now = parse_iso(now_iso())
    rows = conn.execute(
        '''
        SELECT task_id, parent_task_id, owner_agent, status, updated_at
        FROM tasks
        WHERE parent_task_id IS NOT NULL
          AND status IN ('queued', 'claimed', 'in_progress')
        ORDER BY updated_at ASC
        LIMIT ?
        ''',
        (limit,),
    ).fetchall()
    overdue = []
    for row in rows:
        updated = str(row['updated_at'] or '')
        if not updated:
            continue
        try:
            updated_at = parse_iso(updated)
        except Exception:
            continue
        age = now - updated_at
        if age >= timedelta(minutes=timeout_minutes):
            item = dict(row)
            item['age_minutes'] = round(age.total_seconds() / 60.0, 2)
            overdue.append(item)
    return overdue


def timeout_task(conn, task_id: str, actor: str, reason: str) -> None:
    ts = now_iso()
    conn.execute('UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?', ('timed_out', ts, task_id))
    conn.execute(
        '''
        INSERT INTO task_events (task_id, state, progress, note, blockers_json, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (task_id, 'timed_out', 1.0, reason, json.dumps([reason], ensure_ascii=False), actor, ts),
    )


def expire_lock(conn, task_id: str) -> int:
    cur = conn.execute('DELETE FROM task_locks WHERE task_id = ?', (task_id,))
    return int(cur.rowcount)


def mark_bus_timed_out(conn, task_id: str, actor: str) -> int:
    ts = now_iso()
    rows = conn.execute(
        '''
        SELECT message_id, queue_status
        FROM bus_messages
        WHERE task_id = ? AND queue_status IN ('queued', 'retry_wait')
        ''',
        (task_id,),
    ).fetchall()
    for row in rows:
        conn.execute(
            'UPDATE bus_messages SET queue_status = ?, available_at = ?, last_error = ? WHERE message_id = ?',
            ('dead_letter', ts, 'timed out by watcher', row['message_id']),
        )
        conn.execute(
            '''
            INSERT INTO bus_audit (event_type, message_id, task_id, actor, note, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            ('timed_out', row['message_id'], task_id, actor, 'message dead-lettered due to timeout', '{}', ts),
        )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description='timeout watcher for bridge subtasks')
    parser.add_argument('--runtime', default='live')
    parser.add_argument('--timeout-minutes', type=int, default=30)
    parser.add_argument('--limit', type=int, default=200)
    parser.add_argument('--actor', default='main-bridge-timeout-watcher')
    args = parser.parse_args()

    paths = runtime_paths(args.runtime)
    ensure_lifecycle_dirs(paths)

    with connect(paths['task_db']) as task_conn:
        overdue = find_overdue(task_conn, args.timeout_minutes, args.limit)

    timed_out = []
    for item in overdue:
        task_id = str(item['task_id'])
        reason = f"timeout watcher: task exceeded {args.timeout_minutes} minutes (age={item['age_minutes']}m)"
        with connect(paths['task_db']) as task_conn, connect(paths['bus_db']) as bus_conn:
            timeout_task(task_conn, task_id, args.actor, reason)
            released = expire_lock(task_conn, task_id)
            bus_marked = mark_bus_timed_out(bus_conn, task_id, args.actor)
            parent = str(item.get('parent_task_id') or '')
            if parent:
                update_root_status(task_conn, parent)
            task_conn.commit()
            bus_conn.commit()
        timed_out.append({'task_id': task_id, 'released_lock_count': released, 'timedout_message_count': bus_marked, 'reason': reason})

    payload = {
        'mode': 'bridge_timeout_watcher',
        'runtime': args.runtime,
        'timeout_minutes': args.timeout_minutes,
        'scanned': len(overdue),
        'timed_out_count': len(timed_out),
        'timed_out': timed_out,
        'executed_at': now_iso(),
    }
    record = paths['timeouts_dir'] / f"timeout-scan-{payload['executed_at'].replace(':', '-')}.json"
    write_json(record, payload)
    append_jsonl(paths['audit_dir'] / 'lifecycle-audit.jsonl', {
        'event': 'timeout_scan',
        'runtime': args.runtime,
        'record': str(record),
        'timed_out_count': len(timed_out),
        'created_at': now_iso(),
    })
    payload['record'] = str(record)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
