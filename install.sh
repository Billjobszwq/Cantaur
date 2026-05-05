#!/usr/bin/env bash
set -euo pipefail

QYCLAW_HOME="${QYCLAW_HOME:-$HOME/.qyclaw}"
BACKEND_BIN="${CANTAUR_BACKEND_BIN:-qyclaw-core}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_WORKSPACE="${QYCLAW_HOME}/workspace"

mkdir -p "${QYCLAW_HOME}" "${QYCLAW_HOME}/logs" "${QYCLAW_HOME}/state"
mkdir -p "${TARGET_WORKSPACE}"

# Sync workspace files into runtime home
rsync -a --delete \
  --exclude '.git' \
  --exclude '.env' \
  --exclude 'logs' \
  --exclude 'state' \
  --exclude 'tmp' \
  "${SCRIPT_DIR}/" "${TARGET_WORKSPACE}/"

if [[ ! -f "${QYCLAW_HOME}/.env" ]]; then
  cp "${TARGET_WORKSPACE}/.env.example" "${QYCLAW_HOME}/.env"
fi

if [[ ! -f "${QYCLAW_HOME}/qyclaw.json" ]]; then
  cp "${TARGET_WORKSPACE}/config/qyclaw.example.json" "${QYCLAW_HOME}/qyclaw.json"
fi

if command -v "${BACKEND_BIN}" >/dev/null 2>&1; then
  "${BACKEND_BIN}" gateway start || true
fi

"${TARGET_WORKSPACE}/bin/qyclaw" panel start || true

cat <<DONE
Install complete.
QYCLAW_HOME=${QYCLAW_HOME}
Next steps:
  1) edit ${QYCLAW_HOME}/.env
  2) edit ${QYCLAW_HOME}/qyclaw.json
  3) ${TARGET_WORKSPACE}/bin/qyclaw doctor
DONE
