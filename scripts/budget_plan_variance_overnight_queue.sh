#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
ENV_NOLOAD=/tmp/recast_no_env
LOG=$RUN_ROOT/variance_overnight_queue.log
TARGET_PER_RUN=${TARGET_PER_RUN:-30}
SAMPLE_TIMEOUT_SECONDS=${SAMPLE_TIMEOUT_SECONDS:-7200}
RECAST_MAX_TOKENS_FOR_QUEUE=${RECAST_MAX_TOKENS_FOR_QUEUE:-5000}
MIN_MEM_GIB=${MIN_MEM_GIB:-18}
RUN_WORKERS_PER_JOB=${RUN_WORKERS_PER_JOB:-3}

T1_UIDS="89b77229 7ee76c41 1a85388f f6d12075 d9545076 e229c5cd eacb64ff fdada4cc a4b2e2fd 2006d545 d74f7f3e b17c5c02 b35794f3 7a7621e2 a53e0e26"
T2_UIDS="d806d94c feef3933 14897e47 c9cc370e 2c711459 993152aa c03f7b53 60604200 06071a3e 2d92d1c2 fbe6fd55 28daa975 27a52329 830a2e06 a2a3e641"
ALL_UIDS="$T1_UIDS $T2_UIDS"

mkdir -p "$RUN_ROOT" "$RUN_ROOT/variance_run2_30" "$RUN_ROOT/variance_run3_30"
: > "$ENV_NOLOAD"
exec 9>"$RUN_ROOT/variance_overnight_queue.lock"
flock -n 9 || exit 0

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

completed_count() {
  local run="$1"
  find "$RUN_ROOT/$run" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

uid_done() {
  local run="$1"
  local uid="$2"
  find "$RUN_ROOT/$run" -maxdepth 2 -type f -name answer.json -print0 2>/dev/null |
    xargs -0 grep -l "\"uid\": \"${uid}" >/dev/null 2>&1
}

uid_failed() {
  local run="$1"
  local uid="$2"
  local failed_file="$RUN_ROOT/$run/failed_uids.log"
  [ -f "$failed_file" ] || return 1
  grep -q "^${uid}[[:space:]]rc=" "$failed_file" 2>/dev/null
}

uid_running() {
  local run="$1"
  local uid="$2"
  local n
  n=$(ps -eo args= 2>/dev/null | grep -F 'run_new_mem.py' | grep -F -- "--run-name ${run}" | grep -cF -- "--uids ${uid}" 2>/dev/null) || n=0
  [ "${n:-0}" -gt 0 ]
}

run_has_active_worker() {
  local run="$1"
  local n
  n=$(ps -eo args= 2>/dev/null | grep -F 'run_new_mem.py' | grep -cF -- "--run-name ${run}" 2>/dev/null) || n=0
  [ "${n:-0}" -gt 0 ]
}

recent_fatal() {
  local f
  while IFS= read -r -d '' f; do
    local base
    base=$(basename "$f")
    case "$base" in
      overnight_*|cross_cross_*|manager_*|night_helper_*|cross_backbone_queue.log|variance_overnight_queue.log|monitor.log|autoscore.log|answer_sanity_watch.log|resource_watchdog.log|comprehensive_monitor.log|qwen_direct_one.log|cross_qwen35plus*.log|qwen_batch_*.log|qwen_helper_*.log)
        continue
        ;;
    esac
    if tail -n 160 "$f" 2>/dev/null | grep -Eqi 'Error code: 402|requires more credits|Insufficient credits|Insufficient Balance|BALANCE EXHAUSTED|AuthenticationError|ModuleNotFoundError|No available channel|invalid model|Traceback|Killed|OOM|out of memory|failed — skipping|failed -- skipping'; then
      log "STOP: fatal pattern found in $f"
      return 0
    fi
  done < <(find "$RUN_ROOT" -maxdepth 1 -type f \( -name '*.log' -o -name '*.screen.log' \) \
      -mmin -20 -print0 2>/dev/null)
  return 1
}

memory_ok() {
  local avail
  avail=$("$PY" -c "
with open('/proc/meminfo') as f:
    for line in f:
        if line.startswith('MemAvailable:'):
            print(int(line.split()[1]) // 1024 // 1024)
            break
" 2>/dev/null)
  [ "${avail:-0}" -ge "$MIN_MEM_GIB" ]
}

next_uid() {
  local run="$1"
  local uid
  for uid in $ALL_UIDS; do
    uid_done "$run" "$uid" && continue
    uid_failed "$run" "$uid" && continue
    uid_running "$run" "$uid" && continue
    echo "$uid"
    return 0
  done
  return 1
}

start_one() {
  local run="$1"
  local uid="$2"
  local out="$RUN_ROOT/$run"
  local helper_log="$RUN_ROOT/overnight_${run}_${uid}.log"
  log "START run=$run uid=$uid"
  (
    cd "$ROOT" || exit 1
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
    export OPENAI_BASE_URL=https://api.deepseek.com
    export OPENAI_API_KEY="${DEEPSEEK_API_KEY}"
    export TARGET_MODEL=deepseek-v4-flash
    export RECAST_MAX_TOKENS="$RECAST_MAX_TOKENS_FOR_QUEUE"
    timeout "$SAMPLE_TIMEOUT_SECONDS" "$PY" run_new_mem.py \
      --env-file "$ENV_NOLOAD" \
      --run-name "$run" \
      --output-dir "$out" \
      --uids "$uid" \
      --workers "$RUN_WORKERS_PER_JOB" \
      --no-thinking \
      --use-cache \
      --data-path "$DATASET" \
      --embedding-model-path "$EMBED" \
      --embedding-device cpu
    rc=$?
    echo "[$(date '+%F %T')] END run=$run uid=$uid rc=$rc" >> "$helper_log"
    if [ "$rc" -ne 0 ]; then
      echo "$uid rc=$rc" >> "$out/failed_uids.log"
    fi
    exit "$rc"
  ) >> "$helper_log" 2>&1 &
}

ensure_run_progress() {
  local run="$1"
  local n uid
  n=$(completed_count "$run")
  if [ "$n" -ge "$TARGET_PER_RUN" ]; then
    return 0
  fi
  if run_has_active_worker "$run"; then
    return 0
  fi
  uid=$(next_uid "$run") || {
    log "no pending UID for $run"
    return 0
  }
  start_one "$run" "$uid"
}

main() {
  log "variance overnight queue started target_per_run=$TARGET_PER_RUN max_tokens=$RECAST_MAX_TOKENS_FOR_QUEUE min_mem_gib=$MIN_MEM_GIB workers=$RUN_WORKERS_PER_JOB"
  while true; do
    log "status run2=$(completed_count variance_run2_30)/$TARGET_PER_RUN run3=$(completed_count variance_run3_30)/$TARGET_PER_RUN"
    if recent_fatal; then
      log "fatal detected; queue exits without starting more paid calls"
      exit 75
    fi
    if ! memory_ok; then
      log "memory below threshold; wait"
      sleep 120
      continue
    fi
    ensure_run_progress variance_run2_30
    ensure_run_progress variance_run3_30
    sleep 120
  done
}

main "$@"
