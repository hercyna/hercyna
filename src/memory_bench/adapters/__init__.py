"""Memory product adapters: registry."""

from __future__ import annotations

from .base import MemoryAdapter, Record, Retrieved, Usage, enforce_budget, records_of
from .bm25 import Bm25Adapter
from .empty import EmptyAdapter
from .full import FullAdapter
from .graphiti import GraphitiAdapter
from .langmem import LangMemAdapter
from .mem0 import Mem0Adapter
from .recent import RecentAdapter

ADAPTERS = {
    "full": FullAdapter,
    "bm25": Bm25Adapter,
    "recent": RecentAdapter,
    "empty": EmptyAdapter,
    "mem0": Mem0Adapter,
    "langmem": LangMemAdapter,
    "graphiti": GraphitiAdapter,
}


def get_adapter(name: str, **config) -> MemoryAdapter:
    """Build an adapter by name. Product adapters (needs_proxy) also take proxy=."""
    try:
        cls = ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown adapter {name!r}; choose from {sorted(ADAPTERS)}") from None
    return cls(**config)


__all__ = ["ADAPTERS", "get_adapter", "MemoryAdapter", "Record", "Retrieved", "Usage", "enforce_budget", "records_of"]
