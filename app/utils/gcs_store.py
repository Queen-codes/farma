from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


_BASE_DIR = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _BASE_DIR / "reports"
_TMP_AUDIO_DIR = _BASE_DIR / "tmp_audio"


def _split_prefix(key: str) -> tuple[str, str]:
    parts = key.split("/", 1)
    if len(parts) == 1:
        return "", parts[0]
    return parts[0] + "/", parts[1]


def _local_path_for_key(key: str) -> Path:
    prefix, rest = _split_prefix(key)
    if prefix == "reports/":
        return _REPORTS_DIR / rest
    if prefix == "tmp_audio/":
        return _TMP_AUDIO_DIR / rest
    return _BASE_DIR / key


def _gcs_client():
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        return storage.Client()
    except Exception as e:  # pragma: no cover
        print(f"[GCS] GCS client unavailable, using local fallback: {e}")
        return None


def upload_bytes(bucket: str, key: str, data: bytes, content_type: str) -> str:
    """Upload bytes to GCS.

    Returns a public URL or a signed URL if available. Falls back to local file write.
    """
    client = _gcs_client()
    if not client:
        path = _local_path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    try:
        b = client.bucket(bucket)
        blob = b.blob(key)
        blob.upload_from_string(data, content_type=content_type)
        # Try signed URL first (may fail depending on credentials/IAM).
        try:
            url = blob.generate_signed_url(version="v4", expiration=3600, method="GET")
            return url
        except Exception:
            return blob.public_url
    except Exception as e:
        print(f"[GCS] Upload failed, using local fallback: {e}")
        path = _local_path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)


def download_bytes(bucket: str, key: str) -> bytes:
    """Download bytes from GCS. Falls back to local file read."""
    client = _gcs_client()
    if not client:
        return _local_path_for_key(key).read_bytes()

    try:
        blob = client.bucket(bucket).blob(key)
        return blob.download_as_bytes()
    except Exception as e:
        print(f"[GCS] Download failed, trying local fallback: {e}")
        return _local_path_for_key(key).read_bytes()


def list_objects(bucket: str, prefix: str) -> List[Dict[str, Any]]:
    """List objects in GCS prefix. Falls back to local filesystem glob."""
    client = _gcs_client()
    if not client:
        base = _local_path_for_key(prefix)
        if base.is_file():
            base = base.parent
        base.mkdir(parents=True, exist_ok=True)
        out: List[Dict[str, Any]] = []
        for p in base.glob("**/*"):
            if not p.is_file():
                continue
            out.append(
                {
                    "name": f"{prefix}{p.name}" if prefix.endswith("/") else p.name,
                    "size": p.stat().st_size,
                    "updated": p.stat().st_mtime,
                }
            )
        return out

    try:
        blobs = client.list_blobs(bucket, prefix=prefix)
        out = []
        for b in blobs:
            out.append(
                {
                    "name": b.name,
                    "size": int(getattr(b, "size", 0) or 0),
                    "updated": getattr(b, "updated", None),
                }
            )
        return out
    except Exception as e:
        print(f"[GCS] List failed, using local fallback: {e}")
        base = _local_path_for_key(prefix)
        if base.is_file():
            base = base.parent
        base.mkdir(parents=True, exist_ok=True)
        out = []
        for p in base.glob("**/*"):
            if not p.is_file():
                continue
            out.append(
                {
                    "name": f"{prefix}{p.name}" if prefix.endswith("/") else p.name,
                    "size": p.stat().st_size,
                    "updated": p.stat().st_mtime,
                }
            )
        return out


def delete_object(bucket: str, key: str) -> None:
    """Delete GCS object (best-effort). Falls back to local delete."""
    client = _gcs_client()
    if not client:
        try:
            _local_path_for_key(key).unlink(missing_ok=True)
        except Exception:
            pass
        return

    try:
        client.bucket(bucket).blob(key).delete()
    except Exception as e:
        print(f"[GCS] Delete failed, trying local fallback: {e}")
        try:
            _local_path_for_key(key).unlink(missing_ok=True)
        except Exception:
            pass
