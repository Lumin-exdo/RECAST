#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EVAL_DIR=$ROOT/STALE/STALE/Evaluation
EMBED=$ROOT/models/all-MiniLM-L6-v2
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
LOG=$RUN_ROOT/run.log
MONITOR_LOG=$RUN_ROOT/monitor.log
ENV_NOLOAD=/tmp/recast_no_env
SAMPLE_TIMEOUT_SECONDS=${SAMPLE_TIMEOUT_SECONDS:-7200}
EXPECTED_ANSWERS=30

mkdir -p "$RUN_ROOT"
: > "$ENV_NOLOAD"

T1_UIDS="89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,a53e0e26"
T2_UIDS="d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641"
ALL_UIDS="${T1_UIDS},${T2_UIDS}"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

wait_for_probe() {
  while pgrep -af "run_new_mem.py.*gpt5mini_openrouter_one" >/dev/null 2>&1; do
    log "waiting for GPT-5-mini one-case probe to finish before starting budget plan"
    sleep 60
  done
}

monitor_loop() {
  echo "[$(date '+%F %T')] monitor started" > "$MONITOR_LOG"
  while true; do
    [ -f "$RUN_ROOT/STOP_MONITOR" ] && exit 0
    local avail
    avail=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
    if [ -n "$avail" ] && [ "$avail" -lt $((20 * 1024 * 1024)) ]; then
      echo "[$(date '+%F %T')] MemAvailable below 20GiB: ${avail}KB; terminating budget plan workers" | tee -a "$MONITOR_LOG"
      pkill -TERM -f "run_new_mem.py.*budget_plan_t15_t15" || true
      sleep 10
      pkill -KILL -f "run_new_mem.py.*budget_plan_t15_t15" || true
      touch "$RUN_ROOT/ABORTED_BY_MONITOR"
      exit 90
    fi
    ps -eo pid=,rss=,cmd= | awk '/run_new_mem.py/ && /budget_plan_t15_t15/ {print $1, $2, substr($0, index($0,$3))}' |
      while read -r pid rss cmd; do
        [ -z "${pid:-}" ] && continue
        if [ "$rss" -gt $((150 * 1024 * 1024)) ]; then
          echo "[$(date '+%F %T')] PID $pid RSS ${rss}KB above 150GiB; terminating: $cmd" | tee -a "$MONITOR_LOG"
          kill -TERM "$pid" || true
          sleep 10
          kill -KILL "$pid" || true
          touch "$RUN_ROOT/ABORTED_BY_MONITOR"
          exit 91
        fi
      done
    sleep 30
  done
}

prepare_env() {
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  export OPENAI_BASE_URL=https://openrouter.ai/api/v1
  export RECAST_MAX_TOKENS=2500
}

answer_count() {
  local answers="$1"
  "$PY" - "$answers" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
if not p.exists():
    print(0)
    raise SystemExit(0)
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print(0)
    raise SystemExit(0)
print(len(data) if isinstance(data, list) else 0)
PY
}

has_complete_answers() {
  local answers="$1"
  local n
  n=$(answer_count "$answers")
  [ "$n" -ge "$EXPECTED_ANSWERS" ]
}

