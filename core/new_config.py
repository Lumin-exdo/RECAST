from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NewConfig:
    strong_signal_threshold: float = 0.75
    weak_signal_lower_bound: float = 0.35
    pool_trigger_threshold: float = 0.75
    retrieval_top_k: int = 8
    global_impression_max_chars: int = 500
