"""GPU / CUDA capability detection.

Phase 01 only *detects* capabilities; it never initialises CUDA contexts or
loads models. Detection order:

1. ``torch`` (only if it is already installed in the engine venv - never imported
   at module load, never installed automatically),
2. ``nvidia-smi`` (ships with the NVIDIA driver).

Results are cached; call :meth:`GpuManager.refresh` to re-detect.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shared.contracts.job_contracts import GpuDevice

log = logging.getLogger("ai_engine.gpu")


@dataclass
class GpuInfo:
    gpu_available: bool = False
    cuda_available: bool = False
    devices: List[GpuDevice] = field(default_factory=list)
    method: str = "none"
    cuda_version: Optional[str] = None
    notes: List[str] = field(default_factory=list)


class GpuManager:
    def __init__(self, settings: Optional[Dict[str, Any]] = None) -> None:
        settings = settings or {}
        self._use_smi = bool(settings.get("detect_with_nvidia_smi", True))
        self._use_torch = bool(settings.get("detect_with_torch", True))
        self._smi_timeout = float(settings.get("nvidia_smi_timeout_seconds", 5))
        self._lock = threading.Lock()
        self._info: Optional[GpuInfo] = None

    @property
    def info(self) -> GpuInfo:
        with self._lock:
            if self._info is None:
                self._info = self._detect()
            return self._info

    def refresh(self) -> GpuInfo:
        with self._lock:
            self._info = self._detect()
            return self._info

    # ------------------------------------------------------------------ detection
    def _detect(self) -> GpuInfo:
        info = GpuInfo()
        if self._use_torch:
            self._detect_torch(info)
        if not info.devices and self._use_smi:
            self._detect_nvidia_smi(info)
        info.gpu_available = bool(info.devices)
        log.info("GPU detection finished", extra={"gpu_available": info.gpu_available,
                                                    "cuda_available": info.cuda_available,
                                                    "method": info.method, "devices": len(info.devices)})
        return info

    def _detect_torch(self, info: GpuInfo) -> None:
        if "torch" not in sys.modules:
            try:
                import importlib.util
                if importlib.util.find_spec("torch") is None:
                    info.notes.append("torch not installed (expected in Phase 01)")
                    return
            except (ImportError, ValueError):
                return
        try:
            import torch  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on machine
            info.notes.append("torch import failed: %s" % exc)
            return
        try:  # pragma: no cover - depends on machine
            if torch.cuda.is_available():
                info.cuda_available = True
                info.cuda_version = getattr(torch.version, "cuda", None)
                info.method = "torch"
                for idx in range(torch.cuda.device_count()):
                    props = torch.cuda.get_device_properties(idx)
                    info.devices.append(GpuDevice(index=idx, name=props.name,
                                                  memory_total_mb=int(props.total_memory // (1024 * 1024))))
        except Exception as exc:  # pragma: no cover
            info.notes.append("torch CUDA query failed: %s" % exc)

    def _detect_nvidia_smi(self, info: GpuInfo) -> None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            info.notes.append("nvidia-smi not found")
            return
        cmd = [exe, "--query-gpu=index,name,memory.total,driver_version", "--format=csv,noheader,nounits"]
        try:
            kwargs: Dict[str, Any] = {}
            if sys.platform.startswith("win"):
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=self._smi_timeout, **kwargs)
        except (OSError, subprocess.SubprocessError) as exc:
            info.notes.append("nvidia-smi failed: %s" % exc)
            return
        if out.returncode != 0:
            info.notes.append("nvidia-smi exited with %s" % out.returncode)
            return
        info.devices.extend(parse_nvidia_smi_csv(out.stdout))
        if info.devices:
            info.method = "nvidia-smi"
            # A working NVIDIA driver means CUDA-capable hardware. Whether a CUDA
            # *runtime* is usable is only known once a framework is installed (Phase 02).
            info.cuda_available = True


def parse_nvidia_smi_csv(text: str) -> List[GpuDevice]:
    """Parse ``nvidia-smi --format=csv,noheader,nounits`` output."""
    devices: List[GpuDevice] = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        try:
            memory = int(float(parts[2]))
        except ValueError:
            memory = None
        devices.append(GpuDevice(index=index, name=parts[1], memory_total_mb=memory, driver_version=parts[3]))
    return devices
