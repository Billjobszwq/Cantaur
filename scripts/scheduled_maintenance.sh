#!/bin/zsh
set -u
set -o pipefail

ROOT="${QYCLAW_HOME:-$HOME/.qyclaw}"
SCRIPTS="${ROOT}/workspace/scripts"
LOG_DIR="${ROOT}/logs"

PIPELINE="${SCRIPTS}/memory_pipeline.py"
MAINTAIN="${SCRIPTS}/memory_system.py"
KNOWLEDGE="${SCRIPTS}/knowledge_base.py"
FUSION="${SCRIPTS}/unique_fusion_orchestrator.py"
LIFECYCLE="${ROOT}/workspace/integration/qy_code/live-link/scripts/main_bridge_lifecycle.py"
P1_GOVERNANCE="${SCRIPTS}/lifecycle_p1_governance.py"
LIFECYCLE_HEALTH_GATE="${SCRIPTS}/lifecycle_health_gate.py"
ENTRY_CONSISTENCY_AUDIT="${SCRIPTS}/fusion_entry_consistency_audit.py"
P3_DISTILLATION="${SCRIPTS}/p3_distillation_pipeline.py"
P4_SOAK_GATE="${SCRIPTS}/p4_soak_release_gate.py"
PYTHON_BIN="/usr/bin/python3"

MEMORY_LOG="${LOG_DIR}/memory-maintenance.log"
KNOWLEDGE_LOG="${LOG_DIR}/knowledge-update.log"
FUSION_LOG="${LOG_DIR}/fusion-maintenance.log"
P3_LOG="${LOG_DIR}/p3-distillation.log"
P4_LOG="${LOG_DIR}/p4-soak-gate.log"
ALERT_LOG="${LOG_DIR}/maintenance-alert.log"

mkdir -p "${LOG_DIR}"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S %z"
}

append_line() {
  local log_file="$1"
  local message="$2"
  printf "[%s] %s\n" "$(timestamp)" "${message}" >> "${log_file}"
}

send_alert() {
  local scope="$1"
  local message="$2"
  local final_msg="[${scope}] ${message}"
  append_line "${ALERT_LOG}" "ALERT ${final_msg}"

  if command -v osascript >/dev/null 2>&1; then
    osascript - "${final_msg}" <<'APPLESCRIPT' >/dev/null 2>&1 || true
on run argv
  display notification (item 1 of argv) with title "QYclaw Maintenance Alert"
end run
APPLESCRIPT
  fi

  if command -v qyclaw >/dev/null 2>&1 && [[ -n "${QYCLAW_MAINT_ALERT_TARGET:-}" ]]; then
    qyclaw message send \
      --target "${QYCLAW_MAINT_ALERT_TARGET}" \
      --message "QYclaw维护告警: ${final_msg}" >/dev/null 2>&1 || true
  fi
}

run_step() {
  local scope="$1"
  local log_file="$2"
  local description="$3"
  shift 3

  append_line "${log_file}" "START ${description}"
  "$@" >> "${log_file}" 2>&1
  local code=$?

  if [[ ${code} -eq 0 ]]; then
    append_line "${log_file}" "OK ${description}"
    return 0
  fi

  append_line "${log_file}" "FAIL ${description}; exit=${code}"
  send_alert "${scope}" "${description} failed; exit=${code}; log=${log_file}"
  return "${code}"
}

run_memory_maintenance() {
  append_line "${MEMORY_LOG}" "===== MEMORY MAINTENANCE RUN BEGIN ====="
  run_step "memory" "${MEMORY_LOG}" "memory capture --agent all" "${PYTHON_BIN}" "${PIPELINE}" capture --agent all || return 1
  run_step "memory" "${MEMORY_LOG}" "memory extract --agent all" "${PYTHON_BIN}" "${PIPELINE}" extract --agent all || return 1
  run_step "memory" "${MEMORY_LOG}" "memory maintain --interval-days 1" "${PYTHON_BIN}" "${MAINTAIN}" maintain --keep-days 14 --summary-files 7 --interval-days 1 || return 1
  append_line "${MEMORY_LOG}" "===== MEMORY MAINTENANCE RUN END ====="
}

