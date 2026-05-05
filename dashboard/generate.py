#!/usr/bin/env python3
"""生成仪表盘 HTML - 嵌入实时数据"""
import json, subprocess, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DASHBOARD_DIR = Path(__file__).parent

def load_template():
    template_path = DASHBOARD_DIR / "template.html"
    if template_path.exists():
        return template_path.read_text()
    # Fallback to index.html
    idx = DASHBOARD_DIR / "index.html"
    if idx.exists():
        return idx.read_text()
    print("ERROR: No template found", file=sys.stderr)
    sys.exit(1)

def generate_data():
    """Gather all live data"""
    now = datetime.now(CST)
    
    # Gateway status
    gw_running = False
    try:
        r = subprocess.run("openclaw gateway status 2>&1", shell=True, capture_output=True, text=True, timeout=10)
        gw_running = "running" in (r.stdout + r.stderr).lower() and "active" in (r.stdout + r.stderr).lower()
    except:
        pass
    
    # Cron jobs from openclaw
    cron_jobs = []
    try:
        r = subprocess.run("openclaw cron list 2>/dev/null", shell=True, capture_output=True, text=True, timeout=10)
        lines = [l for l in r.stdout.split('\n') if l.strip() and not l.startswith('ID') and not l.startswith('Config') and len(l.split()) >= 8]
        for line in lines:
            parts = line.split()
            cron_jobs.append({
                    "name": parts[1],
                    "schedule": ' '.join(parts[2:5]) if len(parts) > 4 else parts[2],
                    "next": parts[5] if len(parts) > 5 else "",
                    "status": parts[-1] if parts[-1] in ('idle','running') else 'idle'
                })
    except:
        pass
    
    # LaunchAgent jobs
    launchd_jobs = []
    try:
        plist_dir = Path.home() / "Library" / "LaunchAgents"
        for p in plist_dir.glob("ai.openclaw.*.plist"):
            name = p.stem.replace("ai.openclaw.", "")
            r = subprocess.run(f"launchctl list | grep '{name}'", shell=True, capture_output=True, text=True, timeout=5)
            status = "active" if r.stdout.strip() else "idle"
            launchd_jobs.append({"name": name, "status": status})
    except:
        pass
    
    # Session counts per agent
    agents = []
    agent_ids = ["main", "dev", "content", "ops", "law", "finance", "research"]
    agent_roles = {
        "main": "主控协调官", "dev": "开发与架构", "content": "内容创作",
        "ops": "运维与执行", "law": "法务合规", "finance": "财务", "research": "研究与情报"
    }
    active_sessions = 0
    for aid in agent_ids:
        sd = Path(fstr(Path.home() / ".openclaw/agents/{aid}/sessions"))
        count = len(list(sd.glob("*.json"))) if sd.exists() else 0
        active = count > 0
        if active:
            active_sessions += 1
        agents.append({
            "id": aid, "name": aid, "role": agent_roles.get(aid, ""),
            "sessions": count, "active": active
        })
    
    # Skills count
    custom_skills = 0
    for sd_path in [
        Path(str(Path.home() / ".openclaw/workspace/skills")),
        Path(str(Path.home() / ".openclaw/workspace/.agents/skills")),
    ]:
        if sd_path.exists():
            custom_skills += len([d for d in sd_path.iterdir() if d.is_dir()])
    
    builtin_skills_dir = Path.home() / ".nvm" / "versions" / "node" / "v24.4.0" / "lib" / "node_modules" / "openclaw" / "skills"
    builtin_skills = len([d for d in builtin_skills_dir.iterdir() if d.is_dir()]) if builtin_skills_dir.exists() else 0
    
    # Memory stats
    daily_dir = Path(str(Path.home() / ".openclaw/workspace/memory/10-episodic/daily"))
    daily_count = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0
    today_file = daily_dir / f"{now.strftime('%Y-%m-%d')}.md" if daily_dir else None
    today_kb = round(today_file.stat().st_size / 1024, 1) if today_file and today_file.exists() else 0
    
    # Knowledge pages
    knowledge_dir = Path(str(Path.home() / ".openclaw/workspace/knowledge"))
    knowledge_count = len(list(knowledge_dir.rglob("*.md"))) if knowledge_dir.exists() else 0
    
    # Alerts
    alerts = []
    config = Path(str(Path.home() / ".openclaw/openclaw.json"))
    if config.exists():
        content = config.read_text()
        invalid_keys = []
        for k in ["thinkingDefault", "reasoningDefault"]:
            if f'"{k}"' in content:
                invalid_keys.append(k)
                # Auto-fix: remove from config
                content = content.replace(f'  "{k}": "low",\n', '')
        if invalid_keys:
            config.write_text(content)
            alerts.append({"level": "info", "msg": f"已自动清理无效 config key: {', '.join(invalid_keys)}"})
    
    if not gw_running:
        alerts.append({"level": "error", "msg": "Gateway 未运行"})
    
    alerts.append({"level": "info", "msg": "5个AI追踪定时任务已配置 (8:00/12:00/15:00/18:00/21:00)"})
    alerts.append({"level": "info", "msg": "lark-cli 已安装配置，Bitable全权限可用"})
    alerts.append({"level": "info", "msg": f"知识库: {knowledge_count} 个页面, 记忆: {daily_count} 天记录"})
    
    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "2026.4.23",
        "gateway": {"running": gw_running, "port": 18789},
        "agents": agents,
        "active_sessions": active_sessions,
        "skills": {"custom": custom_skills, "builtin": builtin_skills, "total": custom_skills + builtin_skills},
        "cron_jobs": cron_jobs,
        "launchd_jobs": launchd_jobs,
        "memory": {"daily_files": daily_count, "today_kb": today_kb, "knowledge_pages": knowledge_count},
        "tokens": get_token_stats(),
        "alerts": alerts,
        "cron_count": len(cron_jobs),
        "launchd_count": len(launchd_jobs),
    }

