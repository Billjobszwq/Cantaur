# Deployment Steps

1. Clone repo.
2. Run:

```bash
QYCLAW_HOME=$HOME/.qyclaw bash ./install.sh
```

3. Edit runtime configs:
- `$QYCLAW_HOME/.env`
- `$QYCLAW_HOME/qyclaw.json`

4. Start services:

```bash
$QYCLAW_HOME/workspace/bin/qyclaw start
```

5. Open dashboard:

```bash
$QYCLAW_HOME/workspace/bin/qyclaw panel
```
