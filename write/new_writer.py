from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from ..memory.new_models import Evidence, GlobalImpression, MemoryItem, StaleMetadata, VersionEntry
from ..prompt_lib.new_templates import (
    ABDUCTIVE_JUDGMENT_PROMPT,
    GLOBAL_IMPRESSION_UPDATE_PROMPT,
    HYPOTHETICAL_FILTER_PROMPT,
    IMPACT_HYPOTHESIS_PROMPT,
    POOL_SYNTHESIS_PROMPT,
    STATEMENT_EXTRACTOR_PROMPT,
    TRIGGER_GATE_PROMPT,
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

    def _safe_call_json(self, system_prompt: str, user_payload: str, *, phase: str) -> Dict[str, Any]:
        try:
            return self.llm.call_json(
                system_prompt,
                user_payload,
                extra_meta={"phase": phase},
            )
        except Exception as exc:
            return {"_error": str(exc)}

    def _extract_statements(self, session_text: str) -> List[Dict[str, Any]]:
        if not session_text.strip():
            return []
        result = self._safe_call_json(
            STATEMENT_EXTRACTOR_PROMPT,
            f"Session user turns:\n{session_text}",
            phase="statement_extraction",
        )
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

    def _is_factual(self, statement: str) -> Dict[str, Any]:
        result = self._safe_call_json(
            HYPOTHETICAL_FILTER_PROMPT.replace("{statement}", statement),
            "Classify the statement above.",
            phase="hypothetical_filter",
        )
        return result

    def _check_trigger_gate(self, statement: str, global_impression: GlobalImpression) -> Dict[str, Any]:
        prompt = TRIGGER_GATE_PROMPT.replace("{statement}", statement).replace(
            "{global_impression}", global_impression.content or "(no profile yet — user's first sessions)"
        )
        result = self._safe_call_json(prompt, "Assess trigger.", phase="trigger_gate")
        return result

    def _generate_impact_hypotheses(self, statement: str, global_impression: GlobalImpression) -> List[str]:
        prompt = IMPACT_HYPOTHESIS_PROMPT.replace("{statement}", statement).replace(
            "{global_impression}", global_impression.content or "(no profile yet)"
        )
        result = self._safe_call_json(prompt, "Generate impact hypotheses.", phase="impact_hypothesis")
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
        result = self._safe_call_json(prompt, "Run abductive judgment.", phase="abductive_judgment")
        judgments = result.get("judgments", [])
        if not isinstance(judgments, list):
            return []
        return [j for j in judgments if isinstance(j, dict)]

    def _synthesize_pool(self, item: MemoryItem) -> Dict[str, Any]:
        if not item.evidence_pool:
            return {"synthesized_confidence": 0.0, "reasoning": "no evidence", "should_mark_stale": False}

        evidence_text = "\n".join(
            f"- Session {e.session_index}: '{e.statement_text}' | chain: {e.inference_chain} | confidence: {e.confidence:.2f}"
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
        return result

    def _create_new_item(
        self,
        statement: str,
        *,
        session_index: int,
        session_time: str,
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
        confidence = float(judgment.get("confidence", 0.0))
        jtype = str(judgment.get("type", "")).strip()
        inference_chain = str(judgment.get("inference_chain", "")).strip()

        if not target_id:
            return {"action": "skip", "reason": "no target_item_id"}

        item = self.store.get_item(target_id)
        if item is None or item.status == "stale":
            return {"action": "skip", "reason": "item not found or already stale"}

        log: Dict[str, Any] = {
            "target_item_id": target_id,
            "confidence": confidence,
            "type": jtype,
            "inference_chain": inference_chain,
        }

        if confidence >= strong_threshold and jtype == "direct_invalidation":
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

        elif weak_lower <= confidence < strong_threshold and jtype in ("direct_invalidation", "weakens_support"):
            evidence_id = self.store.new_evidence_id()
            evidence = Evidence(
                evidence_id=evidence_id,
                statement_text=statement,
                inference_chain=inference_chain,
                confidence=confidence,
                session_index=session_index,
                session_time=session_time,
            )
            item.evidence_pool.append(evidence)

            synthesis = self._synthesize_pool(item)
            synthesized_confidence = float(synthesis.get("synthesized_confidence", 0.0))
            item.pool_confidence = synthesized_confidence

            if synthesized_confidence >= pool_threshold:
                self._mark_stale(
                    item,
                    session_index=session_index,
                    session_time=session_time,
                    reason=synthesis.get("reasoning", "evidence pool threshold reached"),
                    superseded_by=new_item_id if new_item_created else "",
                )
                log["action"] = "pool_triggered_stale"
            elif synthesized_confidence >= 0.5:
                if item.status == "active":
                    self._mark_uncertain(
                        item,
                        session_index=session_index,
                        session_time=session_time,
                        reason=synthesis.get("reasoning", "evidence pool raised uncertainty"),
                    )
                log["action"] = "marked_uncertain"
            else:
                self.store.update_item(item)
                log["action"] = "added_to_pool"

            log["synthesized_confidence"] = synthesized_confidence

        else:
            log["action"] = "discarded"

        return log

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

        updated_content = str(result.get("updated_impression", "")).strip()
        if not updated_content:
            return

        new_impression = GlobalImpression(
            content=updated_content[:500],
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
        """Extract + filter statements for one session without touching memory state.
        Returns list of FACTUAL statements, or None on error (triggers full re-extraction).
        Safe to call concurrently across sessions."""
        try:
            session_text = self._session_to_user_text(session)
            if not session_text.strip():
                return []
            statements = self._extract_statements(session_text)
            if not statements:
                return []
            n = len(statements)
            if n > 1:
                with ThreadPoolExecutor(max_workers=min(n, max_workers)) as ex:
                    filter_results = list(ex.map(lambda s: self._is_factual(s["text"]), statements))
            else:
                filter_results = [self._is_factual(s["text"]) for s in statements]
            return [
                statements[i] for i, r in enumerate(filter_results)
                if str(r.get("type", "")).strip().upper() == "FACTUAL"
            ]
        except Exception:
            return None  # signal: fall back to full extraction in process_session

    def process_session(
        self,
        *,
        session: List[Dict[str, Any]],
        session_index: int,
        session_time: str,
        precomputed_factual: Optional[List[Dict[str, Any]]] = None,
        max_workers: int = 16,
    ) -> Dict[str, Any]:
        session_id = f"s_{session_index:03d}"
        session_text = self._session_to_user_text(session)

        if precomputed_factual is not None:
            # Pre-scan already did extract+filter — skip those LLM calls
            statements = precomputed_factual
            filter_results = [{"type": "FACTUAL", "reason": "pre-classified"} for _ in statements]
            factual_indices = list(range(len(statements)))
        else:
            statements = self._extract_statements(session_text)
            n = len(statements)

            # ── PHASE A: classify all statements in parallel ──────────────────
            if n > 1:
                with ThreadPoolExecutor(max_workers=min(n, max_workers)) as ex:
                    filter_results = list(ex.map(lambda s: self._is_factual(s["text"]), statements))
            else:
                filter_results = [self._is_factual(s["text"]) for s in statements]

            factual_indices = [
                i for i, r in enumerate(filter_results)
                if str(r.get("type", "")).strip().upper() == "FACTUAL"
            ]

        # ── PHASE B: trigger gate for all FACTUAL statements in parallel ──
        # Snapshot impression once — gate only reads it, never writes.
        impression = self.store.get_global_impression()
        if len(factual_indices) > 1:
            with ThreadPoolExecutor(max_workers=min(len(factual_indices), max_workers)) as ex:
                gate_list = list(ex.map(
                    lambda i: self._check_trigger_gate(statements[i]["text"], impression),
                    factual_indices,
                ))
        else:
            gate_list = [self._check_trigger_gate(statements[i]["text"], impression) for i in factual_indices]
        gate_results: Dict[int, Dict[str, Any]] = dict(zip(factual_indices, gate_list))

        triggered_indices = [i for i in factual_indices if gate_results[i].get("should_trigger", False)]

        # Create new memory items serially (fast, in-memory; must precede search
        # so later searches can find already-created sibling items).
        new_items: Dict[int, Any] = {}
        for i in triggered_indices:
            new_items[i] = self._create_new_item(
                statements[i]["text"],
                session_index=session_index,
                session_time=session_time,
            )

        # ── PHASE C: hypothesis + candidate search + judgment — all parallel ─
        def _process_triggered(i: int):
            text = statements[i]["text"]
            hyps = self._generate_impact_hypotheses(text, impression)
            candidates = self._search_candidates(text, hyps)
            judgments = self._run_abductive_judgment(text, hyps, candidates)
            return i, hyps, candidates, judgments

        if len(triggered_indices) > 1:
            with ThreadPoolExecutor(max_workers=min(len(triggered_indices), max_workers)) as ex:
                triggered_results = list(ex.map(_process_triggered, triggered_indices))
        else:
            triggered_results = [_process_triggered(i) for i in triggered_indices]

        triggered_data: Dict[int, Any] = {
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

            filter_result = filter_results[i]
            stmt_type = str(filter_result.get("type", "")).strip().upper()
            stmt_entry["hypothetical_filter"] = filter_result

            if i not in factual_indices:
                stmt_entry["pipeline"].append(f"dropped at hypothetical_filter (type={stmt_type})")
                statement_log.append(stmt_entry)
                continue

            gate_result = gate_results[i]
            stmt_entry["trigger_gate"] = gate_result

            if i not in triggered_indices:
                stmt_entry["pipeline"].append("dropped at trigger_gate")
                statement_log.append(stmt_entry)
                continue

            stmt_entry["pipeline"].append("passed trigger_gate")
            processed_statements.append(text)

            new_item = new_items[i]
            stmt_entry["new_item_id"] = new_item.item_id

            hyps, candidates, judgments = triggered_data[i]
            stmt_entry["hypotheses"] = hyps
            stmt_entry["candidate_ids"] = [c.item_id for c in candidates]
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

        # ── PHASE E: update global impression (serial, 1 LLM call) ───────
        if self._should_update_impression(all_judgment_logs) or (
            processed_statements and not self.store.get_global_impression().content
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