run_recast_generation() {
  local name="$1"
  local model="$2"
  local out="$RUN_ROOT/$name"
  if [ -f "$out/answers.json" ]; then
    if has_complete_answers "$out/answers.json"; then
      log "skip $name: complete answers.json exists ($(answer_count "$out/answers.json") answers)"
      return 0
    fi
    log "remove incomplete answers.json for $name ($(answer_count "$out/answers.json")/$EXPECTED_ANSWERS answers); per-sample answer.json files remain resumable"
    rm -f "$out/answers.json"
  fi
  prepare_env
  export TARGET_MODEL="$model"
  unset RECAST_REASONING_EFFORT
  if [ "$model" = "openai/gpt-5-mini" ]; then
    export RECAST_REASONING_EFFORT=minimal
  fi
  log "START $name model=$model uids=15T1+15T2"
  cd "$ROOT" || exit 1
  local rc=0
  IFS=',' read -r -a uid_array <<< "$ALL_UIDS"
  for uid in "${uid_array[@]}"; do
    uid="${uid//[[:space:]]/}"
    [ -z "$uid" ] && continue
    local one_rc=1
    local attempt
    for attempt in 1 2; do
      log "START $name uid=$uid attempt=$attempt timeout=${SAMPLE_TIMEOUT_SECONDS}s"
      timeout "$SAMPLE_TIMEOUT_SECONDS" "$PY" run_new_mem.py \
        --env-file "$ENV_NOLOAD" \
        --run-name "$name" \
        --output-dir "$out" \
        --uids "$uid" \
        --workers 1 \
        --no-thinking \
        --use-cache \
        --data-path "$DATASET" \
        --embedding-model-path "$EMBED" \
        --embedding-device cpu \
        2>&1 | tee -a "$LOG"
      one_rc=${PIPESTATUS[0]}
      log "END $name uid=$uid attempt=$attempt rc=$one_rc"
      if [ "$one_rc" -eq 0 ]; then
        break
      fi
      if [ "$one_rc" -eq 124 ] && [ "$attempt" -lt 2 ]; then
        log "RETRY $name uid=$uid after timeout; cache will be reused"
        continue
      fi
      break
    done
    if [ "$one_rc" -ne 0 ]; then
      rc="$one_rc"
      if [ "$one_rc" -ne 75 ]; then
        echo "$uid rc=$one_rc" >> "$out/failed_uids.log"
      fi
      if [ "$one_rc" -eq 75 ]; then
        log "STOP $name: balance exhausted at uid=$uid; stopping remaining UIDs"
        return 75
      fi
    fi
  done
  merge_answers "$out"
  log "END $name rc=$rc"
  if has_complete_answers "$out/answers.json"; then
    score_answers "$name" "$out/answers.json"
  else
    log "skip scoring $name: incomplete answers ($(answer_count "$out/answers.json")/$EXPECTED_ANSWERS)"
  fi
  return "$rc"
}

merge_answers() {
  local out="$1"
  "$PY" - "$out" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
answers = []
for p in sorted(out.glob("*/answer.json")):
    try:
        answers.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        pass
answers_path = out / "answers.json"
if answers:
    answers_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Merged {len(answers)} answers -> {answers_path}")
else:
    answers_path.unlink(missing_ok=True)
    print(f"Merged 0 answers; removed stale {answers_path}")
PY
}

run_fair() {
  local method="$1"
  local out="$RUN_ROOT/fair_${method}_30"
  mkdir -p "$out"
  if [ -f "$out/answers.json" ]; then
    if has_complete_answers "$out/answers.json"; then
      log "skip fair-$method: complete answers.json exists ($(answer_count "$out/answers.json") answers)"
      return 0
    fi
    log "remove incomplete answers.json for fair-$method ($(answer_count "$out/answers.json")/$EXPECTED_ANSWERS answers); per-sample answer.json files remain resumable"
    rm -f "$out/answers.json"
  fi
  prepare_env
  export TARGET_MODEL=deepseek/deepseek-v4-flash
  log "START fair-attribution $method uids=15T1+15T2"
  cd "$ROOT" || exit 1
  local fair_log="$out/fair_${method}.last.log"
  : > "$fair_log"
  "$PY" codex_fairness_audit/run_fair_attribution_rerun.py \
    --method "$method" \
    --output-dir "$out" \
    --t1-uids "$T1_UIDS" \
    --t2-uids "$T2_UIDS" \
    --workers 1 \
    2>&1 | tee -a "$LOG" "$fair_log"
  local rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ] && grep -Eqi 'Error code: 402|requires more credits|Insufficient Balance|BALANCE EXHAUSTED' "$fair_log"; then
    log "STOP fair-attribution $method: balance exhausted"
    return 75
  fi
  merge_answers "$out"
  log "END fair-attribution $method rc=$rc"
  if [ "$rc" -eq 0 ] && has_complete_answers "$out/answers.json"; then
    score_answers "fair_${method}_30" "$out/answers.json"
  elif [ "$rc" -eq 0 ]; then
    log "skip scoring fair-$method: incomplete answers ($(answer_count "$out/answers.json")/$EXPECTED_ANSWERS)"
  fi
  return "$rc"
}

