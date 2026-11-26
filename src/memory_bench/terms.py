"""First-run terms acceptance gate and canonical `TERMS.md` / `LEGAL.md` / `PRIVACY.md` reader.

Kept outside frozen `cli.py` and wired via `site.cli.add_parser(sub)` so `protocol/v2.json` hashes stay untouched.
"""
from __future__ import annotations

import argparse
import functools
import inspect
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

TERMS_VERSION = "2026-11-25-v1"

CORE_POINTS: tuple[str, ...] = (
    "1. Hercyna is an experimental research benchmark. Results are provided as is, with no warranty of any kind.",
    "2. Results describe a fixed protocol, fixed synthetic cases and fixed deciders on a given date. They are not a certification, endorsement, ranking of fitness for any use, or a claim about any product in production.",
    "3. The person using or citing the results is solely responsible for their interpretation and any decision made from them.",
    "4. No liability for any loss arising from use of the software, data, site or results, to the extent the law allows.",
    "5. Product names are trademarks of their owners, used only to identify what was tested. No affiliation or endorsement is implied.",
    "6. Vendors may request a correction or a rerun via the corrections process (corrections.html or .github/ISSUE_TEMPLATE/correction_request.md).",
)


def repo_root() -> Path | None:
    """Locate the repository root when running from a source checkout."""
    here = Path(__file__).resolve()
    for cand in (here.parents[2], Path.cwd()):
        if (cand / "TERMS.md").is_file() and (cand / "LEGAL.md").is_file():
            return cand
    return None


def load_terms_spec(root: Path | None = None) -> tuple[str, list[str]]:
    """Read (version, 6_core_points) from TERMS.md when present, else return bundled constants."""
    r = root or repo_root()
    if r and (r / "TERMS.md").is_file():
        text = (r / "TERMS.md").read_text(encoding="utf-8")
        m = re.search(r"^Terms version:\s*(\S+)", text, re.M)
        version = m.group(1).strip() if m else TERMS_VERSION
        points: list[str] = []
        in_summary = False
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("## Plain-Language Summary"):
                in_summary = True
                continue
            if in_summary and s.startswith("## "):
                break
            if in_summary and re.match(r"^[1-6]\.\s+", s):
                points.append(s)
        if len(points) == 6:
            return version, points
        return version, list(CORE_POINTS)
    return TERMS_VERSION, list(CORE_POINTS)


def current_version(root: Path | None = None) -> str:
    return load_terms_spec(root)[0]


def accepted_path() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else (Path.home() / ".config")
    return base / "hercyna" / "accepted.json"


def is_accepted(version: str | None = None) -> bool:
    want = version or current_version()
    p = accepted_path()
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("terms_version") == want


def record_acceptance(version: str | None = None) -> Path:
    """Store only terms_version and accepted_date. Never records a name, email, or host identity."""
    ver = version or current_version()
    p = accepted_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(p.parent, 0o700)
    except OSError:
        pass
    payload = {
        "terms_version": ver,
        "accepted_date": date.today().isoformat(),
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def print_notice(root: Path | None = None) -> None:
    ver, points = load_terms_spec(root)
    r = root or repo_root()
    terms_ref = str(r / "TERMS.md") if r and (r / "TERMS.md").is_file() else "TERMS.md"
    legal_ref = str(r / "LEGAL.md") if r and (r / "LEGAL.md").is_file() else "LEGAL.md"
    print(f"Hercyna CLI notice (terms version {ver}):", file=sys.stderr)
    for pt in points:
        print(f"  {pt}", file=sys.stderr)
    print(f"Read full terms: {terms_ref} and {legal_ref}", file=sys.stderr)


def ensure_terms_accepted(args: argparse.Namespace) -> bool:
    """Verify prior acceptance, --accept-terms, HERCYNA_ACCEPT_TERMS=1, or interactive 'accept' prompt."""
    if getattr(args, "accept_terms", False):
        return True
    if os.environ.get("HERCYNA_ACCEPT_TERMS", "").strip() == "1":
        return True
    ver = current_version()
    if is_accepted(ver):
        return True

    print_notice()
    if not sys.stdin.isatty():
        print(
            "Non-interactive run blocked: pass --accept-terms or set HERCYNA_ACCEPT_TERMS=1 "
            "after reading TERMS.md and LEGAL.md.",
            file=sys.stderr,
        )
        return False

    try:
        print("Type 'accept' to continue: ", end="", flush=True, file=sys.stderr)
        reply = sys.stdin.readline()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return False

    if reply.strip().lower() != "accept":
        print("Terms not accepted. Exiting.", file=sys.stderr)
        return False

    record_acceptance(ver)
    return True


def full_terms_text(root: Path | None = None) -> str:
    r = root or repo_root()
    if r and (r / "TERMS.md").is_file():
        parts = [(r / "TERMS.md").read_text(encoding="utf-8").strip()]
        if (r / "LEGAL.md").is_file():
            parts.append((r / "LEGAL.md").read_text(encoding="utf-8").strip())
        return "\n\n---\n\n".join(parts)
    ver, points = load_terms_spec(r)
    return "\n".join([f"# Hercyna Terms of Use (version {ver})", "", *points, "", "See TERMS.md and LEGAL.md."])


def _cmd_terms(args: argparse.Namespace) -> int:
    if getattr(args, "version", False):
        print(current_version())
        return 0
    print(full_terms_text())
    return 0


def _wrap_subparsers(sub_action: argparse._SubParsersAction) -> None:
    for name, parser in sub_action.choices.items():
        if name == "terms":
            continue
        _add_flag_if_missing(parser, suppress_default=True)
        nested = next((a for a in parser._actions if isinstance(a, argparse._SubParsersAction)), None)
        if nested is not None:
            _wrap_subparsers(nested)
        orig_func = parser.get_default("func")
        if callable(orig_func) and not getattr(orig_func, "_hercyna_terms_wrapped", False):
            @functools.wraps(orig_func)
            def wrapped(ns: argparse.Namespace, _fn=orig_func) -> int:
                if not ensure_terms_accepted(ns):
                    return 2
                return _fn(ns)

            wrapped._hercyna_terms_wrapped = True  # type: ignore[attr-defined]
            parser.set_defaults(func=wrapped)


def _add_flag_if_missing(parser: argparse.ArgumentParser, *, suppress_default: bool) -> None:
    if "--accept-terms" in parser._option_string_actions:
        return
    kwargs: dict = {
        "action": "store_true",
        "help": "accept TERMS.md and LEGAL.md for non-interactive runs",
    }
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    else:
        kwargs["default"] = False
    parser.add_argument("--accept-terms", **kwargs)


def attach_terms_cli(sub: argparse._SubParsersAction) -> None:
    """Register `bench terms` and attach first-run terms enforcement across all other subcommands."""
    tm = sub.add_parser("terms", help="print TERMS.md and LEGAL.md (or --version)")
    tm.add_argument("--version", action="store_true", help="print the current terms version")
    tm.set_defaults(func=_cmd_terms)

    # Walk caller frames to locate root ArgumentParser in cli.build_parser() without modifying frozen cli.py.
    frame = inspect.currentframe()
    try:
        cur = frame.f_back if frame else None
        for _ in range(4):
            if cur is None:
                break
            for val in cur.f_locals.values():
                if isinstance(val, argparse.ArgumentParser):
                    _add_flag_if_missing(val, suppress_default=False)
                    break
            cur = cur.f_back
    finally:
        del frame

    _wrap_subparsers(sub)
