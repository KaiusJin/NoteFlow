from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


MIB = 1024 * 1024


@dataclass(frozen=True)
class AcceleratorInfo:
    kind: str
    available: bool
    device_count: int = 0
    free_memory_bytes: Optional[int] = None
    name: str = ""


@dataclass(frozen=True)
class ResourcePoolPlan:
    cpu_workers: int
    io_workers: int
    gpu_workers: int
    vlm_workers: int
    accelerator: AcceleratorInfo
    rationale: dict[str, str]


def detect_accelerator() -> AcceleratorInfo:
    """Detect CUDA first, then Apple MPS, without requiring either runtime."""
    cuda = _detect_nvidia_smi()
    if cuda.available:
        return cuda
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            free_bytes, _ = torch.cuda.mem_get_info()
            return AcceleratorInfo(
                kind="cuda",
                available=True,
                device_count=torch.cuda.device_count(),
                free_memory_bytes=int(free_bytes),
                name=torch.cuda.get_device_name(0),
            )
        mps = getattr(torch.backends, "mps", None)
        if mps is not None and mps.is_available():
            return AcceleratorInfo(kind="mps", available=True, device_count=1, name="Apple Metal")
    except (ImportError, RuntimeError, OSError):
        pass
    return AcceleratorInfo(kind="cpu", available=False)


def build_resource_pool_plan(
    *,
    configured_cpu_workers: int = 0,
    configured_io_workers: int = 0,
    configured_gpu_workers: int = 0,
    configured_vlm_workers: int = 4,
    gpu_memory_per_task_mib: int = 2048,
    gpu_memory_reserve_mib: int = 1536,
    gpu_worker_cap: int = 4,
    cpu_count: Optional[int] = None,
    accelerator: Optional[AcceleratorInfo] = None,
) -> ResourcePoolPlan:
    cores = max(1, cpu_count or os.cpu_count() or 1)
    device = accelerator or detect_accelerator()

    # MuPDF already performs substantial native work per page and parallel
    # document handles contend on memory bandwidth. Local 48-page measurements
    # plateaued at 1-2 workers and regressed at 4/8, so the portable default is
    # deliberately conservative. Operators can override after benchmarking.
    cpu_workers = configured_cpu_workers or min(2, max(1, cores // 4))
    # Rendering and network/storage waits benefit from more concurrency, but a
    # hard cap keeps open files and page pixmaps bounded.
    io_workers = configured_io_workers or min(16, max(2, cores))
    vlm_workers = max(1, configured_vlm_workers)

    if configured_gpu_workers > 0:
        gpu_workers = configured_gpu_workers if device.available else 0
        gpu_reason = "explicit configuration" if device.available else "configured but no accelerator detected"
    elif not device.available:
        gpu_workers = 0
        gpu_reason = "no CUDA/MPS accelerator detected; CPU fallback is active"
    elif device.free_memory_bytes:
        usable_mib = max(0, device.free_memory_bytes // MIB - gpu_memory_reserve_mib)
        per_task = max(256, gpu_memory_per_task_mib)
        gpu_workers = min(gpu_worker_cap, max(1, usable_mib // per_task))
        gpu_reason = (
            f"floor((free VRAM {device.free_memory_bytes // MIB} MiB - reserve "
            f"{gpu_memory_reserve_mib} MiB) / {per_task} MiB per task), capped at {gpu_worker_cap}"
        )
    else:
        # MPS does not expose a reliable free-memory API. One worker is the safe
        # default; operators can override after measuring their model footprint.
        gpu_workers = 1
        gpu_reason = "accelerator memory is not measurable; serialized GPU execution"

    return ResourcePoolPlan(
        cpu_workers=cpu_workers,
        io_workers=io_workers,
        gpu_workers=int(gpu_workers),
        vlm_workers=vlm_workers,
        accelerator=device,
        rationale={
            "cpu": f"min(2, logical cores {cores} / 4); MuPDF benchmark plateaus at 1-2 workers",
            "io": f"min(16, logical cores {cores}) for bounded render/storage concurrency",
            "gpu": gpu_reason,
            "vlm": "provider-rate-limit bound; configured independently from CPU/GPU work",
        },
    )


def _detect_nvidia_smi() -> AcceleratorInfo:
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return AcceleratorInfo(kind="cuda", available=False)
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=name,memory.free",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        rows = [row.strip() for row in result.stdout.splitlines() if row.strip()]
        parsed = [row.rsplit(",", 1) for row in rows]
        free_mib = [int(parts[1].strip()) for parts in parsed if len(parts) == 2]
        names = [parts[0].strip() for parts in parsed if len(parts) == 2]
        if not free_mib:
            return AcceleratorInfo(kind="cuda", available=False)
        # A task is pinned to one device. The least-free device gives a safe
        # homogeneous concurrency estimate; explicit config can override it.
        return AcceleratorInfo(
            kind="cuda",
            available=True,
            device_count=len(free_mib),
            free_memory_bytes=min(free_mib) * MIB,
            name=", ".join(names),
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return AcceleratorInfo(kind="cuda", available=False)
