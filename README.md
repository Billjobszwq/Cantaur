# QYclaw (OpenClaw Mode Release)

OpenClaw-style packaged release for local deployment with:
- one-command install
- lifecycle orchestration scripts
- local dashboard
- agent/model/skill configuration templates

## One-line local deploy

```bash
OPENCLAW_HOME=$HOME/.openclaw bash ./install.sh
```

## Start / status

```bash
$HOME/.openclaw/workspace/bin/qyclaw start
$HOME/.openclaw/workspace/bin/qyclaw status
$HOME/.openclaw/workspace/bin/qyclaw panel
```

## Run a fusion task

```bash
$HOME/.openclaw/workspace/bin/qyclaw run --title "Cross-functional report" --goal "Generate a full report"
```

## Configure API/Model/Agents/Skills

1. Edit `$HOME/.openclaw/.env`
2. Edit `$HOME/.openclaw/openclaw.json`
3. Place skills under `$HOME/.openclaw/workspace/skills/`
4. Verify with:

```bash
$HOME/.openclaw/workspace/bin/qyclaw doctor
```

## Security

- No private credentials are included.
- Runtime data/logs are excluded by `.gitignore`.
- All hardcoded user absolute paths were removed from code.
