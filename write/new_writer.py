from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from ..memory.new_models import Evidence, GlobalImpression, MemoryItem, StaleMetadata, VersionEntry
from ..prompt_lib.new_templates import (
    ABDUCTIVE_JUDGMENT_PROMPT,
    GLOBAL_IMPRESSION_UPDATE_PROMPT,
    IMPACT_HYPOTHESIS_PROMPT,
    POOL_SYNTHESIS_PROMPT,
    STATEMENT_EXTRACTOR_PROMPT,
)


class NewSessionWriterMixin:

    @staticmethod
    def _session_to_user_text(session: List[Dict[str, Any]]) -> str:
        lines = []
        for idx, message in enumerate(session):
            if not isinstance(message, dict):
                continue
            if str(message.get("role", "")).strip() != "user":
                continue
            content = str(message.get("content", "")).strip()
            if content:
                lines.append(f"[Turn {idx}] {content}")
        return "\n".join(lines)

    def _safe_call_json(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        phase: str,
        extra_request_kwargs: Optional[Dict[str, Any]] = None,
        json_schema: Optional[Dict[str, Any]] = None,
        schema_name: str = "structured_output",
    ) -> Dict[str, Any]:
        import time as _time, random as _random
        from ..llm_layer.client import InsufficientBalanceError
        for attempt in range(6):
            try:
                if json_schema is not None:
                    return self.llm.call_json_with_schema(
                        system_prompt,
                        user_payload,
                        json_schema=json_schema,
                        schema_name=schema_name,
                        extra_meta={"phase": phase},
                    )
                return self.llm.call_json(
                    system_prompt,
                    user_payload,
                    extra_meta={"phase": phase},
                    extra_request_kwargs=extra_request_kwargs,
                )
            except InsufficientBalanceError:
                # Never worth retrying or degrading to an empty result — every
                # subsequent call will fail the same way. Abort the sample (and
                # let the caller's exception handling stop the whole process)
                # instead of silently continuing the write phase with
                # degraded/empty data.
                print(f"[BALANCE EXHAUSTED] phase={phase} — aborting, not retrying", flush=True)
                raise
            except Exception as exc:
                if attempt < 5:
                    wait = (2 ** attempt) + _random.uniform(0, 1)  # 1+j, 2+j, 4+j, 8+j, 16+j
                    print(f"[API ERROR] phase={phase} attempt={attempt+1}/6, retrying in {wait:.1f}s: {exc}", flush=True)
                    _time.sleep(wait)
                else:
                    print(f"[API ERROR] phase={phase}: {exc}", flush=True)
                    return {"_error": str(exc)}

    def _extract_statements(self, session_text: str) -> Optional[List[Dict[str, Any]]]:
        if not session_text.strip():
            return []
        result = self._safe_call_json(
            STATEMENT_EXTRACTOR_PROMPT,
            f"Session user turns:\n{session_text}",
            phase="statement_extraction",
        )
        if "_error" in result:
            print(f"[API ERROR] statement_extraction failed — session will be skipped: {result['_error']}", flush=True)
            return None  # API failure — caller should fall back, not treat as empty
        statements = result.get("statements", [])
        if not isinstance(statements, list):
            return []
        valid = []
        for s in statements:
            if not isinstance(s, dict):
                continue
            text = str(s.get("text", "")).strip()
            if not text:
                continue
            is_definite = s.get("is_definite", False)
            if is_definite is True:
                category = str(s.get("category", "")).strip()
                valid.append({"text": text, "category": category, "is_definite": is_definite})
        return valid

    def _generate_impact_hypotheses(
        self,
        statement: str,
        global_impression: GlobalImpression,
        preference_anchors: List[str],
    ) -> List[str]:
        anchors_text = (
            "\n".join(f"- {a}" for a in preference_anchors)
            if preference_anchors
            else "(none stored yet)"
        )
        prompt = (
            IMPACT_HYPOTHESIS_PROMPT
            .replace("{statement}", statement)
            .replace("{global_impression}", global_impression.content or "(no profile yet)")
            .replace("{preference_anchors}", anchors_text)
        )
        result = self._safe_call_json(prompt, "Generate impact hypotheses.", phase="impact_hypothesis")
        if "_error" in result:
            print(f"[API ERROR] impact_hypothesis failed — skipping hypotheses for statement: {result['_error']}", flush=True)
            return []
        impacts = result.get("hypothetical_impacts", [])
        if not isinstance(impacts, list):
            return []
        return [str(h).strip() for h in impacts if str(h).strip()]

    def _search_candidates(self, statement: str, hypotheses: List[str]) -> List[MemoryItem]:
        cfg = getattr(self, "thresholds", None)
        top_k = getattr(cfg, "retrieval_top_k", 8) if cfg else 8

        seen_ids = set()
        results: List[MemoryItem] = []

        for query_text in [statement] + hypotheses:
            hits = self.store.search_by_embedding(
                query_text=query_text,
                embedding=self.embedding,
                top_k=top_k,
                status_filter=["active", "uncertain"],
            )
            for item in hits:
                if item.item_id not in seen_ids:
                    seen_ids.add(item.item_id)
                    results.append(item)

        return results

    def _run_abductive_judgment(
        self,
        statement: str,
        hypotheses: List[str],
        candidates: List[MemoryItem],
    ) -> List[Dict[str, Any]]:
        if not candidates:
            return []

        hypotheses_text = "\n".join(f"- {h}" for h in hypotheses) if hypotheses else "- (none generated)"
        candidates_text = "\n".join(
            f"[{item.item_id}] {item.content}" for item in candidates
        )

        prompt = (
            ABDUCTIVE_JUDGMENT_PROMPT
            .replace("{statement}", statement)
            .replace("{hypotheses}", hypotheses_text)
            .replace("{candidates}", candidates_text)
        )
        judgment_schema = {
            "type": "object",
            "properties": {
                "judgments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "target_item_id": {"type": "string"},
                            "target_content": {"type": "string"},
                            "inference_chain": {"type": "string"},
                            "confidence": {"type": "number"},
                            "type": {
                                "type": "string",
                                "enum": ["direct_invalidation", "weakens_support", "no_conflict"],
                            },
                        },
                        "required": ["target_item_id", "inference_chain", "confidence", "type"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["judgments"],
            "additionalProperties": False,
        }
        judge_llm = getattr(self, "filter_llm", None) or self.llm
        try:
            result = judge_llm.call_json_with_schema(
                prompt,
                "Run abductive judgment.",
                json_schema=judgment_schema,
                schema_name="abductive_judgment",
                extra_meta={"phase": "abductive_judgment"},
            )
        except Exception:
            result = self._safe_call_json(
                prompt,
                "Run abductive judgment.",
                phase="abductive_judgment",
                json_schema=judgment_schema,
                schema_name="abductive_judgment",
            )
        if "_error" in result:
            print(f"[API ERROR] abductive_judgment failed — skipping judgment for statement: {result['_error']}", flush=True)
            return []
        judgments = result.get("judgments", [])
        if not isinstance(judgments, list):
            return []
        return [j for j in judgments if isinstance(j, dict)]

    def _run_nli_judgment(self, statement: str, hypotheses: List[str], candidates: List[MemoryItem]) -> List[Dict[str, Any]]:
        """NLI-based replacement for abductive judgment.

        Premises = [statement] + hypotheses — the same text set used by
        _search_candidates for retrieval, so the judgment sees exactly what
        G2's abductive LLM prompt saw (statement + predicted impacts).
        Pairs: (premise, candidate.content).
        NLI contradiction = premise directly invalidates memory → stale.
        Neutral/entailment → no judgment (too noisy / memory confirmed).
        For each candidate we keep the judgment from the highest-scoring premise.
        """
        if not candidates or getattr(self, "_nli_model", None) is None:
            return []
        premises = [statement] + list(hypotheses)
        import numpy as np
        c_idx = getattr(self, "_nli_c_idx", 0)
        e_idx = getattr(self, "_nli_e_idx", 1)

        # best_contradiction[item_id] = (score, premise_text)
        best: Dict[str, tuple] = {}

        for premise in premises:
            pairs = [(premise, c.content) for c in candidates]
            raw = self._nli_model.predict(pairs, apply_softmax=True)
            scores = np.atleast_2d(raw)
            for cand, s in zip(candidates, scores):
                contradiction = float(s[c_idx])
                entailment = float(s[e_idx])
                neutral = 1.0 - contradiction - entailment
                # Only flag when contradiction is argmax and above random baseline
                if contradiction > entailment and contradiction > neutral and contradiction > 0.33:
                    prev = best.get(cand.item_id)
                    if prev is None or contradiction > prev[0]:
                        best[cand.item_id] = (contradiction, premise)

        judgments = []
        for cand in candidates:
            if cand.item_id in best:
                score, premise = best[cand.item_id]
                judgments.append({
                    "target_item_id": cand.item_id,
                    "type": "direct_invalidation",
                    "confidence": score,
                    "inference_chain": f"nli-contradiction={score:.3f} | premise='{premise[:80]}'",
                })
        return judgments

    def _embedding_judgment(self, statement: str, candidates: List[MemoryItem]) -> List[Dict[str, Any]]:
        """Ablation D: replace ONLY the abductive-judgment LLM call with embedding
        similarity, on the SAME candidates that real hypothesis-driven search
        retrieved (unlike A-NoHyp, which also skips hypothesis generation and
        re-derives candidates from the full active+uncertain pool).

        Calibrated against 600 LLM-judged conflict pairs vs 600 no_conflict pairs
        sampled from the G2 trace (all-MiniLM-L6-v2 cosine similarity):
          conflict:    mean=0.225 median=0.193 p60=0.243 p90=0.460
          no_conflict: mean=0.166 median=0.130 p80=0.242 p90=0.342
        Raw cosine barely separates the two classes and essentially never reaches
        the LLM-calibrated 0.75/0.35 dispatch thresholds (only 0.7%/21.8% of real
        conflicts do) — using those thresholds directly on raw cosine would make
        this ablation collapse for threshold-calibration reasons, not because
        embedding similarity lacks reasoning capability. Two fixes:
          1. Emission cutoff lowered to 0.24 (~p60 conflict / ~p80 no_conflict —
             the best recall/false-positive balance this data supports).
          2. Confidence rescaled into [0.35, 0.70) so it can only ever reach
             evidence-pool territory, never bypass straight to immediate-stale —
             embedding evidence alone shouldn't be trusted enough for that, and
             type="weakens_support" enforces this architecturally (no upper-bound
             immediate-stale branch exists for that type, regardless of score)."""
        if not candidates:
            return []
        EMIT_THRESHOLD = 0.24
        RESCALE_LO, RESCALE_HI = 0.35, 0.70
        scored = self.embedding.rank(
            query_text=statement,
            candidates=candidates,
            text_getter=lambda item: item.content,
            top_k=len(candidates),
        )
        judgments = []
        for r in scored:
            raw_score = float(r["score"])
            if raw_score < EMIT_THRESHOLD:
                continue
            # Linear rescale: [EMIT_THRESHOLD, 1.0] -> [RESCALE_LO, RESCALE_HI)
            frac = min((raw_score - EMIT_THRESHOLD) / (1.0 - EMIT_THRESHOLD), 1.0)
            rescaled = RESCALE_LO + frac * (RESCALE_HI - RESCALE_LO)
            judgments.append({
                "target_item_id": r["item"].item_id,
                "type": "weakens_support",
                "confidence": rescaled,
                "inference_chain": f"embedding-similarity={raw_score:.3f} (rescaled={rescaled:.3f})",
            })
        return judgments

    def _synthesize_pool(self, item: MemoryItem) -> Dict[str, Any]:
        if not item.evidence_pool:
            return {"synthesized_confidence": 0.0, "reasoning": "no evidence", "should_mark_stale": False}

        evidence_text = "\n".join(
            f"- Session {e.session_index}: '{e.statement_text}' | type: {getattr(e, 'judgment_type', '') or 'unknown'} | chain: {e.inference_chain} | confidence: {e.confidence:.2f}"
            for e in item.evidence_pool
        )
        prompt = (
            POOL_SYNTHESIS_PROMPT
            .replace("{item_id}", item.item_id)
            .replace("{item_content}", item.content)
            .replace("{item_status}", item.status)
            .replace("{item_confidence}", f"{item.confidence:.2f}")
            .replace("{evidence_list}", evidence_text)
        )
        result = self._safe_call_json(prompt, "Synthesize evidence pool.", phase="pool_synthesis")
        if "_error" in result:
            print(f"[API ERROR] pool_synthesis failed — item {item.item_id} will use default confidence: {result['_error']}", flush=True)
        return result

    @staticmethod
    def _evidence_confidence(evidence: Evidence) -> float:
        try:
            return float(getattr(evidence, "confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _pool_stale_gate(
        self,
        item: MemoryItem,
        synthesized_confidence: float,
        pool_threshold: float,
    ) -> tuple[bool, str]:
        """Only direct contradictions can promote a pooled signal to stale.

        Weakens-support evidence is useful for uncertainty, but treating it as
        enough for stale caused over-invalidation when later facts were merely
        compatible with changed finances, schedules, or habits.
        """
        if synthesized_confidence < pool_threshold:
            return False, "below_pool_threshold"

        for evidence in item.evidence_pool:
            jtype = str(getattr(evidence, "judgment_type", "") or "").strip()
            if jtype == "direct_invalidation" and self._evidence_confidence(evidence) >= 0.65:
                return True, "has_direct_invalidation"

        return False, "weak_evidence_only"

    def _create_new_item(
        self,
        statement: str,
        *,
        session_index: int,
        session_time: str,
        category: str = "",
        confidence: float = 0.85,
        supersedes: str = "",
    ) -> MemoryItem:
        item_id = self.store.new_item_id()
        item = MemoryItem(
            item_id=item_id,
            content=statement,
            status="active",
            confidence=confidence,
            created_session=session_index,
            created_time=session_time,
            last_updated_session=session_index,
            last_updated_time=session_time,
            category=category,
        )
        if supersedes:
            item.version_log.append(VersionEntry(
                session=session_index,
                time=session_time,
                from_status="",
                to_status="active",
                reason=f"supersedes {supersedes}",
            ))
        self.store.add_item(item)
        return item

    def _mark_stale(
        self,
        item: MemoryItem,
        *,
        session_index: int,
        session_time: str,
        reason: str,
        superseded_by: str = "",
    ) -> None:
        item.version_log.append(VersionEntry(
            session=session_index,
            time=session_time,
            from_status=item.status,
            to_status="stale",
            reason=reason,
        ))
        item.status = "stale"
        item.last_updated_session = session_index
        item.last_updated_time = session_time
        item.stale_metadata = StaleMetadata(
            stale_since_session=session_index,
            stale_since_time=session_time,
            stale_reason=reason,
            superseded_by=superseded_by,
        )
        self.store.update_item(item)

    def _mark_uncertain(
        self,
        item: MemoryItem,
        *,
        session_index: int,
        session_time: str,
        reason: str,
    ) -> None:
        item.version_log.append(VersionEntry(
            session=session_index,
            time=session_time,
            from_status=item.status,
            to_status="uncertain",
            reason=reason,
        ))
        item.status = "uncertain"
        item.last_updated_session = session_index
        item.last_updated_time = session_time
        self.store.update_item(item)

    def _dispatch_judgment(
        self,
        judgment: Dict[str, Any],
        statement: str,
        *,
        session_index: int,
        session_time: str,
        new_item_created: bool,
        new_item_id: str,
    ) -> Dict[str, Any]:
        cfg = getattr(self, "thresholds", None)
        strong_threshold = getattr(cfg, "strong_signal_threshold", 0.75) if cfg else 0.75
        weak_lower = getattr(cfg, "weak_signal_lower_bound", 0.35) if cfg else 0.35
        pool_threshold = getattr(cfg, "pool_trigger_threshold", 0.75) if cfg else 0.75

        target_id = str(judgment.get("target_item_id", "")).strip()
        try:
            confidence = float(judgment.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        jtype = str(judgment.get("type", "")).strip()
        inference_chain = str(judgment.get("inference_chain", "")).strip()

        if not target_id:
            return {"action": "skip", "reason": "no target_item_id"}

        item = self.store.get_item(target_id)
        if item is None or item.status == "stale":
            return {"action": "skip", "reason": "item not found or already stale"}
        if item.created_session == session_index:
            return {"action": "skip", "reason": "same-session guard: cannot invalidate memory created in current session"}

        log: Dict[str, Any] = {
            "target_item_id": target_id,
            "confidence": confidence,
            "type": jtype,
            "inference_chain": inference_chain,
        }

        if jtype == "direct_invalidation" and confidence >= strong_threshold:
            self._mark_stale(
                item,
                session_index=session_index,
                session_time=session_time,
                reason=inference_chain or "direct invalidation by new statement",
                superseded_by=new_item_id if new_item_created else "",
            )
            if new_item_created:
                new_item = self.store.get_item(new_item_id)
                if new_item and not any(ve.reason.startswith("supersedes") for ve in new_item.version_log):
                    new_item.version_log.append(VersionEntry(
                        session=session_index,
                        time=session_time,
                        from_status="",
                        to_status="active",
                        reason=f"supersedes {target_id}",
                    ))
                    self.store.update_item(new_item)
            log["action"] = "marked_stale"

        elif jtype in ("direct_invalidation", "weakens_support") and confidence >= weak_lower and getattr(self, "no_pool", False):
            # Ablation A-NoPool: skip evidence accumulation + pool synthesis LLM call.
            # Decide immediately from this single judgment's own confidence.
            if jtype == "direct_invalidation" and confidence >= pool_threshold:
                self._mark_stale(
                    item,
                    session_index=session_index,
                    session_time=session_time,
                    reason=inference_chain or "single-judgment confidence reached pool threshold",
                    superseded_by=new_item_id if new_item_created else "",
                )
                log["action"] = "marked_stale_no_pool"
            elif confidence >= 0.5:
                if item.status == "active":
                    self._mark_uncertain(
                        item,
                        session_index=session_index,
                        session_time=session_time,
                        reason=inference_chain or "single-judgment confidence raised uncertainty",
                    )
                else:
                    self.store.update_item(item)
                log["action"] = "marked_uncertain_no_pool"
            else:
                log["action"] = "discarded_no_pool"

        elif jtype in ("direct_invalidation", "weakens_support") and confidence >= weak_lower:
            # Pool candidates:
            #   direct_invalidation with 0.35 <= conf < 0.75 (< 0.75 implied by elif)
            #   weakens_support with conf >= 0.35 (no upper bound: can never immediately stale alone)
            evidence_id = self.store.new_evidence_id()
            evidence = Evidence(
                evidence_id=evidence_id,
                statement_text=statement,
                inference_chain=inference_chain,
                confidence=confidence,
                session_index=session_index,
                session_time=session_time,
                judgment_type=jtype,
            )
            item.evidence_pool.append(evidence)

            per_evidence_mode = getattr(self, "per_evidence_pool", True)
            if per_evidence_mode:
                # Per-evidence: synthesize immediately after each new evidence addition.
                synthesis = self._synthesize_pool(item)
                try:
                    synthesized_confidence = float(synthesis.get("synthesized_confidence") or 0.0)
                except (TypeError, ValueError):
                    synthesized_confidence = 0.0
                item.pool_confidence = synthesized_confidence

                stale_allowed, stale_gate = self._pool_stale_gate(item, synthesized_confidence, pool_threshold)
                if stale_allowed:
                    self._mark_stale(
                        item,
                        session_index=session_index,
                        session_time=session_time,
                        reason=synthesis.get("reasoning", "evidence pool threshold reached"),
                        superseded_by=new_item_id if new_item_created else "",
                    )
                    log["action"] = "pool_triggered_stale"
                    log["stale_gate"] = stale_gate
                elif synthesized_confidence >= pool_threshold:
                    if item.status == "active":
                        self._mark_uncertain(
                            item,
                            session_index=session_index,
                            session_time=session_time,
                            reason=synthesis.get("reasoning", "evidence pool raised uncertainty but lacked direct invalidation"),
                        )
                    else:
                        self.store.update_item(item)
                    log["action"] = "pool_capped_uncertain"
                    log["stale_gate"] = stale_gate
                elif synthesized_confidence >= 0.5:
                    if item.status == "active":
                        self._mark_uncertain(
                            item,
                            session_index=session_index,
                            session_time=session_time,
                            reason=synthesis.get("reasoning", "evidence pool raised uncertainty"),
                        )
                    else:
                        self.store.update_item(item)
                    log["action"] = "marked_uncertain"
                else:
                    self.store.update_item(item)
                    log["action"] = "added_to_pool"
                log["synthesized_confidence"] = synthesized_confidence
            else:
                # Per-session mode: defer synthesis to _flush_evidence_pools() called after all
                # statements are dispatched. Just record evidence here.
                self.store.update_item(item)
                log["action"] = "added_to_pool"

        else:
            log["action"] = "discarded"

        return log

    def _flush_evidence_pools(
        self,
        pool_item_ids: set,
        *,
        session_index: int,
        session_time: str,
        new_item_id_for: Dict[str, str],
    ) -> Dict[str, Dict[str, Any]]:
        """Per-session synthesis: called once after all statements in a session are dispatched."""
        cfg = getattr(self, "thresholds", None)
        pool_threshold = getattr(cfg, "pool_trigger_threshold", 0.75) if cfg else 0.75
        results: Dict[str, Dict[str, Any]] = {}
        for item_id in pool_item_ids:
            item = self.store.get_item(item_id)
            if item is None or item.status == "stale":
                results[item_id] = {"action": "skip", "synthesized_confidence": 0.0}
                continue
            synthesis = self._synthesize_pool(item)
            try:
                synthesized_confidence = float(synthesis.get("synthesized_confidence") or 0.0)
            except (TypeError, ValueError):
                synthesized_confidence = 0.0
            item.pool_confidence = synthesized_confidence
            new_item_id = new_item_id_for.get(item_id, "")
            stale_allowed, stale_gate = self._pool_stale_gate(item, synthesized_confidence, pool_threshold)
            if stale_allowed:
                self._mark_stale(
                    item,
                    session_index=session_index,
                    session_time=session_time,
                    reason=synthesis.get("reasoning", "evidence pool threshold reached"),
                    superseded_by=new_item_id,
                )
                results[item_id] = {"action": "pool_triggered_stale", "synthesized_confidence": synthesized_confidence, "stale_gate": stale_gate}
            elif synthesized_confidence >= pool_threshold:
                if item.status == "active":
                    self._mark_uncertain(
                        item,
                        session_index=session_index,
                        session_time=session_time,
                        reason=synthesis.get("reasoning", "evidence pool raised uncertainty but lacked direct invalidation"),
                    )
                else:
                    self.store.update_item(item)
                results[item_id] = {"action": "pool_capped_uncertain", "synthesized_confidence": synthesized_confidence, "stale_gate": stale_gate}
            elif synthesized_confidence >= 0.5:
                if item.status == "active":
                    self._mark_uncertain(
                        item,
                        session_index=session_index,
                        session_time=session_time,
                        reason=synthesis.get("reasoning", "evidence pool raised uncertainty"),
                    )
                else:
                    self.store.update_item(item)
                results[item_id] = {"action": "marked_uncertain", "synthesized_confidence": synthesized_confidence}
            else:
                self.store.update_item(item)
                results[item_id] = {"action": "added_to_pool", "synthesized_confidence": synthesized_confidence}

            if getattr(self, "pool_reset_per_session", False):
                # Ablation A-PoolReset: this session's evidence has been judged (whatever
                # the outcome) — discard it so it cannot combine with future sessions'
                # evidence. Bounds the evidence pool's memory horizon to one session.
                item.evidence_pool = []
                self.store.update_item(item)
        return results

    def _should_update_impression(self, judgment_logs: List[Dict[str, Any]]) -> bool:
        core_change_actions = {"marked_stale", "pool_triggered_stale"}
        return any(log.get("action") in core_change_actions for log in judgment_logs)

    def _update_global_impression(
        self,
        statements: List[str],
        judgment_logs: List[Dict[str, Any]],
        *,
        session_index: int,
        session_time: str,
    ) -> None:
        impression = self.store.get_global_impression()

        stale_changes = []
        for log in judgment_logs:
            if log.get("action") in ("marked_stale", "pool_triggered_stale"):
                item = self.store.get_item(log.get("target_item_id", ""))
                if item:
                    stale_changes.append(
                        f"'{item.content}' → stale (reason: {item.stale_metadata.stale_reason if item.stale_metadata else 'unknown'})"
                    )

        memory_changes_text = "\n".join(f"- {c}" for c in stale_changes) if stale_changes else "(no memory changes)"
        statements_text = "\n".join(f"- {s}" for s in statements) if statements else "(none)"

        prompt = (
            GLOBAL_IMPRESSION_UPDATE_PROMPT
            .replace("{current_impression}", impression.content or "(empty — no profile yet)")
            .replace("{memory_changes}", memory_changes_text)
            .replace("{new_statements}", statements_text)
        )
        result = self._safe_call_json(prompt, "Update global impression.", phase="impression_update")
        if "_error" in result:
            print(f"[API ERROR] impression_update failed — global impression NOT updated for session {session_index}: {result['_error']}", flush=True)
            return

        updated_content = str(result.get("updated_impression", "")).strip()
        if not updated_content:
            return

        new_impression = GlobalImpression(
            content=updated_content[:1200],
            last_updated_session=session_index,
            last_updated_time=session_time,
            update_log=list(impression.update_log) + [
                {
                    "session": session_index,
                    "time": session_time,
                    "changed_dimensions": result.get("changed_dimensions", []),
                    "trigger": "session_write",
                }
            ],
        )
        self.store.update_global_impression(new_impression)

    def prescan_session(
        self,
        session: List[Dict[str, Any]],
        *,
        max_workers: int = 16,
    ) -> Optional[List[Dict[str, Any]]]:
        """Extract statements for one session without touching memory state.
        Returns list of statements, or None on error (triggers full re-extraction).
        Safe to call concurrently across sessions."""
        from ..llm_layer.client import InsufficientBalanceError
        try:
            session_text = self._session_to_user_text(session)
            if not session_text.strip():
                return []
            statements = self._extract_statements(session_text)
            # None signals an API error in extraction — fall back to full re-extraction.
            if statements is None:
                return None
            return statements or []
        except InsufficientBalanceError:
            raise  # never worth falling back — every retry will hit the same wall
        except Exception as exc:
            print(f"[API ERROR] prescan_session failed — session will fall back to full extraction: {exc}", flush=True)
            return None  # signal: fall back to full extraction in process_session

    def process_session(
        self,
        *,
        session: List[Dict[str, Any]],
        session_index: int,
        session_time: str,
        precomputed_factual: Optional[List[Dict[str, Any]]] = None,
        precomputed_judgments: Optional[Dict[int, Dict[str, Any]]] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        """
        precomputed_judgments: {stmt_idx: {"hypotheses": [...], "candidate_ids": [...], "judgments_raw": [...]}}
        When provided, skips PHASE C entirely (no hypothesis/search/abductive LLM calls).
        Used by replay-from-trace mode to reuse old abductive outputs with new dispatch logic.
        """
        session_id = f"s_{session_index:03d}"
        session_text = self._session_to_user_text(session)

        if precomputed_factual is not None:
            # Pre-scan already did extraction — skip the LLM call
            statements = precomputed_factual
        else:
            raw_stmts = self._extract_statements(session_text)
            if raw_stmts is None:
                raise RuntimeError(
                    f"statement_extraction exhausted all retries for session {session_index} — aborting sample"
                )
            statements = raw_stmts

        # All extracted statements proceed — hypothetical_filter removed (98.4% pass rate,
        # false negatives on M_new weather/emotion statements outweigh the benefit).
        triggered_indices = list(range(len(statements)))
        impression = self.store.get_global_impression()

        # Create new memory items serially (fast, in-memory; must precede search
        # so later searches can find already-created sibling items).
        new_items: Dict[int, Any] = {}
        for i in triggered_indices:
            new_items[i] = self._create_new_item(
                statements[i]["text"],
                session_index=session_index,
                session_time=session_time,
                category=statements[i].get("category", ""),
            )

        # ── PHASE C: hypothesis + candidate search + judgment ────────────
        if precomputed_judgments is not None and not getattr(self, "nli_judge", False):
            # Replay mode: use cached abductive outputs, skip all Phase C LLM calls.
            triggered_data: Dict[int, Any] = {}
            for i in triggered_indices:
                cached = precomputed_judgments.get(i, {})
                triggered_data[i] = (
                    cached.get("hypotheses", []),
                    [],  # candidates not needed for dispatch
                    cached.get("judgments_raw", []),
                )
        elif getattr(self, "nli_judge", False):
            # Ablation A-NLI: use precomputed hypotheses, NLI over (statement+hypothesis, candidate) pairs.
            triggered_data = {}
            for i in triggered_indices:
                hyps = (precomputed_judgments or {}).get(i, {}).get("hypotheses", []) if precomputed_judgments else []
                text = statements[i]["text"]
                candidates = self._search_candidates(text, hyps)
                judgments = self._run_nli_judgment(text, hyps, candidates)
                triggered_data[i] = (hyps, candidates, judgments)
        elif getattr(self, "no_hypothesis", False):
            # Ablation A-NoHyp: skip hypothesis generation + abductive LLM.
            # Use embedding similarity between statement and existing memories as confidence.
            SIM_THRESHOLD = 0.5
            existing_items = self.store.get_active_items() + self.store.get_uncertain_items()
            existing_items = [
                item for item in existing_items if item.created_session != session_index
            ]
            triggered_data = {}
            for i in triggered_indices:
                text = statements[i]["text"]
                pseudo_judgments: List[Dict[str, Any]] = []
                if existing_items:
                    scored = self.embedding.rank(
                        query_text=text,
                        candidates=existing_items,
                        text_getter=lambda item: item.content,
                        top_k=len(existing_items),
                    )
                    pseudo_judgments = [
                        {
                            "target_item_id": r["item"].item_id,
                            "type": "direct_invalidation",
                            "confidence": float(r["score"]),
                            "inference_chain": f"embedding-similarity={r['score']:.3f}",
                        }
                        for r in scored if float(r["score"]) >= SIM_THRESHOLD
                    ]
                triggered_data[i] = ([], [], pseudo_judgments)
        else:
            # Normal mode: run hypothesis + search + abductive judgment LLM calls.
            preference_anchors = self.store.get_preference_anchors()

            def _process_triggered(i: int):
                text = statements[i]["text"]
                if getattr(self, "skip_hypothesis_gen", False):
                    # Ablation E: no hypothesis generation; judgment LLM still runs,
                    # with "(none generated)" in place of hypotheses in its prompt.
                    hyps: List[str] = []
                else:
                    hyps = self._generate_impact_hypotheses(text, impression, preference_anchors)
                candidates = self._search_candidates(text, hyps)
                if getattr(self, "judgment_via_embedding", False):
                    # Ablation D: hypothesis generation + candidate search unchanged;
                    # only the abductive-judgment LLM call is replaced.
                    judgments = self._embedding_judgment(text, candidates)
                else:
                    judgments = self._run_abductive_judgment(text, hyps, candidates)
                return i, hyps, candidates, judgments

            if len(triggered_indices) > 1:
                with ThreadPoolExecutor(max_workers=min(len(triggered_indices), max_workers)) as ex:
                    triggered_results = list(ex.map(_process_triggered, triggered_indices))
            else:
                triggered_results = [_process_triggered(i) for i in triggered_indices]

            triggered_data = {
                i: (hyps, candidates, judgments)
                for i, hyps, candidates, judgments in triggered_results
            }

        # ── PHASE D: dispatch judgments serially (writes to store) ────────
        statement_log: List[Dict[str, Any]] = []
        all_judgment_logs: List[Dict[str, Any]] = []
        processed_statements: List[str] = []

        for i, stmt_info in enumerate(statements):
            text = stmt_info["text"]
            stmt_entry: Dict[str, Any] = {"statement": text, "pipeline": []}

            processed_statements.append(text)

            new_item = new_items[i]
            stmt_entry["new_item_id"] = new_item.item_id

            hyps, candidates, judgments = triggered_data[i]
            stmt_entry["hypotheses"] = hyps
            stmt_entry["candidate_ids"] = [c.item_id for c in candidates] if isinstance(candidates, list) and candidates and hasattr(candidates[0], "item_id") else candidates
            stmt_entry["judgments_raw"] = judgments

            stmt_judgment_logs: List[Dict[str, Any]] = []
            for j in judgments:
                jlog = self._dispatch_judgment(
                    j,
                    text,
                    session_index=session_index,
                    session_time=session_time,
                    new_item_created=True,
                    new_item_id=new_item.item_id,
                )
                stmt_judgment_logs.append(jlog)

            stmt_entry["judgment_logs"] = stmt_judgment_logs
            all_judgment_logs.extend(stmt_judgment_logs)
            statement_log.append(stmt_entry)

        # ── PHASE D2: per-session pool synthesis (only when per_evidence_pool=False) ─
        if not getattr(self, "per_evidence_pool", True):
            pool_item_id_to_new: Dict[str, str] = {}
            for entry in statement_log:
                entry_new_item_id = entry.get("new_item_id", "")
                for jlog in entry.get("judgment_logs", []):
                    if jlog.get("action") == "added_to_pool":
                        tid = jlog.get("target_item_id", "")
                        if tid and tid not in pool_item_id_to_new:
                            pool_item_id_to_new[tid] = entry_new_item_id
            if pool_item_id_to_new:
                pool_results = self._flush_evidence_pools(
                    set(pool_item_id_to_new.keys()),
                    session_index=session_index,
                    session_time=session_time,
                    new_item_id_for=pool_item_id_to_new,
                )
                for jlog in all_judgment_logs:
                    if jlog.get("action") == "added_to_pool":
                        tid = jlog.get("target_item_id", "")
                        if tid in pool_results:
                            sr = pool_results[tid]
                            jlog["action"] = sr["action"]
                            jlog["synthesized_confidence"] = sr["synthesized_confidence"]

        # ── PHASE E: update global impression (serial, 1 LLM call) ───────
        if not getattr(self, "no_impression", False) and (
            self._should_update_impression(all_judgment_logs) or (
                processed_statements and not self.store.get_global_impression().content
            )
        ):
            self._update_global_impression(
                processed_statements,
                all_judgment_logs,
                session_index=session_index,
                session_time=session_time,
            )

        stale_count = sum(1 for log in all_judgment_logs if log.get("action") in ("marked_stale", "pool_triggered_stale"))
        uncertain_count = sum(1 for log in all_judgment_logs if log.get("action") == "marked_uncertain")

        return {
            "session_id": session_id,
            "session_index": session_index,
            "session_time": session_time,
            "statement_log": statement_log,
            "judgment_logs": all_judgment_logs,
            "valid_chunks": [],
            "delta_logs": [],
            "invalidation_logs": [log for log in all_judgment_logs if log.get("action") in ("marked_stale", "pool_triggered_stale")],
            "session_summary": {
                "total_statements": len(statements),
                "processed_statements": len(processed_statements),
                "new_items_created": len(processed_statements),
                "items_marked_stale": stale_count,
                "items_marked_uncertain": uncertain_count,
                "active_count": len(self.store.get_active_items()),
                "uncertain_count": len(self.store.get_uncertain_items()),
                "stale_count": len(self.store.get_stale_items()),
            },
            "profile_snapshot_after_session": self.store.to_snapshot(),
        }
