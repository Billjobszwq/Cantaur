#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

QYCLAW_ROOT = Path.home() / ".qyclaw"
CONFIG_PATH = QYCLAW_ROOT / 'qyclaw.json'
INTEGRATION_ROOT = QYCLAW_ROOT / 'workspace' / 'integration' / 'qy_code'
REGISTRY_DIR = INTEGRATION_ROOT / 'registries'


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def load_json(path: Path):
    with path.open('r', encoding='utf-8') as fh:
        return json.load(fh)


def dump_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


def role_summary(agent_id: str) -> str:
    defaults = {
        'main': '总协调官，负责收口任务、拆解协作、汇总交付与升级决策。',
        'dev': '负责开发、架构、系统设计与技术实现判断。',
        'content': '负责内容表达、传播包装、文案与热点洞察。',
        'ops': '负责运营执行、试点推进、流程组织与落地节奏平衡。',
        'law': '负责法律、合规、边界与风险解释。',
        'finance': '负责预算、ROI、成本控制与资金安全判断。',
        'research': '负责研究、情报、证据、市场与系统能力深挖。',
    }
    return defaults.get(agent_id, f'{agent_id} agent')


def infer_domain_tags(agent_id: str) -> list[str]:
    defaults = {
        'main': ['coordination', 'decision', 'delivery', 'routing'],
        'dev': ['engineering', 'architecture', 'implementation', 'systems'],
        'content': ['content', 'messaging', 'copywriting', 'market_signal'],
        'ops': ['operations', 'execution', 'rollout', 'process'],
        'law': ['legal', 'compliance', 'risk', 'guardrails'],
        'finance': ['finance', 'roi', 'budget', 'cash_safety'],
        'research': ['research', 'evidence', 'market', 'deep_dive'],
    }
    return defaults.get(agent_id, [agent_id])


def scan_skills(workspace: Path) -> list[str]:
    skills_dir = workspace / 'skills'
    if not skills_dir.exists():
        return []
    return sorted([p.name for p in skills_dir.iterdir() if p.is_dir() and (p / 'SKILL.md').exists()])


def main():
    config = load_json(CONFIG_PATH)
    existing_agents = load_json(REGISTRY_DIR / 'agent-registry.v1.json')
    existing_skills = load_json(REGISTRY_DIR / 'skill-registry.v1.json')
    agent_index = {item['id']: item for item in existing_agents['agents']}
    skill_index = {item['id']: item for item in existing_skills['skills']}

    agents_out = []
    workspace_skills: dict[str, list[str]] = {}
    for item in config['agents']['list']:
        agent_id = item['id']
        workspace = Path(item['workspace']).expanduser().resolve()
        existing = dict(agent_index.get(agent_id, {}))
        allow_agents = item.get('subagents', {}).get('allowAgents', [])
        skills = scan_skills(workspace)
        workspace_skills[agent_id] = skills
        base = {
            'id': agent_id,
            'display_name': item.get('identity', {}).get('name', agent_id),
            'workspace': str(workspace),
            'role_summary': role_summary(agent_id),
            'status': 'active',
            'domain_tags': infer_domain_tags(agent_id),
            'preferred_task_types': existing.get('preferred_task_types', []),
            'forbidden_task_types': existing.get('forbidden_task_types', []),
            'default_output_modes': existing.get('default_output_modes', ['summary']),
            'can_call': allow_agents if agent_id == 'main' else existing.get('can_call', []),
            'can_consult': existing.get('can_consult', []),
            'can_review': existing.get('can_review', [agent_id]),
            'reports_to': None if agent_id == 'main' else 'main',
            'requires_main_handoff': False if agent_id == 'main' else True,
            'risk_level': existing.get('risk_level', 'medium'),
            'decision_scope': existing.get('decision_scope', 'recommendation'),
            'approval_required_for': existing.get('approval_required_for', []),
            'session_mode': existing.get('session_mode', 'persistent'),
            'memory_scope': existing.get('memory_scope', 'agent_workspace' if agent_id != 'main' else 'shared_plus_agent'),
            'browser_capable': 'playwright-cli' in skills,
            'search_capable': 'tavily-search' in skills,
        }
        agents_out.append(base)

    all_skill_ids = sorted({sid for values in workspace_skills.values() for sid in values})
    skills_out = []
    for skill_id in all_skill_ids:
        owners = [agent for agent, skills in workspace_skills.items() if skill_id in skills]
        existing = dict(skill_index.get(skill_id, {}))
        available_to = owners[:]
        scope = 'global' if len(owners) > 1 else 'agent_only'
        default_for_agents = existing.get('default_for_agents', [])
        if skill_id == 'tavily-search':
            default_for_agents = owners[:]
        elif skill_id == 'playwright-cli':
            default_for_agents = owners[:]
        elif scope == 'agent_only' and not default_for_agents:
            default_for_agents = owners[:]
        payload = {
            'id': skill_id,
            'display_name': existing.get('display_name', skill_id.replace('-', ' ').title()),
            'scope': scope,
            'owners': existing.get('owners', owners if scope == 'agent_only' else ['system']),
            'available_to': available_to,
            'default_for_agents': default_for_agents,
            'category': existing.get('category', 'workflow'),
            'supported_task_types': existing.get('supported_task_types', []),
            'output_modes': existing.get('output_modes', []),
            'risk_level': existing.get('risk_level', 'low'),
            'approval_required': existing.get('approval_required', False),
            'restricted_actions': existing.get('restricted_actions', []),
            'runtime_type': existing.get('runtime_type', 'skill'),
            'dependencies': existing.get('dependencies', []),
            'persistent_state': existing.get('persistent_state', False),
            'paths_by_agent': {agent: str(Path(config['agents']['list'][[a['id'] for a in config['agents']['list']].index(agent)]['workspace']).expanduser().resolve() / 'skills' / skill_id) for agent in owners},
        }
        skills_out.append(payload)

    agents_payload = {'version': 'agent-registry/v1', 'generated_at': now_iso(), 'agents': agents_out}
    skills_payload = {'version': 'skill-registry/v1', 'generated_at': now_iso(), 'skills': skills_out}
    dump_json(REGISTRY_DIR / 'agent-registry.v1.json', agents_payload)
    dump_json(REGISTRY_DIR / 'skill-registry.v1.json', skills_payload)
    print(json.dumps({
        'agents': len(agents_out),
        'skills': len(skills_out),
        'updated_at': agents_payload['generated_at'],
        'agent_registry': str(REGISTRY_DIR / 'agent-registry.v1.json'),
        'skill_registry': str(REGISTRY_DIR / 'skill-registry.v1.json'),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
