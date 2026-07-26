#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
ENV_NOLOAD=/tmp/recast_no_env
LOG=$RUN_ROOT/cross_backbone_queue.log
SAMPLE_TIMEOUT_SECONDS=${SAMPLE_TIMEOUT_SECONDS:-9000}
RECAST_MAX_TOKENS_FOR_CROSS=${RECAST_MAX_TOKENS_FOR_CROSS:-5000}

# Budget-aware overnight targets, based on measured one-sample costs:
# GPT-4o-mini ~= $0.27/sample; Qwen3.5-plus ~= $1.00/sample.
GPT_TARGET=${GPT_TARGET:-10}
QWEN_TARGET=${QWEN_TARGET:-2}
QWEN_BASE_URL=${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}
QWEN_API_KEY=${QWEN_API_KEY:-${DASHSCOPE_API_KEY:-}}
QWEN_MODEL=${QWEN_MODEL:-qwen3.5-plus}

GPT_RUN=cross_gpt4omini_30
GPT_MODEL=openai/gpt-4o-mini
QWEN_RUN=cross_qwen35plus_30
QWEN_DIRECT_MODEL="$QWEN_MODEL"

T1_UIDS="89b77229 7ee76c41 1a85388f f6d12075 d9545076"
T2_UIDS="d806d94c feef3933 14897e47 c9cc370e 2c711459"
GPT_UIDS="$T1_UIDS $T2_UIDS"
QWEN_UIDS="89b77229 d806d94c"

mkdir -p "$RUN_ROOT/$GPT_RUN" "$RUN_ROOT/$QWEN_RUN"
: > "$ENV_NOLOAD"
exec 9>"$RUN_ROOT/cross_backbone_queue.lock"
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
  local helper_log="$RUN_ROOT/cross_${run}_${uid}.log"
  if [ -f "$helper_log" ] && tail -n 120 "$helper_log" 2>/dev/null | grep -Eq 'rc=(137|143)|Killed|Terminated'; then
    return 0
  fi
  [ -f "$failed_file" ] || return 1
  grep -q "^${uid}[[:space:]]rc=(137|143)$" "$failed_file" 2>/dev/null
}

run_has_active_worker() {
  local run="$1"
  ps -eo args= |
    awk -v run="$run" 'index($0, "run_new_mem.py") && index($0, "--run-name " run) && $0 !~ /awk/ && $0 !~ /budget_plan_cross_backbone_queue/ {found=1} END {exit found ? 0 : 1}'
}

any_cross_worker() {
  ps -eo args= |
    awk 'index($0, "run_new_mem.py") && (index($0, "--run-name cross_gpt4omini_30") || index($0, "--run-name cross_qwen35plus_30")) && $0 !~ /awk/ && $0 !~ /budget_plan_cross_backbone_queue/ {found=1} END {exit found ? 0 : 1}'
}

recent_fatal() {
  local f
  while IFS= read -r -d '' f; do
    local base
    base=$(basename "$f")
    case "$base" in
      overnight_*|cross_cross_*|manager_*|night_helper_*|cross_backbone_queue.log|variance_overnight_queue.log|monitor.log|autoscore.log|answer_sanity_watch.log|resource_watchdog.log|comprehensive_monitor.log|qwen_direct_one.log|cross_qwen35plus*.log)
        continue
        ;;
    esac
    if tail -n 160 "$f" 2>/dev/null | grep -Eqi 'Error code: 401|invalid_api_key|Error code: 402|requires more credits|Insufficient credits|Insufficient Balance|BALANCE EXHAUSTED|AuthenticationError|ModuleNotFoundError|No available channel|invalid model|Traceback|Killed|OOM|out of memory|failed — skipping|failed -- skipping'; then
      log "STOP: fatal pattern found in $f"
      return 0
    fi
  done < <(find "$RUN_ROOT" -maxdepth 1 -type f \( -name '*.log' -o -name '*.screen.log' \) \
      ! -name 'cross_backbone_queue.log' \
      ! -name 'comprehensive_monitor.log' \
      ! -name 'monitor.log' \
      ! -name 'autoscore.log' \
      ! -name 'answer_sanity_watch.log' \
      ! -name 'resource_watchdog.log' \
      ! -name 'cross_cross_*.log' \
      ! -name 'manager_*.log' \
      -mmin -20 -print0 2>/dev/null)
  return 1
}

