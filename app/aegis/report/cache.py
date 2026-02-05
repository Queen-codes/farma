from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def _sha256_hex(data: bytes) -> str:
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
        safe_ratio = self.aspect_ratio.replace(":", "x")
        return (
            f"scan{self.scan_id}_{self.infographic_type}_{self.prompt_version}_"
            f"{safe_ratio}_{self.image_size}_{self.payload_hash[:16]}.png"
        )


class InfographicCache:
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compute_payload_hash(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return _sha256_hex(raw)

    def get_path(self, key: CacheKey) -> Path:
        return self.cache_dir / key.filename()

    def exists(self, key: CacheKey) -> bool:
        return self.get_path(key).exists()

    def read_bytes(self, key: CacheKey) -> Optional[bytes]:
        path = self.get_path(key)
        if not path.exists():
            return None
        return path.read_bytes()

    def write_bytes(self, key: CacheKey, data: bytes) -> Path:
        path = self.get_path(key)
        path.write_bytes(data)
        return path
