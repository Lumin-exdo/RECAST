# RECAST

**Retroactive Evidence-based Conflict-Aware State Tracking**

RECAST is a schema-free memory system for LLM agents that automatically detects when stored beliefs become stale. When a user shares new information, RECAST reasons backwards to identify which existing memories it contradicts — without requiring a predefined schema or manual memory management.

## Key Ideas

Standard memory systems for agents face a core problem: users' lives change, but stored memories do not. A memory written in January ("lives in San Francisco") may be silently wrong by March ("just signed a lease in Toronto"). Most systems either ignore this or require explicit updates from the user.

RECAST addresses this through **abductive conflict detection**:

1. When a new statement arrives, RECAST generates *impact hypotheses* — what would have had to be true *before* for this statement to represent a change?
2. Each hypothesis is tested against stored memories via **abductive judgment**, which infers whether the new evidence weakens or invalidates an existing belief.
3. Evidence accumulates in a per-memory **pool**. When pool confidence crosses a threshold, the memory is marked *stale* (definitively outdated) or *uncertain* (weakened but unconfirmed).
4. At query time, a **premise check** identifies whether the question rests on a false or outdated assumption, and answer generation incorporates a compressed **profile summary** to resolve ambiguity.

## Pipeline

```
statement_extraction
      ↓
hypothetical_filter       ← removes hypotheticals, keeps factual assertions
      ↓
impact_hypothesis         ← generates "what was previously true?" hypotheses
      ↓
abductive_judgment        ← tests hypotheses against stored memories
      ↓
pool_synthesis            ← accumulates evidence, decides stale/uncertain/active
      ↓
impression_update         ← maintains compressed global profile summary
      ↓
  [ query time ]
      ↓
premise_check             ← flags outdated assumptions in incoming queries
      ↓
answer_generation         ← responds using current memory state + profile summary
```

## Evaluation

Evaluated on [STALE](https://github.com/STALEproj/STALE) — a benchmark designed to test whether memory systems correctly handle temporal conflicts in user profiles.

- **T1**: Direct conflicts (a new fact directly contradicts a stored memory)
- **T2**: Indirect chained conflicts (new facts imply contradictions through multi-hop reasoning)
- **dim1**: Recall — does the system know the old memory is stale?
- **dim2**: Adversarial probe — does the system resist questions that assume the stale memory is still true?
- **dim3**: Action compliance — does the system act on the current state, not the stale one?

## Setup

```bash
git clone https://github.com/Lumin-exdo/RECAST.git
cd RECAST
python -m venv venv && source venv/bin/activate
pip install openai sentence-transformers numpy

cp .env.example .env
# Fill in TARGET_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL in .env
```

Download the STALE dataset and embedding model separately (see STALE repo).

## Running

```bash
# From the parent directory of RECAST/
python -m RECAST.run_new_mem \
  --data-path /path/to/STALE_MAIN.json \
  --embedding-model-path /path/to/all-MiniLM-L6-v2 \
  --run-name my_run \
  --uids uid1,uid2,uid3 \
  --workers 10 \
  --no-thinking
```

Results are written to `RECAST/runs/{commit}/{run-name}/{sample_idx}/answer.json`.

## Notes

- `--no-thinking` is required for DeepSeek models (v4-flash/v4-pro default to thinking=enabled)
- `--workers` should be tuned to available RAM (~8GB per parallel sample)
- Results from multiple machines can be merged by using `--commit-override` to force a consistent run path
