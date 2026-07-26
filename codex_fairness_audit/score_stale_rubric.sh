#!/usr/bin/env bash
set -euo pipefail

root=/mnt/laq/RECAST
run="${1:?run directory required}"
task="${2:?T1 or T2 required}"
rubric="${3:?strict or lenient required}"
method="${4:?model method required}"
key="$(grep -m1 '^DASHSCOPE_API_KEY=' "$root/.env" | cut -d= -f2-)"
tmp_eval="/tmp/stale_eval_${method}_${task}_${rubric}"

rm -rf "$tmp_eval"
mkdir -p "$tmp_eval"
cp "$root/STALE/STALE/Evaluation/"*.py "$tmp_eval/"
if [[ "$rubric" == "lenient" ]]; then
  cp "$root/STALE/STALE/Evaluation/judge_prompts_original.py" "$tmp_eval/judge_prompts.py"
elif [[ "$rubric" != "strict" ]]; then
  echo "rubric must be strict or lenient" >&2
  exit 2
fi

cd "$tmp_eval"
PYTHONPATH="$root/STALE/STALE:${PYTHONPATH:-}" \
QWEN_API_KEY="$key" DASHSCOPE_API_KEY="$key" \
  /mnt/laq/venv/bin/python3 full_eval_performance.py \
  --answers-path "$run/answers_${task}.json" \
  --dataset-path "$root/STALE/STALE/outputs/STALE_MAIN.json" \
  --output-path "$run/scores_${task}_${rubric}.json" \
  --model-method "$method" \
  --conflict-type "$task" \
  --judge-provider QWEN \
  --judge-model qwen3.6-plus \
  --concurrency 2
