"""
Batch abductive judgment experiment.
Runs EXP1 (minimal prompt, no thinking) vs EXP2 (original full-rules prompt, thinking ON)
on all identified B-class and D-class abductive failures from 63d3571 analysis.

B class: LLM saw the candidate but gave wrong confidence (too low / missed)
D class: LLM wrongly stale'd a valid memory (over-application)

For C-class (retrieval miss: candidate never in input) we inject the missing memory manually.
"""

import os, sys, json, re, time
from openai import OpenAI

# ─── Load env ────────────────────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

# ─── Minimal prompt template (current production prompt) ─────────────────────
MINIMAL_PROMPT_TEMPLATE = """For each candidate memory, decide whether the user's new statement makes it outdated.

New statement from user: {statement}

Context — what this statement likely implies has changed (use as reasoning bridges for indirect conflicts):
{hypotheses}

Candidate memories:
{candidates}

Output JSON only — include ALL candidates:
{
  "judgments": [
    {
      "target_item_id": "m_XXXXX",
      "target_content": "exact memory text",
      "inference_chain": "new fact → why this memory is now false or weakened",
      "confidence": 0.0,
      "type": "direct_invalidation|weakens_support|no_conflict"
    }
  ]
}

- direct_invalidation (confidence ≥ 0.65): the memory is now false or no longer applies
- weakens_support (confidence 0.35–0.65): the memory is less reliable but not conclusively outdated
- no_conflict (confidence < 0.35): the memory is unaffected
- Include ALL candidates — even no_conflict ones. Do not return an empty list.
- If a candidate is word-for-word identical to the new statement, set type=no_conflict, confidence=0.
- Biographical facts (birthplace, native language, has children) require direct explicit contradiction to invalidate (confidence ≥ 0.8).
- Do not hallucinate memory content — only judge the exact candidates provided.
"""

# ─── Case definitions ─────────────────────────────────────────────────────────
# Each case: uid, class, record_num, target_mem_kw, expected_type, description
# trace_path and record_num identify the exact abductive call to re-run

BASE = "/mnt/laq/RECAST/runs/63d3571"
CASES = [
    {
        "uid": "7ee76c41",
        "class": "B",
        "trace": f"{BASE}/improved_60_v2/0029/trace.json",
        "record": 285,
        "target_kw": "m_00011",
        "target_content": "currently in the middle of my master's program",
        "expected_type": "direct_invalidation",
        "desc": "starting first full-time job → should invalidate 'in master's program'",
    },
    {
        "uid": "2c711459",
        "class": "B",
        "trace": f"{BASE}/improved_60_fill/0312/trace.json",
        "record": 461,
        "target_kw": "m_00014",
        "target_content": "has had the same smartwatch on their wrist for over three years",
        "expected_type": "weakens_support",
        "desc": "device had swollen battery → LLM wrongly assumed 'device=phone'; should weaken 'same smartwatch 3 years'",
    },
    {
        "uid": "dae22057",
        "class": "B",
        "trace": f"{BASE}/improved_60_v2/0147/trace.json",
        "record": 402,
        "target_kw": "m_00055",
        "target_content": "has been carrying a lot of tension in their body lately",
        "expected_type": "direct_invalidation",
        "desc": "switched into laid-back schedule → body tension should be invalidated (original got conf=0.35, borderline)",
    },
    {
        "uid": "c9cc370e",
        "class": "D",
        "trace": f"{BASE}/improved_60_fill/0382/trace.json",
        "record": 523,
        "target_kw": "walking boot",
        "target_content": "doctor put me in a walking boot today and told me to stay off ladders and uneven surfaces",
        "expected_type": "no_conflict",
        "desc": "planning trip to Japan → original wrongly gave conf=0.6 weakens_support on walking boot (D-class over-application)",
    },
    {
        "uid": "c2cc2d39",
        "class": "D",
        "trace": f"{BASE}/improved_60_fill/0295/trace.json",
        "record": 374,
        "target_kw": "screen avoidance",
        "target_content": "diagnosed with screen-related health issues requiring strict post-work screen avoidance",
        "expected_type": "no_conflict",
        "desc": "headaches triggered by screen use actually CONFIRMS the medical order, not weakens it (original conf=0.4 wrong)",
    },
    # C-class with manually injected missing memory
    {
        "uid": "6ff5a576",
        "class": "C_inject",
        "trace": f"{BASE}/improved_60_v2/0007/trace.json",
        "record": 483,
        "target_kw": "home",
        "inject_memory": ("m_00053", "treats home as their main base and works remotely"),
        "target_content": "treats home as their main base and works remotely",
        "expected_type": "direct_invalidation",
        "desc": "settled into new desk at office downtown → injecting missing 'home as main base' memory to test if LLM would catch it",
    },
    {
        "uid": "eacb64ff",
        "class": "C_inject",
        "trace": f"{BASE}/improved_60_v2/0059/trace.json",
        "record": 547,
        "target_kw": "confidential",
        "inject_memory": ("m_00073", "in a work context where people confide in them because they are known for keeping things confidential"),
        "target_content": "in a work context where people confide in them because they are known for keeping things confidential",
        "expected_type": "weakens_support",
        "desc": "people kept distance at potluck → injecting missing 'trusted with confidential info' to test detection",
    },
]


