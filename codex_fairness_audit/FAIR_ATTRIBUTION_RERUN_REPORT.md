# Fair-Attribution Rerun Report

Date: 2026-07-08

This report addresses a specific fairness concern in the STALE strict scores:
the strict evaluator rewards final answers that visibly ground their reasoning
in the updated memory state, while some baselines were prompted to answer
concisely. The goal was to test whether low baseline strict scores are only an
answer-format artifact or also reflect retrieval/state failures.

## Protocol

The rerun uses `run_fair_attribution_rerun.py`.

For each sample, the rerun gives the answer model only:

- The task question.
- The baseline method's own retrieved memories.
- Recent session context.

It does not reveal oracle `M_old` or `M_new` labels. The answer prompt asks the
model to perform a visible premise check, cite retrieved evidence, and avoid
inventing unsupported state. The same strict qwen3.6-plus evaluator is then used
to score the generated answers.

This is a diagnostic protocol, not a replacement for the main table. It changes
the answer composer while preserving the baseline's evidence source.

## Naive-RAG 60-Sample Result

Run directory:
`codex_fairness_audit/runs/naive_rag_fair_60`

Same-UID comparison against `/mnt/laq/naive_rag/runs/naive_rag_full`:

| Task | Setting | Dim1 | Dim2 | Dim3 | Overall |
| --- | --- | ---: | ---: | ---: | ---: |
| T1 | Original Naive-RAG, same UIDs | 0.0 | 6.7 | 43.3 | 16.7 |
| T1 | Fair-attribution Naive-RAG | 53.3 | 36.7 | 23.3 | 37.8 |
| T2 | Original Naive-RAG, same UIDs | 0.0 | 0.0 | 3.3 | 1.1 |
| T2 | Fair-attribution Naive-RAG | 33.3 | 13.3 | 10.0 | 18.9 |

Interpretation:

- The answer-format concern is real. With the same retrieved evidence and the
  same UIDs, a premise-checking answer prompt substantially raises strict scores.
- The effect is strongest on explicit-recognition and false-premise probes
  (Dim1/Dim2). This matches the strict rubric's demand for visible grounding.
- The improvement is not enough to explain away the main gap. T1 remains below
  40% overall and T2 remains below 20% overall, so retrieval/state selection is
  still failing often.
- T1 Dim3 drops relative to the original same-UID Naive-RAG subset. Manual spot
  checks suggest that attribution-aware answers sometimes refuse or hedge when
  retrieved evidence is insufficient, which helps premise checks but can hurt
  implicit recommendation tasks.

## Naive-RAG 20-Sample Pilot

The earlier 20-sample pilot showed the same pattern:

| Task | Setting | Dim1 | Dim2 | Dim3 | Overall |
| --- | --- | ---: | ---: | ---: | ---: |
| T1 | Original Naive-RAG, same UIDs | 0.0 | 10.0 | 50.0 | 20.0 |
| T1 | Fair-attribution Naive-RAG | 50.0 | 40.0 | 30.0 | 40.0 |
| T2 | Original Naive-RAG, same UIDs | 0.0 | 0.0 | 0.0 | 0.0 |
| T2 | Fair-attribution Naive-RAG | 30.0 | 10.0 | 10.0 | 16.7 |

The 60-sample run therefore confirms rather than reverses the pilot conclusion.

## mem0 10-Sample Pilot

Run directory:
`codex_fairness_audit/runs/mem0_fair_10`

Same-UID result:

| Task | Setting | Dim1 | Dim2 | Dim3 | Overall |
| --- | --- | ---: | ---: | ---: | ---: |
| T1 | Original mem0, same UIDs | 0.0 | 0.0 | 20.0 | 6.7 |
| T1 | Fair-attribution mem0 | 0.0 | 0.0 | 0.0 | 0.0 |
| T2 | Original mem0, same UIDs | 0.0 | 0.0 | 0.0 | 0.0 |
| T2 | Fair-attribution mem0 | 0.0 | 0.0 | 0.0 | 0.0 |

Interpretation:

- The fair answer prompt did not rescue mem0 on this pilot.
- Most failed answers stated that the current state was unsupported or unknown,
  meaning the retrieved memory did not contain the needed updated evidence.
- This points to a memory/retrieval-state problem, not just terse final wording.
- The local mem0 run produced repeated internal warnings around
  `new_retrieved_facts`; answers were still written and scored, but the pilot
  should be treated as diagnostic rather than final.

## A-MEM Status

A-MEM fair-attribution rerun was attempted with the same script, but the local
pipeline stalled during memory construction before producing usable outputs. No
A-MEM fair-attribution scores are reported here.

Do not make strong A-MEM-specific fairness claims from this audit until that
pipeline is optimized or rerun under a controlled configuration.

## Paper Implications

Recommended framing for the AAAI paper:

- Keep the main strict scores, but avoid describing all baseline strict failures
  as pure memory-state failures.
- Add or preserve a limitation/diagnostic note: strict scoring is sensitive to
  whether the final answer exposes conflict-aware attribution.
- For Naive-RAG, say the diagnostic rerun shows answer prompting improves strict
  performance but does not close the gap, indicating both answer-composition and
  retrieval/state-identification failures.
- For mem0, the pilot suggests the failure is primarily upstream memory
  retrieval/state availability.
- Do not insert the 20/60 pilot numbers into the main table as final headline
  results unless the experiment is expanded to the full benchmark and all
  baselines are rerun under a consistent fair-attribution composer.

Bottom line: the fairness concern is valid and should soften the wording around
baseline strict-score interpretation, but the evidence still supports the core
claim that RECAST's conflict-aware memory handling gives a real advantage.
