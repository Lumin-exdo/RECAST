from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "STALE/STALE/outputs/STALE_MAIN.json"
RUN_ROOT = ROOT / "runs/variance_budget_60"

RUNS = ("run2_variance", "run3_variance")

PLANNED_T1 = [
    "89b77229", "7ee76c41", "1a85388f", "f6d12075", "d9545076",
    "e229c5cd", "eacb64ff", "fdada4cc", "a4b2e2fd", "2006d545",
    "d74f7f3e", "b17c5c02", "b35794f3", "7a7621e2", "34d402c0",
    "6ff5a576", "e72a2ba5", "93a1c511", "f7fb891b", "79e4cc40",
    "2ba8e3f4", "26e99c95", "dae22057", "eee1a643", "e51c1d33",
    "e1703b4d", "9867971c", "8aeb8778", "a6170008", "3305ce57",
]

PLANNED_T2 = [
    "d806d94c", "feef3933", "14897e47", "c9cc370e", "2c711459",
    "993152aa", "c03f7b53", "60604200", "06071a3e", "2d92d1c2",
    "fbe6fd55", "28daa975", "27a52329", "830a2e06", "a2a3e641",
    "da38532d", "48707e03", "f50107f1", "ea1bd523", "855155ad",
    "1469bde3", "5a4781fe", "5ae24023", "87ea8043", "14ed299f",
    "4ad50bc6", "5372c535", "d13024ef", "c2cc2d39", "53d876a2",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def dataset_index() -> dict[str, dict[str, Any]]:
    data = load_json(DATASET)
    out: dict[str, dict[str, Any]] = {}
    for idx, record in enumerate(data):
        uid = str(record["uid"])
        out[uid] = {
            "idx": idx,
            "uid": uid,
            "prefix": uid[:8],
            "type": record.get("type") or record.get("conflict_type"),
            "M_old": record.get("M_old"),
            "M_new": record.get("M_new"),
        }
    return out


def find_answers(run_name: str) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for answer_path in sorted((ROOT / "runs").glob(f"*/{run_name}/*/answer.json")):
        if "variance_budget_60" in answer_path.parts:
            continue
        try:
            answer = load_json(answer_path)
        except Exception:
            continue
        uid = str(answer.get("uid") or "")
        if uid:
            found[uid] = answer_path
    return found


def resolve_prefixes(prefixes: list[str], idx: dict[str, dict[str, Any]], expected_type: str) -> list[str]:
    resolved: list[str] = []
    for prefix in prefixes:
        matches = [uid for uid in idx if uid.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f"Prefix {prefix} matched {len(matches)} dataset UIDs: {matches[:5]}")
        uid = matches[0]
        actual = idx[uid]["type"]
        if actual != expected_type:
            raise RuntimeError(f"UID {uid} expected {expected_type}, got {actual}")
        resolved.append(uid)
    return resolved


def copy_checkpoint(src_answer: Path, dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    src_trace = src_answer.with_name("trace.json")
    dst_answer = dst_dir / "answer.json"
    dst_trace = dst_dir / "trace.json"
    if not dst_answer.exists():
        shutil.copy2(src_answer, dst_answer)
    if src_trace.exists() and not dst_trace.exists():
        shutil.copy2(src_trace, dst_trace)


def prepare() -> None:
    idx = dataset_index()
    _planned_t1_uids = resolve_prefixes(PLANNED_T1, idx, "T1")
    t2_uids = resolve_prefixes(PLANNED_T2, idx, "T2")

    answer_maps = {run: find_answers(run) for run in RUNS}
    common_t1 = sorted(
        uid for uid, meta in idx.items()
        if meta["type"] == "T1" and all(uid in answer_maps[run] for run in RUNS)
    )
    if len(common_t1) < 30:
        raise RuntimeError(f"Only {len(common_t1)} T1 UIDs exist in both run2/run3; need 30")
    selected_t1 = common_t1[:30]

    manifest = {
        "run_root": str(RUN_ROOT),
        "policy": {
            "budget": "hard budget outside this script; only T2 is newly generated",
            "workers": 2,
            "batch_size": 2,
            "T1": "deterministically selected from existing common run2/run3 checkpoints; no new API spend",
            "T2": "newly generated for run2/run3 with the same UIDs",
            "judge": "not run automatically; semantic inspection must happen first",
        },
        "selected": {
            "T1": [{"uid": uid, **idx[uid]} for uid in selected_t1],
            "T2": [{"uid": uid, **idx[uid]} for uid in t2_uids],
        },
    }

    for run in RUNS:
        run_dir = RUN_ROOT / run
        run_dir.mkdir(parents=True, exist_ok=True)
        for uid in selected_t1:
            sample_idx = idx[uid]["idx"]
            copy_checkpoint(answer_maps[run][uid], run_dir / f"{sample_idx:04d}")

    write_json(RUN_ROOT / "selected_uids.json", manifest)
    (RUN_ROOT / "t2_uids.txt").write_text(",".join(t2_uids), encoding="utf-8")
    batches = [t2_uids[i : i + 2] for i in range(0, len(t2_uids), 2)]
    (RUN_ROOT / "t2_batches.txt").write_text(
        "\n".join(",".join(batch) for batch in batches) + "\n",
        encoding="utf-8",
    )
    build_answers()
    print(f"Prepared {RUN_ROOT}")
    print(f"  T1 copied: {len(selected_t1)} per run")
    print(f"  T2 planned: {len(t2_uids)} per run")


def answer_files(run_name: str) -> list[Path]:
    return sorted((RUN_ROOT / run_name).glob("*/answer.json"))


def build_answers() -> None:
    for run in RUNS:
        answers = []
        for answer_path in answer_files(run):
            try:
                answers.append(load_json(answer_path))
            except Exception as exc:
                print(f"SKIP unreadable {answer_path}: {exc}")
        answers.sort(key=lambda row: int(row.get("sample_index", 10**9)))
        write_json(RUN_ROOT / run / "answers.json", answers)
        print(f"{run}: built answers.json with {len(answers)} records")


def status() -> int:
    idx = dataset_index()
    manifest_path = RUN_ROOT / "selected_uids.json"
    if not manifest_path.exists():
        print("Manifest missing; run prepare first.")
        return 2
    manifest = load_json(manifest_path)
    wanted = {
        "T1": [row["uid"] for row in manifest["selected"]["T1"]],
        "T2": [row["uid"] for row in manifest["selected"]["T2"]],
    }
    ok = True
    for run in RUNS:
        have = {}
        for answer_path in answer_files(run):
            try:
                answer = load_json(answer_path)
            except Exception:
                ok = False
                continue
            uid = str(answer.get("uid") or "")
            have[uid] = answer_path
        print(f"{run}:")
        for typ in ("T1", "T2"):
            present = [uid for uid in wanted[typ] if uid in have]
            missing = [uid for uid in wanted[typ] if uid not in have]
            print(f"  {typ}: {len(present)}/{len(wanted[typ])}")
            if missing:
                ok = False
                print("    missing prefixes:", ",".join(uid[:8] for uid in missing))
        wrong_type = [uid for uid in have if uid in idx and idx[uid]["type"] not in ("T1", "T2")]
        if wrong_type:
            ok = False
            print("  wrong-type:", wrong_type)
    return 0 if ok else 1


def lint() -> int:
    bad: list[str] = []
    for run in RUNS:
        for answer_path in answer_files(run):
            try:
                answer = load_json(answer_path)
            except Exception as exc:
                bad.append(f"{answer_path}: unreadable: {exc}")
                continue
            uid = str(answer.get("uid") or "")
            responses = answer.get("target_model_responses") or {}
            for dim in ("dim1", "dim2", "dim3"):
                text = str(responses.get(f"{dim}_response") or "").strip()
                lower = text.lower()
                if not text:
                    bad.append(f"{run} {uid[:8]} {dim}: empty answer")
                if any(marker in lower for marker in ["traceback", "insufficient balance", "api error", "error code", "rate limit"]):
                    bad.append(f"{run} {uid[:8]} {dim}: suspicious text: {text[:160]}")
    if bad:
        print("LINT FAILED")
        for item in bad[:200]:
            print("  " + item)
        return 1
    print("LINT OK: no empty/error-looking target responses")
    return 0


def preview(limit_per_run: int = 6) -> None:
    idx = dataset_index()
    for run in RUNS:
        print(f"\n=== {run} semantic preview ===")
        shown = 0
        for answer_path in answer_files(run):
            answer = load_json(answer_path)
            uid = str(answer.get("uid") or "")
            if idx.get(uid, {}).get("type") != "T2":
                continue
            print(f"\n[{run} {uid[:8]} sample_index={answer.get('sample_index')}]")
            print(f"M_old: {idx[uid]['M_old']}")
            print(f"M_new: {idx[uid]['M_new']}")
            responses = answer.get("target_model_responses") or {}
            for dim in ("dim1", "dim2", "dim3"):
                text = str(responses.get(f"{dim}_response") or "").strip()
                print(f"{dim}: {text[:900]}")
            shown += 1
            if shown >= limit_per_run:
                break


def delete_uids(run_name: str, uid_csv: str) -> None:
    idx = dataset_index()
    prefixes = [u.strip() for u in uid_csv.split(",") if u.strip()]
    uids = []
    for prefix in prefixes:
        matches = [uid for uid in idx if uid.startswith(prefix)]
        if len(matches) != 1:
            raise RuntimeError(f"Prefix {prefix} matched {len(matches)} dataset UIDs: {matches[:5]}")
        uids.append(matches[0])

    for uid in uids:
        sample_dir = RUN_ROOT / run_name / f"{idx[uid]['idx']:04d}"
        for name in ("answer.json", "answer.json.tmp", "trace.json", "trace.json.tmp"):
            path = sample_dir / name
            if path.exists():
                path.unlink()
                print(f"deleted {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("cmd", choices=["prepare", "build", "status", "lint", "preview", "delete-uids"])
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--uids", default="")
    args = parser.parse_args()
    if args.cmd == "prepare":
        prepare()
    elif args.cmd == "build":
        build_answers()
    elif args.cmd == "status":
        raise SystemExit(status())
    elif args.cmd == "lint":
        raise SystemExit(lint())
    elif args.cmd == "preview":
        preview(args.limit)
    elif args.cmd == "delete-uids":
        if not args.run_name or not args.uids:
            raise SystemExit("--run-name and --uids are required")
        delete_uids(args.run_name, args.uids)


if __name__ == "__main__":
    main()
