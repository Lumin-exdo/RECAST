#!/usr/bin/env bash
# Batched runner for cross_qwen35plus_30 (30 UIDs, batch=1, workers=1)
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
RUN_NAME=cross_qwen35plus_30
OUTPUT_DIR=$RUN_ROOT/$RUN_NAME
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
PY=/mnt/laq/venv/bin/python3
LOG=$RUN_ROOT/cross_qwen35plus_manager.log
SAMPLE_TIMEOUT=${SAMPLE_TIMEOUT:-9000}
ENV_NOLOAD=/tmp/recast_no_env_qwen
BATCH_SIZE=1

T1_UIDS="89b77229 7ee76c41 1a85388f f6d12075 d9545076 e229c5cd eacb64ff fdada4cc a4b2e2fd 2006d545 d74f7f3e b17c5c02 b35794f3 7a7621e2 34d402c0"
T2_UIDS="d806d94c feef3933 14897e47 c9cc370e 2c711459 993152aa c03f7b53 60604200 06071a3e 2d92d1c2 fbe6fd55 28daa975 27a52329 830a2e06 a2a3e641"
UIDS="$T1_UIDS $T2_UIDS"

: > "$ENV_NOLOAD"
mkdir -p "$OUTPUT_DIR"
exec 9>"$RUN_ROOT/cross_qwen35plus.lock"
flock -n 9 || { echo "another instance running, exit"; exit 0; }

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

uid_done() {
  local uid="$1"
  local files
  files=$(find "$OUTPUT_DIR" -maxdepth 2 -name answer.json 2>/dev/null)
  [ -n "$files" ] && echo "$files" | xargs grep -l "\"uid\": \"${uid}" >/dev/null 2>&1
}

memory_ok() {
  python3 -c "
import sys
with open('/proc/meminfo') as f:
    for line in f:
        if line.startswith('MemAvailable:'):
            gib = int(line.split()[1]) / 1024 / 1024
            sys.exit(0 if gib >= 30 else 1)
" 2>/dev/null
}

run_batch() {
  local pending="$1"
  local uids_csv
  uids_csv=$(echo "$pending" | tr ' ' ',')
  local first_uid="${pending%% *}"
  local helper_log="$RUN_ROOT/qwen_batch_${first_uid}.log"
  log "START batch: $uids_csv"
  while ! memory_ok; do log "waiting for memory..."; sleep 60; done
  (
    cd "$ROOT" || exit 1
    set -a
    . "$ROOT/.env"
    set +a
    export OPENAI_BASE_URL="${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
    export OPENAI_API_KEY="${DASHSCOPE_API_KEY}"
    export TARGET_MODEL="${QWEN_MODEL:-qwen3.5-plus}"
    export RECAST_MAX_TOKENS="${RECAST_MAX_TOKENS_FOR_CROSS:-5000}"
    timeout "$SAMPLE_TIMEOUT" $PY run_new_mem.py \
      --env-file "$ENV_NOLOAD" \
      --run-name "$RUN_NAME" \
      --output-dir "$OUTPUT_DIR" \
      --uids "$uids_csv" \
      --workers $BATCH_SIZE \
      --no-thinking \
      --use-cache \
      --data-path "$DATASET" \
      --embedding-model-path "$EMBED" \
      --embedding-device cpu
    rc=$?
    echo "[$(date '+%F %T')] END batch=$uids_csv rc=$rc" >> "$helper_log"
    exit $rc
  ) >> "$helper_log" 2>&1
  rc=$?
  if [ $rc -eq 75 ]; then
    log "FATAL balance_exhausted — stopping"
    exit 75
  fi
  log "DONE batch: $uids_csv rc=$rc"
}

log "cross_qwen35plus batched runner started (batch=$BATCH_SIZE workers=$BATCH_SIZE)"

pending=""
pending_count=0

for uid in $UIDS; do
  if uid_done "$uid"; then
    log "SKIP $uid (already done)"
    continue
  fi
  pending="${pending:+$pending }$uid"
  pending_count=$((pending_count + 1))
  if [ "$pending_count" -ge "$BATCH_SIZE" ]; then
    run_batch "$pending"
    pending=""
    pending_count=0
  fi
done

if [ -n "$pending" ]; then
  run_batch "$pending"
fi

log "all UIDs processed"
