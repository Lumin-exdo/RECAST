#!/usr/bin/env python3
"""
Expanded temperature sweep for pool_synthesis:
- All 14 genuine-downgrade regression cases (idx=44,80,88,93,99,123,140,143,149,154,161,167,174,191)
- Fine-grained temperatures: [0.3, 0.4, 0.5, 0.6, 0.7]
- 3 trials per temperature per case
- Reports correctness: whether stable mean >= 0.75 (stale threshold)

Run from /mnt/laq:
  python -m RECAST.scripts.pool_synthesis_temperature_sweep_v2
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from statistics import mean, pstdev

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO))

from RECAST.run_new_mem import load_env_file, get_env, DEFAULT_ENV_FILE  # noqa: E402
from RECAST.llm_layer.client import LLMClient  # noqa: E402

TEMPERATURES = [0.3, 0.4, 0.5, 0.6, 0.7]
TRIALS_PER_TEMP = 3
STALE_THRESHOLD = 0.75

CASES_META = [
    (44,  'm_00032'), (80,  'm_00005'), (88,  'm_00064'),
    (93,  'm_00039'), (99,  'm_00022'), (123, 'm_00014'),
    (140, 'm_00056'), (143, 'm_00015'), (149, 'm_00050'),
    (154, 'm_00031'), (161, 'm_00031'), (167, 'm_00010'),
    (174, 'm_00051'), (191, 'm_00047'),
]

RUNS_ROOT = _REPO / "RECAST" / "runs"


def extract_prompts() -> dict[str, dict]:
    """Extract final pool_synthesis prompt for each case from baseline trace."""
    prompts = {}
    for idx, item_id in CASES_META:
        trace_path = RUNS_ROOT / "d215489" / "e2ev3_400" / f"{idx:04d}" / "trace.json"
        t = json.loads(trace_path.read_text(encoding="utf-8"))
        pool_calls = [
            c for c in t["call_records"]
            if c.get("phase") == "pool_synthesis"
            and f"ID: {item_id}" in c["messages"][0]["content"]
        ]
        if not pool_calls:
            print(f"WARNING: idx={idx} item={item_id}: no pool_synthesis calls found, skipping")
            continue
        last = pool_calls[-1]
        resp = json.loads(last["response"])
        case_id = f"{idx}_{item_id}"
        prompts[case_id] = {
            "prompt": last["messages"][0]["content"],
            "baseline_conf": resp.get("synthesized_confidence"),
            "baseline_stale": resp.get("should_mark_stale"),
        }
    return prompts


def run_sweep(prompts: dict, model: str, api_key: str, base_url: str) -> dict:
    results = {}
    total = len(prompts) * len(TEMPERATURES) * TRIALS_PER_TEMP
    done = 0
    for case_id, meta in prompts.items():
        print(f"\n=== {case_id} (baseline conf={meta['baseline_conf']}) ===")
        results[case_id] = {
            "baseline_conf": meta["baseline_conf"],
            "baseline_stale": meta["baseline_stale"],
            "temps": {},
        }
        for temp in TEMPERATURES:
            confs = []
            for trial in range(TRIALS_PER_TEMP):
                llm = LLMClient(
                    model=model,
                    api_key=api_key,
                    base_url=base_url,
                    default_extra_request_kwargs={
                        "extra_body": {"thinking": {"type": "disabled"}},
                        "temperature": temp,
                    },
                )
                raw = llm.call_text(
                    messages=[
                        {"role": "system", "content": meta["prompt"]},
                        {"role": "user", "content": "Synthesize."},
                    ],
                    extra_meta={"phase": "temp_sweep_v2"},
                )
                try:
                    parsed = json.loads(raw)
                    conf = float(parsed.get("synthesized_confidence", -1))
                except Exception:
                    conf = -1
                confs.append(conf)
                done += 1
                print(f"  temp={temp:.1f} trial={trial}: conf={conf}  [{done}/{total}]")

            valid = [c for c in confs if c >= 0]
            mn = mean(valid) if valid else None
            sd = pstdev(valid) if len(valid) > 1 else 0.0
            results[case_id]["temps"][temp] = {
                "raw": confs,
                "mean": mn,
                "stdev": sd,
                "range": (min(valid), max(valid)) if valid else None,
                "stable_correct": (mn >= STALE_THRESHOLD) if mn is not None else None,
            }
    return results


def print_summary(results: dict):
    print("\n\n" + "=" * 70)
    print("SUMMARY: mean confidence at each temperature (baseline=0.75 threshold)")
    print("=" * 70)
    header = f"{'case':<20} {'base':>6}" + "".join(f"  t={t:.1f}" for t in TEMPERATURES)
    print(header)
    print("-" * len(header))
    for case_id, r in results.items():
        base = r["baseline_conf"]
        row = f"{case_id:<20} {base:>6.2f}"
        for temp in TEMPERATURES:
            s = r["temps"].get(temp, {})
            mn = s.get("mean")
            row += f"  {mn:.2f}" if mn is not None else "  ----"
        print(row)

    print("\n\nCORRECTNESS (mean >= 0.75 → STALE, matching baseline):")
    print(f"{'case':<20} {'base':>6}" + "".join(f"  t={t:.1f}" for t in TEMPERATURES))
    print("-" * len(header))
    for case_id, r in results.items():
        base = r["baseline_conf"]
        row = f"{case_id:<20} {base:>6.2f}"
        for temp in TEMPERATURES:
            s = r["temps"].get(temp, {})
            ok = s.get("stable_correct")
            row += f"  {'  OK' if ok else 'FAIL'}" if ok is not None else "  ----"
        print(row)

    print("\n\nSTABILITY (stdev at each temperature):")
    print(f"{'case':<20}" + "".join(f"  t={t:.1f}" for t in TEMPERATURES))
    print("-" * 60)
    for case_id, r in results.items():
        row = f"{case_id:<20}"
        for temp in TEMPERATURES:
            s = r["temps"].get(temp, {})
            sd = s.get("stdev")
            row += f"  {sd:.3f}" if sd is not None else "  ----"
        print(row)


def main():
    load_env_file(DEFAULT_ENV_FILE)
    model = get_env("TARGET_MODEL")
    api_key = get_env("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    base_url = get_env("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"

    print("Extracting pool_synthesis prompts from baseline traces...")
    prompts = extract_prompts()
    print(f"Got {len(prompts)} cases: {list(prompts.keys())}")

    results = run_sweep(prompts, model, api_key, base_url)

    out_path = _REPO / "RECAST" / "analysis_output" / "pool_synthesis_temperature_sweep_v2.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print_summary(results)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
