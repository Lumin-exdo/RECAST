"""
Score RECAST answers against LoCoMo ground truth using qwen3.6-plus as judge.

Usage:
    python scripts/score_locomo.py \
        --answers-dir /path/to/locomo_3conv_60 \
        --data /path/to/locomo_recast.json \
        --output /path/to/locomo_3conv_60/scores_locomo.json \
        --scorer qwen3.6-plus \
        [--workers 5]
"""

import argparse
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI


JUDGE_PROMPT = (
    "I will give you a question, a correct answer, and a response from a model. "
    "Please answer yes if the response contains the correct answer, or if the response "
    "is equivalent to the correct answer (e.g. paraphrase, abbreviation, or reformulation). "
    "Otherwise, answer no.\n\n"
    "Question: {question}\n\n"
    "Correct Answer: {answer}\n\n"
    "Model Response: {response}\n\n"
    "Is the model response correct? Answer yes or no only."
)


def load_answer_map(answers_dir: Path) -> dict:
    """Load all answer.json files from the run directory, keyed by uid (first 8 chars)."""
    answer_map = {}
    for p in answers_dir.glob("*/answer.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            uid = data.get("uid", "")
            if uid:
                answer_map[uid[:8]] = data
        except Exception:
            pass
    return answer_map


def judge_answer(client, scorer_model, question, ground_truth, response):
    prompt = JUDGE_PROMPT.format(
        question=question,
        answer=ground_truth,
        response=response,
    )
    try:
        resp = client.chat.completions.create(
            model=scorer_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=5,
            temperature=0,
        )
        verdict = resp.choices[0].message.content.strip().lower()
        return verdict.startswith("yes")
    except Exception as e:
        print(f"[SCORER ERROR] {e}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers-dir", required=True, help="RECAST run output directory")
    parser.add_argument("--data", required=True, help="locomo_recast.json (with ground truth)")
    parser.add_argument("--output", required=True, help="Output scores JSON path")
    parser.add_argument("--scorer", default="qwen3.6-plus", help="Scorer model")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent scorer calls")
    args = parser.parse_args()

    import os
    api_key = os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
    base_url = os.environ.get("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)

    answers_dir = Path(args.answers_dir)
    answer_map = load_answer_map(answers_dir)
    print(f"Loaded {len(answer_map)} answers from {answers_dir}")

    with open(args.data, encoding="utf-8") as f:
        locomo_items = json.load(f)

    results = []
    lock = threading.Lock()

    def score_item(item):
        uid = item["uid"]
        uid8 = uid[:8]
        question = item["probing_queries"]["dim1_query"]
        ground_truth = item["_ground_truth"]
        q_type = item.get("_question_type", "unknown")

        if uid8 not in answer_map:
            return {
                "uid": uid,
                "uid8": uid8,
                "question": question,
                "ground_truth": ground_truth,
                "question_type": q_type,
                "response": None,
                "correct": None,
                "status": "missing_answer",
            }

        answer_data = answer_map[uid8]
        # New-mem output format: target_model_responses.dim1_response
        tmr = answer_data.get("target_model_responses", {})
        response = tmr.get("dim1_response", "")
        # Fallback: legacy query_logs format
        if not response:
            query_logs = answer_data.get("query_logs", {})
            dim1_result = query_logs.get("dim1_query", {})
            response = dim1_result.get("answer", "")

        if not response:
            return {
                "uid": uid,
                "uid8": uid8,
                "question": question,
                "ground_truth": ground_truth,
                "question_type": q_type,
                "response": response,
                "correct": None,
                "status": "empty_response",
            }

        correct = judge_answer(client, args.scorer, question, ground_truth, response)
        return {
            "uid": uid,
            "uid8": uid8,
            "question": question,
            "ground_truth": ground_truth,
            "question_type": q_type,
            "response": response,
            "correct": correct,
            "status": "scored",
        }

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(score_item, item): item for item in locomo_items}
        for i, fut in enumerate(as_completed(futures)):
            r = fut.result()
            with lock:
                results.append(r)
            if (i + 1) % 10 == 0:
                print(f"  Scored {i+1}/{len(locomo_items)} ...")

    # Aggregate
    scored = [r for r in results if r["correct"] is not None]
    correct = sum(1 for r in scored if r["correct"])
    total = len(scored)
    acc = correct / total if total else 0

    by_type = {}
    for r in scored:
        qt = r["question_type"]
        if qt not in by_type:
            by_type[qt] = {"correct": 0, "total": 0}
        by_type[qt]["total"] += 1
        if r["correct"]:
            by_type[qt]["correct"] += 1

    print(f"\nOverall: {correct}/{total} = {acc:.1%}")
    for qt, stats in sorted(by_type.items()):
        pct = stats["correct"] / stats["total"] if stats["total"] else 0
        print(f"  {qt}: {stats['correct']}/{stats['total']} = {pct:.1%}")

    summary = {
        "overall_accuracy": acc,
        "correct": correct,
        "total": total,
        "missing": len(locomo_items) - len(scored),
        "by_type": by_type,
        "scorer": args.scorer,
        "items": results,
    }
    Path(args.output).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
