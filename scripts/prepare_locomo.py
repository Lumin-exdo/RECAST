"""
Convert LoCoMo dataset to RECAST-compatible format for external evaluation.

Each LoCoMo conversation + QA pair becomes one RECAST item. Items from the
same conversation share identical haystack_sessions, so the LLM cache ensures
write-phase calls are deduplicated (only the first item per conversation pays
the full write cost).

Usage:
    python scripts/prepare_locomo.py \
        --input /tmp/locomo10.json \
        --output /tmp/locomo_recast.json \
        --conv-limit 3 \
        --qa-limit 20 \
        --categories 4,2 \
        --session-limit 8
"""

import argparse
import json
import uuid
from pathlib import Path

CATEGORY_ID_TO_TYPE = {
    1: "multi_hop",
    2: "temporal",
    3: "open_domain",
    4: "single_hop",
    5: "adversarial",
}


def convert_session(msgs, speaker_a_name: str):
    """Convert LoCoMo session turns to RECAST role/content format."""
    turns = []
    for msg in msgs:
        text = msg.get("text", "").strip()
        if not text:
            continue
        role = "user" if msg.get("speaker") == speaker_a_name else "assistant"
        turns.append({"role": role, "content": text})
    return turns


def extract_sessions(conv: dict, speaker_a: str, max_sessions: int):
    """Return (sessions, timestamps) from a LoCoMo conversation dict."""
    session_keys = sorted(
        [k for k in conv if k.startswith("session_") and not k.endswith("_date_time")],
        key=lambda k: int(k.split("_")[1]),
    )[:max_sessions]

    sessions, timestamps = [], []
    for sk in session_keys:
        msgs = conv.get(sk, [])
        turns = convert_session(msgs, speaker_a)
        if turns:
            sessions.append(turns)
            timestamps.append(conv.get(sk + "_date_time", ""))
    return sessions, timestamps


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to locomo10.json")
    parser.add_argument("--output", required=True, help="Output RECAST-format JSON")
    parser.add_argument(
        "--conv-limit", type=int, default=3,
        help="Number of conversations to use (default 3)",
    )
    parser.add_argument(
        "--qa-limit", type=int, default=20,
        help="Max QA pairs per conversation (default 20)",
    )
    parser.add_argument(
        "--categories", default="4,2",
        help="Comma-separated category IDs to include (default: 4=single_hop,2=temporal)",
    )
    parser.add_argument(
        "--session-limit", type=int, default=8,
        help="Max sessions per conversation (default 8)",
    )
    args = parser.parse_args()

    categories = {int(c) for c in args.categories.split(",") if c.strip()}

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    items = []
    for conv_idx, sample in enumerate(data[: args.conv_limit]):
        conv = sample["conversation"]
        speaker_a = conv.get("speaker_a", "SpeakerA")
        sample_id = sample.get("sample_id", conv_idx)

        sessions, timestamps = extract_sessions(conv, speaker_a, args.session_limit)
        if not sessions:
            continue

        qa_pairs = [
            qa for qa in sample.get("qa", [])
            if qa.get("category") in categories
        ][: args.qa_limit]

        print(
            f"Conv {conv_idx} (id={sample_id}, speaker_a={speaker_a!r}): "
            f"{len(sessions)} sessions, {len(qa_pairs)} QA"
        )

        for q_idx, qa in enumerate(qa_pairs):
            item_uid = str(
                uuid.uuid5(uuid.NAMESPACE_DNS, f"locomo_{sample_id}_{q_idx}")
            )
            items.append(
                {
                    "uid": item_uid,
                    "haystack_session": sessions,
                    "timestamps": timestamps,
                    "probing_queries": {"dim1_query": qa["question"]},
                    "_ground_truth": qa["answer"],
                    "_question_type": CATEGORY_ID_TO_TYPE.get(
                        qa.get("category"), "unknown"
                    ),
                    "_conv_idx": conv_idx,
                    "_sample_id": sample_id,
                    "_q_idx": q_idx,
                    "M_old": "",
                    "M_new": "",
                    "type": "LOCOMO",
                    "relevant_session_index": [],
                }
            )

    Path(args.output).write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nWrote {len(items)} items to {args.output}")


if __name__ == "__main__":
    main()
