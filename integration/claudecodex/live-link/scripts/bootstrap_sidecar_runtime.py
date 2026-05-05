#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / 'state'
BUS_DIR = ROOT / 'bus'
RUNTIME_BASE = ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def ensure_runtime(name: str) -> Path:
    runtime_root = RUNTIME_BASE / name
    (runtime_root / 'bus-runtime').mkdir(parents=True, exist_ok=True)
    for rel in ['inbox', 'outbox', 'dead-letter', 'audit', 'blackboard']:
        (runtime_root / 'bus-runtime' / rel).mkdir(parents=True, exist_ok=True)
    (runtime_root / 'runs').mkdir(parents=True, exist_ok=True)
    (runtime_root / 'memory-fusion').mkdir(parents=True, exist_ok=True)
    return runtime_root


def init_sqlite(db_path: Path, schema_path: Path) -> None:
    schema = schema_path.read_text(encoding='utf-8')
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(schema)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    runtime_name = sys.argv[1] if len(sys.argv) > 1 else 'shadow-live'
    runtime_root = ensure_runtime(runtime_name)
    task_db = runtime_root / 'task-board.db'
    bus_db = runtime_root / 'bus.db'
    init_sqlite(task_db, STATE_DIR / 'task-board.schema.sql')
    init_sqlite(bus_db, BUS_DIR / 'bus.schema.sql')
    metadata = {
        'runtime_name': runtime_name,
        'runtime_root': str(runtime_root),
        'task_db': str(task_db),
        'bus_db': str(bus_db),
        'bus_runtime': str(runtime_root / 'bus-runtime'),
        'created_at': now_iso(),
        'mode': 'shadow',
        'production_safe': True,
    }
    (runtime_root / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
