#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
CONTROL_SCRIPT = INTEGRATION_ROOT / 'control' / 'scripts' / 'coordinator_cli.py'
MEMORY_FUSION_SCRIPT = INTEGRATION_ROOT / 'memory-fusion' / 'scripts' / 'memory_fusion_cli.py'
BOOTSTRAP_SCRIPT = INTEGRATION_ROOT / 'live-link' / 'scripts' / 'bootstrap_sidecar_runtime.py'
SYNC_SCRIPT = INTEGRATION_ROOT / 'live-link' / 'scripts' / 'sync_live_registries.py'
KNOWLEDGE_SCRIPT = QYCLAW_ROOT / 'workspace' / 'scripts' / 'knowledge_base.py'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


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


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def run_json(cmd: list[str]) -> dict:
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(proc.stdout)


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def compile_knowledge(memory_output_dir: Path, project_title: str) -> tuple[str | None, str | None]:
    if not memory_output_dir.exists():
        return None, 'memory output directory missing'
    try:
        payload = run_json(
            [
                'python3',
                str(KNOWLEDGE_SCRIPT),
                'compile-fusion',
                str(memory_output_dir),
                '--project',
                project_title,
            ]
        )
    except Exception as exc:
        return None, str(exc)
    return str(payload.get('page', '')) or None, None


def build_intake(args) -> dict:
    task_id = args.task_id or f"task-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{slug(args.title)[:24]}"
    trace_id = args.trace_id or f"trace-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    domains = [item.strip() for item in (args.required_domains or '').split(',') if item.strip()]
    agents = [item.strip() for item in (args.required_agents or '').split(',') if item.strip()]
    return {
        'task_id': task_id,
        'trace_id': trace_id,
        'task_type': args.task_type,
        'title': args.title,
        'goal': args.goal,
        'requested_by': args.requested_by,
        'priority': args.priority,
        'deadline': args.deadline,
        'constraints': args.constraints or [],
        'required_domains': domains,
        'required_agents': agents,
        'source_context': {
            'mode': 'fusion-live' if args.runtime == 'live' else 'shadow',
            'channel': args.source_channel,
            'message_id': args.source_message_id,
            'session_id': args.source_session_id,
            'created_at': now_iso(),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='dispatch a real task into formal fusion runtime')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--task-type', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--requested-by', default='user')
    parser.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--deadline', default='2099-12-31T23:59:59+08:00')
    parser.add_argument('--constraint', dest='constraints', action='append')
    parser.add_argument('--required-domains', default='')
    parser.add_argument('--required-agents', default='')
    parser.add_argument('--task-id')
    parser.add_argument('--trace-id')
    parser.add_argument('--source-channel', default='manual')
    parser.add_argument('--source-message-id', default='')
    parser.add_argument('--source-session-id', default='')
    parser.add_argument('--skip-sync', action='store_true')
    parser.add_argument('--skip-memory-fusion', action='store_true')
    args = parser.parse_args()

    run(['python3', str(BOOTSTRAP_SCRIPT), args.runtime])
    if not args.skip_sync:
        run(['python3', str(SYNC_SCRIPT)])

    runtime_root = RUNTIME_BASE / args.runtime
    task_db = runtime_root / 'task-board.db'
    bus_db = runtime_root / 'bus.db'
    bus_runtime = runtime_root / 'bus-runtime'
    intake = build_intake(args)
    runs_dir = runtime_root / 'runs'
    runs_dir.mkdir(parents=True, exist_ok=True)
    intake_path = runs_dir / f"{intake['task_id']}.intake.json"
    intake_path.write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding='utf-8')

    coordinator = load_module(CONTROL_SCRIPT, 'shadow_coordinator_cli')
    memory_fusion = load_module(MEMORY_FUSION_SCRIPT, 'shadow_memory_fusion_cli')
    plan = coordinator.dispatch(task_db, bus_db, bus_runtime, intake)
    plan_path = runs_dir / f"{intake['task_id']}.plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')

    source_context_payload = {
        'task_id': intake['task_id'],
        'entry_key': 'source_context',
        'entry_value': intake['source_context'],
        'updated_by': 'shadow_dispatch',
        'updated_at': now_iso(),
    }
    source_context_path = runs_dir / f"{intake['task_id']}.source_context.json"
    source_context_path.write_text(json.dumps(source_context_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    bus = load_module(INTEGRATION_ROOT / 'bus' / 'scripts' / 'bus_cli.py', 'shadow_bus_cli')
    bus.blackboard_put(bus_db, bus_runtime, source_context_path)

    memory_output = None
    knowledge_output = None
    knowledge_error = None
    if not args.skip_memory_fusion:
        out_dir = memory_fusion.summarize(task_db, bus_db, intake['task_id'])
        runtime_memory_dir = runtime_root / 'memory-fusion' / intake['task_id']
        if runtime_memory_dir.exists():
            shutil.rmtree(runtime_memory_dir)
        shutil.copytree(out_dir, runtime_memory_dir)
        memory_output = str(runtime_memory_dir)
        knowledge_output, knowledge_error = compile_knowledge(runtime_memory_dir, intake['title'])

    result = {
        'runtime': args.runtime,
        'task_id': intake['task_id'],
        'trace_id': intake['trace_id'],
        'intake_path': str(intake_path),
        'plan_path': str(plan_path),
        'task_db': str(task_db),
        'bus_db': str(bus_db),
        'bus_runtime': str(bus_runtime),
        'memory_output': memory_output,
        'knowledge_output': knowledge_output,
        'knowledge_error': knowledge_error,
        'selected_agents': plan['selected_agents'],
        'subtask_count': len(plan['subtasks']),
        'mode': 'fusion-live' if args.runtime == 'live' else 'shadow',
    }
    result_path = runs_dir / f"{intake['task_id']}.result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
