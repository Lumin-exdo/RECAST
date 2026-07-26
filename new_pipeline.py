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
        filter_llm: Optional[LLMClient] = None,
        embedding_model_path: Optional[str] = None,
        embedding_device: str = "cpu",
        thresholds: Optional[NewConfig] = None,
        retriever: Optional[BaseRetriever] = None,
        per_evidence_pool: bool = False,
        no_hypothesis: bool = False,
        no_impression: bool = False,
        nli_judge: bool = False,
        nli_model_name: str = "cross-encoder/nli-deberta-v3-small",
        no_pool: bool = False,
        pool_reset_per_session: bool = False,
        judgment_via_embedding: bool = False,
        skip_hypothesis_gen: bool = False,
    ):
        self.llm = llm
        self.filter_llm: Optional[LLMClient] = filter_llm
        self.thresholds = thresholds or NewConfig()
        self.per_evidence_pool = per_evidence_pool
        self.no_hypothesis = no_hypothesis
        self.no_impression = no_impression
        self.nli_judge = nli_judge
        self.no_pool = no_pool
        self.pool_reset_per_session = pool_reset_per_session
        self.judgment_via_embedding = judgment_via_embedding
        self.skip_hypothesis_gen = skip_hypothesis_gen
        self._nli_model = None
        if nli_judge:
            from sentence_transformers import CrossEncoder
            self._nli_model = CrossEncoder(nli_model_name)
            id2label = getattr(self._nli_model.model.config, "id2label", {})
            label2idx = {v.lower(): k for k, v in id2label.items()}
            self._nli_c_idx = label2idx.get("contradiction", 0)
            self._nli_e_idx = label2idx.get("entailment", 1)
            self._nli_n_idx = label2idx.get("neutral", 2)
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
