"""The `bench site` command group (kept apart from memory_bench.cli so the two merge cleanly)."""

from __future__ import annotations

import sys
from pathlib import Path


def _site_build(args) -> int:
    from .build import build

    root = Path(args.root)
    out = Path(args.out) if args.out else root / "site" / "dist"
    res = build(root, out, include_exploratory=args.include_exploratory, built_at=args.built_at)
    for name, size in res["bytes"].items():
        print(f"wrote {Path(res['out']) / name} ({size} bytes)")
    print(f"sources: {', '.join(res['sources']) or '-'}", file=sys.stderr)
    return 0


def add_parser(sub) -> None:
    site = sub.add_parser("site", help="static results site")
    ss = site.add_subparsers(dest="site_command", required=True)
    sb = ss.add_parser("build", help="write index.html, method.html and submit.html")
    sb.add_argument("--root", default=".", help="repo root (default: current directory)")
    sb.add_argument("--out", default=None, help="output directory (default: <root>/site/dist)")
    sb.add_argument("--include-exploratory", action="store_true",
                    help="also show exploratory runs, labelled (local builds only)")
    sb.add_argument("--built-at", default=None, help="ISO time to stamp (for tests)")
    sb.set_defaults(func=_site_build)
