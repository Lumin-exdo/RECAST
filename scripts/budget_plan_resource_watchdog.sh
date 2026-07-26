#!/usr/bin/env bash
set -uo pipefail

RUN_ROOT=/mnt/laq/RECAST/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/resource_watchdog.log
MIN_AVAILABLE_KB=${MIN_AVAILABLE_KB:-62914560}  # 60 GiB
KILL_AVAILABLE_KB=${KILL_AVAILABLE_KB:-41943040} # 40 GiB
MAX_PROCESS_RSS_KB=${MAX_PROCESS_RSS_KB:-157286400} # 150 GiB

mkdir -p "$RUN_ROOT"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

our_processes() {
  ps -eo pid=,rss=,args= |
    awk '/run_new_mem.py/ && /budget_plan_t15_t15/ {print}
         /run_fair_attribution_rerun.py/ && /budget_plan_t15_t15/ {print}
         /full_eval_performance.py/ && /budget_plan_t15_t15/ {print}'
}

kill_pid() {
  local pid="$1"
  local why="$2"
  log "KILL pid=$pid reason=$why"
  kill -TERM "$pid" 2>/dev/null || true
  sleep 10
  kill -KILL "$pid" 2>/dev/null || true
}

while true; do
  avail=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
  swap_free=$(awk '/SwapFree:/ {print $2}' /proc/meminfo)
  proc_count=$(our_processes | wc -l)
  top_line=$(our_processes | sort -k2,2nr | head -n 1)
  top_pid=$(echo "$top_line" | awk '{print $1}')
  top_rss=$(echo "$top_line" | awk '{print $2}')
  log "MemAvailable=${avail:-unknown}KB SwapFree=${swap_free:-unknown}KB tracked_processes=$proc_count top=${top_pid:-none}:${top_rss:-0}KB"

  if [ -n "${top_pid:-}" ] && [ -n "${top_rss:-}" ] && [ "$top_rss" -gt "$MAX_PROCESS_RSS_KB" ]; then
    kill_pid "$top_pid" "process RSS ${top_rss}KB above ${MAX_PROCESS_RSS_KB}KB"
  elif [ -n "${avail:-}" ] && [ "$avail" -lt "$KILL_AVAILABLE_KB" ]; then
    if [ -n "${top_pid:-}" ]; then
      kill_pid "$top_pid" "MemAvailable ${avail}KB below ${KILL_AVAILABLE_KB}KB"
    else
      log "LOW MEMORY but no tracked process found"
    fi
  elif [ -n "${avail:-}" ] && [ "$avail" -lt "$MIN_AVAILABLE_KB" ]; then
    log "WARN MemAvailable ${avail}KB below ${MIN_AVAILABLE_KB}KB"
  fi
  sleep 60
done
