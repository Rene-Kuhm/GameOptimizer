from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from pathlib import Path
from typing import Any


def _normalize_path(path: str | Path) -> str:
    candidate = Path(path)
    try:
        return str(candidate.resolve(strict=False)).lower()
    except OSError:
        return str(candidate).lower()


class ExecutableMetadataCache:
    def __init__(self) -> None:
        self._base: dict[tuple[str, int, int], dict[str, Any]] = {}
        self._hashes: dict[tuple[str, int, int], str] = {}
        self._signatures: dict[tuple[str, int, int], dict[str, Any] | None] = {}
        self._lock = threading.Lock()

    def _file_signature(self, executable: Path) -> tuple[str, int, int] | None:
        try:
            stat = executable.stat()
        except OSError:
            return None
        return (_normalize_path(executable), int(stat.st_size), int(stat.st_mtime))

    def collect(
        self,
        executable: str | Path,
        *,
        include_hash: bool = False,
        include_signature: bool = False,
    ) -> dict[str, Any]:
        path = Path(executable)
        signature_key = self._file_signature(path)
        if not signature_key:
            return {"path": str(path), "error": "file_not_accessible"}

        normalized_path, size, mtime = signature_key
        with self._lock:
            if signature_key not in self._base:
                self._base[signature_key] = {
                    "path": normalized_path,
                    "file_size": size,
                    "mtime": mtime,
                }
            payload = dict(self._base[signature_key])

        if include_hash:
            payload["sha256"] = self._get_sha256(path, signature_key)
        if include_signature:
            payload["signature"] = self._get_signature(path, signature_key)
        return payload

    def _get_sha256(self, executable: Path, signature_key: tuple[str, int, int]) -> str | None:
        with self._lock:
            if signature_key in self._hashes:
                return self._hashes[signature_key]

        digest = hashlib.sha256()
        try:
            with executable.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
        except OSError:
            return None

        value = digest.hexdigest()
        with self._lock:
            self._hashes[signature_key] = value
        return value

    def _get_signature(self, executable: Path, signature_key: tuple[str, int, int]) -> dict[str, Any] | None:
        with self._lock:
            if signature_key in self._signatures:
                return self._signatures[signature_key]

        script = (
            "$sig = Get-AuthenticodeSignature -FilePath $args[0];"
            "$out = [PSCustomObject]@{"
            "Status=$sig.Status.ToString();"
            "StatusMessage=$sig.StatusMessage;"
            "Subject=if($sig.SignerCertificate){$sig.SignerCertificate.Subject}else{$null};"
            "Issuer=if($sig.SignerCertificate){$sig.SignerCertificate.Issuer}else{$null};"
            "Thumbprint=if($sig.SignerCertificate){$sig.SignerCertificate.Thumbprint}else{$null}"
            "};"
            "$out | ConvertTo-Json -Compress"
        )
        try:
            proc = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                    str(executable),
                ],
                capture_output=True,
                text=True,
                timeout=6,
                check=False,
            )
            output = (proc.stdout or "").strip()
            parsed = json.loads(output) if output else None
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            signature = {
                "status": parsed.get("Status"),
                "status_message": parsed.get("StatusMessage"),
                "subject": parsed.get("Subject"),
                "issuer": parsed.get("Issuer"),
                "thumbprint": parsed.get("Thumbprint"),
            }
        else:
            signature = None

        with self._lock:
            self._signatures[signature_key] = signature
        return signature


_GLOBAL_EXECUTABLE_META_CACHE = ExecutableMetadataCache()


def get_executable_metadata_cache() -> ExecutableMetadataCache:
    return _GLOBAL_EXECUTABLE_META_CACHE
