#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def read_text(path: Path) -> str:
    return path.read_text(encoding='utf-8') if path.exists() else ''


def resolve_from_task_id(runtime_root: Path, task_id: str) -> tuple[Path, Path, Path]:
    bridge_root = runtime_root / 'bridge'
    trigger = bridge_root / 'triggers' / f'{task_id}.main-trigger.json'
    response = bridge_root / 'responses' / f'{task_id}.bridge-response.json'
    comparison = bridge_root / 'comparisons' / f'{task_id}.comparison-note.md'
    return trigger, response, comparison


def render_markdown(bundle: dict[str, Any]) -> str:
    lines = [
        f"# {bundle['title']} - main bridge result bundle",
        '',
        f"- `task_id`: `{bundle['task_id']}`",
        f"- `trace_id`: `{bundle['trace_id']}`",
        f"- `bridge_mode`: `{bundle['bridge_mode']}`",
        f"- `status`: `{bundle['status']}`",
        f"- `accepted`: `{str(bundle['accepted']).lower()}`",
        f"- `generated_at`: `{bundle['generated_at']}`",
        '',
        '## Goal',
        bundle['goal'],
        '',
        '## Main Decision',
        f"- `decision_note`: {bundle['decision_note']}",
        f"- `bridge_reason`: {bundle['bridge_reason']}",
        '',
        '## Selected Agents',
    ]
    for agent in bundle.get('selected_agents', []):
        lines.append(f'- `{agent}`')
    lines.extend([
        '',
        '## Key Outputs',
        f"- `root_synthesis`: `{bundle.get('root_synthesis', '')}`",
        f"- `memory_output`: `{bundle.get('memory_output', '')}`",
        f"- `comparison_note`: `{bundle.get('comparison_note_path', '')}`",
        '',
        '## Main Consumption Guidance',
        '- 先看 `comparison_note`，确认这轮 bridge 是否值得吸收。',
        '- 再看 `root_synthesis`，获取 sidecar 的统一综合稿。',
        '- 如需深挖，再进入 `memory_output` 查看更细的记忆包。',
        '',
    ])
    if bundle.get('comparison_note_excerpt'):
        lines.extend(['## Comparison Note Excerpt', bundle['comparison_note_excerpt'], ''])
    if bundle.get('root_synthesis_excerpt'):
        lines.extend(['## Root Synthesis Excerpt', bundle['root_synthesis_excerpt'], ''])
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='collect main bridge results into a consumption bundle')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--task-id')
    parser.add_argument('--trigger-record')
    parser.add_argument('--response-record')
    parser.add_argument('--comparison-note')
    args = parser.parse_args()

    if not any([args.task_id, args.trigger_record, args.response_record]):
        raise SystemExit('need --task-id or --trigger-record or --response-record')

    runtime_root = RUNTIME_BASE / args.runtime
    if args.task_id:
        trigger_path, response_path, comparison_path = resolve_from_task_id(runtime_root, args.task_id)
    else:
        trigger_path = Path(args.trigger_record) if args.trigger_record else None
        response_path = Path(args.response_record) if args.response_record else None
        comparison_path = Path(args.comparison_note) if args.comparison_note else None
        if trigger_path and not response_path:
            trigger_json = read_json(trigger_path)
            response_path = Path(trigger_json['bridge_response_record'])
            comparison_path = Path(trigger_json['comparison_note'])
        elif response_path and not trigger_path:
            response_json = read_json(response_path)
            task_id = response_json['task_id']
            trigger_path, _, auto_comparison_path = resolve_from_task_id(runtime_root, task_id)
            if comparison_path is None:
                comparison_path = auto_comparison_path

    if trigger_path is None or response_path is None:
        raise SystemExit('unable to resolve trigger/response records')

    trigger = read_json(trigger_path)
    response = read_json(response_path)
    comparison_path = comparison_path or Path(trigger['comparison_note'])

    root_synthesis_path = Path(response['root_synthesis']) if response.get('root_synthesis') else Path()
    memory_output_path = Path(response['memory_output']) if response.get('memory_output') else Path()

    comparison_text = read_text(comparison_path)
    synthesis_text = read_text(root_synthesis_path) if root_synthesis_path else ''

    bundle = {
        'task_id': response['task_id'],
        'trace_id': response['trace_id'],
        'title': trigger['title'],
        'goal': trigger['goal'],
        'bridge_mode': response['bridge_mode'],
        'accepted': response['accepted'],
        'status': response['status'],
        'selected_agents': response.get('selected_agents', []),
        'decision_note': trigger.get('decision_note', ''),
        'bridge_reason': trigger.get('bridge_reason', ''),
        'root_synthesis': str(root_synthesis_path) if root_synthesis_path else '',
        'memory_output': str(memory_output_path) if memory_output_path else '',
        'comparison_note_path': str(comparison_path),
        'trigger_record': str(trigger_path),
        'response_record': str(response_path),
        'generated_at': now_iso(),
        'comparison_note_excerpt': comparison_text[:1200].strip(),
        'root_synthesis_excerpt': synthesis_text[:1500].strip(),
        'mode': 'main_bridge_result_bundle',
    }

    consumption_root = runtime_root / 'bridge' / 'consumption' / bundle['task_id']
    consumption_root.mkdir(parents=True, exist_ok=True)
    bundle_json = consumption_root / 'main-bridge-result-bundle.json'
    bundle_md = consumption_root / 'main-bridge-result-bundle.md'
    bundle_json.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding='utf-8')
    bundle_md.write_text(render_markdown(bundle), encoding='utf-8')

    result = {
        'runtime': args.runtime,
        'task_id': bundle['task_id'],
        'bundle_json': str(bundle_json),
        'bundle_md': str(bundle_md),
        'selected_agents': bundle['selected_agents'],
        'status': bundle['status'],
        'mode': bundle['mode'],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
