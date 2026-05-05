#!/bin/bash
# Dashboard auto-refresh — regenerates index.html every 2 minutes
set -euo pipefail
QYCLAW_HOME="${QYCLAW_HOME:-$HOME/.qyclaw}"
cd "${QYCLAW_HOME}/workspace/dashboard"
python3 generate.py >> /tmp/dashboard-refresh.log 2>&1
