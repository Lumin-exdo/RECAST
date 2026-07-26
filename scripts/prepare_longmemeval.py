"""
Convert LongMemEval dataset to RECAST-compatible format for evaluation.

Usage:
    python scripts/prepare_longmemeval.py \
        --input /tmp/longmemeval_s_cleaned.json \
        --output /tmp/longmemeval_recast.json \
        --types knowledge-update

The output file is a list of STALE-format items that can be passed to run_new_mem.py
via --data-path.
"""

import argparse
import json
from pathlib import Path


SUPPORTED_TYPES = [
    "single-session-user",
    "single-session-assistant",
    "multi-session",
    "single-session-preference",
    "temporal-reasoning",
    "knowledge-update",
]


def convert_sample(item: dict) -> dict:
    """Convert one LongMemEval item to RECAST run_sample-compatible format."""
    uid = item["question_id"]
    question = item["question"]
    answer = item["answer"]
    qtype = item["question_type"]

    # Map haystack_session_ids to indices for answer sessions
    session_ids = item.get("haystack_session_ids", [])
    answer_ids = set(item.get("answer_session_ids", []))
    relevant_session_index = [
        i for i, sid in enumerate(session_ids) if sid in answer_ids
    ]

    return {
        "uid": uid,
        # RECAST run_sample expects "haystack_session" (singular)
        "haystack_session": item["haystack_sessions"],
        # Timestamps from haystack_dates
        "timestamps": item.get("haystack_dates", [""]*len(item["haystack_sessions"])),
        # Use dim1_query label to match run_new_mem.py output format
        # (the runner hardcodes extraction of dim1/dim2/dim3 from query_logs)
        "probing_queries": {"dim1_query": question},
        # Ground truth answer stored for scoring later
        "_ground_truth": answer,
        # LongMemEval metadata (for scoring)
        "_question_type": qtype,
        "_question_date": item.get("question_date", ""),
        # Hint to runner: which sessions are "relevant" (contain answer)
        "relevant_session_index": relevant_session_index,
        # No M_old/M_new in LongMemEval; leave empty
        "M_old": "",
        "M_new": "",
        "type": "LME",  # distinguish from STALE T1/T2 in run logs
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to longmemeval_s_cleaned.json")
    parser.add_argument("--output", required=True, help="Output path for RECAST-format JSON")
    parser.add_argument(
        "--types",
        default="knowledge-update",
        help="Comma-separated list of question_type to include (default: knowledge-update)",
    )
    parser.add_argument("--max-samples", type=int, default=0, help="Max samples (0=all)")
    args = parser.parse_args()

    target_types = {t.strip() for t in args.types.split(",") if t.strip()}
    invalid = target_types - set(SUPPORTED_TYPES)
    if invalid:
        raise ValueError(f"Unknown question types: {invalid}. Supported: {SUPPORTED_TYPES}")

    with open(args.input) as f:
        data = json.load(f)

    filtered = [s for s in data if s["question_type"] in target_types]
    print(f"Loaded {len(data)} samples, {len(filtered)} match types {target_types}")

    if args.max_samples > 0:
        filtered = filtered[: args.max_samples]
        print(f"Capped at {len(filtered)} samples")

    converted = [convert_sample(s) for s in filtered]
    Path(args.output).write_text(json.dumps(converted, ensure_ascii=False, indent=2))
    print(f"Wrote {len(converted)} samples to {args.output}")


if __name__ == "__main__":
    main()
