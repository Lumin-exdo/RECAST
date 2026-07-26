"""
EXP3: Qwen3.7-plus and Qwen3.7-max on the 5 key abductive failure cases.

For each case:
  ORIGINAL  — result from the actual trace (DeepSeek V4 Flash, old full-rules prompt)
  EXP1 min  — DeepSeek V4 Flash + current minimal prompt  (re-verified with max_tokens=8000)
  EXP3 plus — Qwen3.7-plus + minimal prompt, thinking=OFF
  EXP4 max  — Qwen3.7-max  + minimal prompt, thinking=OFF
  EXP5 max+ — Qwen3.7-max  + minimal prompt, thinking=ON

Cases tested (B-class = missed detection, D-class = over-application):
  1. 7ee76c41 [B]: "starting first full-time job" → m_00011 "in master's program"
  2. 2c711459 [B]: "device had swollen battery" → m_00014 "same smartwatch 3 years"
  3. dae22057 [B]: "switched into laid-back schedule" → m_00055 "carrying body tension"
  4. c9cc370e [D]: "planning trip to Japan" → m_00138 "walking boot" (should be no_conflict)
  5. c2cc2d39 [D]: "experiences headaches from screen" → m_00111 "screen avoidance order"
"""

import os, json, re, time
from openai import OpenAI

# ── env ───────────────────────────────────────────────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

DEEPSEEK_KEY  = os.environ["OPENAI_API_KEY"]
DEEPSEEK_URL  = os.environ.get("OPENAI_BASE_URL", "https://openrouter.ai/api/v1")
DEEPSEEK_MDL  = os.environ.get("TARGET_MODEL", "deepseek-v4-flash")
QWEN_KEY      = os.environ["QWEN_API_KEY"]
QWEN_URL      = os.environ["QWEN_BASE_URL"]
QWEN_PLUS_MDL = "qwen3.7-plus"
QWEN_MAX_MDL  = "qwen3.7-max"

ds_client   = OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_URL)
qwen_client = OpenAI(api_key=QWEN_KEY,     base_url=QWEN_URL)

# ── load minimal prompt from source ──────────────────────────────────────────
_tmpl_src = open(os.path.join(os.path.dirname(__file__), "..", "prompt_lib", "new_templates.py")).read()
_start = _tmpl_src.find('ABDUCTIVE_JUDGMENT_PROMPT = """') + len('ABDUCTIVE_JUDGMENT_PROMPT = """')
_end   = _tmpl_src.find('"""', _start)
MINIMAL_PROMPT = _tmpl_src[_start:_end]
assert "{statement}" in MINIMAL_PROMPT and "{candidates}" in MINIMAL_PROMPT, "prompt template missing placeholders"

# ── cases ─────────────────────────────────────────────────────────────────────
BASE = "/mnt/laq/RECAST/runs/63d3571"
CASES = [
    {
        "uid": "7ee76c41", "class": "B",
        "trace": f"{BASE}/improved_60_v2/0029/trace.json",
        "record": 285,
        "target_id": "m_00011",
        "target_kw": "master",
        "expected": "weakens_support or direct_invalidation",
        "desc": "'starting first full-time job' → should conflict with 'in master's program'",
    },
    {
        "uid": "2c711459", "class": "B",
        "trace": f"{BASE}/improved_60_fill/0312/trace.json",
        "record": 461,
        "target_id": "m_00014",
        "target_kw": "smartwatch",
        "expected": "weakens_support",
        "desc": "'device had swollen battery' → should weaken 'same smartwatch for 3 years'",
    },
    {
        "uid": "dae22057", "class": "B",
        "trace": f"{BASE}/improved_60_v2/0147/trace.json",
        "record": 402,
        "target_id": "m_00055",
        "target_kw": "tension",
        "expected": "direct_invalidation",
        "desc": "'switched into laid-back schedule' → should invalidate 'carrying body tension'",
    },
    {
        "uid": "c9cc370e", "class": "D",
        "trace": f"{BASE}/improved_60_fill/0382/trace.json",
        "record": 523,
        "target_id": "m_00138",
        "target_kw": "walking boot",
        "expected": "no_conflict",
        "desc": "'planning trip to Japan' → should NOT stale 'doctor put me in walking boot'",
    },
    {
        "uid": "c2cc2d39", "class": "D",
        "trace": f"{BASE}/improved_60_fill/0295/trace.json",
        "record": 374,
        "target_id": "m_00111",
        "target_kw": "screen avoidance",
        "expected": "no_conflict",
        "desc": "'experiences headaches from screen' → CONFIRMS screen avoidance order, not weakens",
    },
]


# ── helpers ───────────────────────────────────────────────────────────────────
def extract_parts(sys_msg):
    stmt_m = re.search(r"New statement from user: (.+?)(?:\n|$)", sys_msg)
    statement = stmt_m.group(1).strip() if stmt_m else ""

    hyp_m = re.search(
        r"(?:Supporting impact hypotheses|Context)[^\n]*\n(.*?)\n(?:Candidate memories|Output JSON)",
        sys_msg, re.DOTALL
    )
    hypotheses = hyp_m.group(1).strip() if hyp_m else ""

    cand_m = re.search(r"Candidate memories[^\n]*\n((?:\[m_\d+\][^\n]*\n?)+)", sys_msg)
    candidates = cand_m.group(1).strip() if cand_m else ""
    return statement, hypotheses, candidates


def build_sys(statement, hypotheses, candidates):
    return (MINIMAL_PROMPT
            .replace("{statement}", statement)
            .replace("{hypotheses}", hypotheses)
            .replace("{candidates}", candidates))


