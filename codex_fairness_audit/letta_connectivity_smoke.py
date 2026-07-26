#!/usr/bin/env python3
import json
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://localhost:8283"


def call(method, path, payload=None, timeout=180):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)


agent_payload = json.loads(Path("/tmp/letta_smoke_agent.json").read_text())
agent_payload["name"] = "stale-smoke-local-embedding"
agent = call("POST", "/v1/agents/", agent_payload)
print("agent", agent["id"], agent.get("model"), agent.get("embedding_config"))

passage_payload = json.loads(Path("/tmp/letta_smoke_passage.json").read_text())
passage = call("POST", f"/v1/agents/{agent['id']}/archival-memory", passage_payload)
print("passage", passage)

message = call(
    "POST",
    f"/v1/agents/{agent['id']}/messages",
    {"input": "Based on my history, do I still live in Seattle? Explain which later information you used."},
    timeout=300,
)
Path("/tmp/letta_smoke_message_response.json").write_text(
    json.dumps(message, ensure_ascii=False, indent=2)
)
print("message keys", message.keys())
print(json.dumps(message, ensure_ascii=False)[:4000])
