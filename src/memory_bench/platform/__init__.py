"""Mac Studio platform modules (strictly read-only host checks, zero core deps, A3/A4 compliant)."""

from .governor import (
    PROFILES,
    HostSnapshot,
    PreflightReport,
    StudioResourceGovernor,
    find_duplicate_weights,
)

__all__ = [
    "PROFILES",
    "HostSnapshot",
    "PreflightReport",
    "StudioResourceGovernor",
    "find_duplicate_weights",
]

try:
    from .cli_probe import probe_all_clis, probe_wrapper_executable
except ImportError:
    pass
else:
    __all__.extend(["probe_all_clis", "probe_wrapper_executable"])
