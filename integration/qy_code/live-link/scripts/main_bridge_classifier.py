#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

QYCLAW_ROOT = Path.home() / ".qyclaw"
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
LIVE_LINK_ROOT = INTEGRATION_ROOT / 'live-link'
RUNTIME_BASE = INTEGRATION_ROOT / 'runtime'

REPORT_KEYWORDS = [
    '报告', '汇总', '总结', '综合', '联动', '分析', '研究', '方案', '评估', 'review', 'report', 'brief', 'memo',
]
MULTI_AGENT_HINTS = [
    '跨职能', '多专业', '多 agent', '多agent', '联动', '协作', 'research', 'ops', 'law', 'finance', 'content', 'dev',
]
DELIVERY_HINTS = [
    '交付', '给我一份', '输出', '汇报', '老板', '客户', '底稿', '综合稿', '综合汇总稿', 'summary', 'deliverable',
]
HIGH_RISK_HINTS = [
    '自动外发', '自动消息发送', '实时群聊联动', '法律最终承诺', '财务最终承诺', 'destructive', '删除生产', '销毁生产',
]
EXTERNAL_ACTION_HINTS = [
    '发送给客户', '自动回复用户', '自动外发', '发消息', '邮件发出', '群里直接回复', '对外发送',
]
REAL_TIME_HINTS = [
    '实时', '立刻联动', '马上在群里', '立即回复', '实时群聊',
]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def ensure_dir(runtime_root: Path) -> Path:
    path = runtime_root / 'bridge' / 'classifier'
    path.mkdir(parents=True, exist_ok=True)
    return path


def count_hits(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw.lower() in text]


def is_negated(text: str, keyword: str) -> bool:
    markers = ['不', '非', '禁止', '不要', '无需', '不需要', '不做', '避免']
    start = 0
    while True:
        idx = text.find(keyword, start)
        if idx == -1:
            return False
        window = text[max(0, idx - 6):idx]
        if any(marker in window for marker in markers):
            return True
        start = idx + len(keyword)


def count_effective_hits(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw.lower() in text and not is_negated(text, kw.lower())]


def infer_task_type(text: str, report_hits: list[str], delivery_hits: list[str]) -> str:
    if report_hits and delivery_hits:
        return 'report.cross_functional'
    if report_hits and ('综合汇总稿' in text or '综合稿' in text or '汇总稿' in text):
        return 'report.cross_functional'
    if report_hits:
        return 'report.candidate'
    return 'unknown'


def derive_risk_level(high_risk_hits: list[str], external_hits: list[str], realtime_hits: list[str]) -> str:
    if high_risk_hits or external_hits or realtime_hits:
        return 'high'
    return 'medium'


def classify(title: str, goal: str, decision_note: str, bridge_reason: str) -> dict[str, Any]:
    text = '\n'.join([title, goal, decision_note, bridge_reason]).lower()
    report_hits = count_hits(text, REPORT_KEYWORDS)
    multi_agent_hits = count_hits(text, MULTI_AGENT_HINTS)
    delivery_hits = count_hits(text, DELIVERY_HINTS)
    high_risk_hits = count_effective_hits(text, HIGH_RISK_HINTS)
    external_hits = count_effective_hits(text, EXTERNAL_ACTION_HINTS)
    realtime_hits = count_effective_hits(text, REAL_TIME_HINTS)

    inferred_task_type = infer_task_type(text, report_hits, delivery_hits)
    requires_multi_agent = bool(multi_agent_hits) or sum(agent in text for agent in ['research', 'ops', 'law', 'finance', 'content']) >= 2
    delivery_oriented = bool(delivery_hits) or '综合稿' in text or '报告' in text
    contains_external_action = bool(external_hits)
    contains_real_time_requirement = bool(realtime_hits)
    risk_level = derive_risk_level(high_risk_hits, external_hits, realtime_hits)

    allow_bridge = all([
        inferred_task_type == 'report.cross_functional',
        requires_multi_agent,
        delivery_oriented,
        not contains_external_action,
        not contains_real_time_requirement,
        risk_level not in {'high', 'critical'},
    ])

    reasons = []
    if inferred_task_type != 'report.cross_functional':
        reasons.append('任务类型未稳定落在 report.cross_functional')
    if not requires_multi_agent:
        reasons.append('多 agent 协作信号不足')
    if not delivery_oriented:
        reasons.append('交付导向信号不足')
    if contains_external_action:
        reasons.append('命中外部动作信号')
    if contains_real_time_requirement:
        reasons.append('命中实时联动信号')
    if risk_level in {'high', 'critical'}:
        reasons.append(f'风险等级过高：{risk_level}')
    if allow_bridge:
        reasons.append('满足当前 Main Explicit Bridge 白名单条件')

    return {
        'bridge_mode_candidate': 'main_explicit_bridge',
        'inferred_task_type': inferred_task_type,
        'requires_multi_agent': requires_multi_agent,
        'delivery_oriented': delivery_oriented,
        'contains_external_action': contains_external_action,
        'contains_real_time_requirement': contains_real_time_requirement,
        'risk_level': risk_level,
        'allow_bridge': allow_bridge,
        'matched_signals': {
            'report_hits': report_hits,
            'multi_agent_hits': multi_agent_hits,
            'delivery_hits': delivery_hits,
            'high_risk_hits': high_risk_hits,
            'external_hits': external_hits,
            'realtime_hits': realtime_hits,
        },
        'reasons': reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='rule-based classifier draft for main explicit bridge')
    parser.add_argument('--runtime', default='shadow-live')
    parser.add_argument('--title', required=True)
    parser.add_argument('--goal', required=True)
    parser.add_argument('--decision-note', default='')
    parser.add_argument('--bridge-reason', default='')
    parser.add_argument('--task-id')
    args = parser.parse_args()

    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    task_id = args.task_id or f'classifier-{stamp}'
    runtime_root = RUNTIME_BASE / args.runtime
    out_dir = ensure_dir(runtime_root)

    result = classify(args.title, args.goal, args.decision_note, args.bridge_reason)
    payload = {
        'task_id': task_id,
        'title': args.title,
        'goal': args.goal,
        'decision_note': args.decision_note,
        'bridge_reason': args.bridge_reason,
        'generated_at': now_iso(),
        'mode': 'main_bridge_classifier_draft',
        **result,
    }
    out_path = out_dir / f'{task_id}.classifier.json'
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({**payload, 'classifier_record': str(out_path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
