from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Evidence:
    evidence_id: str
    statement_text: str
    inference_chain: str
    confidence: float
    session_index: int
    session_time: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StaleMetadata:
    stale_since_session: int
    stale_since_time: str
    stale_reason: str
    superseded_by: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VersionEntry:
    session: int
    time: str
    from_status: str
    to_status: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryItem:
    item_id: str
    content: str
    status: str  # active / uncertain / stale
    confidence: float
    created_session: int
    created_time: str
    last_updated_session: int
    last_updated_time: str
    category: str = ""  # current_state|recent_change|biographical|lasting_preference
    stale_metadata: Optional[StaleMetadata] = None
    evidence_pool: List[Evidence] = field(default_factory=list)
    pool_confidence: float = 0.0
    version_log: List[VersionEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "content": self.content,
            "status": self.status,
            "confidence": self.confidence,
            "category": self.category,
            "created_session": self.created_session,
            "created_time": self.created_time,
            "last_updated_session": self.last_updated_session,
            "last_updated_time": self.last_updated_time,
            "stale_metadata": self.stale_metadata.to_dict() if self.stale_metadata else None,
            "evidence_pool": [e.to_dict() for e in self.evidence_pool],
            "pool_confidence": self.pool_confidence,
            "version_log": [v.to_dict() for v in self.version_log],
        }


@dataclass
class GlobalImpression:
    content: str = ""
    last_updated_session: int = -1
    last_updated_time: str = ""
    update_log: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
