"""Graphiti adapter. Part one: a fake package and a fake proxy scope (the contract, the config, the chunking rule,
the private event loop), which runs without graphiti-core installed. Part two: the real package at the pinned
version against a real ProductProxy over the local fake upstream (tests/proxy_fakes.py) and the local FalkorDB
container; it skips when graphiti-core is not importable or the container is not reachable.
No model call, no network beyond the loopback interface."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import socket
import threading
import zlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import proxy_fakes
from proxy_fakes import FakeUpstream, memory_side

from memory_bench.adapters.base import Record, Usage
from memory_bench.adapters.graphiti import (CHAT_MODEL, DEFAULTS, EMBED_MODEL, FALKORDB_IMAGE, PINNED,
                                            GraphitiAdapter, docker_hint, group_id_for, make_noop_reranker)
from memory_bench.engine.run import RunError, _build_adapters, needs_proxy

START = date(2012, 1, 1)
RECORDS = [Record(id=f"e{i:04d}", date=(START + timedelta(days=i)).isoformat(),
                  text=f"e{i:04d} {(START + timedelta(days=i)).isoformat()} Customer paid {i}.00.")
           for i in range(60)]

# -- part one: fakes ----------------------------------------------------------------------------


class FakeScope:
    def __init__(self, adapter, label, case_id):
        self.base_url, self.api_key = f"http://127.0.0.1:9/s/{adapter}.{label}.{case_id}/v1", "bench-local"
        self.phases, self.closed = [], False

    def phase(self, name):
        self.phases.append(name)

    def usage(self):
        return Usage(model_calls=3, input_tokens=30, output_tokens=6, embed_calls=4)

    def close(self):
        self.closed = True


class FakeProxy:
    def __init__(self):
        self.scopes = []

    def scope(self, adapter, label, case_id):
        self.scopes.append(FakeScope(adapter, label, case_id))
        return self.scopes[-1]


class Kw:
    def __init__(self, *a, **kw):
        self.a, self.kw = a, kw


class FakeHttp:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeLLM(Kw):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.client = FakeHttp()


class FakeEmbedder(FakeLLM):
    pass


class FakeFalkorClient:
    def __init__(self):
        self.graphs: set[str] = set()
        self.deleted: list[str] = []

    async def list_graphs(self):
        return sorted(self.graphs)

    def select_graph(self, name):
        client = self

        class G:
            async def delete(self):
                client.graphs.discard(name)
                client.deleted.append(name)
        return G()


class FakeDriver(Kw):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.client = FakeFalkorClient()
        self.closed = False


class FakeGraphiti:
    made: list = []

    def __init__(self, **kw):
        self.kw, self.episodes, self.searches, self.loops = kw, [], [], set()
        self.closed = False
        FakeGraphiti.made.append(self)

    async def build_indices_and_constraints(self):
        self.loops.add(id(asyncio.get_running_loop()))
        self.kw["graph_driver"].client.graphs.add(self.kw["graph_driver"].kw["database"])

    async def add_episode(self, **kw):
        self.loops.add(id(asyncio.get_running_loop()))
        self.episodes.append(kw)

    async def search(self, query, group_ids, num_results):
        self.loops.add(id(asyncio.get_running_loop()))
        self.searches.append((query, group_ids, num_results))
        mk = lambda f: SimpleNamespace(fact=f)  # noqa: E731
        return [mk("first fact"), mk(""), SimpleNamespace(), mk("second fact")]

    async def close(self):
        self.closed = True


class FakeReranker:
    pass


FakePackage = SimpleNamespace(Graphiti=FakeGraphiti, FalkorDriver=FakeDriver, LLMConfig=Kw, OpenAIGenericClient=FakeLLM,
                              OpenAIEmbedder=FakeEmbedder, OpenAIEmbedderConfig=Kw,
                              EpisodeType=SimpleNamespace(text="text"), NoOpReranker=FakeReranker)


@pytest.fixture
def adapter(monkeypatch):
    FakeGraphiti.made = []
    monkeypatch.setattr(GraphitiAdapter, "_package", lambda self: FakePackage)
    monkeypatch.setattr(GraphitiAdapter, "_check_store", lambda self: None)

    def build(**config):
        return GraphitiAdapter(proxy=FakeProxy(), **config)
    return build


def test_defaults_and_config_errors():
    a = GraphitiAdapter(proxy=FakeProxy())
    assert a.config == DEFAULTS and a.needs_proxy and not a.reference and a.config_label == "default"
    assert a.name == "graphiti" and a.product_version == PINNED == "0.31.2"
    assert DEFAULTS["records_per_episode"] == 25 and DEFAULTS["search_limit"] == 60
    assert DEFAULTS["embedding_dims"] == 768 and DEFAULTS["allow_unpinned"] is False
    assert (DEFAULTS["falkordb_host"], DEFAULTS["falkordb_port"]) == ("127.0.0.1", 6390)
    with pytest.raises(ValueError, match="unknown config keys"):
        GraphitiAdapter(proxy=FakeProxy(), records_per_add=5)
    with pytest.raises(ValueError, match="needs the product proxy"):
        GraphitiAdapter()
    for key in ("records_per_episode", "search_limit", "embedding_dims"):
        with pytest.raises(ValueError, match=f"{key} must be at least 1"):
            GraphitiAdapter(proxy=FakeProxy(), **{key: 0})


def test_missing_or_wrong_version_is_an_error(monkeypatch):
    from importlib import metadata

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing)
    with pytest.raises(RuntimeError, match="not installed; run: uv sync --extra graphiti"):
        GraphitiAdapter(proxy=FakeProxy())._package()
    monkeypatch.setattr(metadata, "version", lambda name: "0.31.3")
    with pytest.raises(RuntimeError, match="pinned version is 0.31.2"):
        GraphitiAdapter(proxy=FakeProxy())._package()


def test_telemetry_is_off_before_import(monkeypatch):
    from importlib import metadata
    import os

    monkeypatch.setenv("GRAPHITI_TELEMETRY_ENABLED", "true")
    monkeypatch.setattr(metadata, "version", lambda name: "9.9.9")
    with pytest.raises(RuntimeError):  # the version check fails after the environment is set, before any import
        GraphitiAdapter(proxy=FakeProxy())._package()
    assert os.environ["GRAPHITI_TELEMETRY_ENABLED"] == "false"


def test_unreachable_store_names_the_docker_command():
    with socket.socket() as s:  # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    a = GraphitiAdapter(proxy=FakeProxy(), falkordb_port=port)
    with pytest.raises(RuntimeError, match=re.escape(docker_hint(port))):
        a._check_store()
    assert docker_hint(6390) == f"docker run -d --name bench-falkordb -p 127.0.0.1:6390:6379 {FALKORDB_IMAGE}"
    assert FALKORDB_IMAGE == "falkordb/falkordb:6.0.2"


def test_group_ids_are_valid_and_unique():
    a, b = group_id_for("e25", "h11-0003/x y"), group_id_for("e25", "h11-0003/x y")
    assert a != b and re.fullmatch(r"[A-Za-z0-9_]+", a) and a.startswith("bench_e25_h11_0003_x_y_")


def test_noop_reranker_keeps_order_and_calls_nothing():
    class Base:
        pass
    r = make_noop_reranker(Base)()
    got = asyncio.run(r.rank("q", ["b", "a", "c"]))
    assert [p for p, _ in got] == ["b", "a", "c"] and [s for _, s in got] == sorted((s for _, s in got), reverse=True)


def test_clients_episodes_and_view(adapter):
    a = adapter()
    a.reset("h11-0003")
    scope, g = a.proxy.scopes[0], FakeGraphiti.made[0]
    llm, emb, drv = g.kw["llm_client"], g.kw["embedder"], g.kw["graph_driver"]
    cfg = llm.kw["config"].kw
    assert cfg == {"api_key": "bench-local", "model": CHAT_MODEL, "small_model": CHAT_MODEL, "base_url": scope.base_url}
    assert set(llm.kw) == {"config"}  # no temperature, max_tokens or output-mode change: package defaults
    ecfg = emb.kw["config"].kw
    assert ecfg == {"api_key": "bench-local", "embedding_model": EMBED_MODEL, "embedding_dim": 768,
                    "base_url": scope.base_url}
    assert isinstance(g.kw["cross_encoder"], FakeReranker)
    group = drv.kw["database"]
    assert drv.kw == {"host": "127.0.0.1", "port": 6390, "database": group} and group.startswith("bench_default_h11_0003_")
    assert drv.client.graphs == {group}  # indices built on the new graph
    a.ingest(RECORDS)
    assert len(g.episodes) == 3
    bodies = [e["episode_body"].split("\n") for e in g.episodes]
    assert [len(b) for b in bodies] == [25, 25, 10]
    assert [line for b in bodies for line in b] == [r.text for r in RECORDS]  # unchanged text, history order
    assert [e["reference_time"] for e in g.episodes] == [
        datetime.fromisoformat(RECORDS[i].date).replace(tzinfo=timezone.utc) for i in (24, 49, 59)]
    assert {e["group_id"] for e in g.episodes} == {group} and {e["source"] for e in g.episodes} == {"text"}
    assert [e["name"] for e in g.episodes] == ["episode-1", "episode-2", "episode-3"]
    got = a.retrieve("Why is the statement higher?", 1000)
    assert g.searches == [("Why is the statement higher?", [group], 60)]
    assert got.units == ["first fact", "second fact"] and got.text == "first fact\nsecond fact" and got.record_ids == []
    assert got.usage.model_calls == 3 and got.usage.embed_calls == 4  # measured by the proxy scope
    assert scope.phases == ["ingest", "retrieve"]
    assert len(g.loops) == 1  # one private loop for every call of the case
    loop = a._loop
    a.close()
    assert scope.closed and g.closed and drv.client.graphs == set() and drv.client.deleted == [group]
    assert llm.client.closed and emb.client.closed and loop.is_closed() and a._loop is None
    a.close()  # idempotent


def test_episode_size_is_config(adapter):
    a = adapter(records_per_episode=10, search_limit=5, config_label="e10")
    a.reset("c1")
    a.ingest(RECORDS[:25])
    g = FakeGraphiti.made[0]
    assert [len(e["episode_body"].split("\n")) for e in g.episodes] == [10, 10, 5]
    a.retrieve("q", 100)
    assert g.searches[0][2] == 5 and a.proxy.scopes[0].base_url.endswith("graphiti.e10.c1/v1")
    a.close()


def test_reset_starts_a_fresh_graph_scope_and_loop(adapter):
    a = adapter()
    a.reset("c1")
    a.ingest(RECORDS[:1])
    first_loop, g1 = a._loop, FakeGraphiti.made[0]
    a.reset("c2")
    g2 = FakeGraphiti.made[1]
    assert a.proxy.scopes[0].closed and not a.proxy.scopes[1].closed and first_loop.is_closed()
    assert g1.closed and g1.kw["graph_driver"].client.deleted == [g1.kw["graph_driver"].kw["database"]]
    assert g2.episodes == [] and g2.kw["graph_driver"].kw["database"] != g1.kw["graph_driver"].kw["database"]
    assert a._loop is not first_loop and not a._loop.is_closed() and g2.loops.isdisjoint(g1.loops)
    a.close()


def test_product_errors_pass_through(adapter, monkeypatch):
    a = adapter()
    a.reset("c1")

    async def boom(**kw):
        raise ValueError("schema mismatch from the model")
    monkeypatch.setattr(FakeGraphiti.made[0], "add_episode", boom)
    with pytest.raises(ValueError, match="schema mismatch"):
        a.ingest(RECORDS[:3])
    a.close()


def test_calls_before_reset_are_an_error():
    a = GraphitiAdapter(proxy=FakeProxy())

    async def nothing():
        return None
    with pytest.raises(RuntimeError, match="reset"):
        a._run(nothing())


def test_engine_builds_it_from_a_config_spec():
    spec = {"adapter": "graphiti", "config_label": "e10", "config": {"records_per_episode": 10}}
    a = GraphitiAdapter(proxy=FakeProxy(), config_label="e10", records_per_episode=10)
    assert a.config["records_per_episode"] == 10 and a.config_label == "e10"
    if "graphiti" in __import__("memory_bench.adapters", fromlist=["ADAPTERS"]).ADAPTERS:  # after registration
        assert needs_proxy(spec)
        with pytest.raises(RunError, match="needs the product proxy"):
            _build_adapters([spec])


def test_config_files():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "configs" / "adapters"
    for k in (10, 25, 50):
        spec = json.loads((root / f"graphiti-e{k}.json").read_text())
        assert spec["adapter"] == "graphiti" and spec["config_label"] == f"e{k}"
        assert spec["config"] == {"records_per_episode": k}
        GraphitiAdapter(proxy=FakeProxy(), config_label=spec["config_label"], **spec["config"])


# -- part two: the real package -----------------------------------------------------------------


def _store_up(port: int = DEFAULTS["falkordb_port"]) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


real = pytest.mark.skipif(importlib.util.find_spec("graphiti_core") is None or not _store_up(),
                          reason="graphiti-core is not installed or the bench-falkordb container is not running")

QUERY = "Which payments did the Customer make on the Billing Account?"
FACT = "Customer made the payments listed from {first} to {last} on the Billing Account"


def _minimal(schema: dict, defs: dict):
    """A minimal valid instance of a JSON schema: required keys only, empty arrays, null for optional unions."""
    if "$ref" in schema:
        return _minimal(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return None
    t = schema.get("type")
    if t == "object":
        return {k: _minimal(v, defs) for k, v in schema.get("properties", {}).items() if k in schema.get("required", [])}
    return {"string": "x", "integer": 0, "number": 0, "boolean": False, "array": []}.get(t)


def _fake_chat(req, *a, **k):
    """Answer each Graphiti prompt by the JSON schema it asks for. Entities: Customer and Billing Account. Edges:
    one fact per episode, naming the first and last record id of the CURRENT_MESSAGE. Everything else (dedupe,
    timestamps, summaries): a minimal valid object, which means no duplicates and nothing to change."""
    rf = req.get("response_format") or {}
    js = rf.get("json_schema") or {}
    name, schema = js.get("name"), js.get("schema") or {}
    text = "\n".join(str(m.get("content")) for m in req.get("messages", []))
    if name == "ExtractedEntities":
        obj = {"extracted_entities": [{"name": "Customer", "entity_type_id": 0, "episode_indices": [0]},
                                      {"name": "Billing Account", "entity_type_id": 0, "episode_indices": [0]}]}
    elif name == "ExtractedEdges":
        cur = re.search(r"<CURRENT_MESSAGE>(.*?)</CURRENT_MESSAGE>", text, re.S)
        ids = re.findall(r"^(e\d{4}) ", cur.group(1) if cur else "", re.M)
        obj = {"edges": [{"source_entity_name": "Customer", "target_entity_name": "Billing Account",
                          "relation_type": "PAID", "fact": FACT.format(first=ids[0], last=ids[-1]),
                          "valid_at": None, "invalid_at": None, "episode_indices": [0]}]} if ids else {"edges": []}
    else:
        obj = _minimal(schema, schema.get("$defs", {}))
    content = json.dumps(obj)
    return {"id": "chatcmpl-fake", "object": "chat.completion", "model": req.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": len(text) // 3, "completion_tokens": len(content) // 3,
                      "total_tokens": len(text) // 3 + len(content) // 3}}


def _fake_embed(req, usage=None):
    inp = req.get("input")
    items = inp if isinstance(inp, list) else [inp]
    vec = lambda s: [((zlib.crc32(f"{s}|{j}".encode()) % 2000) - 1000) / 1000 for j in range(768)]  # noqa: E731
    return {"object": "list", "model": req.get("model"),
            "data": [{"object": "embedding", "index": i, "embedding": vec(x)} for i, x in enumerate(items)],
            "usage": {"prompt_tokens": 5 * len(items), "total_tokens": 5 * len(items)}}


def _graphs() -> set[str]:
    from falkordb import FalkorDB

    db = FalkorDB(host="127.0.0.1", port=DEFAULTS["falkordb_port"])
    try:
        return set(db.list_graphs())
    finally:
        db.connection.close()


def _facts(graph: str) -> set[str]:
    from falkordb import FalkorDB

    db = FalkorDB(host="127.0.0.1", port=DEFAULTS["falkordb_port"])
    try:
        rows = db.select_graph(graph).ro_query("MATCH ()-[e:RELATES_TO]->() RETURN e.fact").result_set
        return {r[0] for r in rows}
    finally:
        db.connection.close()


@pytest.fixture
def live(tmp_path, monkeypatch):
    from memory_bench.gateway.proxy import ProductProxy

    monkeypatch.setattr(proxy_fakes, "chat_reply", _fake_chat)
    monkeypatch.setattr(proxy_fakes, "embed_reply", _fake_embed)
    before = _graphs()
    with FakeUpstream() as up:
        cfg = memory_side(up)
        cfg["embeddings"]["dims"] = 768
        proxy = ProductProxy(cfg, run_dir=tmp_path / "run", cache_path=tmp_path / "cache.sqlite", cap_usd=5.0)
        proxy.start()
        try:
            yield SimpleNamespace(up=up, proxy=proxy, log=lambda: [json.loads(x) for x in
                                  (tmp_path / "run" / "memory_calls.jsonl").read_text().splitlines()])
        finally:
            proxy.stop()
            for g in _graphs() - before:  # a failed test must not leave a graph behind
                from falkordb import FalkorDB

                db = FalkorDB(host="127.0.0.1", port=DEFAULTS["falkordb_port"])
                db.select_graph(g).delete()
                db.connection.close()


@real
def test_real_package_end_to_end_through_the_proxy(live):
    before = _graphs()
    a = GraphitiAdapter(proxy=live.proxy)
    a.reset("case-1")
    assert a.product_version == PINNED
    group = a._group
    assert _graphs() - before == {group}  # one new graph for the case
    a.ingest(RECORDS[:50])
    ingest_chats = [r["body"] for r in live.up.requests if r["route"] == "chat"]
    ingest_embeds = live.up.count("embeddings")
    stored = _facts(group)
    # The fake embeddings are random, so cosine finds nothing above the package's floor; BM25 finds the facts.
    got = a.retrieve(QUERY, 1000)

    # chat and embedding calls went through the proxy, for this scope
    log = live.log()
    assert {c["adapter"] for c in log} == {"graphiti"} and {c["case_id"] for c in log} == {"case-1"}
    assert {c["route"] for c in log} == {"chat", "embeddings"} and {c["phase"] for c in log} == {"ingest", "retrieve"}
    assert len(ingest_chats) >= 4 and ingest_embeds >= 2  # at least extract nodes and edges per episode
    # the pinned model reached the upstream, the placeholder never did
    assert {r["body"]["model"] for r in live.up.requests if r["route"] == "chat"} == {"upstream-chat-1"}
    assert {r["body"]["model"] for r in live.up.requests if r["route"] == "embeddings"} == {"upstream-embed-1"}
    assert {c["requested_model"] for c in log if c["route"] == "chat"} == {CHAT_MODEL}
    assert {c["requested_model"] for c in log if c["route"] == "embeddings"} == {EMBED_MODEL}
    # what the real client sent: plain chat completions with a json_schema response format, package sampling defaults
    for body in ingest_chats:
        assert body["response_format"]["type"] == "json_schema" and "strict" not in body["response_format"]["json_schema"]
        assert body["max_tokens"] == 16384 and body["temperature"] == 1
        assert "stream" not in body and "tools" not in body and "logprobs" not in body
    asked = [b["response_format"]["json_schema"]["name"] for b in ingest_chats]
    assert asked.count("ExtractedEntities") == 2 and asked.count("ExtractedEdges") == 2
    edge_prompts = [b for b in ingest_chats if b["response_format"]["json_schema"]["name"] == "ExtractedEdges"]
    current = [re.search(r"<CURRENT_MESSAGE>\n?(.*?)\n?</CURRENT_MESSAGE>", "\n".join(m["content"] for m in b["messages"]),
                         re.S).group(1).strip() for b in edge_prompts]
    assert current == ["\n".join(r.text for r in RECORDS[:25]), "\n".join(r.text for r in RECORDS[25:50])]
    # the view: the product's fact texts, in its order, nothing added
    planted = {FACT.format(first="e0000", last="e0024"), FACT.format(first="e0025", last="e0049")}
    assert stored == planted
    assert got.units and set(got.units) <= stored and len(got.units) == len(set(got.units))
    assert got.text == "\n".join(got.units) and got.record_ids == []
    # usage is the proxy's measure
    assert got.usage.model_calls == sum(c["route"] == "chat" for c in log)
    assert got.usage.embed_calls == sum(c["route"] == "embeddings" for c in log)
    assert got.usage.input_tokens == sum(c["input_tokens"] for c in log) and got.usage.output_tokens > 0
    # another case sees nothing of the first, and the first case's graph is gone
    a.reset("case-2")
    assert group not in _graphs() and a._group != group
    assert a.retrieve(QUERY, 1000).text == ""
    second = a._group
    a.close()
    assert second not in _graphs() and _graphs() == before
    a.close()


@real
def test_real_unreachable_store_is_a_clear_error(live):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    a = GraphitiAdapter(proxy=live.proxy, falkordb_port=port)
    with pytest.raises(RuntimeError, match="docker run -d --name bench-falkordb"):
        a.reset("case-x")
    a.close()


@real
def test_real_adapter_runs_from_a_worker_thread(live):
    """The engine may call an adapter from a thread that is not the main one; the private loop does not care."""
    out: dict = {}

    def work():
        try:
            a = GraphitiAdapter(proxy=live.proxy, records_per_episode=50)
            a.reset("case-t")
            a.ingest(RECORDS[:5])
            out["text"] = a.retrieve(QUERY, 1000).text
            a.close()
        except BaseException as exc:  # noqa: BLE001 - reported in the main thread
            out["error"] = exc
    t = threading.Thread(target=work)
    t.start()
    t.join(120)
    assert "error" not in out, out.get("error")
    assert out["text"] == FACT.format(first="e0000", last="e0004")
