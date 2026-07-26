"""
Experiment B: Does the new anti-hallucination clause in E2E_ANSWER_PROMPT (step 3)
cause dim3 action-compliance regressions?

Tests 4 samples that passed dim3 in e2ev3 (baseline, OLD prompt) by re-running
the exact same system prompt with:
  - OLD step 3 (as found in e2ev3 traces, commit 0b20802)
  - NEW step 3 (current prompt, commit feaa7d9)

Model: deepseek-v4-flash, temperature=0
"""

import json
from openai import OpenAI

client = OpenAI(
    api_key="${DEEPSEEK_API_KEY}",
    base_url="https://openrouter.ai/api/v1"
)
MODEL = "deepseek-v4-flash"

TRACE_BASE = "/mnt/laq/RECAST/runs/0b20802/targeted_15_e2ev3"

SAMPLES = ["0223", "0239", "0240", "0382"]

# The OLD step 3 text (from e2ev3 traces, commit 0b20802)
OLD_STEP3 = "3. If the assumption is contradicted or cast in doubt, open your answer by naming the discrepancy clearly. Do not reconcile conflicting information by assuming one side is right — surface the conflict and let the user know what has changed."

# The NEW step 3 text (current prompt, commit feaa7d9)
NEW_STEP3 = "3. If the assumption is contradicted or cast in doubt, open your answer by naming the discrepancy clearly. Do not reconcile conflicting information by assuming one side is right — surface the conflict and let the user know what has changed. Do not infer what replaced a stale fact from other context clues; if the current state is not confirmed in [ACTIVE] memories, acknowledge the uncertainty and invite the user to share their current situation before giving detailed advice."


def call_llm(system_prompt, label=""):
    """Call LLM and return response text."""
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Respond."}
            ],
            temperature=0.0,
        )
        return resp.choices[0].message.content
    except Exception as e:
        print(f"  ERROR calling LLM for {label}: {e}")
        return f"ERROR: {e}"


def get_dim3_system_prompt(sample_id):
    """Extract the dim3 answer_generation_v2 system prompt from e2ev3 trace."""
    trace_path = f"{TRACE_BASE}/{sample_id}/trace.json"
    with open(trace_path) as f:
        trace = json.load(f)
    calls = [r for r in trace.get('call_records', [])
             if r.get('phase') == 'answer_generation_v2' and r.get('query_label') == 'dim3_query']
    if not calls:
        raise ValueError(f"No dim3 answer_generation_v2 call found in {sample_id}")
    # take last dim3 call
    call = calls[-1]
    msgs = call.get('messages', [])
    sys_msg = next((m for m in msgs if m.get('role') == 'system'), None)
    if not sys_msg:
        raise ValueError(f"No system message in dim3 call for {sample_id}")
    return sys_msg['content'], call.get('response', '')


def main():
    results = []
    for sample_id in SAMPLES:
        print(f"\n{'='*60}")
        print(f"Processing sample {sample_id}...")

        sys_prompt_old, baseline_response = get_dim3_system_prompt(sample_id)

        # Verify the OLD step 3 is in the system prompt
        if OLD_STEP3 not in sys_prompt_old:
            print(f"  WARNING: OLD step 3 not found verbatim in {sample_id} system prompt")
            print(f"  Looking for: {OLD_STEP3[:80]}...")
            # Try to find step 3 text
            idx = sys_prompt_old.find("3. If the assumption")
            if idx >= 0:
                print(f"  Found at idx {idx}: {sys_prompt_old[idx:idx+200]}")
        else:
            print(f"  OLD step 3 confirmed in system prompt")

        # Create NEW system prompt by replacing OLD step 3 with NEW step 3
        sys_prompt_new = sys_prompt_old.replace(OLD_STEP3, NEW_STEP3)

        if sys_prompt_new == sys_prompt_old:
            print(f"  WARNING: No change in system prompt for {sample_id} — OLD step 3 not found")

        # Run both
        print(f"  Running OLD prompt...")
        old_response = call_llm(sys_prompt_old, label=f"{sample_id}_old")
        print(f"  Running NEW prompt...")
        new_response = call_llm(sys_prompt_new, label=f"{sample_id}_new")

        # Parse answers
        def extract_answer(resp_text):
            try:
                data = json.loads(resp_text)
                return data.get('answer', resp_text)
            except Exception:
                return resp_text

        old_answer = extract_answer(old_response)
        new_answer = extract_answer(new_response)
        baseline_answer = extract_answer(baseline_response)

        result = {
            "sample": sample_id,
            "baseline_answer_preview": baseline_answer[:400],
            "old_answer_preview": old_answer[:400],
            "new_answer_preview": new_answer[:400],
            "old_full": old_response,
            "new_full": new_response,
            "baseline_full": baseline_response,
        }
        results.append(result)

        print(f"\n  BASELINE (from trace):")
        print(f"    {baseline_answer[:300]}")
        print(f"\n  OLD prompt response:")
        print(f"    {old_answer[:300]}")
        print(f"\n  NEW prompt response:")
        print(f"    {new_answer[:300]}")

    # Save results
    output_path = "/mnt/laq/RECAST/analysis_output/exp_b_results.json"
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n\nResults saved to {output_path}")
    return results


if __name__ == "__main__":
    main()
