#!/bin/bash
# Wait for CUPMem to finish, then run NewMem relevant_only (4 workers),
# then run NewMem full (subset), then score and compare.

set -uo pipefail

STALE_DIR=/home/lumin_exdo/STALE
MYMEM_DIR=$(dirname "$0")
WORK_DIR=/home/lumin_exdo
PYTHON_CUP=/home/lumin_exdo/miniconda3/envs/cupmem/bin/python
DATA_PATH="$STALE_DIR/STALE/outputs/STALE_MAIN.json"
EMBED_PATH="$STALE_DIR/cup_mem/models/all-MiniLM-L6-v2"
EVAL_SCRIPT="$STALE_DIR/STALE/Evaluation/full_eval_performance.py"

# Embed git commit hash in run paths for reproducibility
COMMIT=$(git -C "$MYMEM_DIR" rev-parse --short HEAD)
RELONLY_ROOT="$MYMEM_DIR/runs/${COMMIT}/relonly"
FULL_ROOT="$MYMEM_DIR/runs/${COMMIT}/full"
LOG_DIR="$MYMEM_DIR/runs/${COMMIT}/launch_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG_DIR/launch.log"; }

# ── 1. Wait for CUPMem to finish ─────────────────────────────────────────────
log "=== PHASE 1: Waiting for CUPMem to finish ==="
while pgrep -f 'cup_mem.run_cup_mem' > /dev/null 2>&1; do
    done_count=$(ls "$STALE_DIR/cup_mem/runs/" 2>/dev/null | wc -l)
    mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    log "CUPMem still running... ${done_count}/400 done, mem_avail=${mem_avail}MB"
    sleep 60
done
log "CUPMem finished! Checking sample count..."
done_count=$(ls "$STALE_DIR/cup_mem/runs/" 2>/dev/null | wc -l)
log "CUPMem: ${done_count}/400 samples completed"

# ── 2. Score CUPMem ───────────────────────────────────────────────────────────
log "=== PHASE 2: Scoring CUPMem ==="
CUP_ANSWERS="$STALE_DIR/cup_mem/runs_aggregated/answers.json"

# Aggregate CUPMem per-sample outputs into one answers.json
# CUPMem writes summary.json per sample; eval expects target_model_responses format
if [ ! -f "$CUP_ANSWERS" ]; then
    mkdir -p "$STALE_DIR/cup_mem/runs_aggregated"
    log "Aggregating CUPMem per-sample results..."
    "$PYTHON_CUP" - <<'EOF'
import json, sys
from pathlib import Path

runs_dir = Path('/home/lumin_exdo/STALE/cup_mem/runs')
out_path = Path('/home/lumin_exdo/STALE/cup_mem/runs_aggregated/answers.json')

answers = []
errors = []
for sample_dir in sorted(runs_dir.iterdir(), key=lambda p: int(p.name.split('_')[1]) if p.name.startswith('sample_') and p.name.split('_')[1].isdigit() else 9999):
    if not sample_dir.is_dir() or not sample_dir.name.startswith('sample_'):
        continue
    # Find the timestamped subdir
    subdirs = [d for d in sample_dir.iterdir() if d.is_dir()]
    if not subdirs:
        continue
    run_subdir = sorted(subdirs)[-1]  # latest run
    summary_file = run_subdir / 'summary.json'
    if not summary_file.exists():
        errors.append(str(sample_dir))
        continue
    try:
        s = json.loads(summary_file.read_text())
        raw_answers = s.get('answers', {})
        # Convert dim1_query/dim2_query/dim3_query → dim1_response/dim2_response/dim3_response
        answers.append({
            'uid': s['uid'],
            'sample_index': s.get('sample_index', -1),
            'type': s.get('type', ''),
            'session_mode': s.get('session_mode', ''),
            'target_model_responses': {
                'dim1_response': raw_answers.get('dim1_query', ''),
                'dim2_response': raw_answers.get('dim2_query', ''),
                'dim3_response': raw_answers.get('dim3_query', ''),
            },
        })
    except Exception as e:
        errors.append(f"{sample_dir}: {e}")

