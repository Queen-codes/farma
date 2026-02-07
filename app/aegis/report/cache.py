"""Disk cache utilities for generated infographic image artifacts.

Purpose:
- Build deterministic cache keys from scan + rendering payload parameters.
- Read/write cached image bytes to reduce repeated image generation calls.

Used by:
- `app.aegis.report.infographics`.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _sha256_hex(data: bytes) -> str:
    """Return SHA-256 hex digest for byte payload."""
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class CacheKey:
    scan_id: int
    infographic_type: str
    prompt_version: str
    aspect_ratio: str
    image_size: str
    payload_hash: str

    def filename(self) -> str:
        """Render deterministic cache filename for this key."""
        safe_ratio = self.aspect_ratio.replace(":", "x")
        return (
            f"scan{self.scan_id}_{self.infographic_type}_{self.prompt_version}_"
            f"{safe_ratio}_{self.image_size}_{self.payload_hash[:16]}.png"
        )


class InfographicCache:
    def __init__(self, cache_dir: Path) -> None:
        """Initialize cache directory and ensure it exists."""
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_payload_hash(self, payload: Dict[str, Any]) -> str:
        """Hash prompt payload to derive cache key stability."""
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return _sha256_hex(raw)

    def get_path(self, key: CacheKey) -> Path:
        """Return full filesystem path for cache key."""
        return self.cache_dir / key.filename()

    def exists(self, key: CacheKey) -> bool:
        """Return whether cached file exists for key."""
        return self.get_path(key).exists()

    def read_bytes(self, key: CacheKey) -> Optional[bytes]:
        """Read cached bytes for key if present, else `None`."""
        path = self.get_path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def write_bytes(self, key: CacheKey, data: bytes) -> Path:
        """Write bytes for key and return resulting path."""
        path = self.get_path(key)
        path.write_bytes(data)
        return path
