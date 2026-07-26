"""P0-1: Independent accuracy validation of the RECAST abductive adjudicator.

Design:
  Positive pairs  (n=150): M_old + M_new from SAME STALE sample — adjudicator should fire
  Negative pairs  (n=150): M_old from sample i + M_new from sample i+150 — should NOT fire

The adjudicator is the ABDUCTIVE_JUDGMENT_PROMPT.  We skip IMPACT_HYPOTHESIS to isolate
the adjudicator itself; a minimal hypothesis derived directly from M_new is used instead.

Outcome labels:
  positive → FIRE   if confidence >= 0.35 (enters pool) or type=direct_invalidation
  negative → NOFIRE if confidence < 0.35 and type=no_conflict (or no judgment emitted)

Metrics reported: precision, recall, F1, accuracy at threshold=0.35 and threshold=0.75.
"""

import json
import os
import sys
import random
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Tuple

# ── project root on path ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from prompt_lib.new_templates import ABDUCTIVE_JUDGMENT_PROMPT
from llm_layer.client import LLMClient

# ── env / API ─────────────────────────────────────────────────────────────────
def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

_load_env(ROOT / ".env")

MODEL     = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")
API_KEY   = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

CACHE_DIR = ROOT / "runs" / "adjudicator_validation" / ".cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

DATA_PATH = ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
OUT_PATH  = ROOT / "runs" / "adjudicator_validation" / "results.json"
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

N_PER_CLASS = 150   # 150 positive + 150 negative
WORKERS     = 8     # concurrent API calls
FIRE_THRESH = 0.35  # minimum confidence to count as "fired"


def make_client() -> LLMClient:
    return LLMClient(
        model=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        cache_dir=CACHE_DIR,
    )


def call_adjudicator(
    client: LLMClient,
    statement: str,
    m_old: str,
    item_id: str = "m_00001",
) -> Dict[str, Any]:
    """Run ABDUCTIVE_JUDGMENT for a single (statement, m_old) pair.
    We pass a minimal hypothesis derived from statement to avoid IMPACT_HYPOTHESIS cost.
    """
    hypothesis = f"The user's situation has changed: {statement[:120]}"
    candidates_text = f"[{item_id}] {m_old}"
    hypotheses_text = f"- {hypothesis}"

    prompt = (
        ABDUCTIVE_JUDGMENT_PROMPT
        .replace("{statement}", statement)
        .replace("{hypotheses}", hypotheses_text)
        .replace("{candidates}", candidates_text)
    )

    try:
        result = client.call_json(
            "You are a precise memory conflict detector. Output JSON only.",
            prompt,
        )
    except Exception as exc:
        return {"_error": str(exc)}

    return result


def process_pair(args: Tuple) -> Dict[str, Any]:
    label, statement, m_old, uid_old, uid_new = args
    client = make_client()
    raw = call_adjudicator(client, statement, m_old)

    if "_error" in raw:
        return {
            "label": label, "uid_old": uid_old, "uid_new": uid_new,
            "error": raw["_error"], "fired": None, "confidence": None, "jtype": None,
        }

    judgments = raw.get("judgments", [])
    # Find judgment for our candidate
    best_conf = 0.0
    best_type = "no_conflict"
    for j in judgments:
        try:
            conf = float(j.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        jtype = str(j.get("type", "no_conflict"))
        if conf > best_conf:
            best_conf = conf
            best_type = jtype

    fired = best_conf >= FIRE_THRESH and best_type != "no_conflict"

    return {
        "label": label,           # "positive" | "negative"
        "uid_old": uid_old,
        "uid_new": uid_new,
        "statement": statement[:120],
        "m_old": m_old[:120],
        "confidence": best_conf,
        "jtype": best_type,
        "fired": fired,
        "judgments_raw": judgments,
    }


def compute_metrics(results: List[Dict], thresh: float) -> Dict[str, float]:
    tp = fp = tn = fn = 0
    for r in results:
        if r.get("confidence") is None:
            continue
        fired = r["confidence"] >= thresh and r.get("jtype", "no_conflict") != "no_conflict"
        pos = r["label"] == "positive"
        if fired and pos:
            tp += 1
        elif fired and not pos:
            fp += 1
        elif not fired and not pos:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)
    acc = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) > 0 else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "accuracy": acc,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def main() -> None:
    random.seed(42)

    print(f"Loading STALE data from {DATA_PATH}", flush=True)
    with open(DATA_PATH) as f:
        data = json.load(f)

    # Shuffle deterministically
    random.shuffle(data)

    # Need at least 2×N_PER_CLASS samples
    assert len(data) >= 2 * N_PER_CLASS, f"Not enough samples: {len(data)}"

    pos_samples = data[:N_PER_CLASS]
    neg_old_samples = data[:N_PER_CLASS]           # M_old from first half
    neg_new_samples = data[N_PER_CLASS:2*N_PER_CLASS]  # M_new from second half

    tasks: List[Tuple] = []
    for s in pos_samples:
        tasks.append(("positive", s["M_new"], s["M_old"], s["uid"], s["uid"]))

    for so, sn in zip(neg_old_samples, neg_new_samples):
        # Cross-pair: M_old from one sample, M_new from another
        tasks.append(("negative", sn["M_new"], so["M_old"], so["uid"], sn["uid"]))

    print(f"Running {len(tasks)} adjudicator calls ({N_PER_CLASS} positive + {N_PER_CLASS} negative)", flush=True)
    print(f"Model: {MODEL}, workers: {WORKERS}", flush=True)

    results = []
    errors = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(process_pair, t): i for i, t in enumerate(tasks)}
        for i, fut in enumerate(concurrent.futures.as_completed(futures)):
            r = fut.result()
            results.append(r)
            if r.get("error"):
                errors += 1
            if (i + 1) % 20 == 0:
                done_pos = sum(1 for x in results if x["label"] == "positive" and x.get("fired") is True)
                done_neg_fire = sum(1 for x in results if x["label"] == "negative" and x.get("fired") is True)
                print(f"  [{i+1}/{len(tasks)}] positive fired so far: {done_pos}  negative fired: {done_neg_fire}  errors: {errors}", flush=True)

    # Metrics
    m35 = compute_metrics(results, thresh=0.35)
    m75 = compute_metrics(results, thresh=0.75)

    print("\n" + "="*60)
    print(f"ADJUDICATOR VALIDATION RESULTS  (n={len(results)}, errors={errors})")
    print("="*60)
    print(f"  threshold=0.35  P={m35['precision']:.3f}  R={m35['recall']:.3f}  F1={m35['f1']:.3f}  Acc={m35['accuracy']:.3f}  (TP={m35['tp']} FP={m35['fp']} TN={m35['tn']} FN={m35['fn']})")
    print(f"  threshold=0.75  P={m75['precision']:.3f}  R={m75['recall']:.3f}  F1={m75['f1']:.3f}  Acc={m75['accuracy']:.3f}  (TP={m75['tp']} FP={m75['fp']} TN={m75['tn']} FN={m75['fn']})")

    output = {
        "n_positive": N_PER_CLASS,
        "n_negative": N_PER_CLASS,
        "n_errors": errors,
        "model": MODEL,
        "metrics_0_35": m35,
        "metrics_0_75": m75,
        "results": results,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
