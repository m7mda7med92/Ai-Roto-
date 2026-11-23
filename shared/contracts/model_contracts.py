"""Contracts for AI model management (catalog entries and install status)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from shared.contracts.errors import ContractError


@dataclass
class ModelFile:
    name: str
    url: str
    sha256: Optional[str] = None
    size_bytes: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "url": self.url, "sha256": self.sha256, "size_bytes": self.size_bytes}

    @classmethod
    def from_dict(cls, data: Any) -> "ModelFile":
        if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not isinstance(data.get("url"), str):
            raise ContractError("model file needs 'name' and 'url'")
        name = data["name"]
        if "/" in name or "\\" in name or name in ("", ".", ".."):
            raise ContractError("model file name must be a plain file name", "name")
        sha = data.get("sha256")
        if sha is not None and (not isinstance(sha, str) or len(sha) != 64):
            raise ContractError("sha256 must be a 64-char hex string", "sha256")
        size = data.get("size_bytes")
        return cls(name=name, url=data["url"], sha256=sha.lower() if sha else None,
                   size_bytes=int(size) if size is not None else None)


@dataclass
class ModelInfo:
    """A catalog entry plus its install state on this machine."""

    model_id: str
    name: str
    task: str
    backend: str
    version: str = ""
    description: str = ""
    license: str = ""
    homepage: str = ""
    approx_size_mb: Optional[int] = None
    recommended_vram_gb: Optional[float] = None
    builtin: bool = False
    files: List[ModelFile] = field(default_factory=list)
    options: Dict[str, Any] = field(default_factory=dict)
    installed: bool = False
    install_path: Optional[str] = None
    verified: Optional[bool] = None    # True: sha256 matched; False: no reference hash to check against

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id, "name": self.name, "task": self.task, "backend": self.backend,
            "version": self.version, "description": self.description, "license": self.license,
            "homepage": self.homepage, "approx_size_mb": self.approx_size_mb,
            "recommended_vram_gb": self.recommended_vram_gb, "builtin": self.builtin,
            "files": [f.to_dict() for f in self.files], "options": dict(self.options),
            "installed": self.installed, "install_path": self.install_path, "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ModelInfo":
        if not isinstance(data, dict):
            raise ContractError("model entry must be an object")
        for key in ("model_id", "name", "task", "backend"):
            if not isinstance(data.get(key), str) or not data[key]:
                raise ContractError("model entry missing '%s'" % key, key)
        return cls(
            model_id=data["model_id"], name=data["name"], task=data["task"], backend=data["backend"],
            version=str(data.get("version", "")), description=str(data.get("description", "")),
            license=str(data.get("license", "")), homepage=str(data.get("homepage", "")),
            approx_size_mb=data.get("approx_size_mb"), recommended_vram_gb=data.get("recommended_vram_gb"),
            builtin=bool(data.get("builtin", False)),
            files=[ModelFile.from_dict(f) for f in data.get("files") or []],
            options=dict(data.get("options") or {}), installed=bool(data.get("installed", False)),
            install_path=data.get("install_path"), verified=data.get("verified"),
        )
