"""LangMem adapter. Part one: a fake package and a fake proxy scope (the contract, the config, the chunking rule),
which runs without langmem installed. Part two: the real package at the pinned version against a real
ProductProxy over the local fake upstream (tests/proxy_fakes.py); it skips when langmem is not importable.
No model call, no network beyond the loopback interface."""

from __future__ import annotations

import importlib.util
import json
import re
import zlib
from types import SimpleNamespace

import pytest
import proxy_fakes
from proxy_fakes import FakeUpstream, memory_side

from memory_bench.adapters.base import Record, Usage
from memory_bench.adapters.langmem import CHAT_MODEL, DEFAULTS, EMBED_MODEL, PINNED, LangMemAdapter, memory_text
from memory_bench.engine.run import RunError, _build_adapters, needs_proxy

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


class FakeChat:
    def __init__(self, **kw):
        self.kw = kw


class FakeEmbed:
    def __init__(self, **kw):
        self.kw = kw


class FakeStore:
    def __init__(self, index):
        self.index, self.searches = index, []

    def search(self, ns, query, limit):
        self.searches.append((ns, query, limit))
        mk = lambda c: SimpleNamespace(value={"kind": "Memory", "content": {"content": c}})  # noqa: E731
        return [mk("first fact"), SimpleNamespace(value={"kind": "Memory", "content": {}}), mk(""), mk("second fact")]


class FakeManager:
    def __init__(self, model, namespace, store):
        self.model, self.namespace, self.store, self.invokes = model, namespace, store, []

    def invoke(self, payload):
        self.invokes.append(payload)


class FakePackage:
    made: list = []

    ChatOpenAI = FakeChat
    OpenAIEmbeddings = FakeEmbed
    InMemoryStore = FakeStore

    @staticmethod
    def create_memory_store_manager(model, namespace, store):
        FakePackage.made.append(FakeManager(model, namespace, store))
        return FakePackage.made[-1]


RECORDS = [Record(id=f"e{i:04d}", date=f"2012-01-{i % 28 + 1:02d}", text=f"e{i:04d} 2012-01-{i % 28 + 1:02d} Customer paid {i}.00.")
           for i in range(60)]


@pytest.fixture
def adapter(monkeypatch):
    FakePackage.made = []
    monkeypatch.setattr(LangMemAdapter, "_package", lambda self: FakePackage)

    def build(**config):
        return LangMemAdapter(proxy=FakeProxy(), **config)
    return build


def test_defaults_and_config_errors():
    a = LangMemAdapter(proxy=FakeProxy())
    assert a.config == DEFAULTS and a.needs_proxy and not a.reference and a.config_label == "default"
    assert a.name == "langmem" and a.product_version == PINNED == "0.0.30"
    assert DEFAULTS["records_per_chunk"] == 25 and DEFAULTS["search_limit"] == 60 and DEFAULTS["allow_unpinned"] is False
    with pytest.raises(ValueError, match="unknown config keys"):
        LangMemAdapter(proxy=FakeProxy(), chunk=5)
    with pytest.raises(ValueError, match="needs the product proxy"):
        LangMemAdapter()
    with pytest.raises(ValueError, match="at least 1"):
        LangMemAdapter(proxy=FakeProxy(), records_per_chunk=0)
    with pytest.raises(ValueError, match="at least 1"):
        LangMemAdapter(proxy=FakeProxy(), search_limit=0)


def test_missing_or_wrong_version_is_an_error(monkeypatch):
    from importlib import metadata

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, "version", missing)
    with pytest.raises(RuntimeError, match="not installed"):
        LangMemAdapter(proxy=FakeProxy())._package()
    monkeypatch.setattr(metadata, "version", lambda name: "0.0.31")
    with pytest.raises(RuntimeError, match="pinned version is 0.0.30"):
        LangMemAdapter(proxy=FakeProxy())._package()


def test_tracing_is_off_before_import(monkeypatch):
    from importlib import metadata

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "x")
    monkeypatch.setattr(metadata, "version", lambda name: "9.9.9")
    import os

    with pytest.raises(RuntimeError):  # the version check fails after the environment is set, before any import
        LangMemAdapter(proxy=FakeProxy())._package()
    assert os.environ["LANGSMITH_TRACING"] == os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert "LANGSMITH_API_KEY" not in os.environ


