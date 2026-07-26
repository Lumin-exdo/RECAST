#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/resume_error_watchdog.log

declare -A JOBS=(
  [recast_resume_run2_one]=resume_run2_7ee76c41.log
  [recast_resume_run3_one]=resume_run3_89b77229.log
)

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

kill_job() {
  local screen_name="$1"
  local run_name="$2"
  local uid="$3"
  log "stopping $screen_name run=$run_name uid=$uid"
  screen -S "$screen_name" -X quit 2>/dev/null || true
  while read -r pid; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null || true
  done < <(ps -eo pid=,args= | awk -v run="$run_name" -v uid="$uid" '/run_new_mem.py/ && index($0, "--run-name " run) && index($0, "--uids " uid) {print $1}')
  sleep 2
  while read -r pid; do
    [ -n "$pid" ] && kill -KILL "$pid" 2>/dev/null || true
  done < <(ps -eo pid=,args= | awk -v run="$run_name" -v uid="$uid" '/run_new_mem.py/ && index($0, "--run-name " run) && index($0, "--uids " uid) {print $1}')
}

log "resume error watchdog started"
while true; do
  for screen_name in "${!JOBS[@]}"; do
    file="$RUN_ROOT/${JOBS[$screen_name]}"
    [ -f "$file" ] || continue
    if tail -n 120 "$file" | grep -Eqi 'Error code: 402|requires more credits|Insufficient credits|BALANCE EXHAUSTED|Traceback|AuthenticationError|ModuleNotFoundError|No available channel|invalid model|Killed|OOM|out of memory|failed — skipping'; then
      if [ "$screen_name" = "recast_resume_run2_one" ]; then
        kill_job "$screen_name" "variance_run2_30" "7ee76c41"
      else
        kill_job "$screen_name" "variance_run3_30" "89b77229"
      fi
    fi
  done
  sleep 5
done
