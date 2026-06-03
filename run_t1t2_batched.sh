#!/usr/bin/env bash
# Run T1/T2 60 samples in batches of 2 (fresh process each batch, workers=2)
# Pattern: fresh process avoids accumulated memory leak; 2 parallel is proven safe

set -e
PY=/home/lumin_exdo/miniconda3/envs/cupmem/bin/python
EMBED=/home/lumin_exdo/STALE/cup_mem/models/all-MiniLM-L6-v2
RUN_NAME=t1t2_v1
LOGFILE=$(dirname "$0")/t1t2_batch_progress.log

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOGFILE"; }

BATCHES=(
  "89b77229,7ee76c41"
  "1a85388f,f6d12075"
  "d9545076,e229c5cd"
  "eacb64ff,fdada4cc"
  "a4b2e2fd,2006d545"
  "d74f7f3e,b17c5c02"
  "b35794f3,7a7621e2"
  "34d402c0,6ff5a576"
  "e72a2ba5,93a1c511"
  "f7fb891b,79e4cc40"
  "2ba8e3f4,26e99c95"
  "dae22057,eee1a643"
  "e51c1d33,e1703b4d"
  "9867971c,8aeb8778"
  "a6170008,3305ce57"
  "d806d94c,feef3933"
  "14897e47,c9cc370e"
  "2c711459,993152aa"
  "c03f7b53,60604200"
  "06071a3e,2d92d1c2"
  "fbe6fd55,28daa975"
  "27a52329,830a2e06"
  "a2a3e641,da38532d"
  "48707e03,f50107f1"
  "ea1bd523,855155ad"
  "1469bde3,5a4781fe"
  "5ae24023,87ea8043"
  "14ed299f,4ad50bc6"
  "5372c535,d13024ef"
  "c2cc2d39,53d876a2"
)

TOTAL=${#BATCHES[@]}
log "T1/T2 batched run: $TOTAL batches of 2, run_name=$RUN_NAME"

for i in "${!BATCHES[@]}"; do
  BATCH="${BATCHES[$i]}"
  BATCH_NUM=$((i+1))
  log "Batch $BATCH_NUM/$TOTAL: $BATCH"
  cd /home/lumin_exdo
  if $PY -m AMBER.run_new_mem \
       --run-name "$RUN_NAME" \
       --uids "$BATCH" \
       --workers 2 \
       --no-thinking \
       --embedding-model-path "$EMBED" \
       --embedding-device cpu; then
    log "Batch $BATCH_NUM DONE"
  else
    log "Batch $BATCH_NUM FAILED (exit $?), continuing"
  fi
done

log "All batches finished."
