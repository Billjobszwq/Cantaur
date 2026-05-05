#!/usr/bin/env python3
"""仪表盘 HTTP 服务器 - 提供实时数据 API + 重启功能"""
import json, subprocess, sys, os
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
DASHBOARD_DIR = Path(__file__).parent
PORT = 8899

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默

    def do_GET(self):
        if self.path == '/data':
            self.send_json_response(get_live_data())
        elif self.path == '/doctor':
            self.send_json_response(get_doctor())
        elif self.path == '/restart':
            self.handle_restart()
        elif self.path == '/' or self.path == '/index.html':
            self.serve_dashboard()
        else:
            self.send_error(404)

    def send_json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def serve_dashboard(self):
        idx = DASHBOARD_DIR / 'index.html'
        if idx.exists():
            content = idx.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def handle_restart(self):
        fix_config()
        """触发 Gateway 重启"""
        try:
            result = subprocess.run(
                "openclaw gateway restart 2>&1",
                shell=True, capture_output=True, text=True, timeout=30
            )
            success = result.returncode == 0
            self.send_json_response({
                "success": success,
                "message": "Gateway 重启成功" if success else "重启失败",
                "output": (result.stdout + result.stderr)[:500]
            })
        except Exception as e:
            self.send_json_response({"success": False, "message": str(e)})


def fix_config():
    """Auto-fix invalid plugin entries in config"""
    try:
        cf = Path(str(Path.home() / ".openclaw/openclaw.json"))
        d = json.loads(cf.read_text())
        plugins = d.get('plugins',{}).get('entries',{})
        bad = [k for k in plugins if k not in ('feishu',)]
        if bad:
            d['plugins']['entries'] = {'feishu': plugins.get('feishu', {'enabled': True})}
            cf.write_text(json.dumps(d, indent=2, ensure_ascii=False))
            return True
    except: pass
    return False

def get_live_data():
    """实时数据 - 从 generate.py 采集逻辑复用"""
    now = datetime.now(CST)
    
    # Gateway
    gw_running = False
    try:
        r = subprocess.run("openclaw gateway status 2>/dev/null", shell=True, capture_output=True, text=True, timeout=10)
        gw_running = "running" in (r.stdout).lower() and "active" in (r.stdout).lower()
    except: pass
    
    fix_config()
    # Cron jobs
    cron_jobs = []
    try:
        r = subprocess.run("openclaw cron list 2>/dev/null", shell=True, capture_output=True, text=True, timeout=10)
        for line in r.stdout.split('\n'):
            parts = line.split()
            if len(parts) >= 8 and not line.startswith('ID') and not line.startswith('Config'):
                cron_jobs.append({
                    "name": parts[1], "schedule": ' '.join(parts[2:4]), "status": parts[-2]
                })
    except: pass
    
    # Agents
    agents = []
    agent_ids = ["main","dev","content","ops","law","finance","research"]
    roles = {"main":"主控协调官","dev":"开发与架构","content":"内容创作","ops":"运维与执行","law":"法务合规","finance":"财务","research":"研究与情报"}
    for aid in agent_ids:
        sd = Path(fstr(Path.home() / ".openclaw/agents/{aid}/sessions"))
        count = len(list(sd.glob("*.json"))) if sd.exists() else 0
        agents.append({"id":aid,"name":aid,"role":roles.get(aid,""),"sessions":count,"active":count>0})
    
    # Token from sessions.json
    token_ctx, token_used, model = 1000000, 38000, "deepseek-v4-pro"
    try:
        sf = Path(str(Path.home() / ".openclaw/agents/main/sessions/sessions.json"))
        if sf.exists():
            data = json.loads(sf.read_text())
            sessions = data if isinstance(data, list) else list(data.values())
            for s in sessions:
                if isinstance(s, dict):
                    token_ctx = max(token_ctx, s.get("contextTokens",0) or token_ctx)
                    token_used = max(token_used, s.get("totalTokens",0) or token_used)
                    if s.get("model"): model = s["model"]
    except: pass
    
    # LaunchAgents
    launchd = []
    try:
        for p in (Path.home() / "Library" / "LaunchAgents").glob("ai.openclaw.*.plist"):
            name = p.stem.replace("ai.openclaw.","")
            r = subprocess.run(f"launchctl list | grep '{name}'", shell=True, capture_output=True, text=True, timeout=5)
            launchd.append({"name":name,"status":"active" if r.stdout.strip() else "idle"})
    except: pass
    
    # Memory
    daily = Path(str(Path.home() / ".openclaw/workspace/memory/10-episodic/daily"))
    today_file = daily / now.strftime("%Y-%m-%d") if daily.exists() else None
    today_kb = round(today_file.stat().st_size/1024,1) if today_file and today_file.exists() else 0
    knowledge = len(list(Path(str(Path.home() / ".openclaw/workspace/knowledge")).rglob("*.md"))) if Path(str(Path.home() / ".openclaw/workspace/knowledge")).exists() else 0
    
    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "version": "2026.4.23",
        "gateway": {"running": gw_running, "port": 18789},
        "agents": agents,
        "active_sessions": sum(1 for a in agents if a["active"]),
        "cron_jobs": cron_jobs,
        "launchd_jobs": launchd,
        "tokens": {"context": token_ctx, "estimated": token_used, "model": model, "fallbacks": ["deepseek-v4-flash","deepseek-reasoner"], "thinking": "low"},
        "memory": {"daily_files": len(list(daily.glob("*.md"))) if daily.exists() else 0, "today_kb": today_kb, "knowledge_pages": knowledge},
        "skills": {"custom": 55, "builtin": 53, "total": 108},
        "alerts": [],
        "cron_count": len(cron_jobs),
        "launchd_count": len(launchd),
    }

def get_doctor():
    fix_config()
    """OpenClaw doctor 诊断结果"""
    try:
        r = subprocess.run("openclaw doctor 2>/dev/null", shell=True, capture_output=True, text=True, timeout=30)
        output = r.stdout
    except:
        output = "无法执行 openclaw doctor"
    
    # Parse key health indicators
    checks = []
    for line in output.split('\n'):
        line = line.strip()
        if 'pass' in line.lower() or 'ok' in line.lower() or 'running' in line.lower():
            checks.append({"item": line[:120], "status": "pass"})
        elif 'fail' in line.lower() or 'error' in line.lower() or 'invalid' in line.lower():
            checks.append({"item": line[:120], "status": "fail"})
    
    return {
        "timestamp": datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S"),
        "raw": output[:3000],
        "checks": checks[-20:],
        "pass_count": sum(1 for c in checks if c["status"] == "pass"),
        "fail_count": sum(1 for c in checks if c["status"] == "fail"),
    }

if __name__ == "__main__":
    print(f"🦞 仪表盘服务启动: http://localhost:{PORT}")
    print(f"   API: http://localhost:{PORT}/data")
    print(f"   诊断: http://localhost:{PORT}/doctor")
    print(f"   重启: http://localhost:{PORT}/restart")
    HTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
