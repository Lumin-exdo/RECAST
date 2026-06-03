"""
Quick prompt unit tests for STATEMENT_EXTRACTOR and IMPACT_HYPOTHESIS.
Run: python -m AMBER.test_prompts
"""
from __future__ import annotations
import os, sys, json
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from AMBER.prompt_lib.new_templates import (
    STATEMENT_EXTRACTOR_PROMPT,
    IMPACT_HYPOTHESIS_PROMPT,
)
from AMBER.llm_layer.client import LLMClient

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = BASE_DIR.parent / "STALE" / "STALE" / ".env"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'\"")
        if k:
            os.environ.setdefault(k, v)


def make_llm() -> LLMClient:
    load_env(DEFAULT_ENV_FILE)
    model = os.environ.get("TARGET_MODEL", "")
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    if not model or not key:
        raise ValueError("TARGET_MODEL / API key not set")
    return LLMClient(model=model, api_key=key, base_url=base_url)


def extract(llm: LLMClient, session_text: str) -> dict:
    return llm.call_json(
        STATEMENT_EXTRACTOR_PROMPT,
        f"Session user turns:\n{session_text}",
        phase="statement_extraction",
    )


def impact(llm: LLMClient, statement: str, profile: str) -> dict:
    prompt = (
        IMPACT_HYPOTHESIS_PROMPT
        .replace("{statement}", statement)
        .replace("{global_impression}", profile)
    )
    return llm.call_json(prompt, "Generate impact hypotheses.", phase="impact_hypothesis")


def run_tests(llm: LLMClient) -> None:
    results = []

    # ── EXTRACTOR tests ─────────────────────────────────────────────────────

    # E1: lasting preference (baseline — should still pass)
    r = extract(llm, "[Turn 0] Lately I feel most recharged when I'm out with people rather than alone at home")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "lasting_preference" and s.get("is_definite") for s in stmts)
    results.append(("E1 lasting_preference (recharged when out)", ok, stmts))

    # E2: religious conversion recent_change (baseline — should still pass)
    r = extract(llm, "[Turn 2] Last month I completed my conversion and was formally received into the Catholic Church")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "recent_change" and s.get("is_definite") for s in stmts)
    results.append(("E2 recent_change (religious conversion)", ok, stmts))

    # E3: possession-altering — SYNTHETIC (not from test set)
    # Principle: user sells their only car → should extract as recent_change
    r = extract(llm, "[Turn 1] I finally sold my only car yesterday. Feels weird not having wheels anymore but I'm going full public transit from now on.")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "recent_change" and s.get("is_definite") for s in stmts)
    results.append(("E3 possession-altering SYNTHETIC (sold only car)", ok, stmts))

    # E4: possession-altering — original failing case (uid 7a7621e2)
    r = extract(llm, "[Turn 0] I dropped off my last winter coat at the donation center this morning. Decided I really don't need it where I'm living now.")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "recent_change" and s.get("is_definite") for s in stmts)
    results.append(("E4 possession-altering ORIGINAL (dropped off last winter coat)", ok, stmts))

    # E5: environmental observation — SYNTHETIC (not from test set)
    # Principle: user notices recurring local wildlife → implies location/climate
    r = extract(llm, "[Turn 3] I keep finding little geckos hanging on my walls every evening — they're everywhere here. Kind of cute once you get used to them.")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "current_state" and s.get("is_definite") for s in stmts)
    results.append(("E5 env-observation SYNTHETIC (geckos on walls)", ok, stmts))

    # E6: environmental observation — original failing case (uid 704cac18, bark scorpion)
    r = extract(llm, "[Turn 0] I've learned the hard way to always shake out my boots before putting them on — found another bark scorpion in there this morning. You'd think I'd be used to it by now, but the relentless dry heat is driving me indoors more and more.")
    stmts = r.get("statements", [])
    ok = any(s.get("category") == "current_state" and s.get("is_definite") for s in stmts)
    results.append(("E6 env-observation ORIGINAL (bark scorpion + dry heat)", ok, stmts))

    # E7: one-time event with no lasting consequence — should NOT extract
    r = extract(llm, "[Turn 0] Had lunch with my colleague Sarah today at that new Thai place downtown.")
    stmts = r.get("statements", [])
    ok = len([s for s in stmts if s.get("is_definite")]) == 0
    results.append(("E7 NO-EXTRACT one-time lunch", ok, stmts))

    # ── IMPACT_HYPOTHESIS tests ──────────────────────────────────────────────

    # H1: dance workshop vs fibula fracture (original test 5 — should still pass)
    r = impact(
        llm,
        "I've been practicing my routines every day and signed up for a weekend workshop next month",
        "User fractured their fibula three months ago and was told they cannot bear weight on their right leg.",
    )
    hyps = r.get("hypothetical_impacts", [])
    ok = len(hyps) >= 3 and any("weight" in h.lower() or "leg" in h.lower() or "fibula" in h.lower() or "walk" in h.lower() for h in hyps)
    results.append(("H1 impact-hypo dance vs fibula (baseline)", ok, hyps))

    # H2: Dhuhr vs Catholic (original test 6 — should still pass)
    r = impact(
        llm,
        "I've started blocking out time for Dhuhr every day around noon",
        "User is an active member of their local Catholic parish and attends mass regularly.",
    )
    hyps = r.get("hypothetical_impacts", [])
    ok = any("catholic" in h.lower() or "christian" in h.lower() or "not muslim" in h.lower() or "parish" in h.lower() for h in hyps)
    results.append(("H2 impact-hypo Dhuhr vs Catholic (baseline)", ok, hyps))

    # H3: geographic contradiction — SYNTHETIC
    r = impact(
        llm,
        "The monsoon season arrived early this year — we've had flooding in the streets all week",
        "User lives in a dry landlocked region and works from home.",
    )
    hyps = r.get("hypothetical_impacts", [])
    ok = any("landlocked" in h.lower() or "dry" in h.lower() or "desert" in h.lower() or "landlocked" in h.lower() for h in hyps)
    results.append(("H3 impact-hypo geographic contradiction SYNTHETIC (monsoon vs dry)", ok, hyps))

    # ── Print results ────────────────────────────────────────────────────────
    print("\n" + "="*70)
    passed = 0
    for name, ok, detail in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        if ok:
            passed += 1
        print(f"\n{status}  {name}")
        if isinstance(detail, list) and detail:
            for item in detail:
                print(f"   {json.dumps(item, ensure_ascii=False)}")
        elif not ok:
            print(f"   (empty or unexpected: {json.dumps(detail, ensure_ascii=False)[:200]})")

    print(f"\n{'='*70}")
    print(f"Results: {passed}/{len(results)} passed")


if __name__ == "__main__":
    llm = make_llm()
    run_tests(llm)
