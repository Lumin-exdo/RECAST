"""P0-1 (revised): Adjudicator validation with HARD negatives.

Strategy
--------
Easy negatives (cross-sample random pairing) have no diagnostic value: the
adjudicator only needs to detect topic mismatch, not semantic conflict.

Hard negatives are generated via two methods:

  Level-1  (same-topic, different-slot):
    Take M_old from sample A (e.g., user location) and pair with M_new from
    a sample B in the SAME attribute category but whose specific content does
    NOT logically contradict A's M_old.

    Operationalised here: use LLM to verify whether M_new from B actually
    contradicts A's M_old — only keep pairs where an expert judge says "no
    conflict", making these confirmed non-conflicting same-domain pairs.

  Level-2  (same content, temporally defused):
    Take the TRUE M_new for a sample (which IS a conflict) and paraphrase it
    with an LLM so it sounds topically similar but is NOT a real invalidation:
      • "I'm thinking about doing X someday" (intention, not fact)
      • "My friend recently did X" (third-person)
      • "I tried X briefly but didn't stick with it" (past, non-persistent)
      • "I spent a few days doing X while travelling" (temporary)

    The adjudicator should give no_conflict on these since the new statement
    no longer conclusively supersedes M_old.

This produces hard negatives where the adjudicator must reason carefully
about semantic conflict — the exact capability being claimed in the paper.

Output
------
  runs/adjudicator_hard/results.json:
    {
      "easy_neg": {...metrics for cross-sample random negatives...},
      "hard_neg_L1": {...metrics for same-domain non-conflicting pairs...},
      "hard_neg_L2": {...metrics for temporally-defused M_new pairs...},
      "combined_hard": {...merged L1+L2 metrics...},
      "positive": {...recall / FN analysis...},
    }
"""

import json
import os
import sys
import random
import concurrent.futures
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from prompt_lib.new_templates import ABDUCTIVE_JUDGMENT_PROMPT
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

MODEL     = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")
API_KEY   = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
BASE_URL  = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")

CACHE_DIR = ROOT / "runs" / "adjudicator_hard" / ".cache"
OUT_PATH  = ROOT / "runs" / "adjudicator_hard" / "results.json"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

DATA_PATH = ROOT / "STALE/STALE/outputs/STALE_MAIN.json"

N_L2         = 30    # Level-2 hard negatives — small batch first, scale after QC pass rate confirmed
N_L1         = 20    # Level-1 hard negatives — small batch first
N_POSITIVE   = 150   # positive pairs (same as original)
N_EASY_NEG   = 150   # easy negatives (keep original, no new API calls needed)
WORKERS      = 8
FIRE_THRESH  = 0.35


# ─────────────────────────────────────────────────────────────────────────────
# LLM helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_client() -> LLMClient:
    return LLMClient(model=MODEL, api_key=API_KEY, base_url=BASE_URL, cache_dir=CACHE_DIR)


# Prompt to defuse M_new into a non-conflicting paraphrase (Level-2)
_DEFUSE_PROMPT = """You are rewriting a user statement so that it NO LONGER invalidates or supersedes the user's old belief.

OLD belief (M_old): {m_old}
CONFLICTING new statement (M_new): {m_new}

Rewrite M_new so that:
1. It is topically in the same domain (same subject, similar vocabulary)
2. It DOES NOT constitute a real conflict with M_old — use ONE of these strategies:
   • "intention, not fact": "I've been thinking about X someday..."
   • "temporary": "I spent a few days doing X while travelling, but that's behind me now"
   • "third-person": "My friend/colleague recently did X"
   • "informational, not personal": "I read that X is becoming common"
   • "past experiment, not ongoing": "I briefly tried X a while back but didn't stick with it"
3. The rewrite must be a natural first-person statement a user might say in conversation.
4. Keep the rewrite SHORT (1-2 sentences).
5. Do NOT mention M_old in the rewrite.

Output JSON only:
{{"defused_statement": "...", "strategy": "intention|temporary|third-person|informational|past-experiment"}}"""


# Prompt to verify a cross-sample pair truly has no conflict (Level-1 verification)
_VERIFY_NO_CONFLICT = """Does this new statement logically contradict or supersede the old belief?

OLD belief: {m_old}
NEW statement: {m_new}

Answer STRICTLY: does the new statement make the old belief FALSE or very likely outdated?
- "yes" only if the old belief is clearly wrong given the new statement
- "no" if they can both be true simultaneously OR if the new statement is compatible with the old one

Output JSON only: {{"conflicts": true/false, "reason": "one sentence"}}"""


