"""Run answer_query_v2 on the 5 crash_fix_5 samples using 807dee0 traces."""
import json, os, sys, time
sys.path.insert(0, "/mnt/laq")

for line in open("/mnt/laq/RECAST/.env").read().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

from RECAST.llm_layer.client import LLMClient
from RECAST.retrieval.embedding import build_retriever
from RECAST.core.new_config import NewConfig
from RECAST.store_layer.new_store import NewProfileStore
from RECAST.memory.new_models import Evidence, MemoryItem, StaleMetadata, VersionEntry
from RECAST.query.new_engine import NewQueryEngineMixin

FIX5 = {
    "feef3933": "/mnt/laq/RECAST/runs/807dee0/crash_fix_5/0201/trace.json",
    "ea1bd523": "/mnt/laq/RECAST/runs/807dee0/crash_fix_5/0211/trace.json",
    "28daa975": "/mnt/laq/RECAST/runs/807dee0/crash_fix_5/0223/trace.json",
    "5a4781fe": "/mnt/laq/RECAST/runs/807dee0/crash_fix_5/0231/trace.json",
    "60604200": "/mnt/laq/RECAST/runs/807dee0/crash_fix_5/0239/trace.json",
}


def build_store(snapshot):
    store = NewProfileStore()

    def mk(d):
        sm = None
        if d.get("stale_metadata"):
            sd = d["stale_metadata"]
            sm = StaleMetadata(
                stale_since_session=sd.get("stale_since_session", 0),
                stale_since_time=sd.get("stale_since_time", ""),
                stale_reason=sd.get("stale_reason", ""),
                superseded_by=sd.get("superseded_by", ""),
            )
        ev = [
            Evidence(
                evidence_id=e.get("evidence_id", ""),
                statement_text=e.get("statement_text", ""),
                inference_chain=e.get("inference_chain", ""),
                confidence=float(e.get("confidence", 0)),
                session_index=int(e.get("session_index", 0)),
                session_time=e.get("session_time", ""),
            )
            for e in (d.get("evidence_pool") or [])
        ]
        vl = [
            VersionEntry(
                session=v.get("session", 0),
                time=v.get("time", ""),
                from_status=v.get("from_status", ""),
                to_status=v.get("to_status", ""),
                reason=v.get("reason", ""),
            )
            for v in (d.get("version_log") or [])
        ]
        return MemoryItem(
            item_id=d["item_id"], content=d["content"], status=d["status"],
            confidence=float(d.get("confidence", 0.85)),
            created_session=int(d.get("created_session", 0)),
            created_time=d.get("created_time", ""),
            last_updated_session=int(d.get("last_updated_session", 0)),
            last_updated_time=d.get("last_updated_time", ""),
            category=d.get("category", ""),
            stale_metadata=sm, evidence_pool=ev,
            pool_confidence=float(d.get("pool_confidence", 0)),
            version_log=vl,
        )

    for bucket in ("active_items", "uncertain_items", "stale_items"):
        for item_dict in (snapshot.get(bucket) or []):
            store.add_item(mk(item_dict))
    gi = snapshot.get("global_impression", {})
    store.global_impression.content = gi.get("content", "") if isinstance(gi, dict) else gi
    return store


stale = json.load(open("/mnt/laq/RECAST/STALE/STALE/outputs/STALE_MAIN.json"))
qmap = {
    s["uid"][:8]: {
        "dim1_query": s.get("probing_queries", {}).get("dim1_query", ""),
        "dim2_query": s.get("probing_queries", {}).get("dim2_query", ""),
        "dim3_query": s.get("probing_queries", {}).get("dim3_query", ""),
    }
    for s in stale
}

llm = LLMClient(
    model="deepseek-v4-flash",
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_BASE_URL"],
    default_extra_request_kwargs={"extra_body": {"thinking": {"type": "disabled"}}},
)
retriever = build_retriever("/mnt/laq/RECAST/models/all-MiniLM-L6-v2", device="cpu")
thresholds = NewConfig()


class Engine(NewQueryEngineMixin):
    pass


results = []
for uid8, tpath in FIX5.items():
    print(f"Processing {uid8}...", flush=True)
    trace = json.load(open(tpath))
    snap = trace["result"]["final_profile_snapshot"]
    store = build_store(snap)
    eng = Engine()
    eng.store = store
    eng.llm = llm
    eng.embedding = retriever
    eng.thresholds = thresholds

    qs = qmap[uid8]
    responses = {}
    meta = {}
    full_uid = trace["uid"]

    for dim in ("dim1_query", "dim2_query", "dim3_query"):
        q = qs[dim]
        t0 = time.time()
        res = eng.answer_query_v2(query_label=dim.replace("_query", ""), query_text=q)
        elapsed = round(time.time() - t0, 1)
        verdict = res.get("verdict", {})
        responses[dim.replace("_query", "_response")] = res.get("answer", "")
        meta[dim.replace("_query", "_meta")] = {**verdict, "elapsed_seconds": elapsed}
        print(f"  {dim}: {elapsed}s  safe={verdict.get('premise_safe')}", flush=True)

    results.append({
        "uid": full_uid,
        "type": "T2",
        "target_model_responses": responses,
        "target_model_meta": meta,
        "usage_summary": {},
    })
    print(f"  Done {uid8}", flush=True)

out = "/mnt/laq/RECAST/runs/807dee0/full_60/qonly_fix5_answers.json"
json.dump(results, open(out, "w"), indent=2, ensure_ascii=False)
print(f"Saved {len(results)} results to {out}", flush=True)
