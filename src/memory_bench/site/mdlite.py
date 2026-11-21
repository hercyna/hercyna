"""The Markdown subset of the copy files: # headings, paragraphs, lists, code spans. Nothing else.

Everything is HTML-escaped first; only the subset is turned into tags.
"""

from __future__ import annotations

import html
import re

_HEAD = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_UL = re.compile(r"^\s*[-*]\s+(.*)$")
_OL = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_CODE = re.compile(r"`([^`\n]+)`")


def inline(text: str) -> str:
    parts = _CODE.split(text)
    out = []
    for i, p in enumerate(parts):
        out.append(f"<code>{html.escape(p)}</code>" if i % 2 else html.escape(p))
    return "".join(out)


def render(md: str) -> str:
    out: list[str] = []
    para: list[str] = []
    items: list[str] = []
    kind = ""

    def flush_para() -> None:
        if para:
            out.append("<p>" + inline(" ".join(s.strip() for s in para)) + "</p>")
            para.clear()

    def flush_list() -> None:
        nonlocal kind
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + f"</{kind}>")
            items.clear()
        kind = ""

    for line in md.splitlines():
        if not line.strip():
            flush_para()
            flush_list()
            continue
        h = _HEAD.match(line)
        if h:
            flush_para()
            flush_list()
            n = len(h.group(1))
            out.append(f"<h{n}>{inline(h.group(2))}</h{n}>")
            continue
        u, o = _UL.match(line), _OL.match(line)
        if u or o:
            flush_para()
            k = "ul" if u else "ol"
            if kind and kind != k:
                flush_list()
            kind = k
            items.append((u or o).group(1))
            continue
        flush_list()
        para.append(line)
    flush_para()
    flush_list()
    return "\n".join(out)
