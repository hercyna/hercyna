"""Graphiti adapter: the open-source `graphiti-core` package (temporal knowledge graph), to be pinned in the
`graphiti` extra as `graphiti-core[falkordb]==0.31.2`. The table row is "Graphiti", not "Zep".

Default config = the package's quickstart for OpenAI-compatible endpoints (README, "Using Graphiti with
OpenAI-compatible providers and local LLMs": `OpenAIGenericClient` + `OpenAIEmbedder`), changed only where the
bench contract requires it (docs/adapter-guide.md): the shared chat model and embedder through the product
proxy, one graph per case, the chunking rule and a no-op reranker. Nothing here sees a Case: only rendered
records and the query string.

The graph store is FalkorDB in Docker (Colima), image pinned by exact tag, published on 127.0.0.1 only:

    docker run -d --name bench-falkordb -p 127.0.0.1:6390:6379 falkordb/falkordb:6.0.2
    docker stop bench-falkordb && docker rm bench-falkordb
    docker ps --filter name=bench-falkordb --format '{{.Names}} {{.Image}} {{.Ports}} {{.Status}}'

6.0.2 is what `falkordb/falkordb:latest` pointed to on the graphiti-core 0.31.2 release day (2026-10-20);
the package's CI runs `falkordb/falkordb:latest` and its FalkorDB driver carries a 6.x index fix from
2026-10-06. Digest: sha256:107455e862a824e45e8e9f5a7fbfdf5e00a08b14e13eae2577c72fd9e3f59ba6.

Graphiti is async; the adapter contract is synchronous. Each adapter instance owns one private event loop,
made in `reset` and closed in `close`, and runs every product coroutine on it under an instance lock. The
FalkorDB client keeps its connection pool across calls, so a fresh `asyncio.run` per call would break it.

The package is imported on first use, so the core still has no runtime dependency.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import threading
import uuid
from datetime import datetime, timezone
from importlib import metadata
from types import SimpleNamespace
from typing import Any, Sequence

from .base import Record, Retrieved, Usage

PACKAGE = "graphiti-core"
PINNED = "0.31.2"
FALKORDB_IMAGE = "falkordb/falkordb:6.0.2"
CONTAINER = "bench-falkordb"

# Hashed into the manifest. Product settings are the package defaults unless a line says why not.
DEFAULTS: dict[str, Any] = {
    "records_per_episode": 25,   # records per add_episode, joined by newlines (the chunking rule, S6.2). The quickstart adds one episode per message
    "search_limit": 60,          # quickstart search() default is 10 facts; too few to fill a 1000-token view. The engine cuts to the budget
    "embedding_dims": 768,       # the shared embedder's size; the package default is 1024 (EMBEDDING_DIM). The quickstart for local models uses 768
    "falkordb_host": "127.0.0.1",  # the bench's own FalkorDB container (see the module docstring); the quickstart uses Neo4j on bolt://localhost:7687
    "falkordb_port": 6390,       # host port of that container; 6379 may belong to something else on this machine
    "allow_unpinned": False,
}
CHAT_MODEL = "bench-chat"        # placeholder names: the proxy pins the real models,
EMBED_MODEL = "bench-embed"      # and a neutral name keeps any name heuristics out of the way
SOURCE_DESCRIPTION = "dated records"  # free text the package puts in its extraction prompt; neutral on purpose
GROUP_PREFIX = "bench"           # graph name = bench_<label>_<case>_<random>; FalkorDB keeps one graph per group_id

# `Graphiti.search` sets `.limit` on a module-level recipe (EDGE_HYBRID_SEARCH_RRF) and reads it after an await.
# Two adapter instances on different threads with different search limits would race; one process lock stops it.
_SEARCH_LOCK = threading.Lock()


def docker_hint(port: int) -> str:
    return f"docker run -d --name {CONTAINER} -p 127.0.0.1:{port}:6379 {FALKORDB_IMAGE}"


def make_noop_reranker(base: type) -> type:
    """A cross-encoder that keeps the input order and makes no model call.

    Departure from the quickstart, forced by the bench: the quickstart passes `OpenAIRerankerClient`, which
    asks the chat model for logprobs, and the shared model does not return them. The default search used here
    (`Graphiti.search`, EDGE_HYBRID_SEARCH_RRF) never calls a reranker, so this only stands in for the
    constructor default, which would otherwise build an OpenAI client with the environment's key.
    """

    class NoOpReranker(base):  # type: ignore[misc, valid-type]
        async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
            n = len(passages)
            return [(p, float(n - i)) for i, p in enumerate(passages)]  # input order, descending scores

    return NoOpReranker


def group_id_for(config_label: str, namespace: str) -> str:
    """One graph per case. The package allows [A-Za-z0-9_-]; we use [A-Za-z0-9_] only. On FalkorDB the package's
    fulltext (BM25) filter escapes "-" as "\\-" inside a quoted group filter, while the index splits stored values
    on "-", so a hyphenated group id matches nothing: BM25 search and the BM25 half of ingest-time dedupe go
    silently empty (measured on 0.31.2 + FalkorDB 6.0.2). The random suffix keeps two runs of the same case (two
    configs, a rerun after a crash, "a-b" vs "a_b") from ever sharing a graph."""
    safe = lambda s: re.sub(r"[^A-Za-z0-9_]", "_", s)  # noqa: E731
    return f"{GROUP_PREFIX}_{safe(config_label)}_{safe(namespace)}_{uuid.uuid4().hex[:8]}"


class GraphitiAdapter:
    name = "graphiti"
    product_version = PINNED
    config_label = "default"
    reference = False
    needs_proxy = True

    def __init__(self, proxy: Any = None, config_label: str = "default", **config: Any) -> None:
        unknown = set(config) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"graphiti adapter: unknown config keys {sorted(unknown)}; known: {sorted(DEFAULTS)}")
        if proxy is None:
            raise ValueError("graphiti adapter: needs the product proxy (bench run --memory-side CONFIG)")
        self.proxy = proxy
        self.config_label = config_label
        self.config = {**DEFAULTS, **config}  # no proxy object in here: this dict is hashed into the manifest
        for key in ("records_per_episode", "search_limit", "embedding_dims"):
            if int(self.config[key]) < 1:
                raise ValueError(f"graphiti adapter: {key} must be at least 1")
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._graphiti: Any = None
        self._driver: Any = None
        self._scope: Any = None
        self._group: str | None = None
        self._episodes = 0
        self._pkg: Any = None
        self._http: list[Any] = []

    # -- the package, imported late -------------------------------------------------------------------

    def _package(self) -> Any:
        os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"  # read per event (PostHog, opt-out); set before import
        try:
            installed = metadata.version(PACKAGE)
        except metadata.PackageNotFoundError:
            raise RuntimeError(f"graphiti adapter: {PACKAGE} is not installed; run: uv sync --extra graphiti") from None
        if installed != PINNED and not self.config["allow_unpinned"]:
            raise RuntimeError(f"graphiti adapter: {PACKAGE} {installed} is installed, the pinned version is {PINNED}")
        self.product_version = installed
        from graphiti_core import Graphiti  # noqa: PLC0415 - optional dependency
        from graphiti_core.cross_encoder.client import CrossEncoderClient  # noqa: PLC0415
        from graphiti_core.driver.falkordb_driver import FalkorDriver  # noqa: PLC0415
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig  # noqa: PLC0415
        from graphiti_core.llm_client.config import LLMConfig  # noqa: PLC0415
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient  # noqa: PLC0415
        from graphiti_core.nodes import EpisodeType  # noqa: PLC0415

        return SimpleNamespace(Graphiti=Graphiti, FalkorDriver=FalkorDriver, LLMConfig=LLMConfig,
                               OpenAIGenericClient=OpenAIGenericClient, OpenAIEmbedder=OpenAIEmbedder,
                               OpenAIEmbedderConfig=OpenAIEmbedderConfig, EpisodeType=EpisodeType,
                               NoOpReranker=make_noop_reranker(CrossEncoderClient))

    def _check_store(self) -> None:
        host, port = str(self.config["falkordb_host"]), int(self.config["falkordb_port"])
        try:
            socket.create_connection((host, port), timeout=3).close()
        except OSError as exc:
            raise RuntimeError(f"graphiti adapter: cannot reach FalkorDB at {host}:{port} ({exc}). Start it with: "
                               f"{docker_hint(port)}") from None

    def _run(self, coro: Any) -> Any:
        """Run one product coroutine on this instance's own loop. Errors pass through unchanged."""
        if self._loop is None:
            coro.close()
            raise RuntimeError("graphiti adapter: call reset() first")
        with self._lock:
            return self._loop.run_until_complete(coro)

    # -- contract -------------------------------------------------------------------------------------

    def reset(self, namespace: str) -> None:
        self.close()
        pkg = self._pkg = self._package()
        self._check_store()
        self._scope = self.proxy.scope(self.name, self.config_label, namespace)
        self._group = group_id_for(self.config_label, namespace)
        self._episodes = 0
        self._loop = asyncio.new_event_loop()
        # The quickstart sets model and small_model to the one local model and no temperature (package default 1)
        # or max_tokens (OpenAIGenericClient default 16384); neither do we. The Generic client sends plain
        # /v1/chat/completions with a json_schema response_format, which is all the proxy offers.
        llm_cfg = pkg.LLMConfig(api_key=self._scope.api_key, model=CHAT_MODEL, small_model=CHAT_MODEL,
                                base_url=self._scope.base_url)
        llm = pkg.OpenAIGenericClient(config=llm_cfg)
        embedder = pkg.OpenAIEmbedder(config=pkg.OpenAIEmbedderConfig(
            api_key=self._scope.api_key, embedding_model=EMBED_MODEL,
            embedding_dim=int(self.config["embedding_dims"]), base_url=self._scope.base_url))
        # database = group_id: add_episode and search then use this driver as is (no per-call clone). Built
        # outside a running loop, so the driver schedules no background index task; indices are built below.
        self._driver = pkg.FalkorDriver(host=str(self.config["falkordb_host"]), port=int(self.config["falkordb_port"]),
                                        database=self._group)
        self._graphiti = pkg.Graphiti(graph_driver=self._driver, llm_client=llm, embedder=embedder,
                                      cross_encoder=pkg.NoOpReranker())
        self._http = [getattr(llm, "client", None), getattr(embedder, "client", None)]  # AsyncOpenAI pools, closed in close()
        self._run(self._graphiti.build_indices_and_constraints())  # FalkorDB: once per new graph

    def ingest(self, records: Sequence[Record]) -> None:
        self._scope.phase("ingest")
        k = int(self.config["records_per_episode"])
        for i in range(0, len(records), k):
            chunk = records[i:i + k]
            # Rendered lines, unchanged and in history order: each already starts with the record id and date.
            # The episode's reference_time is the date of its last record. Sequential add_episode: the package
            # asks for each episode to be awaited before the next, and its edge invalidation then runs in order.
            when = datetime.fromisoformat(chunk[-1].date).replace(tzinfo=timezone.utc)
            self._episodes += 1
            self._run(self._graphiti.add_episode(
                name=f"episode-{self._episodes}", episode_body="\n".join(r.text for r in chunk),
                source_description=SOURCE_DESCRIPTION, reference_time=when,
                source=self._pkg.EpisodeType.text, group_id=self._group))

    def retrieve(self, query: str, budget_tokens: int) -> Retrieved:
        self._scope.phase("retrieve")
        # The package's own hybrid search (BM25 + cosine over edge facts, RRF): Graphiti.search -> list[EntityEdge].
        # The view is each edge's `fact`, in the order returned. Nothing added (no dates, no node summaries).
        with _SEARCH_LOCK:
            edges = self._run(self._graphiti.search(query, group_ids=[self._group],
                                                    num_results=int(self.config["search_limit"])))
        units = [e.fact for e in edges if isinstance(getattr(e, "fact", None), str) and e.fact]
        usage: Usage = self._scope.usage()
        return Retrieved(text="\n".join(units), record_ids=[], units=units, usage=usage)

    def close(self) -> None:
        """Remove this case's graph, close the driver, the loop and the scope. Every step runs; the first error
        is raised at the end (the engine's final close ignores it, a close inside reset reports it)."""
        errors: list[BaseException] = []
        loop, graphiti, driver, group = self._loop, self._graphiti, self._driver, self._group
        self._graphiti = self._driver = None
        if loop is not None and driver is not None and group is not None:
            async def drop() -> None:
                if group in await driver.client.list_graphs():
                    await driver.client.select_graph(group).delete()
            try:
                with self._lock:
                    loop.run_until_complete(drop())
            except Exception as exc:  # noqa: BLE001 - raised below, after the rest of the cleanup
                errors.append(exc)
        if loop is not None and graphiti is not None:
            try:
                with self._lock:
                    loop.run_until_complete(graphiti.close())
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        http, self._http = self._http, []
        if loop is not None:
            for client in http:
                if client is not None and hasattr(client, "close"):
                    try:
                        with self._lock:
                            loop.run_until_complete(client.close())
                    except Exception as exc:  # noqa: BLE001
                        errors.append(exc)
            try:
                with self._lock:
                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()
                self._loop = None
        if self._scope is not None:
            self._scope.close()
            self._scope = None
        self._group = None
        self._episodes = 0
        if errors:
            raise errors[0]