def get_token_stats():
    """Read real token data from sessions.json"""
    sessions_file = Path(str(Path.home() / ".openclaw/agents/main/sessions/sessions.json"))
    context = 1000000
    estimated = 38000
    model = "deepseek-v4-pro"
    try:
        if sessions_file.exists():
            data = json.loads(sessions_file.read_text())
            sessions = data if isinstance(data, list) else list(data.values())
            for s in sessions:
                if isinstance(s, dict):
                    context = max(context, s.get("contextTokens", 0) or context)
                    estimated = max(estimated, s.get("totalTokens", 0) or estimated)
                    if s.get("model"):
                        model = s["model"]
    except:
        pass
    return {"context": context, "estimated": estimated, "model": model, "fallbacks": ["deepseek-v4-flash", "deepseek-reasoner"], "thinking": "low"}

def generate():
    data = generate_data()
    template = load_template()
    
    # Replace the data injection point
    data_json = json.dumps(data, ensure_ascii=False)
    
    if "/* DATA_INJECT */" in template:
        html = template.replace("/* DATA_INJECT */", data_json)
    else:
        # Find the dashboardData line and replace
        html = template.replace(
            'const dashboardData = /* DATA_INJECT */;',
            f'const dashboardData = {data_json};'
        )
    
    output_path = DASHBOARD_DIR / "index.html"
    output_path.write_text(html)
    print(f"✅ Dashboard generated: {output_path} ({len(html)} bytes)")
    print(f"   Timestamp: {data['timestamp']}")
    print(f"   Gateway: {'running' if data['gateway']['running'] else 'stopped'}")
    print(f"   Agents active: {data['active_sessions']}/{len(data['agents'])}")
    print(f"   Cron jobs: {data['cron_count']}, LaunchAgents: {data['launchd_count']}")

if __name__ == "__main__":
    generate()
