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
