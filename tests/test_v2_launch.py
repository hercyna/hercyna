"""v2 launch tests: strict CSP, external assets, terms gates, newsletter/analytics toggles,
OG PNG share cards, neutral badges, downloads, feeds, permalinks, and Railway package hygiene.
"""
from __future__ import annotations

import csv
import io
import json
import re
import struct
from html.parser import HTMLParser
from pathlib import Path

import pytest

from memory_bench.site import build as site_build
from memory_bench.site.cli import STRICT_CSP

REPO = Path(__file__).resolve().parents[1]


class _StrictAuditParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.inline_scripts: int = 0
        self.style_tags: int = 0
        self.inline_style_attrs: int = 0
        self.on_attrs: list[str] = []
        self.links: list[str] = []
        self.external_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        adict = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "src" not in adict and adict.get("type") != "application/ld+json":
            self.inline_scripts += 1
        if tag == "style":
            self.style_tags += 1
        if "style" in adict:
            self.inline_style_attrs += 1
        for k in adict:
            if k.startswith("on"):
                self.on_attrs.append(f"<{tag} {k}>")
        for k in ("href", "src", "poster"):
            if k in adict and adict[k]:
                val = adict[k]
                if val.startswith(("http://", "https://", "//")):
                    self.external_urls.append(val)
                elif not val.startswith(("data:", "mailto:")):
                    self.links.append(val)


