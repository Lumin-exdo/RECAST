"""Variance analysis: compare run2 vs run3 answer-level agreement and score stability.
Usage: python scripts/analyze_variance.py
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
STALE_DATA = ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
RUN_NAME_2 = "run2_variance"
RUN_NAME_3 = "run3_variance"


def load_answers_all_commits(run_name: str) -> dict:
    """uid → answer dict, aggregated across ALL commit directories.
    Later commits take precedence over earlier ones."""
    out = {}
    runs_root = ROOT / "runs"
    all_dirs = []
    for commit_dir in sorted(runs_root.iterdir()):
        d = commit_dir / run_name
        if d.exists() and d.is_dir():
            all_dirs.append(d)
    for run_dir in all_dirs:
        for ans_file in run_dir.glob("*/answer.json"):
            try:
                a = json.loads(ans_file.read_text())
                uid = a.get("uid")
                if uid:
                    out[uid] = a  # later alphabetically = newer commit, takes precedence
            except Exception:
                pass
    return out


def merge_answers_all_commits(run_name: str, out_path: Path) -> None:
    answers_by_uid = load_answers_all_commits(run_name)
    answers = list(answers_by_uid.values())
    out_path.write_text(json.dumps(answers, ensure_ascii=False, indent=2))
    print(f"Merged {len(answers)} answers from all commits → {out_path}")


def main():
    r2 = load_answers_all_commits(RUN_NAME_2)
    r3 = load_answers_all_commits(RUN_NAME_3)

    stale = json.loads(STALE_DATA.read_text())
    uid_to_type = {s["uid"]: s.get("conflict_type", "unknown") for s in stale}

    common = set(r2) & set(r3)
    print(f"run2: {len(r2)} answers, run3: {len(r3)} answers, common: {len(common)}")

    if len(common) == 0:
        print("No common samples yet — run2/run3 still in progress.")
        return

    # Answer-level agreement per query
    agree = {"dim1": 0, "dim2": 0, "dim3": 0, "total": 0}
    total = {"dim1": 0, "dim2": 0, "dim3": 0, "total": 0}
    by_type: dict = defaultdict(lambda: {"dim1": 0, "dim2": 0, "dim3": 0, "n": 0})

    for uid in common:
        a2 = r2[uid]
        a3 = r3[uid]
        ct = uid_to_type.get(uid, "unknown")
        by_type[ct]["n"] += 1
        for dim in ["dim1", "dim2", "dim3"]:
            k = f"{dim}_response"
            r2_ans = (a2.get(k) or "").strip().lower()
            r3_ans = (a3.get(k) or "").strip().lower()
            total[dim] += 1
            total["total"] += 1
            if r2_ans == r3_ans:
                agree[dim] += 1
                agree["total"] += 1
                by_type[ct][dim] += 1

    print("\n=== Answer-level agreement (exact match) ===")
    for dim in ["dim1", "dim2", "dim3", "total"]:
        n = total[dim]
        a = agree[dim]
        print(f"  {dim}: {a}/{n} = {a/n:.1%}" if n > 0 else f"  {dim}: n/a")

    print("\n=== By conflict type ===")
    for ct, d in sorted(by_type.items()):
        n = d["n"]
        dims = [f"dim{i}: {d[f'dim{i}']/n:.1%}" for i in [1, 2, 3]]
        print(f"  {ct} (n={n}): {', '.join(dims)}")

    # Merge answers for scoring (collect across all commits)
    print("\n=== Merging for scorer (all commits) ===")
    out2 = ROOT / "runs" / "variance_merged" / "run2_answers_merged.json"
    out3 = ROOT / "runs" / "variance_merged" / "run3_answers_merged.json"
    out2.parent.mkdir(parents=True, exist_ok=True)
    merge_answers_all_commits(RUN_NAME_2, out2)
    merge_answers_all_commits(RUN_NAME_3, out3)
    print("\nNext: run scorer on merged files (T1 and T2 separately)")


if __name__ == "__main__":
    main()
