#!/usr/bin/env python3
"""
Supplementary experiments to deepen understanding of Exp1/Exp2/Exp3 failures.
"""

import json
import time
from openai import OpenAI

client = OpenAI(
    api_key="${DEEPSEEK_API_KEY}",
    base_url="https://openrouter.ai/api/v1"
)
MODEL = "deepseek-v4-flash"
TRACE_BASE = "/mnt/laq/RECAST/runs"
OUTPUT_FILE = "/mnt/laq/RECAST/analysis_output/module_exp_supplement.json"


def call_llm(system_prompt, user_message, temperature=0.0, label=""):
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=temperature,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  ERROR {label}: {e}")
        return f"ERROR: {e}"


# ============================================================
# SUP-A: Statement Extraction at temperature=1.0 (verify non-determinism)
# ============================================================

def run_sup_a_temperature():
    """Run statement_extraction at temp=1.0 5 times to check variance."""
    print("\n=== SUP-A: Statement Extraction Temperature Variance ===")

    with open(f"{TRACE_BASE}/0b20802/targeted_15_e2ev3/0006/trace.json") as f:
        t = json.load(f)
    c = t['call_records'][24]
    msgs = c['messages']
    system_prompt = msgs[0]['content']
    user_msg = msgs[1]['content']

    results = []
    print("  Testing abs=6/sess34 at temperature=1.0, 5 runs...")
    for i in range(5):
        resp = call_llm(system_prompt, user_msg, temperature=1.0, label=f"temp1_run{i}")
        try:
            parsed = json.loads(resp)
            stmts = parsed.get("statements", [])
            found_wfh = any("apartment" in s["text"].lower() or "working from" in s["text"].lower() for s in stmts)
        except:
            found_wfh = False
            stmts = []
        results.append({
            'run': i,
            'found_wfh': found_wfh,
            'num_statements': len(stmts),
            'response_preview': resp[:200]
        })
        print(f"    Run {i}: found_wfh={found_wfh}, num_statements={len(stmts)}")
        time.sleep(0.5)

    print(f"  WFH found in {sum(r['found_wfh'] for r in results)}/5 runs at temperature=1.0")
    return results


# ============================================================
# SUP-B: Impact Hypothesis with student loan memories injected
# ============================================================

def run_sup_b_impact_with_loans():
    """Run impact_hypothesis for abs=274 with student loan memories added to anchors."""
    print("\n=== SUP-B: Impact Hypothesis with Loan Memories in Anchors ===")

    with open(f"{TRACE_BASE}/0b20802/targeted_15_e2ev3/0274/trace.json") as f:
        t = json.load(f)

    # Find the discharge order impact hypothesis call
    for c in t['call_records']:
        if c.get('phase') == 'impact_hypothesis':
            msgs = c.get('messages', [])
            if 'discharge' in str(msgs).lower():
                system_orig = msgs[0]['content']
                user_msg = msgs[1]['content'] if len(msgs) > 1 else ""
                break
    else:
        print("  ERROR: Could not find impact_hypothesis call")
        return []

    # Inject student loan memories into the preference_anchors section
    # Find the preference_anchors section and append loan data
    loan_injection = """- has student loans with a balance of approximately $38,000 at 4.5-6.2% interest rate
- minimum monthly student loan payment is $410/month
- has a significant portion of student loans left to repay"""

    # Find where preference_anchors ends and inject
    # The format is: "Stored persistent traits and preferences (direct from memory store — complete list):\n..."
    # followed by the actual anchors, then STEP 1
    anchor_marker = "Work through two internal steps, then output your hypotheses."
    if anchor_marker in system_orig:
        injection_point = system_orig.find(anchor_marker)
        system_modified = (
            system_orig[:injection_point] +
            loan_injection + "\n\n" +
            system_orig[injection_point:]
        )
    else:
        system_modified = system_orig + "\n\n" + loan_injection

    print("  Testing with loan memories injected...")
    resp_orig = call_llm(system_orig, user_msg, temperature=0.0, label="orig")
    time.sleep(1)
    resp_mod = call_llm(system_modified, user_msg, temperature=0.0, label="with_loans")
    time.sleep(1)

    orig_has_loan = "loan" in resp_orig.lower()
    mod_has_loan = "loan" in resp_mod.lower()

    print(f"  Original has 'loan': {orig_has_loan}")
    print(f"  Modified (with loans injected) has 'loan': {mod_has_loan}")

    try:
        parsed_orig = json.loads(resp_orig)
        print("  Original hypotheses:")
        for h in parsed_orig.get('hypothetical_impacts', []):
            if any(kw in h.lower() for kw in ['loan', 'debt', 'student', 'financial', 'payment']):
                print(f"    * {h}")
    except:
        pass

    try:
        parsed_mod = json.loads(resp_mod)
        print("  Modified hypotheses (loan-relevant):")
        for h in parsed_mod.get('hypothetical_impacts', []):
            if any(kw in h.lower() for kw in ['loan', 'debt', 'student', 'financial', 'payment', 'discharge']):
                print(f"    * {h}")
    except:
        pass

    return [{
        'label': 'abs=274 impact_hypothesis with loan memories injected',
        'orig_has_loan': orig_has_loan,
        'mod_has_loan': mod_has_loan,
        'orig_response': resp_orig,
        'mod_response': resp_mod,
        'verdict': 'FIXED' if mod_has_loan and not orig_has_loan else
                  ('BOTH_FOUND' if orig_has_loan and mod_has_loan else
                  'BOTH_MISS'),
    }]


