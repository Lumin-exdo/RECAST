"""
Complete abductive judgment experiment: ALL B-class and D-class failure cases.

Model configs tested:
  EXP1 DS-Flash  min noT — DeepSeek V4 Flash, minimal prompt, no thinking
  EXP3 Qwen-Plus min noT — Qwen3.7-plus, minimal prompt, thinking=OFF
  EXP4 Qwen-Max  min noT — Qwen3.7-max,  minimal prompt, thinking=OFF
  EXP5 Qwen-Max  min T   — Qwen3.7-max,  minimal prompt, thinking=ON

Cases:
  B-class (abductive missed detection):
    7ee76c41  record #285  normal
    2c711459  record #461  normal
    dae22057  record #402  normal
    6ff5a576  record #483  B_inject: inject [m_00053] home-base memory as candidate
    eacb64ff  record #547  B_inject: inject [m_00073] confidentiality memory as candidate

  D-class (abductive over-application):
    eee1a643  record #689  normal  — 'pursuing master's degree' wrongly weakens 'new company'
    c9cc370e  record #523  normal
    c2cc2d39  record #374  normal
    48707e03  record #641  D_inject: replace stmt → 'user has been cleared to return'

  Skipped:
    34d402c0  — hypothetical_filter dropped M_old ('unusually cold' SKIP: external world fact),
               so no abductive record exists and testing is impossible.
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
assert "{statement}" in MINIMAL_PROMPT and "{candidates}" in MINIMAL_PROMPT

# ── case definitions ──────────────────────────────────────────────────────────
BASE_V2   = "/mnt/laq/RECAST/runs/63d3571/improved_60_v2"
BASE_FILL = "/mnt/laq/RECAST/runs/63d3571/improved_60_fill"

CASES = [
    # ── B-class (missed detection) ────────────────────────────────────────────
    {
        "uid": "7ee76c41", "class": "B",
        "trace": f"{BASE_V2}/0029/trace.json",
        "record": 285,
        "target_id": "m_00011", "target_kw": "master",
        "expected": "weakens_support or direct_invalidation",
        "desc": "'starting first full-time job' → should conflict with 'in master's program'",
    },
    {
        "uid": "2c711459", "class": "B",
        "trace": f"{BASE_FILL}/0312/trace.json",
        "record": 461,
        "target_id": "m_00014", "target_kw": "smartwatch",
        "expected": "weakens_support",
        "desc": "'device had swollen battery' → should weaken 'same smartwatch for 3 years'",
    },
    {
        "uid": "dae22057", "class": "B",
        "trace": f"{BASE_V2}/0147/trace.json",
        "record": 402,
        "target_id": "m_00055", "target_kw": "tension",
        "expected": "direct_invalidation",
        "desc": "'switched into laid-back schedule' → should invalidate 'carrying body tension'",
    },
    {
        "uid": "6ff5a576", "class": "B_inject",
        "trace": f"{BASE_V2}/0007/trace.json",
        "record": 483,
        "target_id": "m_00053", "target_kw": "main base",
        "inject_candidate": "[m_00053] treats home as their main base and works remotely",
        "expected": "weakens_support or direct_invalidation",
        "desc": "'settled into new desk at office downtown' → injecting home-base memory; should conflict",
    },
    {
        "uid": "eacb64ff", "class": "B_inject",
        "trace": f"{BASE_V2}/0059/trace.json",
        "record": 547,
        "target_id": "m_00073", "target_kw": "confidential",
        "inject_candidate": "[m_00073] in a work context where people confide in them because they are known for keeping things confidential",
        "expected": "weakens_support",
        "desc": "'people kept distance at potluck' → injecting confidentiality memory; should weaken",
    },
    # ── D-class (over-application) ────────────────────────────────────────────
    {
        "uid": "eee1a643", "class": "D",
        "trace": f"{BASE_V2}/0052/trace.json",
        "record": 689,
        "target_id": "m_00141", "target_kw": "new company",
        "expected": "no_conflict",
        "desc": "'pursuing master's degree in Data Science' → should NOT weaken 'started at new company'",
    },
    {
        "uid": "c9cc370e", "class": "D",
        "trace": f"{BASE_FILL}/0382/trace.json",
        "record": 523,
        "target_id": "m_00138", "target_kw": "walking boot",
        "expected": "no_conflict",
        "desc": "'planning trip to Japan' → should NOT stale 'doctor put me in walking boot'",
    },
    {
        "uid": "c2cc2d39", "class": "D",
        "trace": f"{BASE_FILL}/0295/trace.json",
        "record": 374,
        "target_id": "m_00111", "target_kw": "screen avoidance",
        "expected": "no_conflict",
        "desc": "'experiences headaches from screen' → CONFIRMS screen avoidance order, should be NC",
    },
    {
        "uid": "48707e03", "class": "D_inject",
        "trace": f"{BASE_FILL}/0358/trace.json",
        "record": 641,
        "target_id": "m_00171", "target_kw": "hearing clearance",
        "inject_statement": "user has been cleared to return to the conference interpreting roster",
        "expected": "direct_invalidation or weakens_support",
        "desc": "injecting 'user has been cleared' → should stale 'user needs hearing clearance'",
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
            # fallback: keyword match in target_content (avoid matching inference_chain noise)
            tc = jj.get("target_content", "")
            if target_kw.lower() in tc.lower():
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
    print("ALL B+D ABDUCTIVE CASES — 4 model configs")
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
        n_cands_orig = len([l for l in cands.split("\n") if l.strip()])

        # Apply injections
        inject_note = ""
        if "inject_candidate" in case:
            # Prepend the missing memory to candidates
            inject_line = case["inject_candidate"]
            if inject_line not in cands:
                cands = inject_line + "\n" + cands
            inject_note = f" [+injected {case['target_id']}]"

        if "inject_statement" in case:
            # Replace new statement entirely
            stmt = case["inject_statement"]
            inject_note = " [stmt replaced]"

        n_cands = len([l for l in cands.split("\n") if l.strip()])
        min_sys = build_sys(stmt, hyp, cands)

        # Original verdict (from trace — before any injection)
        orig_jdg, _ = parse_target(original_resp, case["target_id"], case["target_kw"])

        print(f"\n{'─'*72}")
        print(f"  {case['uid']} [{case['class']}]  record #{case['record']}  n_cands={n_cands}{inject_note}")
        print(f"  {case['desc']}")
        print(f"  expected: {case['expected']}")
        orig_verdict = fmt_verdict(orig_jdg, "?")
        print(f"  ORIGINAL (trace): {orig_verdict}")
        if orig_jdg:
            print(f"    chain: {orig_jdg.get('inference_chain','')[:110]}")

        experiments = [
            ("EXP1 DS-Flash  min noT", ds_client,   DEEPSEEK_MDL,  False),
            ("EXP3 Qwen-Plus min noT", qwen_client,  QWEN_PLUS_MDL, False),
            ("EXP4 Qwen-Max  min noT", qwen_client,  QWEN_MAX_MDL,  False),
            ("EXP5 Qwen-Max  min T  ", qwen_client,  QWEN_MAX_MDL,  True),
        ]

        row = {
            "uid": case["uid"], "class": case["class"],
            "expected": case["expected"], "original": orig_verdict,
        }

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
    header = (f"{'UID':<12} {'Cls':<8} {'Expected':<32} {'Original':<18} "
              f"{'DS-Flash':<18} {'Q-Plus':<18} {'Q-Max':<18} {'Q-Max+T':<18}")
    print(header)
    print("-" * len(header))
    for row in summary_rows:
        print(
            f"{row['uid']:<12} {row['class']:<8} {row['expected']:<32} "
            f"{row.get('original','?'):<18} "
            f"{row.get('EXP1 DS-Flash  min noT','?'):<18} "
            f"{row.get('EXP3 Qwen-Plus min noT','?'):<18} "
            f"{row.get('EXP4 Qwen-Max  min noT','?'):<18} "
            f"{row.get('EXP5 Qwen-Max  min T  ','?'):<18}"
        )

    out = os.path.join(os.path.dirname(__file__), "..", "analysis_output", "exp_all_results.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump(summary_rows, f, indent=2, ensure_ascii=False)
    print(f"\nSaved to: {out}")


if __name__ == "__main__":
    main()
