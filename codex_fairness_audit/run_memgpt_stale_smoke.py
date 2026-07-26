#!/usr/bin/env python3
import argparse
import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE = "http://localhost:8283"
T1 = ["89b77229", "7ee76c41", "1a85388f", "f6d12075", "d9545076"]
T2 = ["d806d94c", "feef3933", "14897e47", "c9cc370e", "2c711459"]


def call(method, path, payload=None, timeout=360, retries=3):
    body = None if payload is None else json.dumps(payload).encode()
    for attempt in range(retries):
        req = Request(
            BASE + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            detail = ""
            if isinstance(exc, HTTPError):
                detail = exc.read().decode(errors="replace")
            if attempt + 1 == retries:
                raise RuntimeError(f"{method} {path}: {exc}; {detail}") from exc
            time.sleep(2**attempt)


def create_agent(name):
    return call(
        "POST",
        "/v1/agents/",
        {
            "name": name,
            "agent_type": "memgpt_agent",
            "llm_config": {
                "model": "deepseek-v4-flash",
                "model_endpoint_type": "deepseek",
                "model_endpoint": "https://api.deepseek.com/v1",
                "provider_name": "deepseek",
                "context_window": 30000,
                "handle": "deepseek/deepseek-v4-flash",
                "temperature": 0.0,
                "max_tokens": 4096,
                "enable_reasoner": False,
                "parallel_tool_calls": False,
            },
            "embedding_config": {
                "embedding_endpoint_type": "openai",
                "embedding_endpoint": "http://localhost:8290/v1",
                "embedding_model": "all-MiniLM-L6-v2",
                "embedding_dim": 384,
                "embedding_chunk_size": 300,
                "batch_size": 32,
                "handle": "local/all-MiniLM-L6-v2",
            },
            "memory_blocks": [
                {
                    "label": "persona",
                    "value": (
                        "You are a long-term personal assistant. Before answering questions "
                        "about the user, search prior conversation history. Track changes over "
                        "time, prefer later evidence when it supersedes earlier evidence, and "
                        "state the relevant evidence explicitly in the answer."
                    ),
                },
                {
                    "label": "human",
                    "value": "A user whose prior conversational history is available through conversation search.",
                },
            ],
            "include_base_tools": True,
            "include_multi_agent_tools": False,
            "tool_rules": [
                {"tool_name": "send_message", "type": "exit_loop"},
                {
                    "tool_name": "conversation_search",
                    "type": "max_count_per_step",
                    "max_count_limit": 3,
                },
            ],
            "tags": ["stale-smoke", "memgpt", "deepseek-v4-flash"],
        },
    )


def capture_history(agent_id, sessions):
    exchanges = 0
    for session in sessions:
        pending_users = []
        for message in session:
            if message["role"] == "user":
                pending_users.append({"role": "user", "content": message["content"]})
            elif message["role"] == "assistant":
                call(
                    "POST",
                    f"/v1/agents/{agent_id}/messages/capture",
                    {
                        "provider": "dataset",
                        "model": "recorded-assistant",
                        "request_messages": pending_users
                        or [{"role": "user", "content": "[Earlier conversation continued]"}],
                        "response_dict": {"content": message["content"]},
                    },
                    timeout=60,
                )
                pending_users = []
                exchanges += 1
        if pending_users:
            call(
                "POST",
                f"/v1/agents/{agent_id}/messages/capture",
                {
                    "provider": "dataset",
                    "model": "recorded-assistant",
                    "request_messages": pending_users,
                    "response_dict": {"content": "[No recorded assistant reply.]"},
                },
                timeout=60,
            )
            exchanges += 1
    return exchanges


def assistant_text(response):
    texts = []
    for message in response.get("messages", []):
        if message.get("message_type") == "assistant_message":
            value = message.get("content")
            if isinstance(value, str):
                texts.append(value)
            elif isinstance(value, list):
                texts.extend(x.get("text", "") for x in value if isinstance(x, dict))
    return "\n".join(x for x in texts if x).strip()


def run_sample(sample, outdir):
    prefix = sample["uid"][:8]
    sample_dir = outdir / f"{sample['type']}_{prefix}"
    sample_dir.mkdir(parents=True, exist_ok=True)
    result_path = sample_dir / "answer.json"
    if result_path.exists():
        return json.loads(result_path.read_text())

    state_path = sample_dir / "agent_state.json"
    if state_path.exists():
        state = json.loads(state_path.read_text())
        agent = {"id": state["agent_id"]}
        exchanges = state["history_exchanges_captured"]
    else:
        agent = create_agent(f"stale-{sample['type'].lower()}-{prefix}-{int(time.time())}")
        exchanges = capture_history(agent["id"], sample["haystack_session"])
        state_path.write_text(
            json.dumps(
                {"agent_id": agent["id"], "history_exchanges_captured": exchanges},
                ensure_ascii=False,
                indent=2,
            )
        )
    responses, traces = {}, {}
    for dim in ("dim1", "dim2", "dim3"):
        query = sample["probing_queries"][f"{dim}_query"]
        prompted_query = (
            "Use conversation_search to check the relevant earlier and later evidence. "
            "Use at most three searches, do not use date filters, then call send_message "
            "with the final answer. Explicitly state which later evidence determines the "
            "current answer.\n\nQuestion: "
            + query
        )
        raw = call(
            "POST",
            f"/v1/agents/{agent['id']}/messages",
            {"input": prompted_query, "max_steps": 12},
            timeout=600,
        )
        (sample_dir / f"{dim}_trace.json").write_text(
            json.dumps(raw, ensure_ascii=False, indent=2)
        )
        answer = assistant_text(raw)
        if not answer:
            raise RuntimeError(f"{prefix} {dim}: no assistant answer")
        responses[f"{dim}_response"] = answer
        traces[dim] = {
            "stop_reason": raw.get("stop_reason"),
            "usage": raw.get("usage"),
            "tools": [
                m.get("tool_call", {}).get("name")
                for m in raw.get("messages", [])
                if m.get("message_type") == "tool_call_message"
            ],
        }

    result = {
        "uid": sample["uid"],
        "type": sample["type"],
        "method": "memgpt_deepseek_v4_flash_smoke",
        "target_model_responses": responses,
        "target_model_meta": {
            "agent_id": agent["id"],
            "letta_version": "0.16.8",
            "history_exchanges_captured": exchanges,
            "trace_summary": traces,
        },
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    data = json.loads(Path(args.dataset).read_text())
    selected = []
    for kind, prefixes in (("T1", T1), ("T2", T2)):
        for prefix in prefixes:
            selected.append(next(x for x in data if x["type"] == kind and x["uid"].startswith(prefix)))
    selected = selected[: args.limit]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    results = []
    for index, sample in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {sample['type']} {sample['uid'][:8]}", flush=True)
        result = run_sample(sample, outdir)
        results.append(result)
        print(
            "  "
            + " | ".join(
                f"{dim}={result['target_model_responses'][f'{dim}_response'][:100]!r}"
                for dim in ("dim1", "dim2", "dim3")
            ),
            flush=True,
        )
    for kind in ("T1", "T2"):
        subset = [x for x in results if x["type"] == kind]
        (outdir / f"answers_{kind}.json").write_text(
            json.dumps(subset, ensure_ascii=False, indent=2)
        )
    (outdir / "answers_all.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
