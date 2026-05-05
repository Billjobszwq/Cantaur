#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

OPENCLAW_ROOT = Path.home() / ".openclaw"
INTEGRATION_ROOT = OPENCLAW_ROOT / 'workspace' / 'integration' / 'claudecodex'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8'))


def resolve_bundle(runtime_root: Path, task_id: str) -> Path:
    return runtime_root / 'bridge' / 'consumption' / task_id / 'main-bridge-result-bundle.json'


def extract_top_lines(text: str, limit: int = 6) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[:limit]


def build_absorption(bundle: dict[str, Any]) -> dict[str, Any]:
    root_path = Path(bundle['root_synthesis']) if bundle.get('root_synthesis') else Path()
    memory_path = Path(bundle['memory_output']) if bundle.get('memory_output') else Path()
    comparison_path = Path(bundle['comparison_note_path']) if bundle.get('comparison_note_path') else Path()

    root_ready = root_path.exists()
    memory_ready = memory_path.exists()
    comparison_ready = comparison_path.exists()
    accepted = bool(bundle.get('accepted'))
    status = bundle.get('status', '')

    absorb_as_auxiliary = accepted and status in {'shadow_started', 'shadow_synthesized'} and root_ready
    recommend_reference = absorb_as_auxiliary
    recommend_direct_replace = False
    needs_manual_compare = True

    summary_points = []
    if absorb_as_auxiliary:
        summary_points.append('本轮 bridge 结果适合作为 main 的辅助输入。')
    else:
        summary_points.append('本轮 bridge 结果暂不建议进入 main 的吸收链。')
    if needs_manual_compare:
        summary_points.append('仍需人工比较现网主链输出与 sidecar 综合稿。')
    if not recommend_direct_replace:
        summary_points.append('当前不建议用 sidecar 结果直接替代用户最终回复。')

    operator_actions = [
        '先阅读 comparison note，确认是否值得吸收。',
        '再阅读 root synthesis，提取可用判断与结构。',
        '必要时进入 memory package 深挖细节。',
        '最终仍由 main 决定如何引用，不直接照搬 sidecar。',
    ]

    return {
        'task_id': bundle['task_id'],
        'trace_id': bundle['trace_id'],
        'title': bundle['title'],
        'bridge_mode': bundle['bridge_mode'],
        'accepted': accepted,
        'status': status,
        'selected_agents': bundle.get('selected_agents', []),
        'decision_note': bundle.get('decision_note', ''),
        'bridge_reason': bundle.get('bridge_reason', ''),
        'root_synthesis': bundle.get('root_synthesis', ''),
        'memory_output': bundle.get('memory_output', ''),
        'comparison_note_path': bundle.get('comparison_note_path', ''),
        'artifacts_ready': {
            'root_synthesis_ready': root_ready,
            'memory_output_ready': memory_ready,
            'comparison_note_ready': comparison_ready,
        },
        'absorption_decision': {
            'absorb_as_auxiliary': absorb_as_auxiliary,
            'recommend_reference': recommend_reference,
            'recommend_direct_replace': recommend_direct_replace,
            'needs_manual_compare': needs_manual_compare,
        },
        'summary_points': summary_points,
        'operator_actions': operator_actions,
        'comparison_note_glance': extract_top_lines(bundle.get('comparison_note_excerpt', ''), 8),
        'root_synthesis_glance': extract_top_lines(bundle.get('root_synthesis_excerpt', ''), 10),
        'generated_at': now_iso(),
        'mode': 'main_bridge_absorption_bundle',
    }


def render_markdown(absorb: dict[str, Any]) -> str:
    lines = [
        f"# {absorb['title']} - main bridge absorption bundle",
        '',
        f"- `task_id`: `{absorb['task_id']}`",
        f"- `trace_id`: `{absorb['trace_id']}`",
        f"- `status`: `{absorb['status']}`",
        f"- `accepted`: `{str(absorb['accepted']).lower()}`",
        f"- `generated_at`: `{absorb['generated_at']}`",
        '',
        '## Absorption Decision',
        f"- `absorb_as_auxiliary`: `{str(absorb['absorption_decision']['absorb_as_auxiliary']).lower()}`",
        f"- `recommend_reference`: `{str(absorb['absorption_decision']['recommend_reference']).lower()}`",
        f"- `recommend_direct_replace`: `{str(absorb['absorption_decision']['recommend_direct_replace']).lower()}`",
        f"- `needs_manual_compare`: `{str(absorb['absorption_decision']['needs_manual_compare']).lower()}`",
        '',
        '## Summary Points',
    ]
    lines.extend([f'- {item}' for item in absorb['summary_points']])
    lines.extend(['', '## Operator Actions'])
    lines.extend([f'- {item}' for item in absorb['operator_actions']])
    lines.extend(['', '## Selected Agents'])
    lines.extend([f"- `{agent}`" for agent in absorb['selected_agents']])
    lines.extend([
        '',
        '## Artifact Readiness',
        f"- `root_synthesis_ready`: `{str(absorb['artifacts_ready']['root_synthesis_ready']).lower()}`",
        f"- `memory_output_ready`: `{str(absorb['artifacts_ready']['memory_output_ready']).lower()}`",
        f"- `comparison_note_ready`: `{str(absorb['artifacts_ready']['comparison_note_ready']).lower()}`",
        '',
        '## Key Paths',
        f"- `root_synthesis`: `{absorb['root_synthesis']}`",
        f"- `memory_output`: `{absorb['memory_output']}`",
        f"- `comparison_note_path`: `{absorb['comparison_note_path']}`",
        '',
        '## Comparison Glance',
    ])
    lines.extend([f'- {item}' for item in absorb['comparison_note_glance']])
    lines.extend(['', '## Root Synthesis Glance'])
    lines.extend([f'- {item}' for item in absorb['root_synthesis_glance']])
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='build an absorption-ready bundle for main from bridge result bundle')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--task-id', required=True)
    args = parser.parse_args()

    runtime_root = RUNTIME_BASE / args.runtime
    bundle_path = resolve_bundle(runtime_root, args.task_id)
    bundle = read_json(bundle_path)
    absorb = build_absorption(bundle)

    out_dir = runtime_root / 'bridge' / 'absorption' / args.task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / 'main-bridge-absorption-bundle.json'
    md_path = out_dir / 'main-bridge-absorption-bundle.md'
    json_path.write_text(json.dumps(absorb, ensure_ascii=False, indent=2), encoding='utf-8')
    md_path.write_text(render_markdown(absorb), encoding='utf-8')

    result = {
        'runtime': args.runtime,
        'task_id': args.task_id,
        'absorption_json': str(json_path),
        'absorption_md': str(md_path),
        'absorb_as_auxiliary': absorb['absorption_decision']['absorb_as_auxiliary'],
        'recommend_reference': absorb['absorption_decision']['recommend_reference'],
        'recommend_direct_replace': absorb['absorption_decision']['recommend_direct_replace'],
        'mode': absorb['mode'],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
