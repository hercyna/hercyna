"""Static launch asset generators (stdlib only): OG PNG/SVG share cards, favicons, badges, feed, data CSV/JSON,
explainer poster/captions, robots.txt, sitemap.xml, and .well-known/security.txt.
"""
from __future__ import annotations

import binascii
import csv
import html
import io
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

E = html.escape

# Minimal 5x7 uppercase/symbol bitmap glyphs for crisp dependency-free PNG share cards
_FONT_5X7: dict[str, tuple[str, ...]] = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ",": ("00000", "00000", "00000", "00000", "00000", "00100", "01000"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "%": ("11001", "11010", "00010", "00100", "01000", "01011", "10011"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "/": ("00001", "00010", "00100", "00100", "01000", "10000", "00000"),
    "'": ("00100", "00100", "01000", "00000", "00000", "00000", "00000"),
    "|": ("00100", "00100", "00100", "00100", "00100", "00100", "00100"),
}


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = binascii.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _encode_rgb_png(width: int, height: int, rgb: bytearray) -> bytes:
    stride = width * 3
    raw = bytearray((stride + 1) * height)
    for y in range(height):
        raw[y * (stride + 1)] = 0
        raw[y * (stride + 1) + 1 : (y + 1) * (stride + 1)] = rgb[y * stride : (y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw), level=6)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _fill_rect(buf: bytearray, w: int, h: int, x0: int, y0: int, rw: int, rh: int, col: tuple[int, int, int]) -> None:
    x1 = max(0, min(w, x0 + rw))
    y1 = max(0, min(h, y0 + rh))
    x0 = max(0, x0)
    y0 = max(0, y0)
    r, g, b = col
    row_bytes = bytes((r, g, b)) * max(0, x1 - x0)
    if not row_bytes:
        return
    for y in range(y0, y1):
        idx = (y * w + x0) * 3
        buf[idx : idx + len(row_bytes)] = row_bytes


def _draw_text(
    buf: bytearray,
    w: int,
    h: int,
    text: str,
    x: int,
    y: int,
    scale: int,
    col: tuple[int, int, int],
) -> None:
    cx = x
    for ch in text.upper():
        glyph = _FONT_5X7.get(ch, _FONT_5X7[" "])
        for row_i, row in enumerate(glyph):
            for col_i, bit in enumerate(row):
                if bit == "1":
                    _fill_rect(buf, w, h, cx + col_i * scale, y + row_i * scale, scale, scale, col)
        cx += 6 * scale


def _wrap_text(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        cand = f"{cur} {word}".strip()
        if len(cand) <= max_chars or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def render_og_png(title: str, finding: str, tag: str) -> bytes:
    """Render a deterministic 1200x630 RGB PNG share card in the Observatory palette."""
    w, h = 1200, 630
    bg = (247, 246, 242)
    paper = (239, 236, 229)
    ink = (18, 23, 22)
    ink2 = (75, 86, 83)
    teal = (11, 102, 109)
    red = (184, 29, 35)
    line = (214, 210, 200)

    buf = bytearray(bytes(bg) * (w * h))
    # Top accent bar & outer editorial frame
    _fill_rect(buf, w, h, 0, 0, w, 12, teal)
    _fill_rect(buf, w, h, 48, 48, w - 96, 76, paper)
    _fill_rect(buf, w, h, 48, 124, w - 96, 2, ink)
    # Left border accent strip inside card
    _fill_rect(buf, w, h, 48, 156, 8, 280, teal)
    # Minimal geometric Lethe-Mnemosyne mark on top-right
    _fill_rect(buf, w, h, 1070, 64, 10, 44, teal)
    _fill_rect(buf, w, h, 1080, 82, 26, 5, ink2)
    _fill_rect(buf, w, h, 1106, 64, 10, 44, ink)

    _draw_text(buf, w, h, "HERCYNA", 72, 72, 4, ink)
    _draw_text(buf, w, h, tag[:36], 340, 77, 3, teal)

    title_lines = _wrap_text(title, 28)[:2]
    ty = 166
    for ln in title_lines:
        _draw_text(buf, w, h, ln, 82, ty, 6, ink)
        ty += 58

    find_lines = _wrap_text(finding, 46)[:3]
    fy = max(ty + 18, 296)
    for ln in find_lines:
        _draw_text(buf, w, h, ln, 82, fy, 4, ink2)
        fy += 42

    _fill_rect(buf, w, h, 48, 520, w - 96, 2, line)
    _draw_text(buf, w, h, "EXPERIMENTAL BENCHMARK - AS IS - SEE TERMS.MD", 72, 548, 3, red)
    return _encode_rgb_png(w, h, buf)


def render_og_svg(title: str, finding: str, tag: str) -> str:
    title_lines = _wrap_text(title, 34)[:2]
    find_lines = _wrap_text(finding, 62)[:3]
    t_svg = "".join(
        f'<text x="84" y="{206 + i * 58}" font-family="Georgia, serif" font-size="48" font-weight="600" fill="#121716">{E(ln)}</text>'
        for i, ln in enumerate(title_lines)
    )
    f_svg = "".join(
        f'<text x="84" y="{336 + i * 38}" font-family="system-ui, sans-serif" font-size="26" font-weight="500" fill="#4b5653">{E(ln)}</text>'
        for i, ln in enumerate(find_lines)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 630" role="img" '
        f'aria-label="{E(title)}: {E(finding)}">'
        '<rect width="1200" height="630" fill="#f7f6f2"/>'
        '<rect width="1200" height="12" fill="#0b666d"/>'
        '<rect x="48" y="48" width="1104" height="76" fill="#efece5" stroke="#d6d2c8"/>'
        '<text x="72" y="96" font-family="system-ui, sans-serif" font-size="24" font-weight="700" letter-spacing="4" fill="#121716">HERCYNA</text>'
        f'<text x="270" y="95" font-family="monospace" font-size="20" font-weight="600" fill="#0b666d">{E(tag)}</text>'
        '<rect x="48" y="160" width="8" height="270" fill="#0b666d"/>'
        f'{t_svg}{f_svg}'
        '<line x1="48" y1="520" x2="1152" y2="520" stroke="#d6d2c8" stroke-width="2"/>'
        '<text x="72" y="562" font-family="monospace" font-size="18" font-weight="600" fill="#b81d23">'
        'Experimental research benchmark. Results provided as is. See TERMS.md.</text>'
        '</svg>\n'
    )


def render_icon_png(size: int) -> bytes:
    """Square dark-teal Hercyna app icon / favicon PNG of `size x size` pixels."""
    bg = (14, 20, 25)
    teal = (63, 211, 220)
    arch = (154, 168, 178)
    buf = bytearray(bytes(bg) * (size * size))
    s = size / 64.0
    _fill_rect(buf, size, size, int(18 * s), int(12 * s), max(2, int(6 * s)), max(3, int(20 * s)), teal)
    _fill_rect(buf, size, size, int(40 * s), int(12 * s), max(2, int(6 * s)), max(4, int(40 * s)), teal)
    _fill_rect(buf, size, size, int(24 * s), int(28 * s), max(2, int(16 * s)), max(2, int(4 * s)), arch)
    for dy, alpha in ((37, teal), (44, teal), (51, arch)):
        _fill_rect(buf, size, size, int(19 * s), int(dy * s), max(2, int(4 * s)), max(2, int(4 * s)), alpha)
    return _encode_rgb_png(size, size, buf)


def png_to_ico(png_bytes: bytes, size: int = 32) -> bytes:
    """Wrap a 32x32 PNG payload inside a valid single-image Windows/browser .ico container."""
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack("<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0, 0, 0, 1, 32, len(png_bytes), 22)
    return header + entry + png_bytes


def render_neutral_badge_svg(display_name: str, proto_label: str = "v2", sha8: str = "68af8d8e") -> str:
    """Neutral participation badge SVG: states protocol evaluation only, never rank or score."""
    left = f"Hercyna {proto_label}"
    right = f"Evaluated - {display_name} ({sha8})"
    lw = max(86, 16 + len(left) * 7)
    rw = max(140, 18 + len(right) * 7)
    tw = lw + rw
    aria = f"{left}: {right}. Exploratory research evaluation without endorsement or score."
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{tw}" height="22" viewBox="0 0 {tw} 22" role="img" '
        f'aria-label="{E(aria)}">'
        f'<title>{E(aria)}</title>'
        f'<rect width="{lw}" height="22" fill="#121716"/>'
        f'<rect x="{lw}" width="{rw}" height="22" fill="#0b666d"/>'
        f'<text x="{lw / 2:.1f}" y="15" text-anchor="middle" font-family="monospace" font-size="11" font-weight="600" fill="#ffffff">{E(left)}</text>'
        f'<text x="{lw + rw / 2:.1f}" y="15" text-anchor="middle" font-family="monospace" font-size="11" font-weight="600" fill="#ffffff">{E(right)}</text>'
        '</svg>\n'
    )


def render_explainer_poster_svg() -> str:
    """Static 1280x720 poster graphic for the 29-second walkthrough video slot."""
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" role="img" '
        'aria-label="Hercyna 29-second walkthrough poster: dated history narrows to a memory view and a five-choice decision">'
        '<rect width="1280" height="720" fill="#f7f6f2"/>'
        '<rect width="1280" height="10" fill="#0b666d"/>'
        '<rect x="64" y="64" width="1152" height="592" fill="#ffffff" stroke="#121716" stroke-width="2"/>'
        '<text x="104" y="136" font-family="Georgia, serif" font-size="44" font-weight="600" fill="#121716">'
        'How one memory view changes an agent decision</text>'
        '<text x="104" y="182" font-family="monospace" font-size="20" fill="#4b5653">'
        '29-second walkthrough · Protocol v2 (68af8d8e) · 300 synthetic multi-session chat cases</text>'
        '<g font-family="monospace" font-size="18" font-weight="600">'
        '<rect x="104" y="250" width="220" height="150" fill="#efece5" stroke="#121716"/>'
        '<text x="124" y="294" fill="#0b666d">01 HISTORY</text>'
        '<text x="124" y="330" font-size="15" font-weight="400" fill="#4b5653">55 to 190 dated turns</text>'
        '<rect x="374" y="250" width="220" height="150" fill="#efece5" stroke="#121716"/>'
        '<text x="394" y="294" fill="#0b666d">02 MEMORY</text>'
        '<text x="394" y="330" font-size="15" font-weight="400" fill="#4b5653">Ingests in order</text>'
        '<rect x="644" y="250" width="220" height="150" fill="#efece5" stroke="#121716"/>'
        '<text x="664" y="294" fill="#0b666d">03 VIEW (1,000t)</text>'
        '<text x="664" y="330" font-size="15" font-weight="400" fill="#4b5653">Blinded adapter output</text>'
        '<rect x="914" y="250" width="262" height="150" fill="#efece5" stroke="#121716"/>'
        '<text x="934" y="294" fill="#b81d23">04 ACTION</text>'
        '<text x="934" y="330" font-size="15" font-weight="400" fill="#4b5653">Right, withheld, wrong</text>'
        '</g>'
        '<text x="104" y="520" font-family="monospace" font-size="20" font-weight="600" fill="#121716">'
        'Read wrong action, withheld action, right action, and left-out ingest errors side by side.</text>'
        '<text x="104" y="566" font-family="monospace" font-size="16" fill="#4b5653">'
        'Video clip will appear here when published by the maintainer. Full captions and transcript are included below.</text>'
        '</svg>\n'
    )


EXPLAINER_VTT = """WEBVTT

00:00.000 --> 00:05.500
Hercyna tests agent memory products by the action an agent chooses after reading a truncated view.

00:05.500 --> 00:11.500
Each multi-session chat case starts with 55 to 190 dated turns across simulated weeks.

00:11.500 --> 00:17.500
Every memory adapter ingests the history in order and returns at most 1,000 view tokens.

00:17.500 --> 00:23.500
Five gate-passing decider models read only that view and select one of five discrete actions.

00:23.500 --> 00:29.000
Every row reports wrong action, withheld action, right action, floor noise, and left-out ingest errors together.
"""


def parse_changelog_entries(changelog_path: Path) -> list[dict[str, Any]]:
    """Parse release headings, dates, and sections from CHANGELOG.md."""
    if not changelog_path.is_file():
        return []
    text = changelog_path.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    cur_sec: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = re.match(r"^##\s+([^\s(]+)\s*\((\d{4}-\d{2}-\d{2})\)", line)
        if m:
            cur = {"version": m.group(1), "date": m.group(2), "note": "", "sections": []}
            entries.append(cur)
            cur_sec = None
            continue
        if cur is None:
            continue
        if line.startswith("### "):
            cur_sec = {"heading": line[4:].strip(), "items": []}
            cur["sections"].append(cur_sec)
        elif line.startswith("- "):
            if cur_sec is None:
                cur_sec = {"heading": "Notes", "items": []}
                cur["sections"].append(cur_sec)
            cur_sec["items"].append(line[2:].strip())
        elif line.strip() and cur_sec is None and not line.startswith("#"):
            cur["note"] = (cur["note"] + " " + line.strip()).strip()
    return entries


def build_atom_feed(entries: list[dict[str, Any]], site_url: str, built_at: str) -> str:
    base = site_url.rstrip("/")
    updated = (entries[0]["date"] + "T00:00:00Z") if entries else built_at
    items_xml: list[str] = []
    for ent in entries:
        ver = ent["version"]
        dt = f"{ent['date']}T00:00:00Z"
        slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", ver).strip("-")
        bullets = [f"{sec['heading']}: " + "; ".join(sec["items"]) for sec in ent["sections"] if sec["items"]]
        summary = " ".join([ent["note"], *bullets]).strip() or f"Hercyna {ver} release notes."
        items_xml.append(
            "<entry>"
            f"<title>Hercyna {E(ver)}</title>"
            f"<id>{E(base)}/changelog.html#{E(slug)}</id>"
            f"<link href=\"{E(base)}/changelog.html#{E(slug)}\"/>"
            f"<updated>{E(dt)}</updated>"
            f"<summary>{E(summary[:1000])}</summary>"
            "</entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        '<title>Hercyna Benchmark Releases</title>'
        f'<id>{E(base)}/feed.xml</id>'
        f'<link href="{E(base)}/"/>'
        f'<link rel="self" href="{E(base)}/feed.xml"/>'
        f'<updated>{E(updated)}</updated>'
        '<author><name>Hercyna Maintainer</name></author>'
        + "".join(items_xml)
        + "</feed>\n"
    )


def build_chat_downloads(loaded: dict, proto_sha8: str) -> dict[str, bytes]:
    """Build CSV and JSON downloads for the 40-cell v2 chat table with protocol hash in the filenames."""
    deciders = loaded.get("deciders") or {}
    rows_out: list[dict[str, Any]] = []

    def _rvals(stat: dict | None) -> tuple[str, str, str]:
        if not stat or stat.get("value") is None:
            return ("", "", "")
        v = f"{100.0 * float(stat['value']):.2f}"
        ci = stat.get("ci95")
        if ci and len(ci) == 2:
            return (v, f"{100.0 * float(ci[0]):.2f}", f"{100.0 * float(ci[1]):.2f}")
        return (v, "", "")

    for did, slot in sorted(deciders.items()):
        fl = slot.get("floor") or {}
        fl_wc, _, _ = _rvals(fl.get("wrong_commit"))
        fl_ab, _, _ = _rvals(fl.get("abstention"))
        fl_ra, _, _ = _rvals(fl.get("right_action"))
        for row in slot.get("rows") or []:
            cell = row.get("cell") or {}
            wc, wc_lo, wc_hi = _rvals(cell.get("wrong_commit"))
            ab, ab_lo, ab_hi = _rvals(cell.get("abstention"))
            ra, ra_lo, ra_hi = _rvals(cell.get("right_action"))
            rows_out.append(
                {
                    "protocol": "v2",
                    "protocol_sha8": proto_sha8,
                    "track": "chat_agent",
                    "decider": did,
                    "system": row["system"],
                    "config_label": row["config_label"],
                    "budget_tokens": row["budget"],
                    "is_baseline": bool(row.get("base")),
                    "n_cases": int(row.get("n_cases") or 0),
                    "gated_cases": int(row.get("gated_cases") or 0),
                    "ingest_errors_left_out": int(row.get("ingest_errors") or 0),
                    "wrong_action_pct": wc,
                    "wrong_action_ci95_low": wc_lo,
                    "wrong_action_ci95_high": wc_hi,
                    "withheld_action_pct": ab,
                    "withheld_action_ci95_low": ab_lo,
                    "withheld_action_ci95_high": ab_hi,
                    "right_action_pct": ra,
                    "right_action_ci95_low": ra_lo,
                    "right_action_ci95_high": ra_hi,
                    "floor_wrong_action_pct": fl_wc,
                    "floor_withheld_action_pct": fl_ab,
                    "floor_right_action_pct": fl_ra,
                    "note": "Experimental results. See TERMS.md.",
                }
            )

    csv_buf = io.StringIO()
    if rows_out:
        writer = csv.DictWriter(csv_buf, fieldnames=list(rows_out[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows_out)

    json_payload = {
        "disclaimer": "Experimental results. See TERMS.md.",
        "protocol": "v2",
        "protocol_sha8": proto_sha8,
        "track": "chat_agent",
        "n_cells": len(rows_out),
        "separation": loaded.get("separation"),
        "cells": rows_out,
    }
    stem = f"hercyna-v2-chat-{proto_sha8}"
    return {
        f"data/{stem}.csv": csv_buf.getvalue().encode("utf-8"),
        f"data/{stem}.json": (json.dumps(json_payload, indent=2) + "\n").encode("utf-8"),
    }


def build_well_known_files(site_url: str, page_paths: list[str], built_at: str) -> dict[str, bytes]:
    base = site_url.rstrip("/")
    day = built_at[:10]
    robots = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )
    urls_xml = "".join(
        f"<url><loc>{E(base)}/{E('' if p == 'index.html' else p)}</loc><lastmod>{E(day)}</lastmod></url>"
        for p in sorted(set(page_paths))
        if p != "404.html"
    )
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{urls_xml}</urlset>\n"
    )
    security_txt = (
        f"Contact: https://github.com/hercyna/hercyna/security/advisories/new\n"
        f"Policy: {base}/legal.html\n"
        f"Canonical: {base}/.well-known/security.txt\n"
        "Preferred-Languages: en\n"
        "Expires: 2027-11-25T00:00:00Z\n"
    )
    return {
        "robots.txt": robots.encode("utf-8"),
        "sitemap.xml": sitemap.encode("utf-8"),
        ".well-known/security.txt": security_txt.encode("utf-8"),
    }
