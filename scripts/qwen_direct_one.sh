#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
DATASET=$ROOT/STALE/STALE/outputs/STALE_MAIN.json
EMBED=$ROOT/models/all-MiniLM-L6-v2
ENV_NOLOAD=/tmp/recast_no_env

set -a
. "$ROOT/.env"
set +a

export OPENAI_BASE_URL="https://openrouter.ai/api/v1"
export TARGET_MODEL="qwen/qwen3.5-plus-20260420"
export RECAST_MAX_TOKENS="300"

mkdir -p "$RUN_ROOT"
exec > >(tee -a "$RUN_ROOT/qwen_direct_one.log") 2>&1

timeout 9000 "$PY" run_new_mem.py \
  --env-file "$ENV_NOLOAD" \
  --run-name "cross_qwen35plus_30" \
  --output-dir "$RUN_ROOT/cross_qwen35plus_30" \
  --uids "89b77229" \
  --workers 1 \
  --no-thinking \
  --use-cache \
  --data-path "$DATASET" \
  --embedding-model-path "$EMBED" \
  --embedding-device cpu
rc=$?
echo "[$(date '+%F %T')] qwen_direct_one exit rc=$rc"
exit "$rc"
