"""Memory product adapters: registry with zero-edit external adapter discovery."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
from pathlib import Path
from typing import Any

from .base import MemoryAdapter, Record, Retrieved, Usage, enforce_budget, records_of
from .bm25 import Bm25Adapter
from .dense_raw import DenseRawAdapter
from .empty import EmptyAdapter
from .full import FullAdapter
from .graphiti import GraphitiAdapter
from .hybrid_bm25_dense import HybridBm25DenseAdapter
from .langmem import LangMemAdapter
from .mem0 import Mem0Adapter
from .recent import RecentAdapter

_ADAPTER_METHODS = ("reset", "ingest", "retrieve", "close")
_ADAPTERS_PKG_DIR = Path(__file__).resolve().parent


def _is_adapter_class(obj: Any) -> bool:
    return isinstance(obj, type) and all(callable(getattr(obj, m, None)) for m in _ADAPTER_METHODS)


def _pick_adapter_class(mod: Any, expected_name: str | None = None) -> type[MemoryAdapter]:
    explicit = getattr(mod, "ADAPTER", None)
    if _is_adapter_class(explicit):
        return explicit
    candidates = [
        v for v in vars(mod).values()
        if _is_adapter_class(v) and v is not MemoryAdapter and getattr(v, "__module__", None) == mod.__name__
    ]
    if expected_name:
        named = [c for c in candidates if getattr(c, "name", None) == expected_name]
        if named:
            return named[0]
    subclasses = [c for c in candidates if issubclass(c, MemoryAdapter)]
    if subclasses:
        return subclasses[0]
    if candidates:
        return candidates[0]
    raise ValueError(f"no adapter class found in {getattr(mod, '__file__', mod)}")


def _load_module_from_file(py_path: Path, mod_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(mod_name, py_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load adapter module from {py_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_external_adapter(name: str) -> type[MemoryAdapter] | None:
    if not isinstance(name, str) or not name:
        return None
    if ":" in name:
        mod_or_path, _, attr = name.partition(":")
        p = Path(mod_or_path)
        if p.is_file():
            mod = _load_module_from_file(p, f"memory_bench_ext_{p.stem}")
        else:
            mod = importlib.import_module(mod_or_path)
        cls = getattr(mod, attr)
        if not _is_adapter_class(cls):
            raise ValueError(f"{name!r} does not implement reset/ingest/retrieve/close")
        return cls

    candidate_files = [
        Path.cwd() / "adapters" / name / "adapter.py",
        Path.cwd() / "adapters" / f"{name}.py",
        _ADAPTERS_PKG_DIR / name / "adapter.py",
    ]
    for path in candidate_files:
        if path.is_file():
            safe_slug = "".join(ch if ch.isalnum() else "_" for ch in name)
            mod = _load_module_from_file(path, f"memory_bench_ext_{safe_slug}")
            return _pick_adapter_class(mod, expected_name=name)

    try:
        eps = importlib.metadata.entry_points(group="memory_bench.adapters")
    except Exception:  # noqa: BLE001
        eps = ()
    for ep in eps:
        if ep.name == name:
            loaded = ep.load()
            if _is_adapter_class(loaded):
                return loaded
    return None


class _AdapterRegistry(dict[str, type[MemoryAdapter]]):
    """Built-in adapter dict with fallback lookup for external folder/module/entry-point adapters."""

    def __contains__(self, key: object) -> bool:
        if super().__contains__(key):
            return True
        if isinstance(key, str):
            return _load_external_adapter(key) is not None
        return False

    def __getitem__(self, key: str) -> type[MemoryAdapter]:
        if super().__contains__(key):
            return super().__getitem__(key)
        ext = _load_external_adapter(key)
        if ext is not None:
            return ext
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if super().__contains__(key):
            return super().__getitem__(key)
        if isinstance(key, str):
            ext = _load_external_adapter(key)
            if ext is not None:
                return ext
        return default


ADAPTERS: dict[str, type[MemoryAdapter]] = _AdapterRegistry({
    "full": FullAdapter,
    "bm25": Bm25Adapter,
    "dense_raw": DenseRawAdapter,
    "hybrid_bm25_dense": HybridBm25DenseAdapter,
    "recent": RecentAdapter,
    "empty": EmptyAdapter,
    "mem0": Mem0Adapter,
    "langmem": LangMemAdapter,
    "graphiti": GraphitiAdapter,
})


def get_adapter(name: str, **config) -> MemoryAdapter:
    """Build an adapter by name or external spec (`adapters/<name>/adapter.py`, `mod:Class`)."""
    try:
        cls = ADAPTERS[name]
    except KeyError:
        raise ValueError(f"unknown adapter {name!r}; choose from {sorted(ADAPTERS)}") from None
    return cls(**config)


__all__ = ["ADAPTERS", "get_adapter", "MemoryAdapter", "Record", "Retrieved", "Usage", "enforce_budget", "records_of"]
