#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EVAL_DIR=$ROOT/STALE/STALE/Evaluation
LOG=$RUN_ROOT/autoscore.log

mkdir -p "$RUN_ROOT"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

answer_json_count() {
  local answers="$1"
  "$PY" - "$answers" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
if not p.exists():
    print(0)
    raise SystemExit
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print(0)
    raise SystemExit
print(len(data) if isinstance(data, list) else 0)
PY
}

sample_count() {
  local run="$1"
  find "$RUN_ROOT/$run" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

score_threshold() {
  local run="$1"
  case "$run" in
    cross_gpt4omini_30)
      echo 10
      ;;
    cross_qwen35plus_30)
      echo 2
      ;;
    *)
      echo 30
      ;;
  esac
}

merge_answers() {
  local run="$1"
  "$PY" - "$RUN_ROOT/$run" <<'PY'
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
(out / "answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(answers))
PY
}

score_run() {
  local run="$1"
  local answers="$RUN_ROOT/$run/answers.json"
  local threshold
  threshold=$(score_threshold "$run")
  if [ "$(answer_json_count "$answers")" -lt "$threshold" ]; then
    return 0
  fi

  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
  export JUDGE_PROVIDER=QWEN
  export JUDGE_MODEL=qwen3.6-plus
  export QWEN_API_KEY="${QWEN_API_KEY:-${DASHSCOPE_API_KEY:-}}"
  export QWEN_BASE_URL="${QWEN_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"

  for ctype in T1 T2; do
    local out="$RUN_ROOT/$run/scores_${ctype}_strict.json"
    if [ -f "$out" ]; then
      continue
    fi
    log "START strict judge run=$run ctype=$ctype"
    (
      cd "$EVAL_DIR" || exit 1
      "$PY" full_eval_performance.py \
        --answers-path "$answers" \
        --dataset-path "$DATASET" \
        --output-path "$out" \
        --model-method "$run" \
        --conflict-type "$ctype" \
        --judge-provider QWEN \
        --judge-model qwen3.6-plus \
        --concurrency 2
    ) >> "$LOG" 2>&1
    log "END strict judge run=$run ctype=$ctype rc=$?"
  done
}

process_run() {
  local run="$1"
  local n merged threshold
  n=$(sample_count "$run")
  threshold=$(score_threshold "$run")
  if [ "$n" -lt "$threshold" ]; then
    return 0
  fi
  if [ "$(answer_json_count "$RUN_ROOT/$run/answers.json")" -lt "$threshold" ]; then
    merged=$(merge_answers "$run")
    log "merged run=$run answers=$merged"
  fi
  score_run "$run"
}

main() {
  log "autoscore started"
  while true; do
    for run in \
      variance_run2_30 \
      variance_run3_30 \
      cross_gpt4omini_30 \
      cross_qwen35plus_30 \
      fair_naive_rag_30 \
      fair_mem0_30 \
      fair_amem_30
    do
      process_run "$run"
    done
    sleep 300
  done
}

main "$@"