def test_chunks_text_and_view(adapter):
    a = adapter()
    a.reset("h11-0003")
    scope, mgr = a.proxy.scopes[0], FakePackage.made[0]
    assert (mgr.model.kw["model"], mgr.model.kw["streaming"]) == (CHAT_MODEL, False)
    assert mgr.model.kw["base_url"] == scope.base_url and mgr.model.kw["api_key"] == "bench-local"
    assert set(mgr.model.kw) == {"model", "base_url", "api_key", "streaming"}  # no sampling settings
    emb = mgr.store.index["embed"]
    assert emb.kw["model"] == EMBED_MODEL and emb.kw["base_url"] == scope.base_url
    assert emb.kw["check_embedding_ctx_length"] is False and mgr.store.index["dims"] == 768
    assert mgr.namespace == ("bench", "h11-0003")
    a.ingest(RECORDS)
    assert [len(p["messages"]) for p in mgr.invokes] == [25, 25, 10]
    flat = [m for p in mgr.invokes for m in p["messages"]]
    assert flat == [{"role": "user", "content": r.text} for r in RECORDS]  # unchanged text, history order
    got = a.retrieve("Why is the statement higher?", 1000)
    assert mgr.store.searches == [(("bench", "h11-0003"), "Why is the statement higher?", 60)]
    assert got.units == ["first fact", "second fact"] and got.text == "first fact\nsecond fact" and got.record_ids == []
    assert got.usage.model_calls == 3 and got.usage.embed_calls == 4  # measured by the proxy scope
    assert scope.phases == ["ingest", "retrieve"]
    a.close()
    assert scope.closed and a._manager is None and a._store is None
    a.close()  # idempotent


def test_chunk_size_is_config(adapter):
    a = adapter(records_per_chunk=10, search_limit=5, config_label="c10")
    a.reset("c1")
    a.ingest(RECORDS[:25])
    assert [len(p["messages"]) for p in FakePackage.made[0].invokes] == [10, 10, 5]
    a.retrieve("q", 100)
    assert FakePackage.made[0].store.searches[0][2] == 5 and a.proxy.scopes[0].base_url.endswith("langmem.c10.c1/v1")
    a.close()


def test_reset_starts_a_fresh_store_scope_and_namespace(adapter):
    a = adapter()
    a.reset("c1")
    a.ingest(RECORDS[:1])
    a.reset("c2")
    assert len(FakePackage.made) == 2 and a.proxy.scopes[0].closed and not a.proxy.scopes[1].closed
    assert FakePackage.made[1].invokes == [] and FakePackage.made[1].store is not FakePackage.made[0].store
    assert FakePackage.made[1].namespace == ("bench", "c2")
    a.close()


def test_memory_text_shapes():
    mk = lambda v: SimpleNamespace(value=v)  # noqa: E731
    assert memory_text(mk({"kind": "Memory", "content": {"content": "x y"}})) == "x y"
    assert memory_text(mk({"kind": "Memory", "content": {}})) == ""
    assert memory_text(mk({"kind": "Memory", "content": {"content": 5}})) == ""
    assert memory_text(mk("plain")) == "" and memory_text(object()) == ""


def test_engine_builds_it_from_a_config_spec():
    spec = {"adapter": "langmem", "config_label": "c10", "config": {"records_per_chunk": 10}}
    a = LangMemAdapter(proxy=FakeProxy(), config_label="c10", records_per_chunk=10)
    assert a.config["records_per_chunk"] == 10 and a.config_label == "c10"
    assert spec["adapter"] == "langmem"
    if "langmem" in __import__("memory_bench.adapters", fromlist=["ADAPTERS"]).ADAPTERS:  # after registration
        assert needs_proxy(spec)
        with pytest.raises(RunError, match="needs the product proxy"):
            _build_adapters([spec])


# -- part two: the real package -----------------------------------------------------------------

real = pytest.mark.skipif(importlib.util.find_spec("langmem") is None, reason="langmem is not installed")


