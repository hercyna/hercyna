"""Mem0 adapter: the open-source `mem0ai` package, pinned in the `mem0` extra.

Default config = the package quickstart, changed only where the bench contract requires it
(docs/adapter-guide.md): the shared chat model and embedder through the product proxy, and one local store
per case. Nothing here sees a Case: only rendered records and the query string.

The package is imported on first use, so the core still has no runtime dependency.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from importlib import metadata
from typing import Any, Sequence

from .base import Record, Retrieved, Usage

PACKAGE = "mem0ai"
PINNED = "2.2.1"

# Hashed into the manifest. Product settings are the package defaults unless a line says why not.
DEFAULTS: dict[str, Any] = {
    "records_per_add": 25,     # the protocol's chunk rule (Varma, 2026-11-19): 25 records per write for every product
    "infer": True,             # package default: the model extracts facts from each add
    "top_k": 60,               # package default is 20, which cannot fill a 1000-token view; the engine cuts to the budget
    "threshold": 0.1,          # package default
    "embedding_dims": 768,     # the shared embedder's size; the package default (1536) fails at insert
    "reasoning_model": False,  # set true when the memory-side model reasons: the package then sends no max_tokens
    "allow_unpinned": False,
}
CHAT_MODEL = "bench-chat"      # placeholder names: the proxy pins the real models,
EMBED_MODEL = "bench-embed"    # and a neutral name keeps the package's name heuristics out of the way


class Mem0Adapter:
    name = "mem0"
    product_version = PINNED
    config_label = "default"
    reference = False
    needs_proxy = True

    def __init__(self, proxy: Any = None, config_label: str = "default", **config: Any) -> None:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"mem0 adapter: unknown config keys {sorted(unknown)}; known: {sorted(DEFAULTS)}")
        if proxy is None:
            raise ValueError("mem0 adapter: needs the product proxy (bench run --memory-side CONFIG)")
        self.proxy = proxy
        self.config_label = config_label
        self.config = {**DEFAULTS, **config}  # no proxy object in here: this dict is hashed into the manifest
        if int(self.config["records_per_add"]) < 1:
            raise ValueError("mem0 adapter: records_per_add must be at least 1")
        self._memory: Any = None
        self._scope: Any = None
        self._ns: str | None = None
        self._dir: str | None = None

    # -- the package, imported late -------------------------------------------------------------------

    def _package(self) -> Any:
        os.environ.setdefault("MEM0_TELEMETRY", "False")  # read at import; no telemetry client is built
        os.environ.pop("OPENROUTER_API_KEY", None)        # if set, the package ignores our base_url
        try:
            installed = metadata.version(PACKAGE)
        except metadata.PackageNotFoundError:
            raise RuntimeError(f"mem0 adapter: {PACKAGE} is not installed; run: uv sync --extra mem0") from None
        if installed != PINNED and not self.config["allow_unpinned"]:
            raise RuntimeError(f"mem0 adapter: {PACKAGE} {installed} is installed, the pinned version is {PINNED}")
        self.product_version = installed
        from mem0 import Memory  # noqa: PLC0415 - optional dependency

        return Memory

    # -- contract -------------------------------------------------------------------------------------

    def reset(self, namespace: str) -> None:
        self.close()
        memory_cls = self._package()
        self._ns = re.sub(r"[^A-Za-z0-9_-]", "_", namespace)
        self._scope = self.proxy.scope(self.name, self.config_label, namespace)
        self._dir = tempfile.mkdtemp(prefix=f"bench_mem0_{self._ns}_")
        llm: dict[str, Any] = {"model": CHAT_MODEL, "api_key": self._scope.api_key, "openai_base_url": self._scope.base_url}
        if self.config["reasoning_model"]:
            llm["is_reasoning_model"] = True
        self._memory = memory_cls.from_config({
            "llm": {"provider": "openai", "config": llm},
            "embedder": {"provider": "openai", "config": {
                "model": EMBED_MODEL, "api_key": self._scope.api_key, "openai_base_url": self._scope.base_url}},
            "vector_store": {"provider": "qdrant", "config": {
                "collection_name": self._ns, "path": os.path.join(self._dir, "qdrant"),
                "embedding_model_dims": int(self.config["embedding_dims"])}},
            "history_db_path": os.path.join(self._dir, "history.db"),
        })

    def ingest(self, records: Sequence[Record]) -> None:
        self._scope.phase("ingest")
        k = int(self.config["records_per_add"])
        for i in range(0, len(records), k):
            chunk = records[i:i + k]
            # The rendered line already starts with the record id and its date. The package stamps its
            # own "today" on what it extracts, so the date must travel in the text.
            payload: Any = chunk[0].text if k == 1 else [{"role": "user", "content": r.text} for r in chunk]
            self._memory.add(payload, user_id=self._ns, infer=bool(self.config["infer"]),
                             metadata={"created_at": f"{chunk[-1].date}T00:00:00+00:00"})

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        self._scope.phase("retrieve")
        found = self._memory.search(query, filters={"user_id": self._ns}, top_k=int(self.config["top_k"]),
                                    threshold=float(self.config["threshold"]))
        units = [str(hit["memory"]) for hit in found.get("results", []) if hit.get("memory")]
        usage: Usage = self._scope.usage()
        return Retrieved(text="\n".join(units), record_ids=[], units=units, usage=usage)

    def close(self) -> None:
        memory, self._memory = self._memory, None
        if memory is not None:
            for closer in (getattr(memory, "close", None), getattr(getattr(memory, "vector_store", None), "client", None)):
                try:
                    (closer.close if hasattr(closer, "close") else closer)()  # the store client holds a file lock
                except Exception:  # noqa: BLE001 - closing must not mask the run's own outcome
                    pass
        if self._scope is not None:
            self._scope.close()
            self._scope = None
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)  # the adapter's own temp store, one per case
            self._dir = None
        self._ns = None