@pytest.fixture(scope="module")
def built_r14(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("site_r14")
    site_build.build(REPO, out, built_at="2026-11-25T19:10:00+00:00", site_url="https://hercyna.org")
    return out


def test_r14_zero_inline_scripts_styles_and_valid_csp(built_r14: Path) -> None:
    assert "unsafe-inline" not in STRICT_CSP
    assert "unsafe-eval" not in STRICT_CSP
    assert "frame-ancestors 'none'" in STRICT_CSP

    caddy = (REPO / "Caddyfile").read_text(encoding="utf-8")
    assert "unsafe-inline" not in caddy
    assert "unsafe-eval" not in caddy
    for hdr in (
        "Content-Security-Policy",
        "Strict-Transport-Security",
        "X-Content-Type-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "X-Frame-Options",
        "/healthz",
    ):
        assert hdr in caddy, f"Caddyfile missing {hdr}"

    htmls = sorted(built_r14.rglob("*.html"))
    assert len(htmls) == 37  # 22 root + 15 under v2/
    for hp in htmls:
        p = _StrictAuditParser()
        p.feed(hp.read_text(encoding="utf-8"))
        assert p.inline_scripts == 0, f"inline <script> in {hp}"
        assert p.style_tags == 0, f"inline <style> in {hp}"
        assert p.inline_style_attrs == 0, f"inline style= attr in {hp}"
        assert not p.on_attrs, f"inline on* attr in {hp}: {p.on_attrs}"
        # Zero open redirects or broken local links
        for link in p.links:
            path_part = link.split("#", 1)[0].split("?", 1)[0]
            if not path_part:
                continue
            resolved = (hp.parent / path_part).resolve()
            assert resolved.exists(), f"missing asset/link target {link} referenced by {hp.relative_to(built_r14)}"


def test_r14_legal_pages_disclaimer_and_terms_checkbox_gates(built_r14: Path) -> None:
    for fname in ("TERMS.md", "LEGAL.md", "PRIVACY.md", "SECURITY.md", "CODE_OF_CONDUCT.md"):
        assert (REPO / fname).is_file()

    for page in ("terms.html", "legal.html", "privacy.html", "corrections.html", "changelog.html", "404.html"):
        assert (built_r14 / page).is_file()

    terms_html = (built_r14 / "terms.html").read_text(encoding="utf-8")
    assert "experimental research benchmark" in terms_html.lower()
    assert "corrections.html" in terms_html

    idx = (built_r14 / "index.html").read_text(encoding="utf-8")
    sub = (built_r14 / "submit.html").read_text(encoding="utf-8")
    for txt in (idx, sub):
        assert 'class="disc-bar"' in txt
        assert 'href="terms.html"' in txt
        assert 'href="legal.html"' in txt
        assert 'href="privacy.html"' in txt
        assert 'class="terms-gate"' in txt
        assert 'class="terms-ack"' in txt
        assert 'data-terms-target="' in txt
        assert 'checked' not in re.search(r'<input[^>]*class="terms-ack"[^>]*>', txt).group(0)


def test_r14_updates_newsletter_and_analytics_env_gates(tmp_path: Path) -> None:
    # 1. Default off: Email coming soon, no form, no third-party analytics script
    out_off = tmp_path / "off"
    site_build.build(REPO, out_off, built_at="2026-11-25T19:10:00+00:00", newsletter_action="", analytics_src="")
    idx_off = (out_off / "index.html").read_text(encoding="utf-8")
    assert "Email coming soon" in idx_off
    assert "<form" not in idx_off
    assert 'href="feed.xml"' in idx_off
    assert "Watch releases" in idx_off

    # 2. Malicious newsletter/analytics URL rejected cleanly
    out_bad = tmp_path / "bad"
    site_build.build(
        REPO,
        out_bad,
        built_at="2026-11-25T19:10:00+00:00",
        newsletter_action='javascript:alert(1)"',
        analytics_src="http://insecure.invalid/a.js",
    )
    idx_bad = (out_bad / "index.html").read_text(encoding="utf-8")
    assert "<form" not in idx_bad and "javascript:" not in idx_bad and "insecure.invalid" not in idx_bad

    # 3. Valid HTTPS newsletter form & cookie-free analytics emitted cleanly
    out_on = tmp_path / "on"
    site_build.build(
        REPO,
        out_on,
        built_at="2026-11-25T19:10:00+00:00",
        newsletter_action="https://buttondown.com/api/emails/embed-subscribe/hercyna",
        analytics_src="https://stats.hercyna.org/js/script.js",
    )
    idx_on = (out_on / "index.html").read_text(encoding="utf-8")
    assert 'action="https://buttondown.com/api/emails/embed-subscribe/hercyna"' in idx_on
    assert 'name="website"' in idx_on  # honeypot
    assert 'type="checkbox" name="consent"' in idx_on
    assert 'checked' not in re.search(r'<input[^>]*name="consent"[^>]*>', idx_on).group(0)
    assert '<script defer src="https://stats.hercyna.org/js/script.js"></script>' in idx_on


def test_r14_launch_features_og_png_badges_downloads_and_well_known(built_r14: Path) -> None:
    # PNG share cards exist and are valid 1200x630 PNGs
    for slug in ("index", "chat", "v1", "coding", "method", "submit", "changelog", "corrections", "default"):
        png = (built_r14 / "og" / f"{slug}.png").read_bytes()
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        w, h = struct.unpack(">II", png[16:24])
        assert (w, h) == (1200, 630)
        assert (built_r14 / "og" / f"{slug}.svg").is_file()

    # Meta tags on index and v2 permalink
    idx = (built_r14 / "index.html").read_text(encoding="utf-8")
    v2_idx = (built_r14 / "v2" / "index.html").read_text(encoding="utf-8")
    assert '<link rel="canonical" href="https://hercyna.org/">' in idx
    # While v2 is current, the /v2/ copy points its canonical at the root page (one indexed URL per page).
    assert '<link rel="canonical" href="https://hercyna.org/">' in v2_idx
    assert '<meta property="og:image" content="https://hercyna.org/og/index.png">' in idx
    assert '<meta name="twitter:card" content="summary_large_image">' in idx
    assert 'id="coming-next"' in idx and "Coding agents (Mode A), in preparation" in idx
    assert 'id="explainer"' in idx and "media/explainer-poster.svg" in idx and "media/explainer.vtt" in idx

    # Neutral badges (never rank, score, or "best")
    badges = sorted((built_r14 / "badge").glob("*.svg"))
    assert len(badges) == 8
    for b in badges:
        svg = b.read_text(encoding="utf-8")
        assert "Hercyna v2" in svg and "Evaluated" in svg
        assert "best" not in svg.lower() and "rank" not in svg.lower() and "%" not in svg

    # Product page links badge to /v2/product-<slug>.html
    pg = (built_r14 / "product-graphiti.html").read_text(encoding="utf-8")
    assert "](https://hercyna.org/v2/product-graphiti.html)" in pg

    # Downloads with protocol hash in filenames + Atom feed + well-known basics
    csv_path = built_r14 / "data" / "hercyna-v2-chat-68af8d8e.csv"
    json_path = built_r14 / "data" / "hercyna-v2-chat-68af8d8e.json"
    assert csv_path.is_file() and json_path.is_file()
    rows = list(csv.DictReader(io.StringIO(csv_path.read_text(encoding="utf-8"))))
    assert len(rows) == 40
    jdata = json.loads(json_path.read_text(encoding="utf-8"))
    assert jdata["disclaimer"] == "Experimental results. See TERMS.md." and jdata["n_cells"] == 40

    assert "<feed " in (built_r14 / "feed.xml").read_text(encoding="utf-8")
    assert "Sitemap: https://hercyna.org/sitemap.xml" in (built_r14 / "robots.txt").read_text(encoding="utf-8")
    smap = (built_r14 / "sitemap.xml").read_text(encoding="utf-8")
    # The sitemap lists canonical pages only: the root chat page, no /v2/ copies, no 404 page.
    assert "https://hercyna.org/chat.html" in smap and "/v2/" not in smap and "404.html" not in smap
    sec = (built_r14 / ".well-known" / "security.txt").read_text(encoding="utf-8")
    assert "Contact:" in sec and "Expires:" in sec
    for fav in ("favicon.ico", "favicon.svg", "favicon-32x32.png", "apple-touch-icon.png"):
        assert (built_r14 / fav).is_file()