# ─── Extract data from trace ──────────────────────────────────────────────────
def extract_from_sys_msg(sys_msg):
    """Parse statement, hypotheses, candidates from original system message."""
    # Extract statement
    stmt_m = re.search(r"New statement from user: (.+?)(?:\n|$)", sys_msg)
    statement = stmt_m.group(1).strip() if stmt_m else ""

    # Extract hypotheses block
    hyp_m = re.search(
        r"(?:Supporting impact hypotheses|Context)[^\n]*\n(.*?)\n(?:Candidate memories|Output JSON)",
        sys_msg, re.DOTALL
    )
    hypotheses = hyp_m.group(1).strip() if hyp_m else "(no hypotheses)"

    # Extract candidates block
    cand_m = re.search(r"Candidate memories[^\n]*\n((?:\[m_\d+\][^\n]*\n?)+)", sys_msg)
    candidates_raw = cand_m.group(1).strip() if cand_m else ""

    return statement, hypotheses, candidates_raw


def build_minimal_sys(statement, hypotheses, candidates_raw, inject_memory=None):
    """Build EXP1 system message using minimal template."""
    cands = candidates_raw
    if inject_memory:
        mid, content = inject_memory
        # Add to beginning of candidates list so it's visible
        cands = f"[{mid}] {content}\n" + cands

    return (MINIMAL_PROMPT_TEMPLATE
            .replace("{statement}", statement)
            .replace("{hypotheses}", hypotheses)
            .replace("{candidates}", cands))


# ─── API call ─────────────────────────────────────────────────────────────────
def call_api(sys_msg, user_msg, thinking, label, target_kw):
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
    model = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
        max_tokens=3000,
    )
    kwargs["extra_body"] = {
        "thinking": {"type": "enabled", "budget_tokens": 8000} if thinking
                    else {"type": "disabled"}
    }

    t0 = time.time()
    resp = client.chat.completions.create(**kwargs)
    elapsed = time.time() - t0

    content = resp.choices[0].message.content or ""
    thinking_text = getattr(resp.choices[0].message, "reasoning_content", "") or ""

    # Parse JSON and find target
    target_judgment = None
    n_judgments = 0
    try:
        j_start = content.find('{')
        if j_start >= 0:
            parsed = json.loads(content[j_start:])
            judgments = parsed.get("judgments", [])
            n_judgments = len(judgments)
            for jj in judgments:
                if target_kw.lower() in str(jj).lower():
                    target_judgment = jj
                    break
    except Exception as e:
        pass

    return {
        "label": label,
        "thinking": thinking,
        "elapsed": elapsed,
        "thinking_chars": len(thinking_text),
        "n_judgments": n_judgments,
        "target": target_judgment,
        "raw": content[:500],
    }