memory_ok() {
  local avail
  avail=$(awk '/MemAvailable:/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
  [ "${avail:-0}" -ge 20 ]
}

next_uid() {
  local run="$1"
  local uid_list="$2"
  local uid
  for uid in $uid_list; do
    uid_done "$run" "$uid" && continue
    uid_failed "$run" "$uid" && continue
    echo "$uid"
    return 0
  done
  return 1
}

start_one() {
  local run="$1"
  local model="$2"
  local uid="$3"
  local provider="$4"
  local out="$RUN_ROOT/$run"
  local helper_log="$RUN_ROOT/cross_${run}_${uid}.log"
  log "START run=$run model=$model provider=$provider uid=$uid max_tokens=$RECAST_MAX_TOKENS_FOR_CROSS"
  (
    cd "$ROOT" || exit 1
    set -a
    # shellcheck disable=SC1091
    . "$ROOT/.env"
    set +a
    if [ "$provider" = "qwen" ]; then
      export OPENAI_BASE_URL="$QWEN_BASE_URL"
      export OPENAI_API_KEY="$QWEN_API_KEY"
      export TARGET_MODEL="$QWEN_DIRECT_MODEL"
    else
      export OPENAI_BASE_URL=https://openrouter.ai/api/v1
      export TARGET_MODEL="$model"
    fi
    export RECAST_MAX_TOKENS="$RECAST_MAX_TOKENS_FOR_CROSS"
    timeout "$SAMPLE_TIMEOUT_SECONDS" "$PY" run_new_mem.py \
      --env-file "$ENV_NOLOAD" \
      --run-name "$run" \
      --output-dir "$out" \
      --uids "$uid" \
      --workers 1 \
      --no-thinking \
      --use-cache \
      --data-path "$DATASET" \
      --embedding-model-path "$EMBED" \
      --embedding-device cpu
    rc=$?
    echo "[$(date '+%F %T')] END run=$run uid=$uid rc=$rc" >> "$helper_log"
    exit "$rc"
  ) >> "$helper_log" 2>&1 &
}

maybe_start_run() {
  local run="$1"
  local model="$2"
  local target="$3"
  local uid_list="$4"
  local n uid
  n=$(completed_count "$run")
  if [ "$n" -ge "$target" ]; then
    return 1
  fi
  if run_has_active_worker "$run"; then
    return 0
  fi
  uid=$(next_uid "$run" "$uid_list") || {
    log "no pending UID for $run"
    return 1
  }
  local provider="${5:-gpt}"
  start_one "$run" "$model" "$uid" "$provider"
  return 0
}

main() {
  log "cross backbone queue started gpt_target=$GPT_TARGET qwen_target=$QWEN_TARGET max_tokens=$RECAST_MAX_TOKENS_FOR_CROSS"
  while true; do
    log "status gpt=$(completed_count "$GPT_RUN")/$GPT_TARGET qwen=$(completed_count "$QWEN_RUN")/$QWEN_TARGET"
    if recent_fatal; then
      log "fatal detected; queue exits without starting more paid calls"
      exit 75
    fi
    if ! memory_ok; then
      log "memory below threshold; wait"
      sleep 120
      continue
    fi
    if any_cross_worker; then
      sleep 120
      continue
    fi
    # Prefer the cheaper, more statistically useful GPT-4o-mini backbone.
    maybe_start_run "$GPT_RUN" "$GPT_MODEL" "$GPT_TARGET" "$GPT_UIDS" "gpt" || \
      maybe_start_run "$QWEN_RUN" "$QWEN_MODEL" "$QWEN_TARGET" "$QWEN_UIDS" "qwen" || \
      log "all cross-backbone targets reached or no pending UID"
    sleep 120
  done
}

main "$@"
