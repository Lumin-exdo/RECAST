#!/usr/bin/env bash

set -uo pipefail

ROOT=/mnt/laq/RECAST
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
RUN_ROOT=$ROOT/runs/variance_budget_60
LOG=$RUN_ROOT/run.log
MONITOR_LOG=$RUN_ROOT/monitor.log
MEM_RSS_LIMIT_KB=$((150 * 1024 * 1024))
SYS_AVAILABLE_FLOOR_KB=$((20 * 1024 * 1024))

mkdir -p "$RUN_ROOT"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"
}

kill_recast_children() {
  local reason="$1"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] KILL requested: $reason" | tee -a "$MONITOR_LOG"
  pgrep -af "run_new_mem.py.*variance_budget_60" | tee -a "$MONITOR_LOG" || true
  pkill -TERM -f "run_new_mem.py.*variance_budget_60" || true
  sleep 10
  pkill -KILL -f "run_new_mem.py.*variance_budget_60" || true
}

monitor_loop() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] monitor started" > "$MONITOR_LOG"
  while true; do
    if [ -f "$RUN_ROOT/STOP_MONITOR" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] monitor stopping normally" >> "$MONITOR_LOG"
      exit 0
    fi

    local avail
    avail=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
    if [ -n "$avail" ] && [ "$avail" -lt "$SYS_AVAILABLE_FLOOR_KB" ]; then
      kill_recast_children "system MemAvailable below 20GiB (${avail}KB)"
      touch "$RUN_ROOT/ABORTED_BY_MONITOR"
      exit 90
    fi

    while read -r pid rss cmd; do
      [ -z "${pid:-}" ] && continue
      if [ "$rss" -gt "$MEM_RSS_LIMIT_KB" ]; then
        kill_recast_children "RECAST process $pid RSS above 150GiB (${rss}KB): $cmd"
        touch "$RUN_ROOT/ABORTED_BY_MONITOR"
        exit 91
      fi
    done < <(ps -eo pid=,rss=,cmd= | awk '/run_new_mem.py/ && /variance_budget_60/ {print $1, $2, substr($0, index($0,$3))}')

    if grep -Eiq "BALANCE EXHAUSTED|Insufficient Balance|Traceback|Killed|CUDA out of memory|Cannot allocate memory|object has no attribute 'choices'|<!DOCTYPE html" "$LOG" 2>/dev/null; then
      kill_recast_children "fatal pattern seen in run log"
      touch "$RUN_ROOT/ABORTED_BY_MONITOR"
      exit 92
    fi

    sleep 30
  done
}

run_batch() {
  local run_name="$1"
  local batch="$2"
  local out_dir="$RUN_ROOT/$run_name"
  local batch_safe
  batch_safe=$(echo "${run_name}_${batch}" | tr -c 'A-Za-z0-9_' '_')
  local batch_log="$RUN_ROOT/batch_${batch_safe}.log"
  local attempt=1

  while [ "$attempt" -le 3 ]; do
    : > "$batch_log"
    log ">>> $run_name batch attempt $attempt/3: $batch"
    cd "$ROOT" || exit 1
    "$PY" run_new_mem.py \
      --run-name "$run_name" \
      --output-dir "$out_dir" \
      --uids "$batch" \
      --workers 2 \
      --startup-stagger 15 \
      --no-thinking \
      --data-path "$DATASET" \
      --embedding-model-path "$EMBED" \
      --embedding-device cpu \
      2>&1 | tee -a "$LOG" "$batch_log"
    local rc=${PIPESTATUS[0]}
    log "<<< $run_name batch attempt $attempt exit=$rc"
    if [ "$rc" -eq 75 ]; then
      log "Balance exhausted. Re-run this same script after recharge; completed answer.json files will be skipped."
      exit 75
    fi
    if [ "$rc" -ne 0 ]; then
      log "Non-zero exit. Stop for inspection."
      exit "$rc"
    fi
    if [ -f "$RUN_ROOT/ABORTED_BY_MONITOR" ]; then
      log "Monitor aborted the run. Stop for inspection."
      exit 93
    fi
    if grep -Eiq "statement_extraction failed|failed — skipping|will use default confidence|global impression NOT updated|failed for .*retrying in 3s|_error" "$batch_log"; then
      log "Batch $run_name $batch has terminal degraded API output; deleting this batch checkpoints and retrying."
      "$PY" "$ROOT/scripts/variance_budget_60.py" delete-uids --run-name "$run_name" --uids "$batch" 2>&1 | tee -a "$LOG"
      "$PY" "$ROOT/scripts/variance_budget_60.py" build 2>&1 | tee -a "$LOG"
      sleep 180
      attempt=$((attempt + 1))
      continue
    fi
    return 0
  done

  log "Batch $run_name $batch failed clean-output validation after 3 attempts. Stop for manual inspection."
  exit 94
}

main() {
  if [ -f "$LOG" ]; then
    mv "$LOG" "$LOG.$(date '+%Y%m%d_%H%M%S').bak"
  fi
  log "=== variance_budget_60 start ==="
  log "Policy: T1 reuses existing run2/run3 checkpoints; T2 runs new, 30 UIDs x run2/run3."
  log "workers=2, batch_size=2, startup_stagger=15s, RSS kill threshold=150GiB, system MemAvailable floor=20GiB."

  rm -f "$RUN_ROOT/STOP_MONITOR" "$RUN_ROOT/ABORTED_BY_MONITOR" "$RUN_ROOT/INFERENCE_DONE" "$RUN_ROOT/READY_FOR_SEMANTIC_REVIEW"
  "$PY" "$ROOT/scripts/variance_budget_60.py" prepare 2>&1 | tee -a "$LOG"
  "$PY" "$ROOT/scripts/variance_budget_60.py" status 2>&1 | tee -a "$LOG" || true

  monitor_loop &
  MONITOR_PID=$!
  log "monitor pid=$MONITOR_PID"

  export RECAST_INNER_WORKERS=2
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1

  for run_name in run2_variance run3_variance; do
    while IFS= read -r batch; do
      [ -z "$batch" ] && continue
      run_batch "$run_name" "$batch"
      "$PY" "$ROOT/scripts/variance_budget_60.py" build 2>&1 | tee -a "$LOG"
      "$PY" "$ROOT/scripts/variance_budget_60.py" lint 2>&1 | tee -a "$LOG"
    done < "$RUN_ROOT/t2_batches.txt"
  done

  "$PY" "$ROOT/scripts/variance_budget_60.py" build 2>&1 | tee -a "$LOG"
  "$PY" "$ROOT/scripts/variance_budget_60.py" status 2>&1 | tee -a "$LOG"
  "$PY" "$ROOT/scripts/variance_budget_60.py" lint 2>&1 | tee -a "$LOG"
  touch "$RUN_ROOT/INFERENCE_DONE" "$RUN_ROOT/READY_FOR_SEMANTIC_REVIEW"
  log "Inference done. Strict judge intentionally not started; semantic reading is required first."

  touch "$RUN_ROOT/STOP_MONITOR"
  wait "$MONITOR_PID" || true
  log "=== variance_budget_60 inference complete ==="
}

main "$@"