# ============================================================
# SUP-C: E2E with explicit "do not infer new status" rule
# ============================================================

def run_sup_c_e2e_no_hallucination():
    """Run E2E for abs=239 dim2 with explicit anti-hallucination rule for [STALE] status."""
    print("\n=== SUP-C: E2E with Anti-Hallucination for [STALE] Status ===")

    with open(f"{TRACE_BASE}/b78818f/targeted_15_d_fix/0239/trace.json") as f:
        t = json.load(f)

    c = t['call_records'][715]
    msgs = c['messages']
    system_orig = msgs[0]['content']
    user_msg = msgs[1]['content'] if len(msgs) > 1 else ""

    # Modify: add explicit rule about not inferring new status from [STALE]
    anti_hallucination_rule = """
IMPORTANT — DO NOT INFER REPLACEMENT STATUS: When a memory is marked [STALE], this means
the old state is NO LONGER confirmed, but it does NOT mean you know what replaced it.
Do NOT assume a replacement status unless explicitly stated in [ACTIVE] memories.
Examples of WRONG reasoning:
  WRONG: "[STALE] permanent resident of Canada" → "must now be a US permanent resident"
  WRONG: "[STALE] enrolled as undergraduate" → "must have graduated"
  WRONG: "[STALE] worked at company X" → "must now work somewhere else"
Instead, acknowledge the uncertainty:
  RIGHT: "Your previous permanent resident status is marked as uncertain/changed. I cannot
         provide a reliable plan for maintaining permanent resident status without knowing
         your current immigration status. Could you confirm your current status?"

"""

    # Find step 1 in the prompt and insert rule before it
    if "Work through the following before writing your answer:" in system_orig:
        insertion_point = system_orig.find("Work through the following before writing your answer:")
        system_modified = (
            system_orig[:insertion_point] +
            anti_hallucination_rule +
            system_orig[insertion_point:]
        )
    else:
        system_modified = anti_hallucination_rule + system_orig

    print("  Testing abs=239/dim2 with anti-hallucination rule...")
    resp_orig = call_llm(system_orig, user_msg, temperature=0.0, label="orig")
    time.sleep(1)
    resp_mod = call_llm(system_modified, user_msg, temperature=0.0, label="anti_halluc")
    time.sleep(1)

    # Check if original gives a confident PR plan (bad)
    orig_gives_plan = 'maintenance' in resp_orig.lower() or 'maintain' in resp_orig.lower()
    # Check if modified flags uncertainty (good)
    mod_flags_uncertainty = any(m in resp_mod.lower() for m in [
        'uncertain', 'cannot', "can't", 'not sure', 'unclear', 'changed', 'confirm'
    ])

    print(f"  Original gives PR plan (bad): {orig_gives_plan}")
    print(f"  Modified flags uncertainty (good): {mod_flags_uncertainty}")

    try:
        cr = json.loads(resp_orig)
        print(f"  Original assumption: {cr.get('assumption','')[:150]}")
        print(f"  Original answer (first 300): {cr.get('answer','')[:300]}")
    except:
        print(f"  Original (first 400): {resp_orig[:400]}")

    print()

    try:
        mr = json.loads(resp_mod)
        print(f"  Modified assumption: {mr.get('assumption','')[:150]}")
        print(f"  Modified answer (first 400): {mr.get('answer','')[:400]}")
    except:
        print(f"  Modified (first 500): {resp_mod[:500]}")

    return [{
        'label': 'abs=239/dim2 E2E with anti-hallucination rule',
        'orig_gives_plan': orig_gives_plan,
        'mod_flags_uncertainty': mod_flags_uncertainty,
        'orig_response': resp_orig,
        'mod_response': resp_mod,
        'verdict': 'FIXED' if mod_flags_uncertainty and orig_gives_plan else
                  ('BOTH_GOOD' if mod_flags_uncertainty and not orig_gives_plan else
                  ('BOTH_BAD' if not mod_flags_uncertainty and orig_gives_plan else
                  'REGRESSION')),
    }]


# ============================================================
# MAIN
# ============================================================

def main():
    print("Running supplementary experiments...")

    results = {}
    results['sup_a_temperature'] = run_sup_a_temperature()
    results['sup_b_impact_with_loans'] = run_sup_b_impact_with_loans()
    results['sup_c_e2e_no_hallucination'] = run_sup_c_e2e_no_hallucination()

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to {OUTPUT_FILE}")

    print("\n=== SUPPLEMENTARY SUMMARY ===")
    print(f"SUP-A (temperature): {sum(r['found_wfh'] for r in results['sup_a_temperature'])}/5 runs found WFH at temp=1.0")
    for r in results['sup_b_impact_with_loans']:
        print(f"SUP-B (loan injection): {r['verdict']}")
    for r in results['sup_c_e2e_no_hallucination']:
        print(f"SUP-C (anti-hallucination): {r['verdict']}")


if __name__ == "__main__":
    main()