def defuse_m_new(client: LLMClient, m_old: str, m_new: str) -> Optional[Tuple[str, str]]:
    """Use LLM to create a non-conflicting paraphrase of M_new (Level-2).
    Returns (defused_statement, strategy) or None on failure.
    """
    prompt = _DEFUSE_PROMPT.replace("{m_old}", m_old[:300]).replace("{m_new}", m_new[:300])
    try:
        result = client.call_json(
            "You are a precise text transformation assistant. Output JSON only.",
            prompt,
        )
        stmt     = result.get("defused_statement", "")
        strategy = result.get("strategy", "unknown")
        return (str(stmt).strip(), str(strategy).strip()) if stmt else None
    except Exception:
        return None


def verify_no_conflict(client: LLMClient, m_old: str, m_new: str) -> bool:
    """Returns True if the pair is confirmed to have NO conflict (Level-1 filter)."""
    prompt = _VERIFY_NO_CONFLICT.replace("{m_old}", m_old[:300]).replace("{m_new}", m_new[:300])
    try:
        result = client.call_json(
            "You are a precise logical evaluator. Output JSON only.",
            prompt,
        )
        return result.get("conflicts") is False or result.get("conflicts") == "false"
    except Exception:
        return False


def call_adjudicator(
    client: LLMClient,
    statement: str,
    m_old: str,
    item_id: str = "m_00001",
) -> Dict[str, Any]:
    hypothesis = f"The user's situation has changed: {statement[:120]}"
    prompt = (
        ABDUCTIVE_JUDGMENT_PROMPT
        .replace("{statement}", statement)
        .replace("{hypotheses}", f"- {hypothesis}")
        .replace("{candidates}", f"[{item_id}] {m_old}")
    )
    try:
        return client.call_json(
            "You are a precise memory conflict detector. Output JSON only.",
            prompt,
        )
    except Exception as exc:
        return {"_error": str(exc)}


def extract_best_judgment(raw: Dict) -> Tuple[float, str]:
    """Returns (best_confidence, best_type) from a raw judgment dict."""
    if "_error" in raw:
        return 0.0, "error"
    judgments = raw.get("judgments", [])
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
    return best_conf, best_type


# ─────────────────────────────────────────────────────────────────────────────
# Build pair sets
# ─────────────────────────────────────────────────────────────────────────────

def build_l2_hard_negs(data: List[Dict], n: int) -> List[Dict]:
    """Generate Level-2 hard negatives by LLM-defusing real M_new statements.

    Quality control: after defusing, verify with an independent LLM call that
    the defused statement truly does NOT conflict with M_old. Pairs that fail
    QC (i.e., the defused statement still implicitly invalidates M_old) are
    discarded. This matches the STALE paper's own QC methodology.
    """
    client = make_client()
    # Over-sample × 5 because QC will reject some
    candidates = random.sample(data, min(n * 5, len(data)))

    results = []
    qc_rejected = 0
    for s in candidates:
        if len(results) >= n:
            break
        ret = defuse_m_new(client, s["M_old"], s["M_new"])
        if not ret:
            continue
        defused, strategy = ret
        if len(defused) < 20:
            continue
        # QC: verify the defused statement truly has no conflict with M_old
        qc_pass = verify_no_conflict(client, s["M_old"], defused)
        if not qc_pass:
            qc_rejected += 1
            continue
        results.append({
            "uid": s["uid"],
            "m_old": s["M_old"],
            "original_m_new": s["M_new"],
            "defused_statement": defused,
            "strategy": strategy,
            "level": "L2",
        })
        if len(results) % 10 == 0:
            print(f"  [L2 QC] {len(results)}/{n} passed (rejected={qc_rejected})", flush=True)

    print(f"  [L2] final: {len(results)} QC-passed / {qc_rejected} QC-rejected", flush=True)
    return results[:n]


def build_l1_hard_negs(data: List[Dict], n: int) -> List[Dict]:
    """Generate Level-1 hard negatives: cross-sample pairs verified to have no conflict.

    Strategy: pair M_old from sample A with M_new from sample B where
    A.uid != B.uid. Then verify with a judge LLM that the pair has no conflict.
    We over-sample pairs and keep only confirmed non-conflicting ones.
    """
    client = make_client()

    shuffled_a = data[:]
    shuffled_b = data[:]
    random.shuffle(shuffled_b)

    # Create candidate cross-pairs
    pairs = [(a, b) for a, b in zip(shuffled_a, shuffled_b) if a["uid"] != b["uid"]]
    random.shuffle(pairs)

    results = []
    checked = 0
    for a, b in pairs:
        if len(results) >= n:
            break
        checked += 1
        no_conflict = verify_no_conflict(client, a["M_old"], b["M_new"])
        if no_conflict:
            results.append({
                "uid_old": a["uid"],
                "uid_new": b["uid"],
                "m_old": a["M_old"],
                "cross_statement": b["M_new"],
                "level": "L1",
            })
        if checked % 20 == 0:
            print(f"  [L1 filter] checked={checked}, confirmed_no_conflict={len(results)}/{n}", flush=True)
        if checked > n * 10:
            print(f"  [L1] stopping after {checked} checks (found {len(results)})", flush=True)
            break

    print(f"  [L1] retained {len(results)}/{checked} pairs as confirmed non-conflicting", flush=True)
    return results[:n]


