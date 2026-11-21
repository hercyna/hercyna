"""LangMem adapter: the open-source `langmem` package, to be pinned in the `langmem` extra.

Default config = the package's background quickstart (a memory store manager over an `InMemoryStore` with
an embedding index), changed only where the bench contract requires it (docs/adapter-guide.md): the shared
chat model and embedder through the product proxy, one fresh store per case, and the chunking rule.
Nothing here sees a Case: only rendered records and the query string.

The package is imported on first use, so the core still has no runtime dependency.
"""

from __future__ import annotations

import os
import re
from importlib import metadata
from types import SimpleNamespace
from typing import Any, Sequence

from .base import Record, Retrieved, Usage

PACKAGE = "langmem"
PINNED = "0.0.30"

# Hashed into the manifest. Product settings are the package defaults unless a line says why not.
DEFAULTS: dict[str, Any] = {
    "records_per_chunk": 25,   # records per manager.invoke (the chunking rule, S6.2). The quickstart feeds one conversation per call
    "search_limit": 60,        # quickstart search default is 10 items; too few to fill a 1000-token view. The engine cuts to the budget
    "embedding_dims": 768,     # the shared embedder's size; the quickstart index uses 1536 (openai text-embedding-3-small)
    "allow_unpinned": False,
}
CHAT_MODEL = "bench-chat"      # placeholder names: the proxy pins the real models,
EMBED_MODEL = "bench-embed"    # and a neutral name keeps any name heuristics out of the way
NAMESPACE_ROOT = "bench"       # the quickstart uses ("memories",); one store per case already isolates cases


def memory_text(item: Any) -> str:
    """The text of one stored memory item.

    `create_memory_store_manager` stores `{"kind": "Memory", "content": {"content": "<text>"}}` (the default
    `Memory(content: str)` schema), so the text is `item.value["content"]["content"]`. An item without such a
    string (another schema, a malformed value) has no text of ours to show and gives "". Nothing is added.
    """
    value = getattr(item, "value", None)
    inner = value.get("content") if isinstance(value, dict) else None
    if isinstance(inner, dict):
        inner = inner.get("content")
    return inner if isinstance(inner, str) else ""


class LangMemAdapter:
    name = "langmem"
    product_version = PINNED
    config_label = "default"
    reference = False
    needs_proxy = True

    def __init__(self, proxy: Any = None, config_label: str = "default", **config: Any) -> None:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"langmem adapter: unknown config keys {sorted(unknown)}; known: {sorted(DEFAULTS)}")
        if proxy is None:
            raise ValueError("langmem adapter: needs the product proxy (bench run --memory-side CONFIG)")
        self.proxy = proxy
        self.config_label = config_label
        self.config = {**DEFAULTS, **config}  # no proxy object in here: this dict is hashed into the manifest
        if int(self.config["records_per_chunk"]) < 1:
            raise ValueError("langmem adapter: records_per_chunk must be at least 1")
        if int(self.config["search_limit"]) < 1:
            raise ValueError("langmem adapter: search_limit must be at least 1")
        self._manager: Any = None
        self._store: Any = None
        self._scope: Any = None
        self._ns: tuple[str, ...] | None = None

    # -- the package, imported late -------------------------------------------------------------------

    def _package(self) -> Any:
        # Tracing is read at import and per call (langsmith utils); force it off, and drop any key.
        for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2", "LANGCHAIN_TRACING"):
            os.environ[var] = "false"
        for var in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY"):
            os.environ.pop(var, None)
        try:
            installed = metadata.version(PACKAGE)
        except metadata.PackageNotFoundError:
            raise RuntimeError(f"langmem adapter: {PACKAGE} is not installed; run: uv sync --extra langmem") from None
        if installed != PINNED and not self.config["allow_unpinned"]:
            raise RuntimeError(f"langmem adapter: {PACKAGE} {installed} is installed, the pinned version is {PINNED}")
        self.product_version = installed
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings  # noqa: PLC0415 - optional dependency
        from langgraph.store.memory import InMemoryStore  # noqa: PLC0415
        from langmem import create_memory_store_manager  # noqa: PLC0415

        return SimpleNamespace(ChatOpenAI=ChatOpenAI, OpenAIEmbeddings=OpenAIEmbeddings, InMemoryStore=InMemoryStore,
                               create_memory_store_manager=create_memory_store_manager)

    # -- contract -------------------------------------------------------------------------------------

    def reset(self, namespace: str) -> None:
        self.close()
        pkg = self._package()
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", namespace)
        self._ns = (NAMESPACE_ROOT, safe)
        self._scope = self.proxy.scope(self.name, self.config_label, namespace)
        # The quickstart passes a model string ("anthropic:claude-3-5-sonnet-latest") and sets no temperature or
        # other sampling option; neither do we. Streaming is off because the proxy refuses it.
        llm = pkg.ChatOpenAI(model=CHAT_MODEL, base_url=self._scope.base_url, api_key=self._scope.api_key,
                             streaming=False)
        # check_embedding_ctx_length=False: otherwise the client sends token-id lists, which OpenAI-compatible
        # embedding servers reject. The proxy needs plain strings.
        embed = pkg.OpenAIEmbeddings(model=EMBED_MODEL, base_url=self._scope.base_url, api_key=self._scope.api_key,
                                     check_embedding_ctx_length=False)
        self._store = pkg.InMemoryStore(index={"dims": int(self.config["embedding_dims"]), "embed": embed})
        self._manager = pkg.create_memory_store_manager(llm, namespace=self._ns, store=self._store)

    def ingest(self, records: Sequence[Record]) -> None:
        self._scope.phase("ingest")
        k = int(self.config["records_per_chunk"])
        for i in range(0, len(records), k):
            # One user message per record, text unchanged: the rendered line already starts with the record id
            # and its date. The package merges consecutive same-role messages into one block of its prompt.
            messages = [{"role": "user", "content": r.text} for r in records[i:i + k]]
            self._manager.invoke({"messages": messages})

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        self._scope.phase("retrieve")
        found = self._store.search(self._ns, query=query, limit=int(self.config["search_limit"]))
        units = [t for t in (memory_text(hit) for hit in found) if t]
        usage: Usage = self._scope.usage()
        return Retrieved(text="\n".join(units), record_ids=[], units=units, usage=usage)

    def close(self) -> None:
        self._manager = self._store = None  # in memory only: dropping the references frees the case's store
        if self._scope is not None:
            self._scope.close()
            self._scope = None
        self._ns = None
