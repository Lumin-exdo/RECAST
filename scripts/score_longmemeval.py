"""
Score RECAST answers against LongMemEval ground truth.

Usage:
    python scripts/score_longmemeval.py \
        --answers /path/to/run/answers_LME.json \
        --data /tmp/longmemeval_recast.json \
        --output /path/to/run/scores_LME.json \
        --scorer qwen3.6-plus \
        [--workers 8]

The scorer calls qwen3.6-plus with the LongMemEval judge prompt.
"""

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI


# --- LongMemEval judge prompts (adapted from longmemeval paper) ---

JUDGE_TEMPLATES = {
    # Standard QA
    "default": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
        "If the response is equivalent to the correct answer or contains all the intermediate steps "
        "to get the correct answer, you should also answer yes. "
        "If the response only contains a subset of the information required by the answer, answer no.\n\n"
        "Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
        "Is the model response correct? Answer yes or no only."
    ),
    # knowledge-update: allow if updated answer present alongside old one
    "knowledge-update": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
        "If the response contains some previous information along with an updated answer, "
        "the response should be considered as correct as long as the updated answer is the required answer.\n\n"
        "Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
        "Is the model response correct? Answer yes or no only."
    ),
    # temporal-reasoning: off-by-one allowed
    "temporal-reasoning": (
        "I will give you a question, a correct answer, and a response from a model. "
        "Please answer yes if the response contains the correct answer. Otherwise, answer no. "
        "If the response is equivalent to the correct answer or contains all the intermediate steps "
        "to get the correct answer, you should also answer yes. "
        "In addition, do not penalize off-by-one errors for the number of days. "
        "If the question asks for the number of days/weeks/months, etc., and the model makes "
        "off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response "
        "is still correct.\n\n"
        "Question: {question}\n\nCorrect Answer: {answer}\n\nModel Response: {response}\n\n"
        "Is the model response correct? Answer yes or no only."
    ),
}


def get_judge_prompt(question_type: str, question: str, answer: str, response: str) -> str:
    tmpl = JUDGE_TEMPLATES.get(question_type, JUDGE_TEMPLATES["default"])
    return tmpl.format(question=question, answer=answer, response=response)


def call_judge(client: OpenAI, model: str, prompt: str, max_retries: int = 3) -> bool:
    for attempt in range(max_retries):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.0,
            )
            text = (completion.choices[0].message.content or "").strip().lower()
            if text.startswith("yes"):
                return True
            if text.startswith("no"):
                return False
            # ambiguous — treat as no
            print(f"[WARN] ambiguous judge response: {text!r}")
            return False
        except Exception as e:
            print(f"[Retry {attempt+1}/{max_retries}] judge error: {e}")
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="", help="run_new_mem run dir (reads per-sample answer.json files)")
    parser.add_argument("--answers", default="", help="merged answers.json (alternative to --run-dir)")
    parser.add_argument("--data", required=True, help="longmemeval_recast.json (with _ground_truth)")
    parser.add_argument("--output", required=True, help="Output scores JSON")
    parser.add_argument("--scorer", default="qwen3.6-plus")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--scorer-base-url", default="", help="Override scorer API base URL")
    parser.add_argument("--scorer-api-key", default="", help="Override scorer API key")
    args = parser.parse_args()

    # Load answers — prefer per-sample files (survive partial runs)
    answers = {}
    if args.run_dir:
        run_dir = Path(args.run_dir)
        loaded = 0
        for ans_file in sorted(run_dir.glob("*/answer.json")):
            try:
                a = json.loads(ans_file.read_text())
                uid = a["uid"]
                resp = a.get("target_model_responses", {})
                answers[uid] = resp.get("dim1_response", "") or ""
                loaded += 1
            except Exception as e:
                print(f"[WARN] failed to read {ans_file}: {e}")
        print(f"Loaded {loaded} answers from per-sample files in {run_dir}")
    elif args.answers:
        with open(args.answers) as f:
            answers_list = json.load(f)
        for a in answers_list:
            uid = a["uid"]
            resp = a.get("target_model_responses", {})
            answers[uid] = resp.get("dim1_response", "") or ""
        print(f"Loaded {len(answers)} answers from {args.answers}")
    else:
        raise ValueError("Must specify --run-dir or --answers")

    # Load ground truth
    with open(args.data) as f:
        data = json.load(f)
    gt_map = {
        s["uid"]: {
            "answer": s["_ground_truth"],
            "question": s["probing_queries"]["dim1_query"],
            "question_type": s["_question_type"],
        }
        for s in data
    }

    # Set up scorer client
    api_key = args.scorer_api_key or os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url = args.scorer_base_url or os.environ.get("SCORER_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    client = OpenAI(api_key=api_key, base_url=base_url)

    results = {}
    lock = threading.Lock()

    def score_one(uid: str) -> tuple:
        if uid not in gt_map:
            return uid, None
        gt = gt_map[uid]
        response = answers.get(uid, "")
        if not response:
            return uid, False
        prompt = get_judge_prompt(
            gt["question_type"], gt["question"], gt["answer"], response
        )
        passed = call_judge(client, args.scorer, prompt)
        return uid, passed

    uids_to_score = [s["uid"] for s in data if s["uid"] in answers]
    print(f"Scoring {len(uids_to_score)} samples with {args.scorer}...")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(score_one, uid): uid for uid in uids_to_score}
        for i, fut in enumerate(as_completed(futures)):
            uid, passed = fut.result()
            with lock:
                results[uid] = passed
            if (i + 1) % 10 == 0:
                correct = sum(1 for v in results.values() if v)
                total = len(results)
                print(f"  Progress: {i+1}/{len(uids_to_score)} | acc={correct/total*100:.1f}%")

    # Compute summary
    correct = sum(1 for v in results.values() if v is True)
    total = len([v for v in results.values() if v is not None])
    accuracy = correct / total if total > 0 else 0.0
    print(f"\nFinal accuracy: {correct}/{total} = {accuracy*100:.1f}%")

    # Per question-type breakdown
    type_results: dict = {}
    for uid, passed in results.items():
        qtype = gt_map.get(uid, {}).get("question_type", "unknown")
        if qtype not in type_results:
            type_results[qtype] = {"correct": 0, "total": 0}
        if passed is not None:
            type_results[qtype]["total"] += 1
            if passed:
                type_results[qtype]["correct"] += 1
    for qtype, counts in type_results.items():
        acc = counts["correct"] / counts["total"] * 100 if counts["total"] else 0
        print(f"  {qtype}: {counts['correct']}/{counts['total']} = {acc:.1f}%")

    output = {
        "config": {
            "scorer": args.scorer,
            "n_scored": total,
        },
        "summary": {
            "accuracy": accuracy,
            "correct": correct,
            "total": total,
            "by_type": type_results,
        },
        "details": {uid: {"pass": passed, **gt_map.get(uid, {})} for uid, passed in results.items()},
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2))
    print(f"Saved scores to {args.output}")


if __name__ == "__main__":
    main()
