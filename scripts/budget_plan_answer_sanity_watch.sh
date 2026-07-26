#!/usr/bin/env bash
set -uo pipefail

ROOT=/mnt/laq/RECAST
RUN_ROOT=$ROOT/runs/budget_plan_t15_t15
PY=/mnt/laq/venv/bin/python3
LOG=$RUN_ROOT/answer_sanity_watch.log
STATE=$RUN_ROOT/.answer_sanity_seen

mkdir -p "$RUN_ROOT"
touch "$STATE"

log() {
  echo "[$(date '+%F %T')] $*" | tee -a "$LOG"
}

check_answer() {
  local file="$1"
  "$PY" - "$file" <<'PY'
import json
import sys
from pathlib import Path

p = Path(sys.argv[1])
try:
    data = json.loads(p.read_text(encoding="utf-8"))
except Exception as exc:
    print(f"BAD_JSON {p}: {exc}")
    raise SystemExit(2)

responses = data.get("target_model_responses", {})
keys = ["dim1_response", "dim2_response", "dim3_response"]
bad = []
for key in keys:
    text = str(responses.get(key, "") or "").strip()
    low = text.lower()
    if not text:
        bad.append(f"{key}=EMPTY")
    elif any(marker in low for marker in [
        "error code:",
        "traceback",
        "insufficient credits",
        "requires more credits",
        "<html",
        "bad gateway",
        "rate limit",
    ]):
        bad.append(f"{key}=ERROR_TEXT")

if bad:
    print("BAD_RESPONSE " + str(p) + " " + ",".join(bad))
    raise SystemExit(1)
print("OK " + str(p))
PY
}

log "answer sanity watch started"
while true; do
  while IFS= read -r -d '' file; do
    if grep -Fxq "$file" "$STATE"; then
      continue
    fi
    if check_answer "$file" >> "$LOG" 2>&1; then
      log "sanity ok $file"
    else
      log "SANITY_FAIL $file"
    fi
    echo "$file" >> "$STATE"
  done < <(find "$RUN_ROOT" -maxdepth 3 -type f -name answer.json -print0 2>/dev/null)
  sleep 60
done
