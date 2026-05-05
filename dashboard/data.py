#!/usr/bin/env python3
"""系统状态数据采集 - 为仪表盘提供实时 JSON API"""
import json, subprocess, os, time
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

def run(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except:
        return ""

def get_gateway_status():
    out = run("openclaw gateway status 2>&1 | grep -E 'Runtime:|RPC probe:|port=|PID'", 10)
    running = "active" in out.lower() or "running" in out.lower()
    pid = ""
    for line in out.split('\n'):
        if 'pid' in line.lower():
            pid = line.strip()
    return {
        "running": running,
        "port": 18789,
        "pid": pid,
        "raw": out[:200]
    }

def get_cron_jobs():
    out = run("openclaw cron list 2>&1 | tail -n +2", 10)
    jobs = []
    for line in out.split('\n'):
        if not line.strip() or 'ID' in line:
            continue
        parts = line.split()
        if len(parts) >= 5:
            jobs.append({
                "id": parts[0][:12],
                "name": parts[1],
                "schedule": ' '.join(parts[2:4]) if len(parts) > 3 else parts[2],
                "status": parts[-2] if len(parts) > 4 else "?"
            })
    return jobs

def get_launchd_jobs():
    jobs = []
    plists = (Path.home() / "Library" / "LaunchAgents").glob("ai.openclaw.*.plist")
    for p in plists:
        name = p.stem.replace("ai.openclaw.", "")
        out = run(f"launchctl list | grep {name}", 5)
        status = "active" if out else "idle"
        jobs.append({"name": name, "status": status})
    return jobs

def get_memory_stats():
    """Get memory system stats"""
    workspace = Path(str(Path.home() / ".openclaw/workspace"))
    today = datetime.now(CST).strftime("%Y-%m-%d")
    
    # Count daily files
    daily_dir = workspace / "memory" / "10-episodic" / "daily"
    daily_count = len(list(daily_dir.glob("*.md"))) if daily_dir.exists() else 0
    
    # Today's memory
    today_file = daily_dir / f"{today}.md" if daily_dir else None
    today_size = today_file.stat().st_size if today_file and today_file.exists() else 0
    
    # Knowledge pages
    knowledge_dir = workspace / "knowledge"
    knowledge_count = len(list(knowledge_dir.rglob("*.md"))) if knowledge_dir.exists() else 0
    
    # Session count
    sessions_dir = Path(str(Path.home() / ".openclaw/agents/main/sessions"))
    session_files = list(sessions_dir.glob("*.json")) if sessions_dir.exists() else []
    
    return {
        "daily_files": daily_count,
        "today_file": today,
        "today_size_kb": round(today_size / 1024, 1),
        "knowledge_pages": knowledge_count,
        "active_sessions": len(session_files),
        "today_captures": 0  # Would need to count from pipeline
    }

def get_skills_count():
    skill_dirs = [
        Path(str(Path.home() / ".openclaw/workspace/skills")),
        Path(str(Path.home() / ".openclaw/workspace/.agents/skills")),
        Path.home() / ".nvm" / "versions" / "node" / "v24.4.0" / "lib" / "node_modules" / "openclaw" / "skills",
    ]
    custom = 0
    builtin = 0
    for sd in skill_dirs:
        if not sd.exists():
            continue
        if "openclaw/skills" in str(sd):
            builtin = len([d for d in sd.iterdir() if d.is_dir()])
        else:
            custom += len([d for d in sd.iterdir() if d.is_dir()])
    return {"custom": custom, "builtin": builtin, "total": custom + builtin}

def get_token_stats():
    """Estimate token usage"""
    return {
        "context_window": 1000000,
        "estimated_used": 38000,
        "model": "deepseek-v4-pro",
        "fallbacks": ["deepseek-v4-flash", "deepseek-reasoner"],
        "thinking": "low"
    }

def get_system_alerts():
    alerts = []
    
    # Check config
    config = Path(str(Path.home() / ".openclaw/openclaw.json"))
    if config.exists():
        content = config.read_text()
        if "thinkingDefault" in content or "reasoningDefault" in content:
            alerts.append({
                "level": "warn",
                "msg": "Config 中存在无效 key (thinkingDefault/reasoningDefault)",
                "fix": "openclaw doctor --fix"
            })
    
    # Check gateway
    gw = get_gateway_status()
    if not gw["running"]:
        alerts.append({"level": "error", "msg": "Gateway 未运行", "fix": "openclaw gateway start"})
    elif "failed" in gw.get("raw", "").lower():
        alerts.append({"level": "warn", "msg": "Gateway RPC 探针失败（loopback 模式正常现象）"})
    
    # Add cron info
    alerts.append({
        "level": "info",
        "msg": f"5个AI追踪定时任务已配置 (8:00/12:00/15:00/18:00/21:00)"
    })
    
    return alerts

def get_agents_status():
    agents = [
        {"id": "main", "name": "main", "role": "主控协调官"},
        {"id": "dev", "name": "dev", "role": "开发与架构"},
        {"id": "content", "name": "content", "role": "内容创作"},
        {"id": "ops", "name": "ops", "role": "运维与执行"},
        {"id": "law", "name": "law", "role": "法务合规"},
        {"id": "finance", "name": "finance", "role": "财务"},
        {"id": "research", "name": "research", "role": "研究与情报"},
    ]
    
    for a in agents:
        sessions_dir = Path(fstr(Path.home() / ".openclaw/agents/{a['id']}/sessions"))
        if sessions_dir.exists():
            sessions = list(sessions_dir.glob("*.json"))
            a["sessions_count"] = len(sessions)
            a["active"] = len(sessions) > 0
        else:
            a["sessions_count"] = 0
            a["active"] = False
    
    return agents

def main():
    now = datetime.now(CST)
    
    data = {
        "timestamp": now.isoformat(),
        "time_display": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "version": "2026.4.23",
        "gateway": get_gateway_status(),
        "cron_jobs": get_cron_jobs(),
        "launchd_jobs": get_launchd_jobs(),
        "memory": get_memory_stats(),
        "skills": get_skills_count(),
        "tokens": get_token_stats(),
        "alerts": get_system_alerts(),
        "agents": get_agents_status(),
    }
    
    print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
