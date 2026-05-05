# Deployment Steps

1. Clone repo.
2. Run:

```bash
OPENCLAW_HOME=$HOME/.openclaw bash ./install.sh
```

3. Edit runtime configs:
- `$OPENCLAW_HOME/.env`
- `$OPENCLAW_HOME/openclaw.json`

4. Start services:

```bash
$OPENCLAW_HOME/workspace/bin/qyclaw start
```

5. Open dashboard:

```bash
$OPENCLAW_HOME/workspace/bin/qyclaw panel
```
