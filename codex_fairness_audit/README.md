# Codex fairness audit

This folder is intentionally outside `paper/`. It holds diagnostic material for
the answer-prompt fairness concern: strict STALE scoring requires traceable
grounding in `M_new`, while some baselines were prompted to answer concisely.

## Current confirmed points

- The strict judge scores only final response text. It does not know how the
  answer was generated.
- A-MEM, mem-0, and Naive-RAG use a concise answer prompt ending with:
  "Respond concisely with just the answer. Do not add disclaimers."
- CupMem is different: its `ANSWER_COMPOSER_PROMPT` explicitly requires pushing
  back when an old premise is unsafe and citing current grounding.
- RECAST's default query path uses a structured E2E answer prompt, while the
  `A-NaiveAnswer` ablation shows that removing the structured answer prompt
  substantially reduces strict scores, especially probe resistance.

## Fair-attribution rerun status

See `FAIR_ATTRIBUTION_RERUN_REPORT.md` for the full protocol and interpretation.

- Naive-RAG was rerun on 60 same-UID samples with an attribution-aware answer
  prompt that only exposes retrieved memories and recent context, not oracle
  `M_old`/`M_new`.
- Strict qwen3.6-plus scoring improved Naive-RAG on the same UIDs:
  - T1 overall: 16.7% -> 37.8%.
  - T2 overall: 1.1% -> 18.9%.
- The improvement confirms that terse baseline answer prompts depress strict
  scores. The remaining low scores show that retrieval/state identification is
  still a real gap; this does not collapse the main RECAST/CupMem advantage.
- A 10-sample mem0 rerun did not improve scores, because retrieved memory often
  failed to expose the needed updated state. This points to a memory/retrieval
  failure rather than a final-answer-format-only issue.
- A-MEM fair rerun was attempted but not completed because the local A-MEM
  pipeline was too slow/hung during memory construction. Do not make strong
  A-MEM-specific claims from the pilot until this is resolved.

## How to regenerate the audit

Run from `/mnt/laq/RECAST`:

```bash
python3 codex_fairness_audit/audit_existing_results.py
```

The script writes `existing_result_audit.json` with prompt excerpts, answer
length statistics, score summaries, and concrete lenient-pass/strict-fail cases.

## Interpretation guardrail

The existing strict table should not be framed as pure memory-state accuracy for
all methods. For A-MEM, mem-0, and Naive-RAG it also measures whether a terse
answer exposes the evidence chain required by the strict rubric. CupMem and
RECAST are closer to an apples-to-apples conflict-aware answer generation
comparison because both use explicit premise-correction answer prompts.