answers.sort(key=lambda x: x.get('sample_index', 0))
out_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2))
print(f"Aggregated {len(answers)} CUPMem answers (errors: {len(errors)})")
if errors:
    print("  Errors:", errors[:5])
EOF
fi

if [ -f "$CUP_ANSWERS" ]; then
    log "Scoring CUPMem..."
    cd "$STALE_DIR/STALE/Evaluation"
    "$PYTHON_CUP" full_eval_performance.py \
        --answers-path "$CUP_ANSWERS" \
        --dataset-path "$DATA_PATH" \
        --output-path "$STALE_DIR/cup_mem/runs_aggregated/scores.json" \
        --model-method cup_mem \
        2>&1 | tee -a "$LOG_DIR/launch.log" || log "CUPMem scoring failed (check structure)"
else
    log "WARNING: Could not find/build CUPMem answers.json — check cup_mem/runs/ structure"
fi

# ── 3. Launch NewMem relevant_only (4 workers × 100 samples) ─────────────────
log "=== PHASE 3: NewMem relevant_only, 4 workers ==="
mkdir -p "$RELONLY_ROOT"
RELONLY_PIDS=()

for chunk in "0 100" "100 200" "200 300" "300 400"; do
    read -r start end <<< "$chunk"
    worker_id=$((start / 100))

    # Memory check
    mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    while [ "$mem_avail" -lt 2500 ]; do
        log "Low memory (${mem_avail}MB), waiting 30s before launching worker $worker_id..."
        sleep 30
        mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    done

    log "Launching relonly worker $worker_id: samples $start-$((end-1)) (${mem_avail}MB avail)"
    (
        cd "$WORK_DIR" && \
        "$PYTHON_CUP" -m AMBER.run_new_mem \
            --data-path "$DATA_PATH" \
            --session-mode relevant_only \
            --start-index "$start" \
            --end-index "$end" \
            --n-samples 0 \
            --output-root "$RELONLY_ROOT" \
            --embedding-model-path "$EMBED_PATH" \
            --embedding-device cpu \
            2>&1
    ) >> "$LOG_DIR/worker_relonly_${worker_id}.log" &
    RELONLY_PIDS+=($!)

    # Stagger 60s between workers to avoid simultaneous model loading
    if [ "$end" -lt 400 ]; then
        log "Staggering 60s before next worker..."
        sleep 60
    fi
done

log "All 4 relonly workers launched: PIDs ${RELONLY_PIDS[*]}"

