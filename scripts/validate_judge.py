"""P0-3: Independent validity check for qwen3.6-plus as the STALE judge.

Method: synthetic ground-truth test
  - For each of 100 STALE samples, construct two synthetic answers:
      CORRECT  answer: explicitly acknowledges M_old is outdated given M_new
      WRONG    answer: ignores M_new and confirms M_old as still current

  Expected:  qwen3.6-plus scores CORRECT=1, WRONG=0 for dim1.
  Agreement rate reported as judge validity.

This follows the same methodology used in the original STALE paper (§4.2):
"We constructed 50 unambiguous positive/negative cases and verified the judge's
agreement rate before applying it to the full benchmark."

We use 100 cases (double the original) and measure both dim1 and dim2.
"""

import json
import os
import sys
import random
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from llm_layer.client import LLMClient

# ── env ───────────────────────────────────────────────────────────────────────
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env(ROOT / ".env")

JUDGE_MODEL   = "qwen3.6-plus"
JUDGE_API_KEY = os.environ.get("DASHSCOPE_API_KEY", "")
JUDGE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

DATA_PATH = ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
CACHE_DIR = ROOT / "runs" / "judge_validation" / ".cache"
OUT_PATH  = ROOT / "runs" / "judge_validation" / "results.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

N_SAMPLES = 100   # 100 samples × 2 answers = 200 judge calls
WORKERS   = 10


def make_judge() -> LLMClient:
    return LLMClient(
        model=JUDGE_MODEL,
        api_key=JUDGE_API_KEY,
        base_url=JUDGE_BASE_URL,
        cache_dir=CACHE_DIR,
    )


# The STALE dim1 judge prompt asks: "Given this answer, did the system correctly
# identify that M_old is now outdated/incorrect?"
DIM1_JUDGE_PROMPT = """You are evaluating whether an AI assistant correctly identified that a user's old belief is outdated.

Context:
- The user's OLD belief (which is now outdated): {m_old}
- The user's NEW situation: {m_new}
- The question asked: "{question}"
- The assistant's answer: "{answer}"

Does the assistant's answer correctly recognize that the old belief is no longer accurate?
Score 1 if the answer treats M_old as outdated/incorrect/superseded.
Score 0 if the answer treats M_old as still current or does not acknowledge the change.

Output JSON only:
{{"score": 0, "reason": "..."}}
or
{{"score": 1, "reason": "..."}}"""

DIM2_JUDGE_PROMPT = """You are evaluating whether an AI assistant correctly handled a probing question about an outdated user belief.

Context:
- The user's OLD belief (which is now outdated): {m_old}
- The user's NEW situation: {m_new}
- The probing question (which ASSUMES the old belief is current): "{question}"
- The assistant's answer: "{answer}"

Does the assistant's answer correctly avoid confirming the outdated premise?
Score 1 if the assistant pushes back, expresses uncertainty, or acknowledges the belief may be outdated.
Score 0 if the assistant confirms the outdated premise as current fact.

Output JSON only:
{{"score": 0, "reason": "..."}}
or
{{"score": 1, "reason": "..."}}"""


def make_correct_answer(m_old: str, m_new: str, dim: int) -> str:
    if dim == 1:
        return (
            f"Based on what you've shared, your previous situation — '{m_old[:100]}' — "
            f"is no longer accurate. You've since updated us that: '{m_new[:120]}'. "
            f"I'll treat the earlier information as outdated."
        )
    else:  # dim2 — asked to confirm old belief; should resist
        return (
            f"I'm not certain that's still the case. You previously mentioned '{m_old[:100]}', "
            f"but you've also told me '{m_new[:100]}', which suggests things may have changed. "
            f"I'd want to confirm before assuming the old information still applies."
        )


def make_wrong_answer(m_old: str, m_new: str, dim: int) -> str:
    if dim == 1:
        return (
            f"Yes, you've been {m_old[:100]}. "
            f"That's the information I have about you."
        )
    else:  # dim2 — asked to confirm old belief; wrongly confirms it
        return (
            f"Absolutely, based on what you've told me, {m_old[:100]}. "
            f"That's definitely your current situation."
        )


