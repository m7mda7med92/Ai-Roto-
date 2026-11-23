"""Local model store: catalog, install (download), verify, remove.

Layout::

    <store_dir>/<model_id>/<file>          model weights
    <store_dir>/<model_id>/installed.json  written last; its presence means "installed"

Downloads stream to ``<file>.part`` and are renamed only after size/hash checks,
so an interrupted or cancelled install never leaves a half-written model that
looks installed. Weights are only ever fetched on an explicit install request.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import ssl
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from shared.contracts.errors import ContractError
from shared.contracts.model_contracts import ModelInfo
from shared.paths import toolkit_root, user_dir

log = logging.getLogger("ai_engine.models")

CHUNK = 1024 * 1024
MARKER = "installed.json"


class ModelError(Exception):
    pass


#: Optional extra PEM file with CA certificates (e.g. a company's SSL-inspection root).
CA_BUNDLE_ENV = "MAIT_CA_BUNDLE"


def https_context() -> ssl.SSLContext:
    """TLS context for model downloads.

    Uses the operating system's trust store through ``truststore`` when it is installed (it is in
    requirements_engine.txt). On Windows that is the certificate store that IT departments use
    for SSL-inspection proxies, so downloads work behind them without disabling verification.
    Falls back to Python's default context. ``MAIT_CA_BUNDLE`` can name an extra PEM file.
    Certificate verification is never turned off.
    """
    context: ssl.SSLContext
    try:
        import truststore  # type: ignore
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        source = "system trust store (truststore)"
    except Exception:  # truststore missing, or the OS store is unavailable
        context = ssl.create_default_context()
        source = "Python default certificates"
    extra = os.environ.get(CA_BUNDLE_ENV, "").strip()
    if extra:
        try:
            context.load_verify_locations(cafile=extra)
            source += " + %s" % extra
        except (OSError, ssl.SSLError) as exc:
            raise ModelError("%s=%s could not be loaded: %s" % (CA_BUNDLE_ENV, extra, exc))
    log.debug("HTTPS trust: %s", source)
    return context


def extracted_dir(zip_name: str) -> str:
    """Folder an extracted archive goes to, next to it: ``rtmpose.zip`` -> ``rtmpose``."""
    return zip_name[:-4] if zip_name.lower().endswith(".zip") else zip_name + ".d"


def extract_zip(archive: Path, target: Path) -> None:
    """Unpack a downloaded archive, refusing absolute paths and ``..`` (zip-slip)."""
    import zipfile
    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True)
    root = target.resolve()
    try:
        with zipfile.ZipFile(str(archive)) as zf:
            for member in zf.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise ModelError("unsafe path in archive %s: %s" % (archive.name, member.filename))
            zf.extractall(str(root))
    except zipfile.BadZipFile as exc:
        shutil.rmtree(str(target), ignore_errors=True)
        raise ModelError("%s is not a valid zip file: %s" % (archive.name, exc))


def find_file(folder: Path, suffix: str) -> Optional[Path]:
    """First file under ``folder`` (recursively, sorted) whose name ends with ``suffix``."""
    matches = sorted(p for p in Path(folder).rglob("*" + suffix) if p.is_file())
    return matches[0] if matches else None


class UnknownModel(ModelError, KeyError):
    pass


class ModelBusy(ModelError):
    pass


def default_catalog_path() -> Path:
    return toolkit_root() / "config" / "model_catalog.json"


def load_catalog(path: Optional[Path] = None) -> Dict[str, ModelInfo]:
    path = Path(path) if path else default_catalog_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ModelError("cannot read model catalog %s: %s" % (path, exc))
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise ModelError("model catalog must contain a 'models' list")
    catalog: Dict[str, ModelInfo] = {}
    for entry in entries:
        try:
            info = ModelInfo.from_dict(entry)
        except ContractError as exc:
            raise ModelError("invalid catalog entry: %s" % exc)
        if info.model_id in catalog:
            raise ModelError("duplicate model_id in catalog: %s" % info.model_id)
        for model_file in info.files:
            for url in [model_file.url] + model_file.mirrors:
                _check_url(url)
        catalog[info.model_id] = info
    return catalog


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    loopback = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise ModelError("model URLs must use https (got %s)" % url)


class ModelManager:
    def __init__(self, store_dir: Optional[Path] = None, catalog_path: Optional[Path] = None,
                 timeout: float = 30.0) -> None:
        self.store_dir = Path(store_dir) if store_dir else user_dir() / "models"
        self.catalog = load_catalog(catalog_path)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._busy: set = set()

    # ------------------------------------------------------------------ queries
    def list(self) -> List[ModelInfo]:
        return [self.get(model_id) for model_id in self.catalog]

    def get(self, model_id: str) -> ModelInfo:
        base = self.catalog.get(model_id)
        if base is None:
            raise UnknownModel(model_id)
        info = ModelInfo.from_dict(base.to_dict())
        if info.builtin:
            info.installed = True
            return info
        folder = self._folder(model_id)
        marker = folder / MARKER
        if marker.is_file() and all((folder / f.name).is_file() for f in info.files):
            info.installed = True
            info.install_path = str(folder)
            try:
                record = json.loads(marker.read_text(encoding="utf-8"))
                info.verified = bool(record.get("verified"))
            except (OSError, ValueError):
                info.verified = None
        return info

    def file_path(self, model_id: str, index: int = 0) -> Path:
        info = self.get(model_id)
        if not info.installed or not info.files:
            raise ModelError("model %s is not installed" % model_id)
        return self._folder(model_id) / info.files[index].name

    # ------------------------------------------------------------------ install / remove
    def install(self, model_id: str, report: Callable[[float, str], None],
                checkpoint: Callable[[], None]) -> ModelInfo:
        """Download all files of ``model_id``. ``checkpoint`` raises to cancel."""
        info = self.get(model_id)
        if info.builtin:
            return info
        self._acquire(model_id)
        try:
            folder = self._folder(model_id)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / MARKER).unlink(missing_ok=True)  # re-install: not "installed" until done
            hashes: Dict[str, str] = {}
            verified = True
            total_files = len(info.files)
            for index, model_file in enumerate(info.files):
                def file_report(fraction: float, message: str, _i: int = index) -> None:
                    report((_i + fraction) / total_files, message)
                digest = self._download_any([model_file.url] + model_file.mirrors, folder / model_file.name,
                                            model_file.size_bytes, file_report, checkpoint)
                if model_file.sha256:
                    if digest != model_file.sha256:
                        (folder / model_file.name).unlink(missing_ok=True)
                        raise ModelError("checksum mismatch for %s" % model_file.name)
                else:
                    verified = False
                hashes[model_file.name] = digest
                if model_file.extract:
                    report((index + 0.999) / total_files, "Unpacking %s" % model_file.name)
                    extract_zip(folder / model_file.name, folder / extracted_dir(model_file.name))
            (folder / MARKER).write_text(json.dumps({
                "model_id": model_id, "installed_at": time.time(), "sha256": hashes, "verified": verified,
            }, indent=2), encoding="utf-8")
            report(1.0, "Installed %s" % info.name)
            log.info("Model installed", extra={"model_id": model_id, "verified": verified})
            return self.get(model_id)
        finally:
            self._release(model_id)

    def remove(self, model_id: str) -> ModelInfo:
        info = self.get(model_id)
        if info.builtin:
            raise ModelError("built-in model cannot be removed")
        self._acquire(model_id)
        try:
            folder = self._folder(model_id)
            if folder.exists():
                shutil.rmtree(str(folder))
            log.info("Model removed", extra={"model_id": model_id})
            return self.get(model_id)
        finally:
            self._release(model_id)

    # ------------------------------------------------------------------ internals
    def _folder(self, model_id: str) -> Path:
        folder = (self.store_dir / model_id).resolve()
        if self.store_dir.resolve() not in folder.parents:
            raise ModelError("invalid model id")
        return folder

    def _acquire(self, model_id: str) -> None:
        with self._lock:
            if model_id in self._busy:
                raise ModelBusy("model %s is already being installed or removed" % model_id)
            self._busy.add(model_id)

    def _release(self, model_id: str) -> None:
        with self._lock:
            self._busy.discard(model_id)

    def _download_any(self, urls: List[str], target: Path, expected_size: Optional[int],
                      report: Callable[[float, str], None], checkpoint: Callable[[], None]) -> str:
        """Try ``urls`` in order (official host first, then mirrors). Cancellation is never retried."""
        errors = []
        for url in urls:
            try:
                return self._download(url, target, expected_size, report, checkpoint)
            except ModelError as exc:
                errors.append(str(exc))
                log.warning("Download failed, trying next source", extra={"url": url, "error": str(exc)})
        raise ModelError(" | ".join(errors) if errors else "no download URL")

    def _download(self, url: str, target: Path, expected_size: Optional[int],
                  report: Callable[[float, str], None], checkpoint: Callable[[], None]) -> str:
        _check_url(url)
        part = target.with_name(target.name + ".part")
        sha = hashlib.sha256()
        received = 0
        request = urllib.request.Request(url, headers={"User-Agent": "MayaAIToolkit-ModelManager"})
        try:
            context = https_context() if url.lower().startswith("https:") else None
            with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response, \
                    open(str(part), "wb") as out:
                total = int(response.headers.get("Content-Length") or 0) or expected_size or 0
                last = 0.0
                while True:
                    checkpoint()
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    sha.update(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    if now - last > 0.2:
                        last = now
                        fraction = received / total if total else 0.0
                        report(min(fraction, 0.999), "Downloading %s: %.1f / %s MB" % (
                            target.name, received / 1e6, ("%.1f" % (total / 1e6)) if total else "?"))
        except BaseException as exc:
            part.unlink(missing_ok=True)
            if isinstance(exc, ModelError):
                raise
            if isinstance(exc, (OSError, ValueError)):
                hint = ""
                if "CERTIFICATE_VERIFY_FAILED" in str(exc):
                    hint = (" - the certificate is not trusted. If your network inspects SSL, make sure "
                            "'truststore' is installed in the engine venv (re-run setup_engine.ps1) or set "
                            "%s to your company's CA file." % CA_BUNDLE_ENV)
                raise ModelError("download failed for %s: %s%s" % (url, exc, hint))
            raise
        if expected_size is not None and received != expected_size:
            part.unlink(missing_ok=True)
            raise ModelError("size mismatch for %s: got %d bytes, expected %d" % (target.name, received, expected_size))
        part.replace(target)
        return sha.hexdigest()
