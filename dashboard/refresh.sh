#!/bin/bash
# Dashboard auto-refresh — regenerates index.html every 2 minutes
set -euo pipefail
OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
cd "${OPENCLAW_HOME}/workspace/dashboard"
python3 generate.py >> /tmp/dashboard-refresh.log 2>&1