# ─── Run single case ──────────────────────────────────────────────────────────
def run_case(case):
    with open(case["trace"]) as f:
        t = json.load(f)
    r = t["call_records"][case["record"]]
    original_sys = next((m["content"] for m in r.get("messages", []) if m["role"] == "system"), "")
    original_user = next((m["content"] for m in r.get("messages", []) if m["role"] == "user"), "For each candidate, reason using abductive inference.")
    original_resp = r.get("response", "")

    statement, hypotheses, candidates_raw = extract_from_sys_msg(original_sys)

    inject = case.get("inject_memory")
    minimal_sys = build_minimal_sys(statement, hypotheses, candidates_raw, inject_memory=inject)

    # Original verdict
    orig_target = None
    try:
        j = json.loads(original_resp[original_resp.find('{'):] if '{' in original_resp else "{}")
        for jj in j.get("judgments", []):
            if case["target_kw"].lower() in str(jj).lower():
                orig_target = jj
                break
    except:
        pass

    print(f"\n{'='*70}")
    print(f"  {case['uid']} [{case['class']}]  record #{case['record']}")
    print(f"  {case['desc']}")
    print(f"{'='*70}")
    print(f"  new_stmt: '{statement[:80]}'")
    print(f"  target:   '{case['target_content'][:80]}'")
    print(f"  expected: {case['expected_type']}")
    if orig_target:
        print(f"  ORIGINAL: conf={orig_target.get('confidence')} type={orig_target.get('type')}")
    else:
        print(f"  ORIGINAL: target NOT in response (empty/skipped)")

    # EXP1: minimal, no thinking
    print(f"\n  → Running EXP1 (minimal, no thinking)...")
    exp1 = call_api(minimal_sys, original_user, thinking=False,
                    label="EXP1 minimal no-think", target_kw=case["target_kw"])

    # EXP2: original full prompt, thinking ON
    print(f"  → Running EXP2 (full rules, thinking ON)...")
    exp2 = call_api(original_sys, original_user, thinking=True,
                    label="EXP2 full thinking", target_kw=case["target_kw"])

    def fmt_verdict(res):
        t = res["target"]
        if t is None:
            return f"MISSED ({res['n_judgments']} judgments total)"
        return f"conf={t.get('confidence')} type={t.get('type')} [{res['n_judgments']} total, {res['elapsed']:.0f}s, think={res['thinking_chars']}chars]"

    print(f"\n  SUMMARY:")
    print(f"    ORIGINAL  → {fmt_verdict({'target': orig_target, 'n_judgments': '?', 'elapsed': 0, 'thinking_chars': 0})}")
    print(f"    EXP1 min  → {fmt_verdict(exp1)}")
    print(f"    EXP2 full → {fmt_verdict(exp2)}")

    if exp1["target"]:
        print(f"    EXP1 chain: {exp1['target'].get('inference_chain','')[:150]}")
    if exp2["target"]:
        print(f"    EXP2 chain: {exp2['target'].get('inference_chain','')[:150]}")

    return {
        "uid": case["uid"],
        "class": case["class"],
        "desc": case["desc"],
        "stmt": statement,
        "target_content": case["target_content"],
        "expected": case["expected_type"],
        "original": orig_target,
        "exp1": exp1,
        "exp2": exp2,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("ABDUCTIVE BATCH EXPERIMENT")
    print("EXP1: minimal prompt, no thinking  |  EXP2: original rules, thinking ON")
    print("=" * 70)

    results = []
    for i, case in enumerate(CASES):
        print(f"\n[{i+1}/{len(CASES)}] Starting {case['uid']}...")
        try:
            res = run_case(case)
            results.append(res)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()

    # Final comparison table
    print("\n\n" + "=" * 70)
    print("FINAL COMPARISON TABLE")
    print("=" * 70)
    print(f"{'UID':<12} {'Class':<8} {'Expected':<22} {'Original':<25} {'EXP1 min':<25} {'EXP2 think':<25}")
    print("-" * 120)

    def short_verdict(t, n=None):
        if t is None:
            return "MISSED"
        c = t.get("confidence", "?")
        tp = t.get("type", "?")[:3].upper()
        return f"{tp} {c}"

    for res in results:
        orig_v = short_verdict(res["original"])
        exp1_v = short_verdict(res["exp1"]["target"] if res["exp1"] else None)
        exp2_v = short_verdict(res["exp2"]["target"] if res["exp2"] else None)
        print(f"{res['uid']:<12} {res['class']:<8} {res['expected']:<22} {orig_v:<25} {exp1_v:<25} {exp2_v:<25}")

    # Save results
    out_path = os.path.join(os.path.dirname(__file__), "..", "analysis_output", "abductive_batch_exp_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    # Make serializable
    def make_serial(r):
        r2 = dict(r)
        for k in ["exp1", "exp2"]:
            if r2.get(k):
                r2[k] = dict(r2[k])
                r2[k].pop("raw", None)
        return r2
    with open(out_path, "w") as f:
        json.dump([make_serial(r) for r in results], f, indent=2, ensure_ascii=False)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    main()
