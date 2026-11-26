"""Cryptographically random seed drawing with audit logging (R3 A6)."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

from . import files as F

DEFAULT_SEED_LOG = Path("results/v2/seed_draws.jsonl")


def draw_seed(
    purpose: str,
    *,
    log_path: str | Path = DEFAULT_SEED_LOG,
    bits: int = 31,
    extra: dict[str, Any] | None = None,
) -> int:
    """Draw a positive integer seed using `secrets.randbelow` and append a JSONL audit entry."""
    if not purpose or not isinstance(purpose, str):
        raise ValueError("purpose must be a non-empty string")
    if not 8 <= int(bits) <= 63:
        raise ValueError(f"bits must be between 8 and 63, got {bits}")
    seed = secrets.randbelow((1 << int(bits)) - 1) + 1
    entry: dict[str, Any] = {
        "at": F.now(),
        "purpose": purpose,
        "seed": seed,
        "bits": int(bits),
        "source": "secrets.randbelow",
    }
    if extra:
        entry["meta"] = dict(extra)
    p = Path(log_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n")
        fh.flush()
    return seed


__all__ = ["draw_seed", "DEFAULT_SEED_LOG"]
