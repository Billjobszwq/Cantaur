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
CLASSIFIER = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_classifier.py'
TRIGGER = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_trigger.py'
COLLECT = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_collect.py'
ABSORB = LIVE_LINK_ROOT / 'scripts' / 'main_bridge_absorb.py'
DEFAULT_REQUIRED_DOMAINS = 'research,ops,legal,finance,content'


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
        raise RuntimeError('workflow subprocess returned empty stdout')
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


def ensure_workflow_dir(runtime_root: Path) -> Path:
    path = runtime_root / 'bridge' / 'workflow'
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        f"# {summary['title']} - main bridge workflow summary",
        '',
        f"- `task_id`: `{summary['task_id']}`",
        f"- `trace_id`: `{summary['trace_id']}`",
        f"- `workflow_mode`: `{summary['workflow_mode']}`",
        f"- `status`: `{summary['status']}`",
        f"- `generated_at`: `{summary['generated_at']}`",
        '',
        '## Summary',
    ]
    lines.extend([f"- {item}" for item in summary['summary_points']])
    lines.extend(['', '## Key Records'])
    for key in ['classifier_record', 'main_trigger_record', 'bridge_response_record', 'collect_bundle', 'absorption_bundle']:
        value = summary.get(key)
        if value:
            lines.append(f"- `{key}`: `{value}`")
    lines.extend(['', '## Next Actions'])
    lines.extend([f'- {item}' for item in summary['next_actions']])
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='manual-switch main workflow wrapper for bridge chain')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--workflow-mode', default='assist', choices=['off', 'assist', 'enforce'])
    parser.add_argument('--execution-mode', default='simulate', choices=['start-only', 'simulate'])
    parser.add_argument('--title', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--requested-by', default='main')
    parser.add_argument('--priority', default='high', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--deadline', default=default_deadline())
    parser.add_argument('--source-channel', default='main-workflow')
    parser.add_argument('--source-message-id', default='')
    parser.add_argument('--source-session-id', default='')
    parser.add_argument('--required-domains', default=DEFAULT_REQUIRED_DOMAINS)
    parser.add_argument('--required-agents', default='')
    parser.add_argument('--constraint', dest='constraints', action='append')
    parser.add_argument('--decision-note', default='main 判断该任务需要桥接辅助。')
    parser.add_argument('--bridge-reason', default='当前任务适合走正式融合协作。')
    parser.add_argument('--risk-level', default='medium', choices=['low', 'medium', 'high', 'critical'])
    parser.add_argument('--task-id')
    parser.add_argument('--trace-id')
    parser.add_argument('--skip-sync', action='store_true')
    args = parser.parse_args()

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    task_id = args.task_id or f"task-{stamp}-{slug(args.title)[:24]}"
    trace_id = args.trace_id or f"trace-{stamp}"
    runtime_root = RUNTIME_BASE / args.runtime
    workflow_dir = ensure_workflow_dir(runtime_root)

    summary: dict[str, Any] = {
        'task_id': task_id,
        'trace_id': trace_id,
        'title': args.title,
        'goal': args.goal,
        'workflow_mode': args.workflow_mode,
        'generated_at': now_iso(),
        'status': 'started',
        'summary_points': [],
        'next_actions': [],
    }

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
    summary['classifier_record'] = classifier_result.get('classifier_record')
    summary['classifier_allow_bridge'] = classifier_result.get('allow_bridge')
    summary['classifier_reasons'] = classifier_result.get('reasons', [])

    if args.workflow_mode == 'off':
        summary['status'] = 'classifier_only'
        summary['summary_points'] = [
            '当前工作流模式为 off，只运行 classifier。',
            f"classifier_allow_bridge={classifier_result.get('allow_bridge')}",
        ]
        summary['next_actions'] = [
            '如需继续桥接，切换到 assist 或 enforce。',
            '先阅读 classifier reasons，确认判断是否合理。',
        ]
    else:
        trigger_cmd = [
            'python3', str(TRIGGER),
            '--runtime', args.runtime,
            '--execution-mode', args.execution_mode,
            '--title', args.title,
            '--goal', args.goal,
            '--requested-by', args.requested_by,
            '--priority', args.priority,
            '--deadline', args.deadline,
            '--source-channel', args.source_channel,
            '--source-message-id', args.source_message_id,
            '--source-session-id', args.source_session_id,
            '--required-domains', args.required_domains,
            '--required-agents', args.required_agents,
            '--decision-note', args.decision_note,
            '--bridge-reason', args.bridge_reason,
            '--risk-level', args.risk_level,
            '--task-id', task_id,
            '--trace-id', trace_id,
        ]
        if args.workflow_mode == 'enforce':
            trigger_cmd.append('--require-allow-bridge')
        if args.skip_sync:
            trigger_cmd.append('--skip-sync')
        for item in (args.constraints or []):
            trigger_cmd.extend(['--constraint', item])

        trigger_result = parse_json_output(trigger_cmd)
        summary['main_trigger_record'] = trigger_result.get('main_trigger_record')
        summary['bridge_response_record'] = trigger_result.get('response_record')
        summary['trigger_status'] = trigger_result.get('status')
        summary['accepted'] = trigger_result.get('accepted')

        if trigger_result.get('accepted'):
            collect_cmd = [
                'python3', str(COLLECT),
                '--runtime', args.runtime,
                '--task-id', task_id,
            ]
            collect_result = parse_json_output(collect_cmd)
            absorb_cmd = [
                'python3', str(ABSORB),
                '--runtime', args.runtime,
                '--task-id', task_id,
            ]
            absorb_result = parse_json_output(absorb_cmd)
            summary.update({
                'status': 'bridge_absorbed',
                'collect_bundle': collect_result.get('bundle_md'),
                'absorption_bundle': absorb_result.get('absorption_md'),
                'absorb_as_auxiliary': absorb_result.get('absorb_as_auxiliary'),
                'recommend_reference': absorb_result.get('recommend_reference'),
                'recommend_direct_replace': absorb_result.get('recommend_direct_replace'),
                'summary_points': [
                    'classifier 已运行并记录。',
                    'bridge 已成功触发。',
                    'collect bundle 已生成。',
                    'absorption bundle 已生成。',
                    f"absorb_as_auxiliary={absorb_result.get('absorb_as_auxiliary')}",
                ],
                'next_actions': [
                    '优先查看 absorption bundle，决定是否吸收 sidecar 判断。',
                    '仍需人工比较现网主链与 sidecar 结果。',
                    '当前不要直接用 sidecar 结果替代用户最终回复。',
                ],
            })
        else:
            summary['status'] = trigger_result.get('status', 'trigger_rejected')
            summary['summary_points'] = [
                'classifier 已运行并记录。',
                'trigger 在前置层被拒绝，未继续 bridge。',
            ]
            summary['next_actions'] = [
                '先阅读 classifier reasons，确认拒绝是否合理。',
                '如判断为误拒绝，再调整 classifier 规则，不直接跳过。',
            ]

    out_dir = workflow_dir / task_id
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_json = out_dir / 'main-bridge-workflow-summary.json'
    summary_md = out_dir / 'main-bridge-workflow-summary.md'
    write_json(summary_json, summary)
    summary_md.write_text(render_markdown(summary), encoding='utf-8')

    result = {
        'runtime': args.runtime,
        'task_id': task_id,
        'workflow_mode': args.workflow_mode,
        'status': summary['status'],
        'workflow_summary_json': str(summary_json),
        'workflow_summary_md': str(summary_md),
        'mode': 'main_bridge_workflow',
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