def _fake_chat(req, *a, **k):
    """Answer a memory-manager call with one tool call that stores a memory (the default `Memory` schema)."""
    names = [t["function"]["name"] for t in req.get("tools") or []]
    text = "\n".join(str(m["content"]) for m in req["messages"] if m["role"] == "user")
    first = re.search(r"^(e\d{4} \d{4}-\d{2}-\d{2} .*)$", text, re.M)
    memory = "Remembered: " + (first.group(1) if first else "nothing")
    calls = [{"id": "call_1", "type": "function",
              "function": {"name": "Memory" if "Memory" in names else names[0], "arguments": json.dumps({"content": memory})}}]
    return {"id": "chatcmpl-fake", "object": "chat.completion", "model": req.get("model"),
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": calls},
                         "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": len(text) // 3, "completion_tokens": 20, "total_tokens": len(text) // 3 + 20}}


def _fake_embed(req, usage=None):
    inp = req.get("input")
    items = inp if isinstance(inp, list) else [inp]
    vec = lambda s: [((zlib.crc32(f"{s}|{j}".encode()) % 2000) - 1000) / 1000 for j in range(768)]  # noqa: E731
    return {"object": "list", "model": req.get("model"),
            "data": [{"object": "embedding", "index": i, "embedding": vec(x)} for i, x in enumerate(items)],
            "usage": {"prompt_tokens": 5 * len(items), "total_tokens": 5 * len(items)}}


@pytest.fixture
def live(tmp_path, monkeypatch):
    from memory_bench.gateway.proxy import ProductProxy

    monkeypatch.setattr(proxy_fakes, "chat_reply", _fake_chat)
    monkeypatch.setattr(proxy_fakes, "embed_reply", _fake_embed)
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


@real
def test_real_package_end_to_end_through_the_proxy(live):
    a = LangMemAdapter(proxy=live.proxy)
    a.reset("case-1")
    a.ingest(RECORDS[:50])
    got = a.retrieve("Why is the statement higher?", 1000)
    assert a.product_version == PINNED
    chats, embeds = live.up.count("chat"), live.up.count("embeddings")
    assert chats >= 1 and embeds >= 1
    assert chats == 2  # 50 records at 25 per chunk: one model call per chunk
    log = [c for c in live.log() if c["case_id"] == "case-1"]
    assert {c["adapter"] for c in log} == {"langmem"} and {c["phase"] for c in log} == {"ingest", "retrieve"}
    assert {c["route"] for c in log} == {"chat", "embeddings"}
    # the pinned model reached the upstream, the placeholder never did
    assert {r["body"]["model"] for r in live.up.requests if r["route"] == "chat"} == {"upstream-chat-1"}
    assert {r["body"]["model"] for r in live.up.requests if r["route"] == "embeddings"} == {"upstream-embed-1"}
    assert {c["requested_model"] for c in log if c["route"] == "chat"} == {CHAT_MODEL}
    assert {c["requested_model"] for c in log if c["route"] == "embeddings"} == {EMBED_MODEL}
    # what the real client sent
    first, later = [r["body"] for r in live.up.requests if r["route"] == "chat"][:2]
    assert "tool_choice" not in first and later["tool_choice"] == "required"
    assert first["stream"] is False and "parallel_tool_calls" not in first and "response_format" not in first
    assert {t["function"]["name"] for t in first["tools"]} == {"Memory"}
    prompt = [m["content"] for m in first["messages"] if m["role"] == "user"][-1]
    assert all(r.text in prompt for r in RECORDS[:25]) and RECORDS[25].text not in prompt  # text unchanged, chunk of 25
    assert prompt.index(RECORDS[0].text) < prompt.index(RECORDS[24].text)
    # the view: the stored memories, nothing added
    assert got.text.strip() and got.units and all(u.startswith("Remembered: e00") for u in got.units)
    assert got.text == "\n".join(got.units) and got.record_ids == []
    # usage is the proxy's measure
    assert got.usage.model_calls == sum(c["route"] == "chat" for c in log)
    assert got.usage.embed_calls == sum(c["route"] == "embeddings" for c in log)
    assert got.usage.input_tokens == sum(c["input_tokens"] for c in log) and got.usage.output_tokens > 0
    # another case sees nothing of the first
    a.reset("case-2")
    assert a.retrieve("Why is the statement higher?", 1000).text == ""
    a.close()
    a.close()
