#!/bin/bash
# 等 run2/run3 完成后自动评分并计算方差
# 用法: screen -dmS auto_variance bash /mnt/laq/RECAST/scripts/auto_score_variance.sh

set -uo pipefail

PYTHON=/mnt/laq/venv/bin/python3
DATASET=/mnt/laq/RECAST/STALE/STALE/outputs/STALE_MAIN.json
EVAL_DIR=/mnt/laq/RECAST/STALE/STALE/Evaluation
RUN_DIR=/mnt/laq/RECAST/runs/c58e71d
LOG=$RUN_DIR/auto_variance.log
QWEN_API_KEY=${QWEN_API_KEY}

echo "=== auto_score_variance started $(date) ===" | tee "$LOG"

build_answers() {
    local run_subdir=$1
    local out=$RUN_DIR/${run_subdir}/answers.json
    echo "[$(date '+%H:%M:%S')] Building $out..." | tee -a "$LOG"
    $PYTHON - << EOF
import json, pathlib
answers = []
for f in sorted(pathlib.Path("$RUN_DIR/$run_subdir").glob("*/answer.json")):
    try: answers.append(json.loads(f.read_text()))
    except: pass
pathlib.Path("$out").write_text(json.dumps(answers, ensure_ascii=False, indent=2))
print(f"  Built: {len(answers)} records -> $out")
EOF
}

score_run() {
    local name=$1
    local answers=$RUN_DIR/${name}/answers.json
    for ctype in T1 T2; do
        local out=$RUN_DIR/${name}/scores_${ctype}.json
        if [ -f "$out" ]; then
            echo "[$(date '+%H:%M:%S')] $name $ctype already scored, skip" | tee -a "$LOG"
            continue
        fi
        echo "[$(date '+%H:%M:%S')] Scoring $name $ctype ..." | tee -a "$LOG"
        cd "$EVAL_DIR"
        QWEN_API_KEY=$QWEN_API_KEY DASHSCOPE_API_KEY=$QWEN_API_KEY \
        QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1 \
        $PYTHON full_eval_performance.py \
            --answers-path "$answers" \
            --dataset-path "$DATASET" \
            --output-path "$out" \
            --model-method "$name" \
            --conflict-type "$ctype" \
            --judge-model qwen3.6-plus \
            --judge-provider QWEN \
            2>&1 | tee -a "$LOG"
        echo "[$(date '+%H:%M:%S')] $name $ctype done" | tee -a "$LOG"
    done
}

# 等待 run2 和 run3 各达到 200 个子目录
while true; do
    r2=$(ls $RUN_DIR/run2_variance/ | grep -E "^[0-9]{4}$" | wc -l)
    r3=$(ls $RUN_DIR/run3_variance/ | grep -E "^[0-9]{4}$" | wc -l)
    echo "[$(date '+%H:%M:%S')] Progress: run2=$r2/200  run3=$r3/200" | tee -a "$LOG"
    if [ "$r2" -ge 200 ] && [ "$r3" -ge 200 ]; then
        echo "[$(date '+%H:%M:%S')] Both complete!" | tee -a "$LOG"
        break
    fi
    sleep 300
done

# merge answers
build_answers run2_variance
build_answers run3_variance

# score both
score_run run2_variance
score_run run3_variance

# 计算方差
echo "" | tee -a "$LOG"
echo "=== VARIANCE REPORT ===" | tee -a "$LOG"
$PYTHON - << 'PYEOF' | tee -a "$LOG"
import json, math

RUN1_T1 = "/mnt/laq/RECAST/runs/rescore_strict/dispatch_fix_T1_strict.json"
RUN1_T2 = "/mnt/laq/RECAST/runs/rescore_strict/dispatch_fix_T2_strict.json"
RUN2_T1 = "/mnt/laq/RECAST/runs/c58e71d/run2_variance/scores_T1.json"
RUN2_T2 = "/mnt/laq/RECAST/runs/c58e71d/run2_variance/scores_T2.json"
RUN3_T1 = "/mnt/laq/RECAST/runs/c58e71d/run3_variance/scores_T1.json"
RUN3_T2 = "/mnt/laq/RECAST/runs/c58e71d/run3_variance/scores_T2.json"

def load_acc(path, split):
    with open(path) as f: d = json.load(f)
    acc = d["summary"]["accuracy"][split]
    return {k: acc[k]["accuracy"] for k in ["dim1","dim2","dim3","overall"]}

dims = ["dim1","dim2","dim3","overall"]
dnames = {"dim1":"SR","dim2":"PR","dim3":"IPA","overall":"Overall"}

for split, paths in [("T1",[RUN1_T1,RUN2_T1,RUN3_T1]),("T2",[RUN1_T2,RUN2_T2,RUN3_T2])]:
    print(f"\n--- {split} ---")
    print(f"{'':8}", end="")
    for d in dims: print(f"  {dnames[d]:>8}", end="")
    print()
    runs_data = []
    for i, p in enumerate(paths, 1):
        try:
            acc = load_acc(p, split)
            runs_data.append(acc)
            print(f"{'Run'+str(i):<8}", end="")
            for d in dims: print(f"  {acc[d]:>8.1%}", end="")
            print()
        except Exception as e:
            print(f"Run{i}: ERROR {e}")
    if len(runs_data) >= 2:
        means = {d: sum(r[d] for r in runs_data)/len(runs_data) for d in dims}
        stds  = {d: math.sqrt(sum((r[d]-means[d])**2 for r in runs_data)/(len(runs_data)-1)) for d in dims}
        print(f"{'Mean':<8}", end="")
        for d in dims: print(f"  {means[d]:>8.1%}", end="")
        print()
        print(f"{'±σ':<8}", end="")
        for d in dims: print(f"  {stds[d]:>7.2%}p", end="")
        print()
PYEOF

echo "" | tee -a "$LOG"
echo "=== DONE $(date) ===" | tee -a "$LOG"
