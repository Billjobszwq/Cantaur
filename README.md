# Cantaur (QYclaw Mode Release)

QYclaw-style packaged release for local deployment with:
- one-command install
- lifecycle orchestration scripts
- local dashboard
- agent/model/skill configuration templates

## One-line local deploy

```bash
QYCLAW_HOME=$HOME/.qyclaw bash ./install.sh
```

## Start / status

```bash
$HOME/.qyclaw/workspace/bin/qyclaw start
$HOME/.qyclaw/workspace/bin/qyclaw status
$HOME/.qyclaw/workspace/bin/qyclaw panel
```

## Run a fusion task

```bash
$HOME/.qyclaw/workspace/bin/qyclaw run --title "Cross-functional report" --goal "Generate a full report"
```

## Configure API/Model/Agents/Skills

1. Edit `$HOME/.qyclaw/.env`
2. Edit `$HOME/.qyclaw/qyclaw.json`
3. Place skills under `$HOME/.qyclaw/workspace/skills/`
4. Verify with:

```bash
$HOME/.qyclaw/workspace/bin/qyclaw doctor
```

## Security

- No private credentials are included.
- Runtime data/logs are excluded by `.gitignore`.
- All hardcoded user absolute paths were removed from code.

## Publishing

- GitHub publishing and privacy checklist: `docs/GITHUB-PUBLISH.md`
