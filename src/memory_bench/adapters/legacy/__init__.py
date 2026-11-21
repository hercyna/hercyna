"""Legacy LENS memory systems: full, recent and rag-bm25, kept for result reproduction."""

from __future__ import annotations

from .base import MemorySystem, count_tokens
from .full import FullSystem
from .rag_bm25 import RagBm25System
from .recent import RecentSystem

SYSTEMS = {
    "full": FullSystem,
    "recent": RecentSystem,
    "rag-bm25": RagBm25System,
}


def get_system(name: str) -> MemorySystem:
    """Build a legacy system by name: full, recent or rag-bm25."""
    try:
        cls = SYSTEMS[name]
    except KeyError:
        raise ValueError(f"unknown system {name!r}; choose from {sorted(SYSTEMS)}") from None
    return cls()


__all__ = ["MemorySystem", "count_tokens", "get_system", "SYSTEMS", "FullSystem", "RecentSystem", "RagBm25System"]
