#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EVAL_DIR=$ROOT/STALE/STALE/Evaluation
LOG=$RUN_ROOT/cross_autoscore.log

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

threshold_for_run() {
  case "$1" in
    cross_gpt4omini_30) echo 10 ;;
    cross_qwen35plus_30) echo 29 ;;
    *) echo 999999 ;;
  esac
}

sample_count() {
  find "$RUN_ROOT/$1" -maxdepth 2 -type f -name answer.json 2>/dev/null | wc -l
}

answer_json_count() {
  local answers="$1"
  "$PY" - "$answers" <<'PY'
import json, sys
from pathlib import Path
p=Path(sys.argv[1])
if not p.exists():
    print(0); raise SystemExit
try:
    data=json.loads(p.read_text(encoding="utf-8"))
except Exception:
    print(0); raise SystemExit
print(len(data) if isinstance(data, list) else 0)
PY
}

merge_answers() {
  local run="$1"
  "$PY" - "$RUN_ROOT/$run" <<'PY'
import json, sys
from pathlib import Path
out=Path(sys.argv[1])
answers=[]
for p in sorted(out.glob("*/answer.json")):
    try:
        answers.append(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        pass
(out/"answers.json").write_text(json.dumps(answers, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(answers))
PY
}

score_run() {
  local run="$1"
  local threshold answers merged ctype out
  threshold=$(threshold_for_run "$run")
  [ "$(sample_count "$run")" -ge "$threshold" ] || return 0
  answers="$RUN_ROOT/$run/answers.json"
  if [ "$(answer_json_count "$answers")" -lt "$threshold" ]; then
    merged=$(merge_answers "$run")
    log "merged run=$run answers=$merged"
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
    out="$RUN_ROOT/$run/scores_${ctype}_strict.json"
    [ -f "$out" ] && continue
    log "START cross strict judge run=$run ctype=$ctype"
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
    log "END cross strict judge run=$run ctype=$ctype rc=$?"
  done
}

log "cross autoscore started"
while true; do
  score_run cross_gpt4omini_30
  score_run cross_qwen35plus_30
  sleep 300
done
