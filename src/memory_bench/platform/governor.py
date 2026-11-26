"""StudioResourceGovernor: strictly read-only Mac Studio preflight & concurrency guard (A4).

Never modifies any host setting, LaunchAgent, global config, Colima allocation, or model weight.
Performs read-only checks (`df`, `sysctl`, `vm_stat`, `pgrep`, `docker`, `tart`) and enforces
the three mutually exclusive Mac Studio execution profiles.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

PROFILES: dict[str, dict[str, Any]] = {
    "profile_a_local_silicon": {
        "min_free_ssd_gib": 40.0,
        "max_local_gpu_parallel": 1,
        "max_ingest_workers_local": 1,
        "description": "Local M3 Ultra silicon inference (MLX :11440, private Ollama :11535, AFM, MPS :11435)",
    },
    "profile_b_mode_b_docker": {
        "min_free_ssd_gib": 55.0,
        "max_local_gpu_parallel": 1,
        "max_docker_parallel_12gib": 2,
        "description": "Mode B Colima Docker sb sandboxes + FalkorDB (:6390) without resizing Colima",
    },
    "profile_c_tart_vm": {
        "min_free_ssd_gib": 40.0,
        "max_local_gpu_parallel": 1,
        "max_vm_parallel": 1,
        "description": "Tart Virtualization.framework (openbot-vm) APFS CoW serial VM runs",
    },
}

CmdRunner = Callable[[Sequence[str], float], tuple[int, str, str]]


def _default_runner(argv: Sequence[str], timeout: float = 5.0) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


@dataclass(frozen=True)
class HostSnapshot:
    total_memory_gib: float
    free_ssd_gib: float
    swapins: int | None
    ollama_running: bool
    docker_available: bool
    docker_mem_gib: float | None
    tart_vms: tuple[str, ...]


@dataclass(frozen=True)
class PreflightReport:
    profile: str
    ok: bool
    clamped_parallel: int
    clamped_ingest_workers: int
    reasons: tuple[str, ...]
    snapshot: HostSnapshot


def _parse_vm_stat_swapins(text: str) -> int | None:
    m = re.search(r"Swapins:\s+(\d+)", text)
    return int(m.group(1)) if m else None


class StudioResourceGovernor:
    """Read-only host resource inspector and concurrency clamper."""

    def __init__(
        self,
        *,
        disk_path: str | Path = "/",
        runner: CmdRunner | None = None,
    ) -> None:
        self.disk_path = Path(disk_path)
        self.runner = runner or _default_runner

    def snapshot(self) -> HostSnapshot:
        usage = shutil.disk_usage(self.disk_path)
        free_gib = round(usage.free / (1024**3), 2)

        code, out, _ = self.runner(["sysctl", "-n", "hw.memsize"], 3.0)
        total_mem = round(int(out.strip()) / (1024**3), 2) if code == 0 and out.strip().isdigit() else 0.0

        code, vm_out, _ = self.runner(["vm_stat"], 3.0)
        swapins = _parse_vm_stat_swapins(vm_out) if code == 0 else None

        code, pg_out, _ = self.runner(["pgrep", "-f", "ollama"], 3.0)
        ollama_running = code == 0 and bool(pg_out.strip())

        code, dock_out, _ = self.runner(["docker", "info", "--format", "{{.MemTotal}}"], 4.0)
        docker_ok = code == 0 and dock_out.strip().isdigit()
        docker_mem = round(int(dock_out.strip()) / (1024**3), 2) if docker_ok else None

        code, tart_out, _ = self.runner(["tart", "list", "--quiet"], 4.0)
        tart_vms = tuple(line.strip() for line in tart_out.splitlines() if line.strip()) if code == 0 else ()

        return HostSnapshot(
            total_memory_gib=total_mem,
            free_ssd_gib=free_gib,
            swapins=swapins,
            ollama_running=ollama_running,
            docker_available=docker_ok,
            docker_mem_gib=docker_mem,
            tart_vms=tart_vms,
        )

    def preflight(
        self,
        profile: str,
        *,
        requested_parallel: int = 1,
        requested_ingest_workers: int = 1,
        uses_local_gpu: bool = True,
        snapshot: HostSnapshot | None = None,
    ) -> PreflightReport:
        if profile not in PROFILES:
            raise ValueError(f"unknown profile {profile!r}; choose from {sorted(PROFILES)}")
        spec = PROFILES[profile]
        snap = snapshot or self.snapshot()
        reasons: list[str] = []

        min_ssd = float(spec["min_free_ssd_gib"])
        if snap.free_ssd_gib < min_ssd:
            reasons.append(f"free SSD {snap.free_ssd_gib:.1f} GiB is below {min_ssd:.0f} GiB floor for {profile}")

        parallel = max(1, int(requested_parallel))
        ingest_workers = max(1, int(requested_ingest_workers))

        if uses_local_gpu:
            if parallel > spec["max_local_gpu_parallel"]:
                parallel = int(spec["max_local_gpu_parallel"])
            if "max_ingest_workers_local" in spec and ingest_workers > spec["max_ingest_workers_local"]:
                ingest_workers = int(spec["max_ingest_workers_local"])

        if profile == "profile_b_mode_b_docker":
            if not snap.docker_available:
                reasons.append("Colima/Docker daemon is not reachable")
            elif snap.docker_mem_gib is not None and snap.docker_mem_gib <= 14.0:
                parallel = min(parallel, int(spec["max_docker_parallel_12gib"]))

        if profile == "profile_c_tart_vm":
            parallel = min(parallel, int(spec["max_vm_parallel"]))
            if not snap.tart_vms:
                reasons.append("no Tart VMs reported by `tart list --quiet`")

        return PreflightReport(
            profile=profile,
            ok=not reasons,
            clamped_parallel=parallel,
            clamped_ingest_workers=ingest_workers,
            reasons=tuple(reasons),
            snapshot=snap,
        )


def _norm_model_stem(name: str) -> str:
    s = name.lower()
    s = re.sub(r"^models--[^-]+--", "", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def find_duplicate_weights(
    hf_cache_dir: str | Path | None = None,
    ollama_manifests_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read-only report of overlapping model families across Hugging Face and Ollama caches.

    Strictly read-only (A4): returns a list of candidate overlaps for maintainer review.
    Never modifies, moves, or deletes any model file.
    """
    home = Path(os.path.expanduser("~"))
    hf_root = Path(hf_cache_dir) if hf_cache_dir is not None else home / ".cache" / "huggingface" / "hub"
    ol_root = (
        Path(ollama_manifests_dir)
        if ollama_manifests_dir is not None
        else home / ".ollama" / "models" / "manifests" / "registry.ollama.ai" / "library"
    )

    hf_models: dict[str, list[str]] = {}
    if hf_root.is_dir():
        for entry in sorted(hf_root.iterdir()):
            if entry.is_dir() and entry.name.startswith("models--"):
                stem = _norm_model_stem(entry.name)
                for family in ("qwen3", "qwen25", "llama3", "mistral", "deepseek", "gemma", "nomic", "bge"):
                    if family in stem:
                        hf_models.setdefault(family, []).append(entry.name)

    ol_models: dict[str, list[str]] = {}
    if ol_root.is_dir():
        for entry in sorted(ol_root.iterdir()):
            if entry.is_dir():
                stem = _norm_model_stem(entry.name)
                for family in ("qwen3", "qwen25", "llama3", "mistral", "deepseek", "gemma", "nomic", "bge"):
                    if family in stem:
                        tags = [t.name for t in sorted(entry.iterdir()) if t.is_file()]
                        for tag in tags or ["latest"]:
                            ol_models.setdefault(family, []).append(f"{entry.name}:{tag}")

    overlaps: list[dict[str, Any]] = []
    for family in sorted(set(hf_models) & set(ol_models)):
        overlaps.append({
            "family": family,
            "huggingface_entries": sorted(hf_models[family]),
            "ollama_entries": sorted(ol_models[family]),
            "action": "report_only_for_review",
        })
    return overlaps


__all__ = [
    "PROFILES",
    "HostSnapshot",
    "PreflightReport",
    "StudioResourceGovernor",
    "find_duplicate_weights",
]
