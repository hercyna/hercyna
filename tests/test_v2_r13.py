"""Round 13 checks: clean v2 index, archived v1 board (v1.html), strict per-page protocol headers and tags."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from memory_bench.site.build import build

REPO = Path(__file__).resolve().parents[1]

CORE_V2_PAGES = (
    "index.html",
    "chat.html",
    "coding.html",
    "method.html",
    "submit.html",
    "terms.html",
    "legal.html",
    "privacy.html",
    "changelog.html",
    "corrections.html",
    "404.html",
    "why.html",
    "about.html",
)
V2_DECIDERS = (
    "haiku-5-5",
    "opencode-deepseek-v4.1-flash",
    "opencode-glm-5.3-flash",
    "opencode-minimax-m3",
    "opencode-qwen3.8-flash",
)
FORBIDDEN_V2_IN_V1 = (
    "opencode-deepseek-v4.1-flash",
    "opencode-glm-5.3-flash",
    "opencode-minimax-m3",
    "opencode-qwen3.8-flash",
    "68af8d8e",
    "2026112302",
    'id="cause-split"',
    'data-protocol="v2"',
)
FORBIDDEN_V1_IN_INDEX = (
    "grok-4-20-r",
    "claude-haiku-4-5",
    "1283645051",
    "99494597",
    "Rule met: 2 pairs separate",
    "Graphiti and Mem0 below LangMem",
    'data-protocol="v1"',
)


@pytest.fixture(scope="module")
def built_r13(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site_r13")
    build(REPO, out, built_at="2026-11-25T16:20:00+00:00")
    return out


def _read(out: Path, name: str) -> str:
    return (out / name).read_text(encoding="utf-8")


def test_r13_all_expected_pages_emitted_and_footer_links_v1(built_r13: Path) -> None:
    product_pages = sorted(p.name for p in built_r13.glob("product-*.html"))
    assert len(product_pages) == 8
    all_pages = (*CORE_V2_PAGES, "v1.html", *product_pages)
    assert len(all_pages) == 22
    for fname in all_pages:
        html = _read(built_r13, fname)
        assert 'href="v1.html"' in html, f"{fname} footer missing link to v1.html"
        assert "\u2014" not in html, f"em dash found in {fname}"
        assert "Rank #" not in html, f"bare rank label found in {fname}"


def test_r13_per_page_protocol_header_and_blocks_strictly_match(built_r13: Path) -> None:
    product_pages = sorted(p.name for p in built_r13.glob("product-*.html"))
    for fname in (*CORE_V2_PAGES, *product_pages):
        html = _read(built_r13, fname)
        m = re.search(r'data-page-protocol="(v[12])"', html)
        assert m and m.group(1) == "v2", f"{fname} missing data-page-protocol=\"v2\""
        assert "Protocol v2 · 68af8d8e" in html, f"{fname} header missing Protocol v2 · 68af8d8e"
        found_protos = set(re.findall(r'data-protocol="(v[12])"', html))
        assert found_protos <= {"v2"}, f"{fname} leaked data-protocol tags: {found_protos}"
        assert 'data-protocol="v1"' not in html, f"{fname} leaked data-protocol=\"v1\""

    v1_html = _read(built_r13, "v1.html")
    m1 = re.search(r'data-page-protocol="(v[12])"', v1_html)
    assert m1 and m1.group(1) == "v1"
    assert "Protocol v1 · 99494597" in v1_html
    found_v1 = set(re.findall(r'data-protocol="(v[12])"', v1_html))
    assert found_v1 == {"v1"}, f"v1.html data-protocol tags must be only v1, got {found_v1}"


def test_r13_index_leads_with_v2_only(built_r13: Path) -> None:
    idx = _read(built_r13, "index.html")
    for did in V2_DECIDERS:
        assert did in idx, f"index.html missing v2 decider {did}"
    assert "300" in idx
    assert "2026112302" in idx
    assert 'id="board"' in idx
    assert 'id="cause-split"' in idx
    assert 'id="how"' in idx
    assert 'id="anatomy"' in idx
    assert "protocol/v2.json" in idx
    assert "Wrong action" in idx
    assert "Withheld action" in idx
    assert "Right action" in idx
    assert "Ingest-error cases left out" in idx
    assert "Graphiti" in idx and "Mem0" in idx
    for bad in FORBIDDEN_V1_IN_INDEX:
        assert bad not in idx, f"index.html must not contain v1 token {bad!r}"


def test_r13_v1_archive_clean_without_v2_results(built_r13: Path) -> None:
    v1 = _read(built_r13, "v1.html")
    assert "v1 archive: account review" in v1
    assert "Protocol v1 · 99494597" in v1
    assert "table seed 1283645051" in v1
    assert "grok-4-20-r" in v1
    assert "Rule met: 2 pairs separate" in v1
    assert "Graphiti and Mem0 below LangMem" in v1
    assert 'href="packs/v1/protocol-v1.json"' in v1
    assert (built_r13 / "packs" / "v1" / "protocol-v1.json").is_file()
    for bad in FORBIDDEN_V2_IN_V1:
        assert bad not in v1, f"v1.html leaked v2 content {bad!r}"


def test_r13_coding_page_uses_v2_protocol_and_states_held(built_r13: Path) -> None:
    coding = _read(built_r13, "coding.html")
    assert 'data-page-protocol="v2"' in coding
    assert "Protocol v2 · 68af8d8e" in coding
    assert "held" in coding.lower()
    assert "1283645051" not in coding
    assert "99494597" not in coding
    assert "grok-4-20-r" not in coding


_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)


class _ProtocolAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_protocol: str | None = None
        self._stack: list[tuple[str, str | None]] = []
        self.mismatches: list[str] = []
        self.missing: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = {k: (v or "") for k, v in attrs}
        if tag == "body":
            self.page_protocol = adict.get("data-page-protocol")
        own_proto = adict.get("data-protocol")
        inherited = own_proto or next((p for _, p in reversed(self._stack) if p), None)
        classes = set(adict.get("class", "").split())
        is_number_container = (
            tag == "table"
            or (tag == "dl" and bool(classes & {"kpis", "params", "dl-tight"}) and "elsewhere" not in classes)
            or bool(classes & {"ci-cell", "num"})
        )
        if is_number_container:
            desc = f"<{tag} class={adict.get('class', '')!r}>"
            if not inherited:
                self.missing.append(desc)
            elif self.page_protocol and inherited != self.page_protocol:
                self.mismatches.append(f"{desc} got {inherited!r} expected {self.page_protocol!r}")
        if own_proto and self.page_protocol and own_proto != self.page_protocol:
            self.mismatches.append(f"<{tag}> explicit {own_proto!r} != page {self.page_protocol!r}")
        if tag not in _VOID_TAGS:
            self._stack.append((tag, inherited))

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        while self._stack:
            top, _ = self._stack.pop()
            if top == tag:
                break


def test_r13_every_number_container_inherits_matching_protocol_tag(built_r13: Path) -> None:
    for hp in sorted(built_r13.glob("*.html")):
        parser = _ProtocolAuditParser()
        parser.feed(hp.read_text(encoding="utf-8"))
        assert parser.page_protocol in ("v1", "v2"), f"{hp.name} missing body data-page-protocol"
        assert not parser.missing, f"{hp.name} number containers without protocol tag: {parser.missing}"
        assert not parser.mismatches, f"{hp.name} protocol mismatches: {parser.mismatches}"

