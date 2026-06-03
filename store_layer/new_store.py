from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from ..memory.new_models import Evidence, GlobalImpression, MemoryItem, StaleMetadata, VersionEntry


class NewProfileStore:
    def __init__(self):
        self._items: Dict[str, MemoryItem] = {}
        self._item_counter = 0
        self._evidence_counter = 0
        self.global_impression = GlobalImpression()

    def reset(self) -> None:
        self._items = {}
        self._item_counter = 0
        self._evidence_counter = 0
        self.global_impression = GlobalImpression()

    def new_item_id(self) -> str:
        self._item_counter += 1
        return f"m_{self._item_counter:05d}"

    def new_evidence_id(self) -> str:
        self._evidence_counter += 1
        return f"e_{self._evidence_counter:05d}"

    def add_item(self, item: MemoryItem) -> None:
        self._items[item.item_id] = item

    def get_item(self, item_id: str) -> Optional[MemoryItem]:
        return self._items.get(item_id)

    def update_item(self, item: MemoryItem) -> None:
        self._items[item.item_id] = item

    def get_active_items(self) -> List[MemoryItem]:
        return [item for item in self._items.values() if item.status == "active"]

    def get_uncertain_items(self) -> List[MemoryItem]:
        return [item for item in self._items.values() if item.status == "uncertain"]

    def get_stale_items(self) -> List[MemoryItem]:
        return [item for item in self._items.values() if item.status == "stale"]

    def get_searchable_items(self) -> List[MemoryItem]:
        return [item for item in self._items.values() if item.status in ("active", "uncertain")]

    def get_all_items(self) -> List[MemoryItem]:
        return list(self._items.values())

    def get_preference_anchors(self, embedding=None) -> List[str]:
        """Return content of active/uncertain lasting_preference, biographical, and
        top social-reputation current_state memories for impact hypothesis cross-referencing.
        Social-reputation current_state memories (e.g. 'people trust me with X') are
        missed by default anchors but can invalidate just as strongly as preferences."""
        anchors = []
        for item in self._items.values():
            if item.status in ("active", "uncertain") and item.category in (
                "lasting_preference", "biographical"
            ):
                anchors.append(item.content)

        if embedding is not None:
            social_candidates = [
                item for item in self._items.values()
                if item.status in ("active", "uncertain") and item.category == "current_state"
            ]
            if social_candidates:
                SOCIAL_REPUTATION_PROBE = (
                    "user's social role, reputation, trust level, standing in a group "
                    "or community, how others perceive or treat the user socially"
                )
                ranked = embedding.rank(
                    query_text=SOCIAL_REPUTATION_PROBE,
                    candidates=social_candidates,
                    text_getter=lambda item: item.content,
                    top_k=3,
                )
                for r in ranked:
                    anchors.append(r["item"].content)

        return anchors

    def search_by_embedding(
        self,
        *,
        query_text: str,
        embedding: Any,
        top_k: int = 8,
        status_filter: Optional[List[str]] = None,
    ) -> List[MemoryItem]:
        if status_filter is None:
            status_filter = ["active", "uncertain"]
        candidates = [item for item in self._items.values() if item.status in status_filter]
        if not candidates:
            return []
        ranked = embedding.rank(
            query_text=query_text,
            candidates=candidates,
            text_getter=lambda item: item.content,
            top_k=top_k,
        )
        return [r["item"] for r in ranked]

    def get_global_impression(self) -> GlobalImpression:
        return self.global_impression

    def update_global_impression(self, impression: GlobalImpression) -> None:
        self.global_impression = impression

    def from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Restore store state from a to_snapshot() dict (for query-only reruns)."""
        self._items = {}
        max_item_num = 0
        for status_key in ("active_items", "uncertain_items", "stale_items"):
            for d in snapshot.get(status_key, []):
                item = MemoryItem(
                    item_id=d["item_id"],
                    content=d["content"],
                    status=d["status"],
                    confidence=float(d.get("confidence", 0.85)),
                    created_session=d.get("created_session", 0),
                    created_time=d.get("created_time", ""),
                    last_updated_session=d.get("last_updated_session", 0),
                    last_updated_time=d.get("last_updated_time", ""),
                    category=d.get("category", ""),
                    pool_confidence=float(d.get("pool_confidence", 0.0)),
                )
                if d.get("stale_metadata"):
                    sm = d["stale_metadata"]
                    item.stale_metadata = StaleMetadata(
                        stale_since_session=sm["stale_since_session"],
                        stale_since_time=sm["stale_since_time"],
                        stale_reason=sm.get("stale_reason", ""),
                        superseded_by=sm.get("superseded_by", ""),
                    )
                for e_dict in d.get("evidence_pool", []):
                    item.evidence_pool.append(Evidence(
                        evidence_id=e_dict["evidence_id"],
                        statement_text=e_dict["statement_text"],
                        inference_chain=e_dict["inference_chain"],
                        confidence=float(e_dict["confidence"]),
                        session_index=e_dict["session_index"],
                        session_time=e_dict["session_time"],
                    ))
                for v_dict in d.get("version_log", []):
                    item.version_log.append(VersionEntry(
                        session=v_dict["session"],
                        time=v_dict["time"],
                        from_status=v_dict["from_status"],
                        to_status=v_dict["to_status"],
                        reason=v_dict["reason"],
                    ))
                self._items[item.item_id] = item
                try:
                    num = int(item.item_id.replace("m_", ""))
                    max_item_num = max(max_item_num, num)
                except ValueError:
                    pass
        self._item_counter = max_item_num
        gi = snapshot.get("global_impression", {})
        if gi:
            self.global_impression = GlobalImpression(
                content=gi.get("content", ""),
                last_updated_session=gi.get("last_updated_session", -1),
                last_updated_time=gi.get("last_updated_time", ""),
                update_log=list(gi.get("update_log", [])),
            )

    def to_snapshot(self) -> Dict[str, Any]:
        items_by_status: Dict[str, List[Dict[str, Any]]] = {"active": [], "uncertain": [], "stale": []}
        for item in self._items.values():
            bucket = items_by_status.get(item.status)
            if bucket is not None:
                bucket.append(item.to_dict())
        return {
            "active_items": items_by_status["active"],
            "uncertain_items": items_by_status["uncertain"],
            "stale_items": items_by_status["stale"],
            "global_impression": self.global_impression.to_dict(),
            "counts": {
                "active": len(items_by_status["active"]),
                "uncertain": len(items_by_status["uncertain"]),
                "stale": len(items_by_status["stale"]),
                "total": len(self._items),
            },
        }
