from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..memory.new_models import MemoryItem
from ..prompt_lib.new_templates import (
    ANSWER_GENERATION_PROMPT,
    E2E_ANSWER_PROMPT,
    NAIVE_ANSWER_PROMPT,
    PREMISE_CHECK_PROMPT,
    QUERY_HYPOTHESIS_PROMPT,
)


class NewQueryEngineMixin:

    def _safe_call_json_q(self, system_prompt: str, user_payload: str, *, phase: str, query_label: str = "") -> Dict[str, Any]:
        import time as _time, random as _random
        from ..llm_layer.client import InsufficientBalanceError
        for attempt in range(6):
            try:
                return self.llm.call_json(
                    system_prompt,
                    user_payload,
                    extra_meta={"phase": phase, "query_label": query_label},
                )
            except InsufficientBalanceError:
                print(f"[BALANCE EXHAUSTED] query phase={phase} label={query_label} — aborting, not retrying", flush=True)
                raise
            except Exception as exc:
                if attempt < 5:
                    wait = (2 ** attempt) + _random.uniform(0, 1)  # 1+j, 2+j, 4+j, 8+j, 16+j
                    print(f"[API ERROR] query phase={phase} label={query_label} attempt={attempt+1}/6, retrying in {wait:.1f}s: {exc}", flush=True)
                    _time.sleep(wait)
                else:
                    print(f"[API ERROR] query phase={phase} label={query_label}: {exc}", flush=True)
                    return {"_error": str(exc)}

    def _retrieve_for_query(self, query_text: str) -> Dict[str, List[MemoryItem]]:
        cfg = getattr(self, "thresholds", None)
        top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8

        active = self.store.search_by_embedding(
            query_text=query_text,
            embedding=self.embedding,
            top_k=top_k,
            status_filter=["active"],
        )
        uncertain = self.store.search_by_embedding(
            query_text=query_text,
            embedding=self.embedding,
            top_k=top_k,
            status_filter=["uncertain"],
        )
        stale = self.store.search_by_embedding(
            query_text=query_text,
            embedding=self.embedding,
            top_k=top_k,
            status_filter=["stale"],
        )
        return {"active": active, "uncertain": uncertain, "stale": stale}

    def _check_premise(
        self,
        query_text: str,
        active_items: List[MemoryItem],
        uncertain_items: List[MemoryItem],
        stale_items: List[MemoryItem],
        *,
        query_label: str = "",
    ) -> Dict[str, Any]:
        active_text = "\n".join(f"- [{item.item_id}] {item.content}" for item in active_items) or "(none)"
        uncertain_text = "\n".join(f"- [{item.item_id}] {item.content}" for item in uncertain_items) or "(none)"
        stale_text = "\n".join(
            f"- [{item.item_id}] {item.content} (stale since session {item.stale_metadata.stale_since_session if item.stale_metadata else '?'}: {item.stale_metadata.stale_reason if item.stale_metadata else 'unknown'})"
            for item in stale_items
        ) or "(none)"

        prompt = (
            PREMISE_CHECK_PROMPT
            .replace("{query_text}", query_text)
            .replace("{active_memories}", active_text)
            .replace("{uncertain_memories}", uncertain_text)
            .replace("{stale_memories}", stale_text)
        )
        return self._safe_call_json_q(prompt, "Check premise.", phase="premise_check", query_label=query_label)

    def _generate_answer(
        self,
        query_text: str,
        active_items: List[MemoryItem],
        uncertain_items: List[MemoryItem],
        stale_items: List[MemoryItem],
        premise_result: Dict[str, Any],
        profile_summary: str,
        *,
        query_label: str = "",
    ) -> Dict[str, Any]:
        premise_safe = bool(premise_result.get("premise_safe", True))
        correction = str(premise_result.get("correction", "")).strip()

        usable_active = premise_result.get("usable_active_facts", [])
        if usable_active:
            active_facts_text = "\n".join(f"- {f}" for f in usable_active)
        elif premise_safe:
            active_facts_text = "\n".join(f"- {item.content}" for item in active_items) or "(none)"
        else:
            # Premise is unsafe and no facts were cleared as usable — exposing all active
            # items would hand answer_gen memories that support the outdated state, causing
            # the correction to be overridden by contradicting "current facts".
            active_facts_text = "(premise unsafe — no unambiguously current facts identified; see correction)"

        uncertain_facts_text = "\n".join(f"- {item.content} (uncertain)" for item in uncertain_items) or "(none)"

        outdated = premise_result.get("outdated_facts", [])
        if outdated:
            stale_facts_text = "\n".join(f"- {f}" for f in outdated)
        else:
            stale_facts_text = "\n".join(
                f"- {item.content} (was true, now outdated)"
                for item in stale_items
            ) or "(none)"

        correction_header = ""
        if not premise_safe and correction:
            correction_header = (
                "CORRECTION — THIS TAKES PRECEDENCE OVER CONFLICTING MEMORIES BELOW:\n"
                f"{correction}\n"
                "Build ALL specific recommendations on this corrected state. "
                "Do not suggest actions, plans, or values that only apply under the outdated assumption.\n\n"
            )

        prompt = (
            ANSWER_GENERATION_PROMPT
            .replace("{query_text}", query_text)
            .replace("{active_facts}", active_facts_text)
            .replace("{uncertain_facts}", uncertain_facts_text)
            .replace("{stale_facts}", stale_facts_text)
            .replace("{premise_safe}", str(premise_safe))
            .replace("{correction}", correction or "none")
            .replace("{profile_summary}", profile_summary or "(no profile)")
            # {correction_header} replaced last so LLM-generated correction text
            # cannot accidentally match any earlier placeholder key.
            .replace("{correction_header}", correction_header)
        )
        return self._safe_call_json_q(prompt, "Generate answer.", phase="answer_generation", query_label=query_label)

    def answer_query(self, *, query_label: str, query_text: str) -> Dict[str, Any]:
        retrieved = self._retrieve_for_query(query_text)
        active_items = retrieved["active"]
        uncertain_items = retrieved["uncertain"]
        stale_items = retrieved["stale"]

        profile_summary = self.store.get_global_impression().content or ""

        premise_result = self._check_premise(
            query_text,
            active_items,
            uncertain_items,
            stale_items,
            query_label=query_label,
        )
        if "_error" in premise_result:
            print(f"[API ERROR] premise_check failed for {query_label}, retrying in 3s: {premise_result['_error']}", flush=True)
            time.sleep(3)
            premise_result = self._check_premise(
                query_text,
                active_items,
                uncertain_items,
                stale_items,
                query_label=query_label,
            )
        if "_error" in premise_result:
            print(f"[API ERROR] premise_check failed after retry for {query_label} — defaulting to premise_safe=True (result is UNRELIABLE): {premise_result['_error']}", flush=True)
            premise_result = {"premise_safe": True, "correction": "", "usable_active_facts": [], "outdated_facts": []}

        answer_result = self._generate_answer(
            query_text,
            active_items,
            uncertain_items,
            stale_items,
            premise_result,
            profile_summary,
            query_label=query_label,
        )
        if "_error" in answer_result or not str(answer_result.get("answer", "")).strip():
            print(f"[API ERROR] answer_generation failed or empty for {query_label}, retrying in 3s: {answer_result.get('_error', 'empty answer')}", flush=True)
            time.sleep(3)
            answer_result = self._generate_answer(
                query_text,
                active_items,
                uncertain_items,
                stale_items,
                premise_result,
                profile_summary,
                query_label=query_label,
            )

        if "_error" in answer_result:
            raise RuntimeError(
                f"answer_generation exhausted all retries for {query_label} — aborting sample: {answer_result['_error']}"
            )

        premise_safe = bool(premise_result.get("premise_safe", True))
        answer_text = str(answer_result.get("answer", "")).strip()

        verdict_status = "SAFE" if premise_safe else "OUTDATED"
        verdict_confidence = 0.85 if premise_safe else 0.75

        return {
            "answer": answer_text,
            "verdict": {
                "status": verdict_status,
                "confidence": verdict_confidence,
                "premise_safe": premise_safe,
                "correction": premise_result.get("correction", ""),
            },
            "premise_result": premise_result,
            "retrieved": {
                "active_ids": [item.item_id for item in active_items],
                "uncertain_ids": [item.item_id for item in uncertain_items],
                "stale_ids": [item.item_id for item in stale_items],
            },
        }

    # ------------------------------------------------------------------ #
    #  v2 query: unified retrieval (all statuses) + single E2E LLM call   #
    # ------------------------------------------------------------------ #

    def _generate_query_hypotheses(self, query_text: str, profile_summary: str, *, query_label: str = "") -> List[str]:
        """Generate hypothetical memory statements to broaden retrieval coverage."""
        if not profile_summary.strip():
            return []
        prompt = (
            QUERY_HYPOTHESIS_PROMPT
            .replace("{query_text}", query_text)
            .replace("{profile_summary}", profile_summary)
        )
        result = self._safe_call_json_q(prompt, "Generate query hypotheses.", phase="query_hypothesis", query_label=query_label)
        if "_error" in result:
            print(f"[API ERROR] query_hypothesis failed — falling back to query-only retrieval: {result['_error']}", flush=True)
            return []
        hyps = result.get("hypotheses", [])
        if not isinstance(hyps, list):
            return []
        return [str(h).strip() for h in hyps if str(h).strip()]

    def _retrieve_unified(self, query_text: str, top_k: int = 20, hypotheses: Optional[List[str]] = None) -> List[MemoryItem]:
        """Retrieve top-k memories across ALL statuses using query + hypotheses as multiple embedding queries."""
        all_items = self.store.get_all_items()
        if not all_items:
            return []
        queries = [query_text] + (hypotheses or [])
        # Score each item by its best score across all queries
        item_scores: Dict[str, float] = {}
        item_map: Dict[str, MemoryItem] = {}
        for q in queries:
            for r in self.embedding.rank(
                query_text=q,
                candidates=all_items,
                text_getter=lambda item: item.content,
                top_k=top_k,
            ):
                iid = r["item"].item_id
                score = float(r["score"])
                if iid not in item_scores or score > item_scores[iid]:
                    item_scores[iid] = score
                    item_map[iid] = r["item"]
        sorted_ids = sorted(item_scores, key=lambda iid: item_scores[iid], reverse=True)
        return [item_map[iid] for iid in sorted_ids[:top_k]]

    def _format_memories(self, items: List[MemoryItem]) -> str:
        lines = []
        for item in items:
            tag = item.status.upper()
            if item.status == "stale" and item.stale_metadata:
                reason = (item.stale_metadata.stale_reason or "")[:120]
                lines.append(f"[{tag}] {item.item_id}: {item.content}  (was true, changed — {reason})")
            else:
                lines.append(f"[{tag}] {item.item_id}: {item.content}")
        return "\n".join(lines) or "(no memories retrieved)"

    def answer_query_v2(self, *, query_label: str, query_text: str) -> Dict[str, Any]:
        """New E2E query: hypothesis-expanded retrieval + single LLM call (no premise_check stage)."""
        cfg = getattr(self, "thresholds", None)
        top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8
        retrieve_k = max(top_k * 3, 20)

        profile_summary = self.store.get_global_impression().content or ""
        if getattr(self, "no_query_hypothesis", False):
            hypotheses = []
        else:
            hypotheses = self._generate_query_hypotheses(query_text, profile_summary, query_label=query_label)
        items = self._retrieve_unified(query_text, top_k=retrieve_k, hypotheses=hypotheses)
        memories_text = self._format_memories(items)

        answer_template = NAIVE_ANSWER_PROMPT if getattr(self, "naive_answer_prompt", False) else E2E_ANSWER_PROMPT
        prompt = (
            answer_template
            .replace("{query_text}", query_text)
            .replace("{memories_text}", memories_text)
            .replace("{profile_summary}", profile_summary or "(no profile)")
        )

        result = self._safe_call_json_q(prompt, "Answer.", phase="answer_generation_v2", query_label=query_label)
        if "_error" in result or not str(result.get("answer", "")).strip():
            print(f"[API ERROR] answer_generation_v2 failed or empty for {query_label}, retrying in 3s: {result.get('_error', 'empty answer')}", flush=True)
            import time; time.sleep(3)
            result = self._safe_call_json_q(prompt, "Answer.", phase="answer_generation_v2", query_label=query_label)
        if "_error" in result:
            raise RuntimeError(
                f"answer_generation_v2 exhausted all retries for {query_label} — aborting sample: {result['_error']}"
            )

        answer_text = str(result.get("answer", "")).strip()
        assumption = result.get("assumption", "")
        # Infer premise status from whether the answer starts with a correction
        answer_lower = answer_text.lower().strip()
        premise_safe = not (answer_lower.startswith("actually") or
                            answer_lower.startswith("i should mention") or
                            assumption == "past-state query")

        return {
            "answer": answer_text,
            "verdict": {
                "status": "OUTDATED" if not premise_safe else "SAFE",
                "premise_safe": premise_safe,
                "assumption": assumption,
            },
            "retrieved_ids": [item.item_id for item in items],
        }
