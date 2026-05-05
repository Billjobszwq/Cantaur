#!/usr/bin/env bash
set -euo pipefail

OPENCLAW_HOME="${OPENCLAW_HOME:-$HOME/.openclaw}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_WORKSPACE="${OPENCLAW_HOME}/workspace"

mkdir -p "${OPENCLAW_HOME}" "${OPENCLAW_HOME}/logs" "${OPENCLAW_HOME}/state"
mkdir -p "${TARGET_WORKSPACE}"

# Sync workspace files into runtime home
rsync -a --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'logs' \
  --exclude 'state' \
  --exclude 'tmp' \
  "${SCRIPT_DIR}/" "${TARGET_WORKSPACE}/"

if [[ ! -f "${OPENCLAW_HOME}/.env" ]]; then
  cp "${TARGET_WORKSPACE}/.env.example" "${OPENCLAW_HOME}/.env"
fi

if [[ ! -f "${OPENCLAW_HOME}/openclaw.json" ]]; then
  cp "${TARGET_WORKSPACE}/config/openclaw.example.json" "${OPENCLAW_HOME}/openclaw.json"
fi

if command -v openclaw >/dev/null 2>&1; then
  openclaw gateway start || true
fi

"${TARGET_WORKSPACE}/bin/qyclaw" panel start || true

cat <<DONE
Install complete.
OPENCLAW_HOME=${OPENCLAW_HOME}
Next steps:
  1) edit ${OPENCLAW_HOME}/.env
  2) edit ${OPENCLAW_HOME}/openclaw.json
  3) ${TARGET_WORKSPACE}/bin/qyclaw doctor
DONE