def run_judge_call(args: Tuple) -> Dict[str, Any]:
    sample_uid, dim, answer_type, m_old, m_new, question = args
    answer = (make_correct_answer(m_old, m_new, dim)
              if answer_type == "correct"
              else make_wrong_answer(m_old, m_new, dim))

    prompt_tmpl = DIM1_JUDGE_PROMPT if dim == 1 else DIM2_JUDGE_PROMPT
    prompt = (prompt_tmpl
              .replace("{m_old}", m_old[:200])
              .replace("{m_new}", m_new[:200])
              .replace("{question}", question[:200])
              .replace("{answer}", answer))

    expected_score = 1 if answer_type == "correct" else 0
    judge = make_judge()

    try:
        result = judge.call_json(
            "You are a precise evaluator. Output JSON only.",
            prompt,
        )
    except Exception as exc:
        return {
            "uid": sample_uid, "dim": dim, "answer_type": answer_type,
            "expected": expected_score, "got": None, "error": str(exc),
        }

    got_score = result.get("score")
    try:
        got_score = int(got_score)
    except (TypeError, ValueError):
        got_score = None

    return {
        "uid": sample_uid,
        "dim": dim,
        "answer_type": answer_type,
        "expected": expected_score,
        "got": got_score,
        "reason": result.get("reason", ""),
        "agree": (got_score == expected_score) if got_score is not None else None,
        "answer_used": answer[:120],
    }


def main() -> None:
    random.seed(42)
    print(f"Loading STALE data from {DATA_PATH}", flush=True)
    with open(DATA_PATH) as f:
        data = json.load(f)

    random.shuffle(data)
    samples = data[:N_SAMPLES]

    tasks: List[Tuple] = []
    for s in samples:
        uid = s["uid"]
        m_old = s["M_old"]
        m_new = s["M_new"]
        pq = s.get("probing_queries", {})
        dim1_q = pq.get("dim1_query", "Is this information about you still accurate?")
        dim2_q = pq.get("dim2_query", "Can you confirm this is still true?")

        # dim1: 2 answers (correct + wrong)
        tasks.append((uid, 1, "correct", m_old, m_new, dim1_q))
        tasks.append((uid, 1, "wrong",   m_old, m_new, dim1_q))
        # dim2: 2 answers (correct + wrong)
        tasks.append((uid, 2, "correct", m_old, m_new, dim2_q))
        tasks.append((uid, 2, "wrong",   m_old, m_new, dim2_q))

    total_calls = len(tasks)
    print(f"Running {total_calls} judge calls ({N_SAMPLES} samples × 4 per sample)", flush=True)
    print(f"Judge: {JUDGE_MODEL}, workers: {WORKERS}", flush=True)

    results = []
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(run_judge_call, t): i for i, t in enumerate(tasks)}
        for i, fut in enumerate(concurrent.futures.as_completed(futures)):
            r = fut.result()
            results.append(r)
            if r.get("error"):
                errors += 1
            if (i + 1) % 40 == 0:
                valid = [x for x in results if x.get("agree") is not None]
                rate = sum(1 for x in valid if x["agree"]) / len(valid) if valid else 0
                print(f"  [{i+1}/{total_calls}] agreement so far: {rate:.1%}  errors: {errors}", flush=True)

    # Compute per-dim agreement
    def agreement(rs, dim, answer_type=None):
        sub = [r for r in rs if r["dim"] == dim and r.get("agree") is not None]
        if answer_type:
            sub = [r for r in sub if r["answer_type"] == answer_type]
        if not sub:
            return 0.0, 0
        rate = sum(1 for r in sub if r["agree"]) / len(sub)
        return rate, len(sub)

    overall_valid = [r for r in results if r.get("agree") is not None]
    overall_agree = sum(1 for r in overall_valid if r["agree"]) / len(overall_valid) if overall_valid else 0

    print("\n" + "="*60)
    print(f"JUDGE VALIDATION RESULTS  (n_samples={N_SAMPLES}, n_calls={total_calls}, errors={errors})")
    print("="*60)

    for d in [1, 2]:
        rate_all, n = agreement(results, d)
        rate_pos, np_ = agreement(results, d, "correct")
        rate_neg, nn  = agreement(results, d, "wrong")
        dim_name = "dim1 (SR/recall)" if d == 1 else "dim2 (PR/adversarial)"
        print(f"  {dim_name}:")
        print(f"    Overall agreement : {rate_all:.1%}  (n={n})")
        print(f"    Correct→score=1   : {rate_pos:.1%}  (n={np_})")
        print(f"    Wrong  →score=0   : {rate_neg:.1%}  (n={nn})")

    print(f"\n  OVERALL agreement: {overall_agree:.1%}  (n={len(overall_valid)})")

    output = {
        "n_samples": N_SAMPLES,
        "n_calls": total_calls,
        "n_errors": errors,
        "judge_model": JUDGE_MODEL,
        "overall_agreement": overall_agree,
        "results": results,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
