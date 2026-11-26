"""`bench submit pack RUN --out DIR [--slug S] [--exploratory]` (docs/submission.md sections 1 and 2).

Copies the released files of a complete run, byte for byte, into `DIR/<system>/<YYYY-MM-DD>-<slug>/` and writes
`submission.json` with every field the manifest knows. `private/` is never read.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..engine import files as F
from ..engine.health import sanitize_health_errors

SUBMISSION = "submission.json"
SUBMISSION_SCHEMA = "memory_bench.submission/1"
REQUIRED = (F.MANIFEST, F.METRICS, F.SUMMARY, F.SPEND, F.HEALTH)
OPTIONAL = (F.TIMINGS, "calls.jsonl", "memory_calls.jsonl", F.STABILITY, F.POWER, "view-audit.json", F.ACBI)
ALLOWED = (*REQUIRED, *OPTIONAL, SUBMISSION)
SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
BUILTIN = "builtin"


class PackError(RuntimeError):
    """`bench submit pack` refuses."""


def submitted_adapter(manifest: dict) -> dict:
    """The adapter entry a submission is about: the one product adapter (product_version not `builtin`) among the
    view systems; with no product, the one view system. Anything else is ambiguous and refused."""
    views = list(manifest.get("adapters") or [])[1:]  # entry 0 is `full`, the gate and floor system
    products = [a for a in views if a.get("product_version") != BUILTIN]
    if len(products) == 1:
        return products[0]
    if not products and len(views) == 1:
        return views[0]
    names = [f"{a.get('name')}/{a.get('config_label')}" for a in (products or views)]
    raise PackError(f"cannot tell which system this run submits: {names or 'no view systems'}; a submission run "
                    f"holds one product adapter (or one baseline)")


def pack(run_dir: str | Path, out: str | Path, *, slug: str | None = None, exploratory: bool = False) -> Path:
    """Write the submission folder and return its path. Raises PackError on a refusal."""
    run_dir, out = Path(run_dir), Path(out)
    m_path = run_dir / F.MANIFEST
    if not m_path.is_file():
        raise PackError(f"{m_path} does not exist")
    manifest = F.read_json(m_path)
    if manifest.get("state") != "complete":
        raise PackError(f"run state is {manifest.get('state')!r}; only a complete run is packed")
    grade = manifest.get("grade") or "exploratory"
    if grade == "table" and exploratory:
        raise PackError("the run is table-grade; drop --exploratory")
    if grade != "table" and not exploratory:
        raise PackError(f"run grade is {grade!r}; only a table-grade run is packed unless --exploratory is passed")
    missing = [n for n in REQUIRED if not (run_dir / n).is_file()]
    if missing:
        raise PackError(f"the run misses {', '.join(missing)}")
    adapter = submitted_adapter(manifest)
    slug = slug if slug is not None else str(adapter.get("config_label") or "default").lower()
    if not SLUG.fullmatch(slug):
        raise PackError(f"slug {slug!r} must be short and lowercase: letters, digits and '-', 32 at most")
    date = str(manifest.get("finished") or manifest.get("created") or "")[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise PackError("the manifest has no finished date")
    dest = out / str(adapter["name"]) / f"{date}-{slug}"
    if dest.exists() and any(dest.iterdir()):
        raise PackError(f"{dest} exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    for name in (*REQUIRED, *OPTIONAL):
        src = run_dir / name
        if src.is_file():
            shutil.copyfile(src, dest / name)
    health_dest = dest / F.HEALTH
    health_obj = F.read_json(health_dest)
    _, health_changed = sanitize_health_errors(health_obj)
    if health_changed:
        F.write_json(health_dest, health_obj)
        m_dest = F.read_json(dest / F.MANIFEST)
        if isinstance(m_dest.get("files"), dict) and F.HEALTH in m_dest["files"]:
            m_dest["files"][F.HEALTH] = F.sha256_file(health_dest)
            F.write_json(dest / F.MANIFEST, m_dest)
            manifest = m_dest
    protocol = manifest.get("protocol") or {}
    sub = {"schema": SUBMISSION_SCHEMA, "system": adapter["name"], "config_label": adapter.get("config_label"),
           "product_version": adapter.get("product_version"), "submitter": None, "organisation": None,
           "adapter_source": None, "run_id": manifest.get("run_id"),
           "protocol_sha256": protocol.get("protocol_sha256"), "reproduced": None}
    if exploratory:
        sub["grade"] = "exploratory"
    F.write_json(dest / SUBMISSION, sub)
    return dest


__all__ = ["pack", "PackError", "submitted_adapter", "SUBMISSION", "SUBMISSION_SCHEMA", "REQUIRED", "OPTIONAL",
           "ALLOWED"]
