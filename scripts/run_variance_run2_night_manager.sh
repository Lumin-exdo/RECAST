#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
PY=/mnt/laq/venv/bin/python3
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
OUT=$RUN_ROOT/variance_run2_30
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
ENV_NOLOAD=/tmp/recast_no_env
LOG=$RUN_ROOT/night_manager_run2.log
MAX_PARALLEL=${MAX_PARALLEL:-3}
SAMPLE_TIMEOUT_SECONDS=${SAMPLE_TIMEOUT_SECONDS:-7200}

T1_UIDS="89b77229 7ee76c41 1a85388f f6d12075 d9545076 e229c5cd eacb64ff fdada4cc a4b2e2fd 2006d545 d74f7f3e b17c5c02 b35794f3 7a7621e2 a53e0e26"
T2_UIDS="d806d94c feef3933 14897e47 c9cc370e 2c711459 993152aa c03f7b53 60604200 06071a3e 2d92d1c2 fbe6fd55 28daa975 27a52329 830a2e06 a2a3e641"
ALL_UIDS="$T1_UIDS $T2_UIDS"

mkdir -p "$RUN_ROOT" "$OUT"
: > "$ENV_NOLOAD"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

prepare_env() {
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1
  export TARGET_MODEL=deepseek/deepseek-v4-flash
  export RECAST_MAX_TOKENS=2500
}

running_count() {
  ps -eo args= |
    awk '$1 == "/mnt/laq/venv/bin/python3" && $2 == "run_new_mem.py" && /variance_run2_30/ {count++} END {print count + 0}'
}

uid_running() {
  local uid="$1"
  ps -eo args= |
    awk -v uid="$uid" '$1 == "/mnt/laq/venv/bin/python3" && $2 == "run_new_mem.py" && /variance_run2_30/ && index($0, "--uids " uid) {found=1} END {exit found ? 0 : 1}'
}

uid_done() {
  local uid="$1"
  find "$OUT" -maxdepth 2 -type f -name answer.json -print0 2>/dev/null |
    xargs -0 grep -l "\"uid\": \"${uid}" >/dev/null 2>&1
}

uid_failed() {
  local uid="$1"
  [ -f "$OUT/failed_uids.log" ] && grep -q "^${uid} " "$OUT/failed_uids.log"
}

balance_exhausted_seen() {
  grep -R -Eqi "Error code: 402|requires more credits|Insufficient Balance|BALANCE EXHAUSTED" \
    "$RUN_ROOT"/helper_*.log "$RUN_ROOT"/night_helper_*.log "$RUN_ROOT"/driver.screen.log 2>/dev/null
}

completed_count() {
  find "$OUT" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

start_uid() {
  local uid="$1"
  local helper_log="$RUN_ROOT/night_helper_${uid}.log"
  log "START helper uid=$uid"
  (
    cd "$ROOT" || exit 1
    prepare_env
    timeout "$SAMPLE_TIMEOUT_SECONDS" "$PY" run_new_mem.py \
      --env-file "$ENV_NOLOAD" \
      --run-name variance_run2_30 \
      --output-dir "$OUT" \
      --uids "$uid" \
      --workers 1 \
      --no-thinking \
      --use-cache \
      --data-path "$DATASET" \
      --embedding-model-path "$EMBED" \
      --embedding-device cpu
    rc=$?
    echo "[$(date '+%F %T')] END helper uid=$uid rc=$rc" >> "$helper_log"
    if [ "$rc" -ne 0 ]; then
      echo "$uid rc=$rc" >> "$OUT/failed_uids.log"
    fi
    exit "$rc"
  ) >> "$helper_log" 2>&1 &
}

next_pending_uid() {
  local uid
  for uid in $ALL_UIDS; do
    uid_done "$uid" && continue
    uid_running "$uid" && continue
    uid_failed "$uid" && continue
    echo "$uid"
    return 0
  done
  return 1
}

main() {
  log "night manager started max_parallel=$MAX_PARALLEL"
  while true; do
    if balance_exhausted_seen; then
      log "STOP: balance exhaustion detected"
      exit 75
    fi
    local done_count
    done_count=$(completed_count)
    if [ "$done_count" -ge 30 ]; then
      log "DONE: variance_run2_30 has $done_count answers"
      exit 0
    fi
    local current
    current=$(running_count)
    log "status completed=$done_count running=$current max=$MAX_PARALLEL"
    while [ "$current" -lt "$MAX_PARALLEL" ]; do
      local uid
      uid=$(next_pending_uid) || {
        log "no pending UID available"
        break
      }
      start_uid "$uid"
      sleep 5
      current=$(running_count)
    done
    sleep 120
  done
}

main "$@"