# ─────────────────────────────────────────────────────────────────────────────
# Run adjudicator on a labeled set
# ─────────────────────────────────────────────────────────────────────────────

def run_adjudicator_batch(
    pairs: List[Tuple[str, str, str]],  # (label, statement, m_old)
    workers: int = WORKERS,
) -> List[Dict]:
    """Run adjudicator on a list of (label, statement, m_old) pairs."""

    def _process(args):
        label, statement, m_old, meta = args
        client = make_client()
        raw = call_adjudicator(client, statement, m_old)
        conf, jtype = extract_best_judgment(raw)
        fired = conf >= FIRE_THRESH and jtype != "no_conflict"
        return {
            "label": label,
            "meta": meta,
            "statement": statement[:100],
            "m_old": m_old[:100],
            "confidence": conf,
            "jtype": jtype,
            "fired": fired,
            "error": raw.get("_error"),
        }

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_process, t): i for i, t in enumerate(pairs)}
        for i, fut in enumerate(concurrent.futures.as_completed(futs)):
            results.append(fut.result())
            if (i + 1) % 20 == 0:
                print(f"    [{i+1}/{len(pairs)}] done", flush=True)
    return results


def compute_metrics(results: List[Dict], thresh: float, label_class: str) -> Dict:
    """Compute P/R/F1 for a single class label (positive/negative)."""
    tp = fp = tn = fn = 0
    errors = 0
    for r in results:
        if r.get("error") or r.get("confidence") is None:
            errors += 1
            continue
        conf = r["confidence"]
        jtype = r.get("jtype", "no_conflict")
        fired = conf >= thresh and jtype != "no_conflict"
        pos = r["label"] == "positive"
        if fired and pos:
            tp += 1
        elif fired and not pos:
            fp += 1
        elif not fired and not pos:
            tn += 1
        else:
            fn += 1

    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1   = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    acc  = (tp + tn) / (tp + fp + tn + fn) if (tp + fp + tn + fn) > 0 else 0.0
    fpr  = fp / (fp + tn) if (fp + tn) > 0 else 0.0  # false positive rate (negatives misfired)

    return {
        "precision": prec, "recall": rec, "f1": f1, "accuracy": acc,
        "false_positive_rate": fpr,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn, "errors": errors,
        "threshold": thresh,
    }


