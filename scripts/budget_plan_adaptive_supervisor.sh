#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/adaptive_supervisor.log

mkdir -p "$RUN_ROOT"

T1_UIDS="89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,a53e0e26"
T2_UIDS="d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

answer_count() {
  local run="$1"
  find "$RUN_ROOT/$run" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

screen_exists() {
  local name="$1"
  screen -ls | grep -q "[.]${name}[[:space:]]"
}

recent_balance_error() {
  local f
  while IFS= read -r -d '' f; do
    case "$(basename "$f")" in
      run.log|driver.screen.log)
        continue
        ;;
    esac
    if tail -n 200 "$f" 2>/dev/null | grep -Eqi "Error code: 402|requires more credits|Insufficient Balance|BALANCE EXHAUSTED"; then
      return 0
    fi
  done < <(find "$RUN_ROOT" -maxdepth 1 -type f \( -name '*.log' -o -name '*.screen.log' \) -mmin -15 -print0)
  return 1
}

mem_available_gib() {
  awk '/MemAvailable:/ {printf "%.0f", $2/1024/1024}' /proc/meminfo
}

tracked_rss_gib() {
  ps -eo rss,args |
    awk '(/run_new_mem.py/ || /run_fair_attribution_rerun.py/) && /budget_plan_t15_t15/ {sum+=$1} END {printf "%.1f", sum/1024/1024}'
}

tracked_python_count() {
  ps -eo args |
    awk '(/run_new_mem.py/ || /run_fair_attribution_rerun.py/) && /budget_plan_t15_t15/ && $1 ~ /python/ {count++} END {print count + 0}'
}

ensure_recast_manager() {
  local screen_name="$1"
  local run_name="$2"
  local model="$3"
  local max_parallel="$4"
  local count
  count=$(answer_count "$run_name")
  if [ "$count" -ge 30 ]; then
    return 0
  fi
  if screen_exists "$screen_name"; then
    return 0
  fi
  log "restart manager $screen_name run=$run_name count=$count max_parallel=$max_parallel"
  screen -dmS "$screen_name" bash -lc "cd '$ROOT' && bash scripts/run_recast_task_manager.sh '$run_name' '$model' '$max_parallel'"
}

ensure_fair_task() {
  local screen_name="$1"
  local method="$2"
  local workers="$3"
  local py="/mnt/laq/venv/bin/python3"
  local extra_env=""
  local count
  count=$(answer_count "fair_${method}_30")
  if [ "$count" -ge 30 ]; then
    return 0
  fi
  if screen_exists "$screen_name"; then
    return 0
  fi
  if [ "$method" = "mem0" ] || [ "$method" = "amem" ]; then
    py="/mnt/laq/mem0_eval/venv/bin/python"
    extra_env="PYTHONPATH=$ROOT"
  fi
  if [ "$method" = "amem" ]; then
    extra_env="$extra_env AMEM_FAST_INGEST=1"
  fi
  log "restart fair $method count=$count workers=$workers"
  screen -dmS "$screen_name" bash -lc "cd '$ROOT' && set -a && . ./.env && set +a && export OPENAI_BASE_URL=https://openrouter.ai/api/v1 TARGET_MODEL=deepseek/deepseek-v4-flash RECAST_MAX_TOKENS=2500 $extra_env && '$py' codex_fairness_audit/run_fair_attribution_rerun.py --method '$method' --output-dir '$RUN_ROOT/fair_${method}_30' --t1-uids '$T1_UIDS' --t2-uids '$T2_UIDS' --workers '$workers' > '$RUN_ROOT/fair_${method}_30.screen.log' 2>&1"
}

main() {
  log "adaptive supervisor started"
  while true; do
    local mem rss workers
    mem=$(mem_available_gib)
    rss=$(tracked_rss_gib)
    workers=$(tracked_python_count)
    log "status mem_available=${mem}GiB tracked_rss=${rss}GiB workers=$workers counts run2=$(answer_count variance_run2_30) run3=$(answer_count variance_run3_30) gpt4omini=$(answer_count cross_gpt4omini_30) qwen=$(answer_count cross_qwen35plus_30) naive=$(answer_count fair_naive_rag_30) mem0=$(answer_count fair_mem0_30) amem=$(answer_count fair_amem_30)"

    if recent_balance_error; then
      log "recent balance error detected; not starting new workers this cycle"
      sleep 180
      continue
    fi

    if [ "$mem" -lt 20 ]; then
      log "memory below 20GiB; not starting new workers this cycle"
      sleep 180
      continue
    fi

    ensure_recast_manager recast_mgr_run2_boost variance_run2_30 deepseek/deepseek-v4-flash 6
    ensure_recast_manager recast_mgr_run3_boost variance_run3_30 deepseek/deepseek-v4-flash 5
    ensure_recast_manager recast_mgr_gpt4o_boost cross_gpt4omini_30 openai/gpt-4o-mini 3
    ensure_recast_manager recast_mgr_qwen_boost cross_qwen35plus_30 qwen/qwen3.5-plus-20260420 3
    ensure_fair_task fair_naive_rag_30 naive_rag 2
    ensure_fair_task fair_mem0_30 mem0 1
    ensure_fair_task fair_amem_30 amem 1

    sleep 180
  done
}

main "$@"