def parse_target(content, target_id, target_kw):
    """Return the judgment dict for the target memory, or None."""
    try:
        j_start = content.find('{')
        if j_start < 0:
            return None, 0
        parsed = json.loads(content[j_start:])
        judgments = parsed.get("judgments", [])
        n = len(judgments)
        for jj in judgments:
            if jj.get("target_item_id") == target_id:
                return jj, n
            # fallback: keyword match in content or target_content
            if target_kw.lower() in (jj.get("target_content", "") + jj.get("target_item_id", "")).lower():
                return jj, n
        return None, n
    except Exception:
        return None, 0


def call_model(client, model, sys_msg, thinking, max_tokens=8000):
    if "deepseek" in model:
        extra = {"thinking": {"type": "enabled" if thinking else "disabled"}}
    else:
        extra = {"enable_thinking": thinking}

    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user",   "content": "Run abductive judgment."},
        ],
        max_tokens=max_tokens,
        extra_body=extra,
    )
    elapsed = time.time() - t0
    content = resp.choices[0].message.content or ""
    thinking_chars = len(getattr(resp.choices[0].message, "reasoning_content", "") or "")
    return content, elapsed, thinking_chars


def fmt_verdict(jdg, n_total):
    if jdg is None:
        return f"MISSED  (n={n_total})"
    c = jdg.get("confidence", "?")
    t = jdg.get("type", "?")
    abbr = {"direct_invalidation": "DI", "weakens_support": "WS", "no_conflict": "NC"}.get(t, t[:3].upper())
    return f"{abbr} {c}  (n={n_total})"


# ── main ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 72)
    print("EXP3: Qwen3.7-plus / Qwen3.7-max  vs  DeepSeek V4 Flash")
    print("All experiments use the current minimal ABDUCTIVE_JUDGMENT_PROMPT")
    print("=" * 72)

    summary_rows = []

    for case in CASES:
        with open(case["trace"]) as f:
            t = json.load(f)
        r = t["call_records"][case["record"]]
        original_sys = next((m["content"] for m in r["messages"] if m["role"] == "system"), "")
        original_resp = r.get("response", "")

        stmt, hyp, cands = extract_parts(original_sys)
        n_cands = len([l for l in cands.split("\n") if l.strip()])
        min_sys  = build_sys(stmt, hyp, cands)

        # Original verdict (from trace)
        orig_jdg, _ = parse_target(original_resp, case["target_id"], case["target_kw"])

        print(f"\n{'─'*72}")
        print(f"  {case['uid']} [{case['class']}]  record #{case['record']}  n_candidates={n_cands}")
        print(f"  {case['desc']}")
        print(f"  expected: {case['expected']}")
        print(f"  ORIGINAL (trace): {fmt_verdict(orig_jdg, '?')}")
        if orig_jdg:
            print(f"    chain: {orig_jdg.get('inference_chain','')[:110]}")

        experiments = [
            ("EXP1 DS-Flash  min noT", ds_client,   DEEPSEEK_MDL,  False),
            ("EXP3 Qwen-Plus min noT", qwen_client,  QWEN_PLUS_MDL, False),
            ("EXP4 Qwen-Max  min noT", qwen_client,  QWEN_MAX_MDL,  False),
            ("EXP5 Qwen-Max  min T  ", qwen_client,  QWEN_MAX_MDL,  True),
        ]

        row = {"uid": case["uid"], "class": case["class"], "expected": case["expected"],
               "original": fmt_verdict(orig_jdg, "?")}

        for label, cl, model, thinking in experiments:
            print(f"\n  → {label} ...", end="", flush=True)
            try:
                content, elapsed, think_chars = call_model(cl, model, min_sys, thinking)
                jdg, n_total = parse_target(content, case["target_id"], case["target_kw"])
                verdict = fmt_verdict(jdg, n_total)
                chain = jdg.get("inference_chain", "")[:110] if jdg else ""
                print(f"  {elapsed:.0f}s  think={think_chars}ch")
                print(f"    verdict: {verdict}")
                if chain:
                    print(f"    chain:   {chain}")
                row[label.strip()] = verdict
            except Exception as e:
                print(f"\n    ERROR: {e}")
                row[label.strip()] = f"ERROR: {e}"

        summary_rows.append(row)

    # ── summary table ─────────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("FINAL SUMMARY TABLE")
    print("=" * 72)
    cols = ["uid", "class", "expected", "original",
            "EXP1 DS-Flash  min noT", "EXP3 Qwen-Plus min noT",
            "EXP4 Qwen-Max  min noT", "EXP5 Qwen-Max  min T  "]
    header = f"{'UID':<12} {'Cls':<3} {'Expected':<22} {'Original':<18} {'EXP1 DS-Flash':<18} {'EXP3 Q-Plus':<18} {'EXP4 Q-Max':<18} {'EXP5 Q-Max+T':<18}"
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['uid']:<12} {row['class']:<3} {row['expected']:<22} "
            f"{row.get('original','?'):<18} "
            f"{row.get('EXP1 DS-Flash  min noT','?'):<18} "
            f"{row.get('EXP3 Qwen-Plus min noT','?'):<18} "
            f"{row.get('EXP4 Qwen-Max  min noT','?'):<18} "
            f"{row.get('EXP5 Qwen-Max  min T  ','?'):<18}"
        )

    # save
    out = os.path.join(os.path.dirname(__file__), "..", "analysis_output", "exp3_qwen_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(summary_rows, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out}")


if __name__ == "__main__":
    main()