def print_metrics(name: str, m35: Dict, m75: Dict) -> None:
    print(f"\n  [{name}]")
    print(f"    τ=0.35  P={m35['precision']:.3f} R={m35['recall']:.3f} F1={m35['f1']:.3f} FPR={m35['false_positive_rate']:.3f} (TP={m35['tp']} FP={m35['fp']} TN={m35['tn']} FN={m35['fn']})")
    print(f"    τ=0.75  P={m75['precision']:.3f} R={m75['recall']:.3f} F1={m75['f1']:.3f} FPR={m75['false_positive_rate']:.3f} (TP={m75['tp']} FP={m75['fp']} TN={m75['tn']} FN={m75['fn']})")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    random.seed(42)
    print(f"Loading STALE data from {DATA_PATH}", flush=True)
    with open(DATA_PATH) as f:
        data = json.load(f)
    random.shuffle(data)

    # ── 1. Build positive pairs (same as original) ────────────────────────────
    print("\n[1/4] Building positive pairs (150)...", flush=True)
    pos_samples = data[:N_POSITIVE]
    pos_pairs = [
        ("positive", s["M_new"], s["M_old"], {"uid": s["uid"]})
        for s in pos_samples
    ]

    # ── 2. Easy negatives (cross-sample random; no new generation needed) ────
    print("[2/4] Building easy negatives (150, cross-sample random)...", flush=True)
    # Same construction as original validate_adjudicator.py for reproducibility
    neg_old_samples = data[:N_EASY_NEG]
    neg_new_samples = data[N_EASY_NEG:2 * N_EASY_NEG]
    easy_neg_pairs = [
        ("negative", sn["M_new"], so["M_old"],
         {"uid_old": so["uid"], "uid_new": sn["uid"], "neg_type": "easy"})
        for so, sn in zip(neg_old_samples, neg_new_samples)
    ]

    # ── 3. Level-2 hard negatives (LLM-defused M_new) ────────────────────────
    print(f"[3/4] Generating Level-2 hard negatives ({N_L2}, defused M_new)...", flush=True)
    l2_source = data[2 * N_EASY_NEG:]   # use remaining samples, no overlap with above
    l2_records = build_l2_hard_negs(l2_source, N_L2)
    l2_pairs = [
        ("negative", r["defused_statement"], r["m_old"],
         {"uid": r["uid"], "neg_type": "L2_defused"})
        for r in l2_records
    ]

    # ── 4. Level-1 hard negatives (same-domain cross-pair, verified) ─────────
    print(f"[4/4] Building Level-1 hard negatives ({N_L1}, same-domain, verified)...", flush=True)
    l1_source = data[2 * N_EASY_NEG:]   # same pool as L2
    l1_records = build_l1_hard_negs(l1_source, N_L1)
    l1_pairs = [
        ("negative", r["cross_statement"], r["m_old"],
         {"uid_old": r["uid_old"], "uid_new": r["uid_new"], "neg_type": "L1_same_domain"})
        for r in l1_records
    ]

    # ── 5. Run adjudicator on all pairs ──────────────────────────────────────
    all_pos_plus_easy = pos_pairs + easy_neg_pairs
    print(f"\nRunning adjudicator on {len(all_pos_plus_easy)} pos+easy pairs...", flush=True)
    results_easy = run_adjudicator_batch(all_pos_plus_easy)

    print(f"Running adjudicator on {len(l2_pairs)} L2 hard-neg pairs...", flush=True)
    results_l2 = run_adjudicator_batch(l2_pairs)

    print(f"Running adjudicator on {len(l1_pairs)} L1 hard-neg pairs...", flush=True)
    results_l1 = run_adjudicator_batch(l1_pairs)

    # ── 6. Compute metrics ───────────────────────────────────────────────────
    # Baseline (pos vs easy-neg)
    easy_35 = compute_metrics(results_easy, 0.35, "positive")
    easy_75 = compute_metrics(results_easy, 0.75, "positive")

    # L2: positive recall + L2 FPR
    combined_l2 = results_easy[:N_POSITIVE] + results_l2  # pos + l2-neg
    l2_35 = compute_metrics(combined_l2, 0.35, "positive")
    l2_75 = compute_metrics(combined_l2, 0.75, "positive")

    # L1: positive recall + L1 FPR
    combined_l1 = results_easy[:N_POSITIVE] + results_l1
    l1_35 = compute_metrics(combined_l1, 0.35, "positive")
    l1_75 = compute_metrics(combined_l1, 0.75, "positive")

    # Combined hard (pos + L1 + L2)
    combined_hard = results_easy[:N_POSITIVE] + results_l1 + results_l2
    hard_35 = compute_metrics(combined_hard, 0.35, "positive")
    hard_75 = compute_metrics(combined_hard, 0.75, "positive")

    # Per-strategy breakdown for L2
    strategy_counts: Dict[str, Dict] = {}
    for r2, rec in zip(results_l2, l2_records):
        strat = rec.get("strategy", "unknown")
        if strat not in strategy_counts:
            strategy_counts[strat] = {"total": 0, "fired": 0}
        strategy_counts[strat]["total"] += 1
        if r2.get("fired"):
            strategy_counts[strat]["fired"] += 1

    print("\n" + "=" * 70)
    print("ADJUDICATOR HARD-NEGATIVE VALIDATION RESULTS")
    print("=" * 70)
    print_metrics("Baseline (pos=150 + easy-neg=150)", easy_35, easy_75)
    print_metrics(f"With L2 hard-neg (pos=150 + L2={len(l2_pairs)}, QC-passed)", l2_35, l2_75)
    print_metrics(f"With L1 hard-neg (pos=150 + L1={len(l1_pairs)})", l1_35, l1_75)
    print_metrics(f"Combined hard (pos=150 + L1={len(l1_pairs)} + L2={len(l2_pairs)})", hard_35, hard_75)

    print("\n  [L2 per-strategy FPR at τ=0.35]")
    for strat, cnts in sorted(strategy_counts.items()):
        fpr = cnts["fired"] / cnts["total"] if cnts["total"] > 0 else 0.0
        print(f"    {strat:30s}: FPR={fpr:.3f}  ({cnts['fired']}/{cnts['total']} fired)")

    output = {
        "n_positive": N_POSITIVE,
        "n_easy_neg": N_EASY_NEG,
        "n_l1": len(l1_pairs),
        "n_l2": len(l2_pairs),
        "model": MODEL,
        "baseline_easy_neg": {"0.35": easy_35, "0.75": easy_75},
        "with_l2_hard_neg":  {"0.35": l2_35,   "0.75": l2_75},
        "with_l1_hard_neg":  {"0.35": l1_35,   "0.75": l1_75},
        "combined_hard_neg": {"0.35": hard_35,  "0.75": hard_75},
        "l2_per_strategy_fpr": strategy_counts,
        "l2_records": l2_records,
        "l1_records": l1_records,
        "results_easy": results_easy,
        "results_l2": results_l2,
        "results_l1": results_l1,
    }
    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved to {OUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
