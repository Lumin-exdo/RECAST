from __future__ import annotations

from typing import List, Optional

from .core.engine_utils import EngineUtilityMixin
from .core.new_config import NewConfig
from .core.sample_runner import SampleRunnerMixin
from .llm_layer.client import LLMClient
from .memory.new_models import MemoryItem
from .query.new_engine import NewQueryEngineMixin
from .retrieval.embedding import BaseRetriever, build_retriever
from .store_layer.new_store import NewProfileStore
from .write.new_writer import NewSessionWriterMixin


class NewMemEngine(
    EngineUtilityMixin,
    NewSessionWriterMixin,
    NewQueryEngineMixin,
    SampleRunnerMixin,
):
    def __init__(
        self,
        *,
        llm: LLMClient,
        embedding_model_path: Optional[str] = None,
        embedding_device: str = "cpu",
        thresholds: Optional[NewConfig] = None,
        retriever: Optional[BaseRetriever] = None,
    ):
        self.llm = llm
        self.thresholds = thresholds or NewConfig()
        if retriever is not None:
            self.embedding: BaseRetriever = retriever
        elif embedding_model_path:
            self.embedding = build_retriever(embedding_model_path, device=embedding_device)
        else:
            raise ValueError(
                "Either embedding_model_path or retriever is required."
            )
        self.store = NewProfileStore()
        self.chunk_bank: List[MemoryItem] = []
        self.delta_store: List[MemoryItem] = []

    def reset(self) -> None:
        self.store.reset()
        self.chunk_bank = []
        self.delta_store = []
