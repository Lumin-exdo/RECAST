# RECAST — Claude Code Instructions

## Project
NewMem is a schema-free memory system for LLM agents implementing abductive conflict detection. It is evaluated on the STALE benchmark (400 samples, T1=direct conflict, T2=indirect chained conflict; dims: dim1=recall, dim2=adversarial probe, dim3=action compliance).

**Working directory for all commands:** `/home/lumin_exdo` (not inside RECAST)
**Run command:** `python -m RECAST.run_new_mem`
**Python env:** whichever env has `openai` installed (on WSL: `/home/lumin_exdo/miniconda3/envs/cupmem/bin/python`; on server: verify with `which python` or check env)
**Dataset:** `STALE/STALE/outputs/STALE_MAIN.json`
**Embedding model:** `STALE/cup_mem/models/all-MiniLM-L6-v2` (or download equivalent on server)

## Hard Rules — Never Violate

### 1. No reward hacking on prompts
**Do NOT craft prompts or rules that target specific test answers.** Every prompt change must be motivated by a generalizable principle derived from understanding WHY something fails — not by observing what the scorer expects. Before accepting any fix, generate adversarial examples that attack the proposed fix; if the fix only patches one hole without generalizing, reject it.

### 2. DeepSeek thinking must be disabled
Pass `--no-thinking` flag on every eval run. DeepSeek v4-flash and v4-pro default to thinking=enabled, which causes 30+ min first-response latency with no benefit.

### 3. Scorer is qwen3.6-plus
The STALE evaluator must use `--scorer qwen3.6-plus` (or equivalent config). Do not use the target model as scorer.

### 4. No cache by default
Do NOT pass `--use-cache` unless deliberately replaying a prior run. Default is no cache.

### 5. Git commit before each eval run
Always `git commit` the current code state before launching any eval so the run directory embeds the correct commit hash.

### 6. Smoke test before large-scale runs
Run 3–5 samples first to verify no crashes before spending API budget on a full run. Past bug (null confidence from LLM) cost significant API spend.

## Methodology for Fixing Prompts

When a sample fails:
1. Identify the **precise pipeline stage** where the failure occurs (extraction → filter → impact → abductive → synthesis → premise_check → answer_gen)
2. Understand the **root cause** — not just what went wrong but why the prompt structure failed to generalize
3. **Attack your own fix**: generate adversarial examples that would break the proposed change; if you can break it easily, the fix is too narrow
4. Only implement if the fix is principled and generalizes beyond the failing sample
5. If prompt alone can't fix it (structural/retrieval issue), consider a design change — but minimize code changes

**Never**: patch a specific case, add a rule that names a specific entity/topic, or change a prompt just because the scorer penalized an answer.

## Eval Run Configuration

```bash
cd /home/lumin_exdo
python -m RECAST.run_new_mem \
  --run-name <name> \
  --uids <comma-separated UIDs> \
  --workers 2 \        # max safe on 32GB WSL; use batches of 2 with fresh process each time
  --no-thinking \
  --embedding-model-path STALE/cup_mem/models/all-MiniLM-L6-v2 \
  --embedding-device cpu
```

**Memory constraint**: On 32GB WSL, each Python process can safely run ~2 samples in parallel before OOM. Run multiple batches of 2 with separate process invocations (see `run_t1t2_batched.sh`). On a server with more RAM, increase `--workers` accordingly.

## Pipeline Stages
statement_extraction → hypothetical_filter → impact_hypothesis → abductive_judgment → pool_synthesis → impression_update → premise_check → answer_generation

Key components:
- `memory/new_models.py` — MemoryItem with `category` field (current_state|recent_change|biographical|lasting_preference)
- `store_layer/new_store.py` — `get_preference_anchors()` fetches lasting_preference+biographical memories
- `write/new_writer.py` — session processing, abductive judgment, pool synthesis
- `query/new_engine.py` — premise_check + answer_generation with `uncertain_memories` and `profile_summary`
- `prompt_lib/new_templates.py` — all 8 prompts

## Known Issues / Design Decisions
- **preference_anchors** only fetches `lasting_preference` + `biographical` — `current_state` social reputation memories (e.g. "trusted with confidential info") are NOT included, causing misses on T1 conflicts involving dynamic social roles
- **stale_reason propagation**: abductive judgment inference_chain gets stored as stale_reason and echoed verbatim in premise_check correction — imprecise inferences (e.g. "likely the US" instead of "Canada") propagate to final answer
- **global_impression 1000-char limit**: [HABITS] must be preserved first when space is tight
- **Memory leak in Python process**: each sample accumulates ~5-8GB that GC doesn't release; use fresh process per 2-sample batch

## T1/T2 Sample UIDs (for STALE eval)
Pre-selected random sample (no overlap with 10-sample reeval set):

**T1 (30):** 89b77229,7ee76c41,1a85388f,f6d12075,d9545076,e229c5cd,eacb64ff,fdada4cc,a4b2e2fd,2006d545,d74f7f3e,b17c5c02,b35794f3,7a7621e2,34d402c0,6ff5a576,e72a2ba5,93a1c511,f7fb891b,79e4cc40,2ba8e3f4,26e99c95,dae22057,eee1a643,e51c1d33,e1703b4d,9867971c,8aeb8778,a6170008,3305ce57

**T2 (30):** d806d94c,feef3933,14897e47,c9cc370e,2c711459,993152aa,c03f7b53,60604200,06071a3e,2d92d1c2,fbe6fd55,28daa975,27a52329,830a2e06,a2a3e641,da38532d,48707e03,f50107f1,ea1bd523,855155ad,1469bde3,5a4781fe,5ae24023,87ea8043,14ed299f,4ad50bc6,5372c535,d13024ef,c2cc2d39,53d876a2

**Reeval set (10 failing samples):** abs_idx 13,44,101,111,193,244,264,266,311,387