# Monitor until all relonly workers done
while true; do
    all_done=true
    for pid in "${RELONLY_PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && all_done=false && break
    done
    $all_done && break

    # Count answers written so far (sum across all worker dirs)
    total_done=0
    for dir in "$RELONLY_ROOT"/*/; do
        if [ -f "${dir}answers.json" ]; then
            n=$(python3 -c "import json; d=json.load(open('${dir}answers.json')); print(len(d))" 2>/dev/null || echo 0)
            total_done=$((total_done + n))
        fi
    done
    mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    log "Relonly progress: ${total_done}/400 answers written, mem_avail=${mem_avail}MB"
    sleep 120
done
log "All relonly workers finished!"

# ── 4. Score NewMem relevant_only ────────────────────────────────────────────
log "=== PHASE 4: Scoring NewMem relevant_only ==="

# Merge 4 worker answer files into one
MERGED_ANSWERS="$RELONLY_ROOT/answers_merged.json"
"$PYTHON_CUP" - <<EOF
import json
from pathlib import Path

root = Path('$RELONLY_ROOT')
all_answers = []
for d in sorted(root.iterdir()):
    af = d / 'answers.json'
    if af.exists() and d.name != 'answers_merged.json':
        try:
            data = json.loads(af.read_text())
            all_answers.extend(data)
        except Exception as e:
            print(f"  skip {d.name}: {e}")

# Sort by sample_index for clean output
all_answers.sort(key=lambda x: x.get('sample_index', 0))
Path('$MERGED_ANSWERS').write_text(json.dumps(all_answers, ensure_ascii=False, indent=2))
print(f"Merged {len(all_answers)} answers from {root}")
EOF

cd "$STALE_DIR/STALE/Evaluation"
for conflict_type in T1 T2; do
    "$PYTHON_CUP" full_eval_performance.py \
        --answers-path "$MERGED_ANSWERS" \
        --dataset-path "$DATA_PATH" \
        --output-path "$RELONLY_ROOT/scores_${conflict_type}.json" \
        --model-method new_mem \
        --conflict-type "$conflict_type" \
        2>&1 | tee -a "$LOG_DIR/launch.log" || log "WARNING: scoring $conflict_type failed"
done

# ── 5. Launch NewMem full mode (100-sample subset) ───────────────────────────
log "=== PHASE 5: NewMem full mode, 100 samples (2 workers × 50) ==="
mkdir -p "$FULL_ROOT"
FULL_PIDS=()

for chunk in "0 50" "50 100"; do
    read -r start end <<< "$chunk"
    worker_id=$((start / 50))

    mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    while [ "$mem_avail" -lt 3000 ]; do
        log "Low memory (${mem_avail}MB), waiting 30s before full worker $worker_id..."
        sleep 30
        mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    done

    log "Launching full worker $worker_id: samples $start-$((end-1)) (${mem_avail}MB avail)"
    (
        cd "$WORK_DIR" && \
        "$PYTHON_CUP" -m AMBER.run_new_mem \
            --data-path "$DATA_PATH" \
            --session-mode full \
            --start-index "$start" \
            --end-index "$end" \
            --n-samples 0 \
            --output-root "$FULL_ROOT" \
            --embedding-model-path "$EMBED_PATH" \
            --embedding-device cpu \
            2>&1
    ) >> "$LOG_DIR/worker_full_${worker_id}.log" &
    FULL_PIDS+=($!)

    if [ "$end" -lt 100 ]; then
        sleep 90
    fi
done

log "Full workers launched: PIDs ${FULL_PIDS[*]}"

while true; do
    all_done=true
    for pid in "${FULL_PIDS[@]}"; do
        kill -0 "$pid" 2>/dev/null && all_done=false && break
    done
    $all_done && break

    total_done=0
    for dir in "$FULL_ROOT"/*/; do
        if [ -f "${dir}answers.json" ]; then
            n=$(python3 -c "import json; d=json.load(open('${dir}answers.json')); print(len(d))" 2>/dev/null || echo 0)
            total_done=$((total_done + n))
        fi
    done
    mem_avail=$(free -m | awk '/^Mem:/ {print $7}')
    log "Full progress: ${total_done}/100 answers written, mem_avail=${mem_avail}MB"
    sleep 120
done
log "Full workers finished!"

# ── 6. Score NewMem full ──────────────────────────────────────────────────────
log "=== PHASE 6: Scoring NewMem full ==="
MERGED_FULL="$FULL_ROOT/answers_merged.json"
"$PYTHON_CUP" - <<EOF
import json
from pathlib import Path

root = Path('$FULL_ROOT')
all_answers = []
for d in sorted(root.iterdir()):
    af = d / 'answers.json'
    if af.exists():
        try:
            data = json.loads(af.read_text())
            all_answers.extend(data)
        except Exception as e:
            print(f"  skip {d.name}: {e}")

all_answers.sort(key=lambda x: x.get('sample_index', 0))
Path('$MERGED_FULL').write_text(json.dumps(all_answers, ensure_ascii=False, indent=2))
print(f"Merged {len(all_answers)} answers (full mode)")
EOF

cd "$STALE_DIR/STALE/Evaluation"
for conflict_type in T1 T2; do
    "$PYTHON_CUP" full_eval_performance.py \
        --answers-path "$MERGED_FULL" \
        --dataset-path "$DATA_PATH" \
        --output-path "$FULL_ROOT/scores_${conflict_type}.json" \
        --model-method new_mem \
        --conflict-type "$conflict_type" \
        2>&1 | tee -a "$LOG_DIR/launch.log" || log "WARNING: scoring full $conflict_type failed"
done

log "=== ALL DONE ==="
log "Results:"
log "  CUPMem:          $STALE_DIR/cup_mem/runs_aggregated/scores.json"
log "  NewMem relonly:  $RELONLY_ROOT/scores_*.json"
log "  NewMem full:     $FULL_ROOT/scores_*.json"
log "  Logs:            $LOG_DIR/"
