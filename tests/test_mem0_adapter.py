"""Mem0 adapter against a fake package and a fake proxy scope: the contract, the config and the chunking rule.
The real package is exercised by the live five-case probe, not here."""

from __future__ import annotations

import os

import pytest

from memory_bench.adapters.base import Record, Usage
from memory_bench.adapters.mem0 import CHAT_MODEL, DEFAULTS, EMBED_MODEL, Mem0Adapter
from memory_bench.engine.run import RunError, _build_adapters, needs_proxy


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


class FakeMemory:
    made: list = []

    def __init__(self, cfg):
        self.cfg, self.adds, self.searches, self.closed = cfg, [], [], False

    @classmethod
    def from_config(cls, cfg):
        cls.made.append(cls(cfg))
        return cls.made[-1]

    def add(self, payload, **kw):
        self.adds.append((payload, kw))

    def search(self, query, **kw):
        self.searches.append((query, kw))
        return {"results": [{"memory": "first fact"}, {"memory": ""}, {"memory": "second fact"}]}

    def close(self):
        self.closed = True


RECORDS = [Record(id=f"e{i:04d}", date=f"2012-01-{i + 1:02d}", text=f"e{i:04d} 2012-01-{i + 1:02d} Customer paid {i}.00.")
           for i in range(5)]


@pytest.fixture
def adapter(monkeypatch):
    FakeMemory.made = []
    monkeypatch.setattr(Mem0Adapter, "_package", lambda self: FakeMemory)

    def build(**config):
        return Mem0Adapter(proxy=FakeProxy(), **config)
    return build


def test_defaults_and_config_errors():
    a = Mem0Adapter(proxy=FakeProxy())
    assert a.config == DEFAULTS and a.needs_proxy and not a.reference and a.config_label == "default"
    with pytest.raises(ValueError, match="unknown config keys"):
        Mem0Adapter(proxy=FakeProxy(), topk=5)
    with pytest.raises(ValueError, match="needs the product proxy"):
        Mem0Adapter()
    with pytest.raises(ValueError, match="at least 1"):
        Mem0Adapter(proxy=FakeProxy(), records_per_add=0)


def test_one_add_per_record_and_view(adapter):
    assert DEFAULTS["records_per_add"] == 25  # the protocol's chunk rule
    a = adapter(records_per_add=1)
    a.reset("h11-0003")
    scope, mem = a.proxy.scopes[0], FakeMemory.made[0]
    llm, emb, store = mem.cfg["llm"]["config"], mem.cfg["embedder"]["config"], mem.cfg["vector_store"]["config"]
    assert (llm["model"], emb["model"]) == (CHAT_MODEL, EMBED_MODEL)  # placeholders: the proxy pins the real models
    assert llm["openai_base_url"] == emb["openai_base_url"] == scope.base_url and llm["api_key"] == "bench-local"
    assert "temperature" not in llm and "is_reasoning_model" not in llm  # package defaults stay
    assert store["embedding_model_dims"] == 768 and os.path.isdir(os.path.dirname(store["path"]))
    a.ingest(RECORDS)
    assert [p for p, _ in mem.adds] == [r.text for r in RECORDS]  # rendered text, unchanged, history order
    assert all(kw["user_id"] == "h11-0003" and kw["infer"] is True for _, kw in mem.adds)
    assert mem.adds[2][1]["metadata"] == {"created_at": "2012-01-03T00:00:00+00:00"}
    got = a.retrieve("Why is the statement higher?", 1000)
    assert mem.searches == [("Why is the statement higher?", {"filters": {"user_id": "h11-0003"}, "top_k": 60, "threshold": 0.1})]
    assert got.units == ["first fact", "second fact"] and got.text == "first fact\nsecond fact" and got.record_ids == []
    assert got.usage.model_calls == 3 and got.usage.embed_calls == 4  # measured by the proxy scope
    assert scope.phases == ["ingest", "retrieve"]
    tmp = os.path.dirname(store["path"])
    a.close()
    assert mem.closed and scope.closed and not os.path.exists(tmp)
    a.close()  # idempotent


def test_chunking_rule_and_reasoning_flag(adapter):
    a = adapter(records_per_add=2, reasoning_model=True, config_label="k2")
    a.reset("c1")
    mem = FakeMemory.made[0]
    assert mem.cfg["llm"]["config"]["is_reasoning_model"] is True
    a.ingest(RECORDS)
    assert [len(p) if isinstance(p, list) else 1 for p, _ in mem.adds] == [2, 2, 1]
    assert mem.adds[0][0] == [{"role": "user", "content": RECORDS[0].text}, {"role": "user", "content": RECORDS[1].text}]
    assert mem.adds[1][1]["metadata"]["created_at"].startswith("2012-01-04")  # the chunk's last record
    a.close()


def test_reset_starts_a_fresh_store_and_scope(adapter):
    a = adapter()
    a.reset("c1")
    a.ingest(RECORDS[:1])
    a.reset("c2")
    assert len(FakeMemory.made) == 2 and FakeMemory.made[0].closed and a.proxy.scopes[0].closed
    assert FakeMemory.made[1].adds == []
    a.close()


def test_engine_builds_it_from_a_config_spec(monkeypatch):
    spec = {"adapter": "mem0", "config_label": "k10", "config": {"records_per_add": 10}}
    assert needs_proxy(spec) and needs_proxy("mem0") and not needs_proxy("bm25")
    with pytest.raises(RunError, match="needs the product proxy"):
        _build_adapters([spec])
    built = _build_adapters(["bm25", spec, {"adapter": "mem0", "config_label": "k1"}], proxy=FakeProxy())
    assert [(a.name, a.config_label) for a in built] == [("bm25", "default"), ("mem0", "k10"), ("mem0", "k1")]
    assert built[1].config["records_per_add"] == 10
    with pytest.raises(RunError, match="repeat"):
        _build_adapters([spec, spec], proxy=FakeProxy())
    with pytest.raises(RunError, match="adapter config needs"):
        _build_adapters([{"name": "mem0"}], proxy=FakeProxy())
