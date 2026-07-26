#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/comprehensive_monitor.log
START_EPOCH=$(date +%s)

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

answer_counts() {
  find "$RUN_ROOT" -maxdepth 3 -type f -name answer.json 2>/dev/null |
    sed "s#$RUN_ROOT/##" |
    awk -F/ '{count[$1]++} END{for (k in count) printf "%s=%d ", k, count[k]}'
}

score_files() {
  find "$RUN_ROOT" -maxdepth 2 -type f -name 'scores_*_strict.json' 2>/dev/null |
    sed "s#$RUN_ROOT/##" |
    tr '\n' ' '
}

tracked_processes() {
  ps -eo pid=,rss=,args= |
    awk '(/run_new_mem.py/ || /run_fair_attribution_rerun.py/ || /full_eval_performance.py/) && /budget_plan_t15_t15/ {print}'
}

kill_openrouter_generation() {
  local reason="$1"
  log "FATAL OpenRouter generation error detected: $reason"
  for s in recast_resume_run2_one recast_resume_run3_one fair_mem0_30 recast_mgr_run2 recast_mgr_run3 recast_mgr_gpt4o recast_mgr_qwen recast_mgr_run2_boost recast_mgr_run3_boost recast_mgr_gpt4o_boost recast_mgr_qwen_boost budget_plan_t15; do
    screen -S "$s" -X quit 2>/dev/null || true
  done
  while read -r pid; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done < <(tracked_processes | awk '/run_new_mem.py/ || /run_fair_attribution_rerun.py/ {print $1}')
  sleep 2
  while read -r pid; do
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
  done < <(tracked_processes | awk '/run_new_mem.py/ || /run_fair_attribution_rerun.py/ {print $1}')
}

check_recent_fatal_logs() {
  local f
  while IFS= read -r -d '' f; do
    local base
    base=$(basename "$f")
    case "$base" in
      overnight_*|cross_cross_*|manager_*|night_helper_*|cross_backbone_queue.log|variance_overnight_queue.log|monitor.log|autoscore.log|answer_sanity_watch.log|resource_watchdog.log|comprehensive_monitor.log|qwen_direct_one.log|cross_qwen35plus*.log|qwen_batch_*.log|qwen_helper_*.log)
        continue
        ;;
    esac
    if tail -n 120 "$f" 2>/dev/null | grep -Eqi 'Error code: 402|requires more credits|Insufficient credits|BALANCE EXHAUSTED'; then
      kill_openrouter_generation "$f has 402/balance exhaustion"
      return 0
    fi
    if tail -n 120 "$f" 2>/dev/null | grep -Eqi 'AuthenticationError|ModuleNotFoundError|No available channel|invalid model|Traceback|Killed|OOM|out of memory'; then
      log "FATAL non-balance error seen in $f"
    fi
    retry_count=$(tail -n 120 "$f" 2>/dev/null | grep -Ec '\[API ERROR\].*retrying' || true)
    if [ "${retry_count:-0}" -gt 0 ]; then
      log "retry warnings in $f count_last120=$retry_count"
    fi
    if tail -n 120 "$f" 2>/dev/null | grep -Eqi 'failed — skipping'; then
      log "FATAL final API failure seen in $f"
      kill_openrouter_generation "$f has final failed-skipping"
      return 0
    fi
  done < <(find "$RUN_ROOT" -maxdepth 1 -type f \( -name '*.log' -o -name '*.screen.log' \) -newermt "@$START_EPOCH" -print0 2>/dev/null)
}

log "comprehensive monitor started start_epoch=$START_EPOCH"
while true; do
  mem_avail=$(awk '/MemAvailable:/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
  swap_free=$(awk '/SwapFree:/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
  proc_summary=$(tracked_processes | awk '{n++; rss+=$2} END{printf "tracked=%d rssGiB=%.1f", n+0, rss/1024/1024}')
  log "status memAvailGiB=$mem_avail swapFreeGiB=$swap_free $proc_summary answers=$(answer_counts) scores=$(score_files)"
  check_recent_fatal_logs
  sleep 30
done