run_knowledge_update() {
  local month
  month="$(date "+%Y-%m")"
  append_line "${KNOWLEDGE_LOG}" "===== KNOWLEDGE UPDATE RUN BEGIN (month=${month}) ====="
  run_step "knowledge" "${KNOWLEDGE_LOG}" "knowledge convergence-bootstrap month=${month}" "${PYTHON_BIN}" "${KNOWLEDGE}" convergence-bootstrap --runtime live --month "${month}" --limit 50 --review-limit 10000 || return 1
  run_step "knowledge" "${KNOWLEDGE_LOG}" "knowledge convergence-report" "${PYTHON_BIN}" "${KNOWLEDGE}" convergence-report --runtime live --limit 50 || return 1
  run_step "knowledge" "${KNOWLEDGE_LOG}" "knowledge lint" "${PYTHON_BIN}" "${KNOWLEDGE}" lint || return 1
  append_line "${KNOWLEDGE_LOG}" "===== KNOWLEDGE UPDATE RUN END ====="
}

should_run_knowledge_today() {
  local day_of_year
  day_of_year="$(date "+%j")"
  day_of_year=$((10#${day_of_year}))
  if (( (day_of_year - 1) % 3 == 0 )); then
    return 0
  fi
  return 1
}

run_knowledge_update_every_3_days() {
  if should_run_knowledge_today; then
    run_knowledge_update
    return
  fi
  append_line "${KNOWLEDGE_LOG}" "SKIP knowledge update: today is not in 3-day cadence window"
}

run_fusion_cycle() {
  append_line "${FUSION_LOG}" "===== FUSION CYCLE RUN BEGIN ====="
  local args=("${PYTHON_BIN}" "${FUSION}" run --runtime live --days 14 --knowledge-limit 80 --review-limit 10000 --with-bridge-guard)
  if [[ "${QYCLAW_FUSION_WITH_TIMEOUT_SCAN:-0}" == "1" ]]; then
    args+=(--bridge-timeout-scan --bridge-timeout-minutes "${QYCLAW_FUSION_TIMEOUT_MINUTES:-30}" --bridge-timeout-limit "${QYCLAW_FUSION_TIMEOUT_LIMIT:-200}")
  fi
  if [[ "${QYCLAW_HERMES_AUTOPILOT:-0}" == "1" ]]; then
    args+=(--apply-auto --max-knowledge-auto 8 --max-memory-auto 20 --autopilot-tier "${QYCLAW_HERMES_AUTOPILOT_TIER:-low}")
  fi
  run_step "fusion" "${FUSION_LOG}" "unique fusion cycle run" "${args[@]}" || return 1
  append_line "${FUSION_LOG}" "===== FUSION CYCLE RUN END ====="
}

run_bridge_timeout_scan() {
  append_line "${FUSION_LOG}" "===== BRIDGE TIMEOUT SCAN BEGIN ====="
  run_step "fusion" "${FUSION_LOG}" "bridge lifecycle timeout-scan" "${PYTHON_BIN}" "${LIFECYCLE}" timeout-scan --runtime live --timeout-minutes 30 --limit 200 || return 1
  append_line "${FUSION_LOG}" "===== BRIDGE TIMEOUT SCAN END ====="
}

run_bridge_executor() {
  append_line "${FUSION_LOG}" "===== BRIDGE EXECUTOR RUN BEGIN ====="
  run_step "fusion" "${FUSION_LOG}" "bridge lifecycle run" "${PYTHON_BIN}" "${LIFECYCLE}" run --runtime live --max-tasks "${QYCLAW_BRIDGE_RUN_MAX_TASKS:-20}" --lock-ttl-seconds "${QYCLAW_BRIDGE_RUN_LOCK_TTL_SECONDS:-900}" || return 1
  append_line "${FUSION_LOG}" "===== BRIDGE EXECUTOR RUN END ====="
}

run_p1_governance() {
  append_line "${FUSION_LOG}" "===== P1 GOVERNANCE RUN BEGIN ====="
  local args=("${PYTHON_BIN}" "${P1_GOVERNANCE}" --runtime live)
  if [[ "${QYCLAW_P1_GOVERNANCE_APPLY:-0}" == "1" ]]; then
    args+=(--apply)
  fi
  run_step "fusion" "${FUSION_LOG}" "lifecycle p1 governance" "${args[@]}" || return 1
  append_line "${FUSION_LOG}" "===== P1 GOVERNANCE RUN END ====="
}

run_lifecycle_health_gate() {
  append_line "${FUSION_LOG}" "===== LIFECYCLE HEALTH GATE BEGIN ====="
  run_step "fusion" "${FUSION_LOG}" "lifecycle health gate" "${PYTHON_BIN}" "${LIFECYCLE_HEALTH_GATE}" --runtime live || return 1
  append_line "${FUSION_LOG}" "===== LIFECYCLE HEALTH GATE END ====="
}

run_entry_consistency_audit() {
  append_line "${FUSION_LOG}" "===== ENTRY CONSISTENCY AUDIT BEGIN ====="
  run_step "fusion" "${FUSION_LOG}" "fusion entry consistency audit" "${PYTHON_BIN}" "${ENTRY_CONSISTENCY_AUDIT}" --runtime live || return 1
  append_line "${FUSION_LOG}" "===== ENTRY CONSISTENCY AUDIT END ====="
}

run_p3_distillation() {
  append_line "${P3_LOG}" "===== P3 DISTILLATION RUN BEGIN ====="
  run_step "p3" "${P3_LOG}" "p3 distillation pipeline run" "${PYTHON_BIN}" "${P3_DISTILLATION}" run --runtime live --limit "${QYCLAW_P3_LIMIT:-80}" || return 1
  append_line "${P3_LOG}" "===== P3 DISTILLATION RUN END ====="
}

run_p4_soak_gate() {
  append_line "${P4_LOG}" "===== P4 SOAK GATE RUN BEGIN ====="
  run_step "p4" "${P4_LOG}" "p4 soak release gate run" "${PYTHON_BIN}" "${P4_SOAK_GATE}" run --runtime live || return 1
  append_line "${P4_LOG}" "===== P4 SOAK GATE RUN END ====="
}

mode="${1:-}"
case "${mode}" in
  memory)
    run_memory_maintenance
    ;;
  knowledge)
    run_knowledge_update
    ;;
  knowledge-3d)
    run_knowledge_update_every_3_days
    ;;
  fusion)
    run_fusion_cycle
    ;;
  bridge-timeout-scan)
    run_bridge_timeout_scan
    ;;
  bridge-run)
    run_bridge_executor
    ;;
  p1-governance)
    run_p1_governance
    ;;
  lifecycle-health-gate)
    run_lifecycle_health_gate
    ;;
  entry-consistency-audit)
    run_entry_consistency_audit
    ;;
  p3-distillation)
    run_p3_distillation
    ;;
  p4-soak-gate)
    run_p4_soak_gate
    ;;
  all)
    run_memory_maintenance
    run_knowledge_update
    run_fusion_cycle
    run_bridge_executor
    run_p1_governance
    run_lifecycle_health_gate
    run_entry_consistency_audit
    run_p3_distillation
    run_p4_soak_gate
    run_bridge_timeout_scan
    ;;
  *)
    echo "Usage: $0 {memory|knowledge|knowledge-3d|fusion|bridge-run|p1-governance|lifecycle-health-gate|entry-consistency-audit|p3-distillation|p4-soak-gate|bridge-timeout-scan|all}" >&2
    exit 2
    ;;
esac
