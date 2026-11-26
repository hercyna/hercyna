"""The `bench site` command group (kept apart from memory_bench.cli so the two merge cleanly)."""

from __future__ import annotations

import functools
import gzip
import os
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_COMPRESS_SUFFIXES = {".html", ".css", ".js", ".json", ".svg", ".txt", ".xml", ".csv", ".vtt"}
_IMMUTABLE_SUFFIXES = {".woff2", ".jpg", ".jpeg", ".png", ".mp4", ".webp", ".ico"}

STRICT_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; media-src 'self'; connect-src 'self'; form-action 'self' https:; "
    "base-uri 'self'; object-src 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = (
    ("Content-Security-Policy", STRICT_CSP),
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "accelerometer=(), autoplay=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"),
    ("X-Frame-Options", "DENY"),
)


def _site_build(args) -> int:
    from .build import build

    root = Path(args.root)
    out = Path(args.out) if args.out else root / "site" / "dist"
    res = build(
        root,
        out,
        include_exploratory=args.include_exploratory,
        built_at=args.built_at,
        site_url=getattr(args, "site_url", None),
    )
    for name, size in res["bytes"].items():
        print(f"wrote {Path(res['out']) / name} ({size} bytes)")
    print(f"sources: {', '.join(res['sources']) or '-'}", file=sys.stderr)
    return 0


class _QuietHandler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        return

    def _cache_control_value(self) -> str:
        path_only = self.path.split("?", 1)[0].split("#", 1)[0]
        if path_only == "/healthz":
            return "no-store"
        ext = Path(path_only).suffix.lower()
        if path_only.startswith("/assets/"):
            return "public, max-age=86400, must-revalidate"
        if ext in _IMMUTABLE_SUFFIXES or path_only.startswith(("/fonts/", "/media/", "/og/")):
            return "public, max-age=31536000, immutable"
        return "public, max-age=300, must-revalidate"

    def end_headers(self) -> None:
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        if "Cache-Control" not in getattr(self, "_headers_buffer_seen", set()):
            self.send_header("Cache-Control", self._cache_control_value())
        super().end_headers()

    def _send_healthz(self, *, send_body: bool) -> bool:
        path_only = self.path.split("?", 1)[0].split("#", 1)[0]
        if path_only != "/healthz":
            return False
        body = b'{"ok":true}'
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)
        return True

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        if code == HTTPStatus.NOT_FOUND:
            not_found = Path(self.directory) / "404.html"
            if not_found.is_file():
                raw = not_found.read_bytes()
                self.send_response(HTTPStatus.NOT_FOUND)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(raw)
                return
        super().send_error(code, message, explain)

    def _maybe_send_gzip(self, *, send_body: bool) -> bool:
        accept = (self.headers.get("Accept-Encoding") or "").lower()
        if "gzip" not in accept:
            return False
        fs_path = Path(self.translate_path(self.path))
        if fs_path.is_dir():
            for idx in ("index.html", "index.htm"):
                cand = fs_path / idx
                if cand.is_file():
                    fs_path = cand
                    break
        if not fs_path.is_file() or fs_path.suffix.lower() not in _COMPRESS_SUFFIXES:
            return False
        raw = fs_path.read_bytes()
        gz = gzip.compress(raw, compresslevel=6)
        ctype = self.guess_type(str(fs_path))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(gz)))
        self.send_header("Last-Modified", self.date_time_string(os.stat(fs_path).st_mtime))
        self.end_headers()
        if send_body:
            self.wfile.write(gz)
        return True

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib name
        if self._send_healthz(send_body=False):
            return
        if self._maybe_send_gzip(send_body=False):
            return
        super().do_HEAD()

    def do_GET(self) -> None:  # noqa: N802 - stdlib name
        if self._send_healthz(send_body=True):
            return
        if self._maybe_send_gzip(send_body=True):
            return
        super().do_GET()


class _ReusableServer(ThreadingHTTPServer):
    allow_reuse_address = True


def _resolve_serve_dir(root: Path, dir_arg: str | None, rebuild: bool) -> Path:
    if dir_arg:
        target = Path(dir_arg)
    elif (root / "site" / "preview-v2" / "index.html").is_file():
        target = root / "site" / "preview-v2"
    elif (root / "site" / "dist" / "index.html").is_file():
        target = root / "site" / "dist"
    else:
        target = root / "site" / "preview-v2"
        rebuild = True
    if rebuild or not (target / "index.html").is_file():
        from .build import build

        build(root, target)
    return target


def _site_serve(args) -> int:
    root = Path(args.root).resolve()
    target = _resolve_serve_dir(root, args.dir, args.build).resolve()
    handler = functools.partial(_QuietHandler, directory=str(target))
    srv = _ReusableServer((args.host, int(args.port)), handler)
    print(f"Serving {target} at http://{args.host}:{srv.server_port}/ (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


def add_parser(sub) -> None:
    site = sub.add_parser("site", help="static results site")
    ss = site.add_subparsers(dest="site_command", required=True)
    sb = ss.add_parser("build", help="write index.html, chat.html, product pages, method.html and submit.html")
    sb.add_argument("--root", default=".", help="repo root (default: current directory)")
    sb.add_argument("--out", default=None, help="output directory (default: <root>/site/dist)")
    sb.add_argument("--include-exploratory", action="store_true",
                    help="also show exploratory runs, labelled (local builds only)")
    sb.add_argument("--built-at", default=None, help="ISO time to stamp (for tests)")
    sb.add_argument("--site-url", default=None, help="Canonical origin URL (default HERCYNA_SITE_URL or https://hercyna.org)")
    sb.set_defaults(func=_site_build)

    sv = ss.add_parser("serve", help="serve the built static site over loopback HTTP")
    sv.add_argument("--root", default=".", help="repo root (default: current directory)")
    sv.add_argument("--dir", default=None, help="directory to serve (default: site/preview-v2 or site/dist)")
    sv.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    sv.add_argument("--port", type=int, default=8795, help="port to listen on (default: 8795)")
    sv.add_argument("--build", action="store_true", help="rebuild the site before serving")
    sv.set_defaults(func=_site_serve)

    from ..terms import attach_terms_cli

    attach_terms_cli(sub)
