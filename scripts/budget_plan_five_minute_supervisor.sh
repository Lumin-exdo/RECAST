#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/five_minute_supervisor.log

GPT_SCREEN=recast_cross_gpt_only
VAR_SCREEN=recast_variance_overnight
QWEN_SCREEN=recast_qwen_direct_one

GPT_CMD="cd /mnt/laq/RECAST && GPT_TARGET=10 QWEN_TARGET=0 RECAST_MAX_TOKENS_FOR_CROSS=5000 bash scripts/budget_plan_cross_backbone_queue.sh"
VAR_CMD="cd /mnt/laq/RECAST && bash scripts/budget_plan_variance_overnight_queue.sh"
QWEN_CMD="cd /mnt/laq/RECAST && bash scripts/qwen_direct_one.sh"

mkdir -p "$RUN_ROOT"
exec 9>"$RUN_ROOT/five_minute_supervisor.lock"
flock -n 9 || exit 0

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

count_answers() {
  local run="$1"
  find "$RUN_ROOT/$run" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

screen_active() {
  local screen_name="$1"
  screen -ls 2>/dev/null | awk -v n="$screen_name" 'index($1, "." n) {found=1} END {exit found ? 0 : 1}'
}

worker_active() {
  local run="$1"
  ps -eo args= |
    awk -v run="$run" 'index($0, "run_new_mem.py") && index($0, "--run-name " run) {found=1} END {exit found ? 0 : 1}'
}

any_major_worker_active() {
  worker_active cross_gpt4omini_30 && return 0
  worker_active variance_run2_30 && return 0
  worker_active variance_run3_30 && return 0
  return 1
}

screen_age_seconds() {
  local screen_log="$1"
  [ -f "$screen_log" ] || { echo 999999; return; }
  local now mtime
  now=$(date +%s)
  mtime=$(stat -c %Y "$screen_log" 2>/dev/null || echo 0)
  echo $((now - mtime))
}

restart_screen() {
  local screen_name="$1"
  local cmd="$2"
  log "RESTART screen=$screen_name"
  screen -ls 2>/dev/null | awk -v n="$screen_name" 'index($1, "." n) {print $1}' | while read -r sid; do
    [ -n "$sid" ] && screen -S "$sid" -X quit 2>/dev/null || true
  done
  sleep 2
  screen -dmS "$screen_name" bash -lc "$cmd"
}

ensure_cross() {
  local count age
  count=$(count_answers cross_gpt4omini_30)
  log "cross_gpt4omini_30 count=$count screen=$(screen_active "$GPT_SCREEN" && echo up || echo down) worker=$(worker_active cross_gpt4omini_30 && echo up || echo down)"
  if [ "$count" -lt 10 ]; then
    if ! worker_active cross_gpt4omini_30; then
      if ! screen_active "$GPT_SCREEN"; then
        restart_screen "$GPT_SCREEN" "$GPT_CMD"
      fi
    fi
  fi
}

ensure_variance() {
  local r2 r3 age
  r2=$(count_answers variance_run2_30)
  r3=$(count_answers variance_run3_30)
  log "variance_run2_30=$r2 variance_run3_30=$r3 screen=$(screen_active "$VAR_SCREEN" && echo up || echo down) worker2=$(worker_active variance_run2_30 && echo up || echo down) worker3=$(worker_active variance_run3_30 && echo up || echo down)"
  if [ "$r2" -lt 30 ] || [ "$r3" -lt 30 ]; then
    if ! worker_active variance_run2_30 && ! worker_active variance_run3_30; then
      if ! screen_active "$VAR_SCREEN"; then
        restart_screen "$VAR_SCREEN" "$VAR_CMD"
      fi
    fi
  fi
}

ensure_qwen() {
  local count age
  count=$(count_answers cross_qwen35plus_30)
  log "cross_qwen35plus_30 count=$count screen=$(screen_active "$QWEN_SCREEN" && echo up || echo down) worker=$(worker_active cross_qwen35plus_30 && echo up || echo down)"
  if [ "$(count_answers cross_gpt4omini_30)" -lt 10 ] || [ "$(count_answers variance_run2_30)" -lt 30 ] || [ "$(count_answers variance_run3_30)" -lt 30 ]; then
    return 0
  fi
  if [ "$count" -lt 2 ]; then
    if ! worker_active cross_qwen35plus_30; then
      if ! screen_active "$QWEN_SCREEN"; then
        restart_screen "$QWEN_SCREEN" "$QWEN_CMD"
      fi
    fi
  fi
}

log "five minute supervisor started"
while true; do
  ensure_cross
  ensure_variance
  ensure_qwen
  log "summary answers: cross=$(count_answers cross_gpt4omini_30) variance_run2=$(count_answers variance_run2_30) variance_run3=$(count_answers variance_run3_30) qwen=$(count_answers cross_qwen35plus_30)"
  sleep 300
done
