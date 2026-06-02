from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..memory.new_models import MemoryItem
from ..prompt_lib.new_templates import (
    ANSWER_GENERATION_PROMPT,
    PREMISE_CHECK_PROMPT,
)


class NewQueryEngineMixin:

    def _safe_call_json_q(self, system_prompt: str, user_payload: str, *, phase: str, query_label: str = "") -> Dict[str, Any]:
        try:
            return self.llm.call_json(
                system_prompt,
                user_payload,
                extra_meta={"phase": phase, "query_label": query_label},
            )
        except Exception as exc:
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
        usable_active = premise_result.get("usable_active_facts", [])
        if usable_active:
            active_facts_text = "\n".join(f"- {f}" for f in usable_active)
        else:
            active_facts_text = "\n".join(f"- {item.content}" for item in active_items) or "(none)"

        uncertain_facts_text = "\n".join(f"- {item.content} (uncertain)" for item in uncertain_items) or "(none)"

        outdated = premise_result.get("outdated_facts", [])
        if outdated:
            stale_facts_text = "\n".join(f"- {f}" for f in outdated)
        else:
            stale_facts_text = "\n".join(
                f"- {item.content} (was true, now outdated)"
                for item in stale_items
            ) or "(none)"

        premise_safe = bool(premise_result.get("premise_safe", True))
        correction = str(premise_result.get("correction", "")).strip()

        prompt = (
            ANSWER_GENERATION_PROMPT
            .replace("{query_text}", query_text)
            .replace("{active_facts}", active_facts_text)
            .replace("{uncertain_facts}", uncertain_facts_text)
            .replace("{stale_facts}", stale_facts_text)
            .replace("{premise_safe}", str(premise_safe))
            .replace("{correction}", correction or "none")
            .replace("{profile_summary}", profile_summary or "(no profile)")
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

        answer_result = self._generate_answer(
            query_text,
            active_items,
            uncertain_items,
            stale_items,
            premise_result,
            profile_summary,
            query_label=query_label,
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
