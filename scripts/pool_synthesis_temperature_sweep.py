#!/usr/bin/env python3
"""
Lightweight experiment: take the EXACT same pool_synthesis prompt from 4
known-divergent regression cases, re-send it at several temperatures with
multiple trials each, to see how much temperature actually controls the
output variance (rather than jumping straight to temperature=0).

Run from /mnt/laq:
  python -m RECAST.scripts.pool_synthesis_temperature_sweep
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

TEMPERATURES = [0.0, 0.3, 0.7, 1.0]
TRIALS_PER_TEMP = 3


def main():
    load_env_file(DEFAULT_ENV_FILE)
    model = get_env("TARGET_MODEL")
    api_key = get_env("OPENAI_API_KEY", "DEEPSEEK_API_KEY")
    base_url = get_env("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1"

    prompts = json.load(open("/tmp/pool_synthesis_test_prompts.json"))

    results = {}
    for case_id, prompt in prompts.items():
        print(f"\n=== {case_id} ===")
        results[case_id] = {}
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
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": "Synthesize."},
                    ],
                    extra_meta={"phase": "temp_sweep"},
                )
                try:
                    parsed = json.loads(raw)
                    conf = float(parsed.get("synthesized_confidence", -1))
                except Exception:
                    conf = -1
                confs.append(conf)
                print(f"  temp={temp} trial={trial}: conf={conf}")
            valid = [c for c in confs if c >= 0]
            results[case_id][temp] = {
                "raw": confs,
                "mean": mean(valid) if valid else None,
                "stdev": pstdev(valid) if len(valid) > 1 else 0.0,
                "range": (min(valid), max(valid)) if valid else None,
            }

    out_path = _REPO / "RECAST" / "analysis_output" / "pool_synthesis_temperature_sweep.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n\n=== SUMMARY ===")
    for case_id, by_temp in results.items():
        print(f"\n{case_id}:")
        for temp, stats in by_temp.items():
            print(f"  temp={temp}: mean={stats['mean']}, stdev={stats['stdev']:.3f}, range={stats['range']}")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
