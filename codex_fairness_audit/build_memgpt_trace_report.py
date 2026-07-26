#!/usr/bin/env python3
import json
import re
from pathlib import Path

BASE = Path("/mnt/laq/RECAST/codex_fairness_audit/runs/memgpt_deepseek_smoke10_20260724")
DATASET = Path("/mnt/laq/RECAST/STALE/STALE/outputs/STALE_MAIN.json")


def flatten(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(flatten(v) for v in value)
    return str(value)


dataset = {x["uid"]: x for x in json.loads(DATASET.read_text())}
scores = {}
for task in ("T1", "T2"):
    for rubric in ("strict", "lenient"):
        data = json.loads((BASE / f"scores_{task}_{rubric}.json").read_text())
        scores[(task, rubric)] = {x["uid"]: x for x in data["details"]}

answers = json.loads((BASE / "answers_all.json").read_text())
lines = [
    "# MemGPT / Letta DeepSeek-V4-Flash smoke-test trace report",
    "",
    "Protocol: Letta v0.16.8 `memgpt_agent`; 5 T1 + 5 T2 fixed UIDs; "
    "recorded sessions imported into ordered recall memory; native "
    "`conversation_search`; at most three searches per probe; no oracle "
    "M_old/M_new labels; Qwen3.6-plus strict and original lenient judges.",
    "",
    "| Task | UID | Strict SR/PR/IPA | Lenient SR/PR/IPA | Exact M_new visible in search returns (SR/PR/IPA) | Earliest observed failure |",
    "|---|---|---:|---:|---:|---|",
]

for result in answers:
    uid, task = result["uid"], result["type"]
    sample = dataset[uid]
    strict = scores[(task, "strict")][uid]["evaluation"]
    lenient = scores[(task, "lenient")][uid]["evaluation"]
    strict_bits = [
        strict[f"dim{i}_eval"]["pass"] for i in (1, 2, 3)
    ]
    lenient_bits = [
        lenient[f"dim{i}_eval"]["pass"] for i in (1, 2, 3)
    ]
    normalize = lambda text: " ".join(re.findall(r"\w+", text.lower()))
    mnew = normalize(flatten(sample["M_new"]))
    visible = []
    for i in (1, 2, 3):
        trace = json.loads((BASE / f"{task}_{uid[:8]}" / f"dim{i}_trace.json").read_text())
        returns = " ".join(
            flatten(m.get("tool_return", ""))
            for m in trace.get("messages", [])
            if m.get("message_type") == "tool_return_message"
        )
        normalized = normalize(returns)
        visible.append(bool(mnew and mnew in normalized))
    if all(strict_bits):
        earliest = "No strict failure"
    elif any(visible[i] and not strict_bits[i] for i in range(3)):
        earliest = "Post-retrieval selection/reasoning or realization"
    else:
        earliest = "Recall search did not surface exact M_new"
    bits = lambda xs: "/".join("1" if x else "0" for x in xs)
    vis = "/".join("Y" if x else "N" for x in visible)
    lines.append(
        f"| {task} | `{uid[:8]}` | {bits(strict_bits)} | {bits(lenient_bits)} | {vis} | {earliest} |"
    )

    lines.extend(
        [
            "",
            f"## {task} `{uid}`",
            "",
            f"- M_old: {flatten(sample['M_old']).strip()}",
            f"- M_new: {flatten(sample['M_new']).strip()}",
        ]
    )
    for i, label in enumerate(("SR", "PR", "IPA"), 1):
        answer = result["target_model_responses"][f"dim{i}_response"].replace("\n", " ")
        reason = strict[f"dim{i}_eval"]["reasoning"].replace("\n", " ")
        lines.extend(
            [
                f"- {label} answer: {answer}",
                f"- {label} strict judge: {'PASS' if strict_bits[i-1] else 'FAIL'} — {reason}",
                f"- {label} exact M_new in tool returns: {'yes' if visible[i-1] else 'no'}",
            ]
        )

(BASE / "MEMGPT_TRACE_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
print(BASE / "MEMGPT_TRACE_REPORT.md")