score_answers() {
  local name="$1"
  local answers="$2"
  if [ ! -f "$answers" ]; then
    log "skip scoring $name: missing answers file $answers"
    return 0
  fi
  prepare_env
  export JUDGE_PROVIDER=QWEN
  export JUDGE_MODEL=qwen3.6-plus
  export QWEN_API_KEY="${QWEN_API_KEY:-${DASHSCOPE_API_KEY:-}}"
  export QWEN_BASE_URL="${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
  for ctype in T1 T2; do
    local out="$RUN_ROOT/$name/scores_${ctype}_strict.json"
    if [ -f "$out" ]; then
      log "skip scoring $name $ctype: $out exists"
      continue
    fi
    log "START strict judge $name $ctype"
    cd "$EVAL_DIR" || exit 1
    "$PY" full_eval_performance.py \
      --answers-path "$answers" \
      --dataset-path "$DATASET" \
      --output-path "$out" \
      --model-method "$name" \
      --conflict-type "$ctype" \
      --judge-provider QWEN \
      --judge-model qwen3.6-plus \
      --concurrency 2 \
      2>&1 | tee -a "$LOG"
    local rc=${PIPESTATUS[0]}
    log "END strict judge $name $ctype rc=$rc"
  done
}

write_zero_cost_notes() {
  cat > "$RUN_ROOT/zero_cost_tasks.md" <<'EOF'
# Zero-Cost / Manual Tasks In This Plan

- P0 main full strict table: already completed; no new run.
- P0 RECAST failure attribution: use `analysis_output/precise_failure_attribution_v2.md`.
- P0 CupMem failure audit: use existing CupMem strict failure audit artifacts and add qualitative cases manually.
- P1 human validation: API cost is zero, but requires manual annotation time; this script does not fabricate human labels.
- P2 LongMemEval baseline sanity: not run here because there is no confirmed same-harness baseline wrapper in this repo for 15 T1 + 15 T2; LongMemEval has no T1/T2 split.
- P2 cross-rubric: not run here because the existing one-case probe is not exposed as a reusable 30-sample CLI.
- P3 CupMem token cost: not run here because current CupMem wrapper lacks usage logging.
- P3 Zep/LiCoMemory: not run because no runnable local implementation was found.
- Haiku: excluded from batch because the OpenRouter one-case full RECAST run exceeded a reasonable probe window and showed JSON stability/time-cost problems.
EOF
}

main() {
  log "budget plan t15+t15 queued"
  wait_for_probe
  log "budget plan t15+t15 started"
  rm -f "$RUN_ROOT/STOP_MONITOR" "$RUN_ROOT/ABORTED_BY_MONITOR"
  monitor_loop &
  MONITOR_PID=$!
  write_zero_cost_notes

  # Order follows the larger budget-priority table: main/zero-cost first,
  # variance, cross-backbone, then attribution diagnostics.
  for task in \
    "recast variance_run2_30 deepseek/deepseek-v4-flash" \
    "recast variance_run3_30 deepseek/deepseek-v4-flash" \
    "recast cross_gpt4omini_30 openai/gpt-4o-mini" \
    "recast cross_qwen35plus_30 qwen/qwen3.5-plus-20260420" \
    "fair naive_rag" \
    "fair mem0" \
    "fair amem"
  do
    set -- $task
    local kind="$1"
    local rc=0
    if [ "$kind" = "recast" ]; then
      run_recast_generation "$2" "$3"
      rc=$?
    else
      run_fair "$2"
      rc=$?
    fi
    if [ "$rc" -eq 75 ]; then
      log "budget plan stopped because OpenRouter balance is insufficient; top up and rerun to resume"
      touch "$RUN_ROOT/STOP_MONITOR"
      wait "$MONITOR_PID" || true
      exit 75
    fi
  done

  touch "$RUN_ROOT/STOP_MONITOR"
  wait "$MONITOR_PID" || true
  log "budget plan t15+t15 finished"
}

main "$@"
