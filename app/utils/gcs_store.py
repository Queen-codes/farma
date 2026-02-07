"""Storage helper wrappers with GCS-first, local-filesystem fallback behavior.

This module centralizes binary object operations used by API/workflow code:
- Upload artifact/audio/report bytes.
- Download previously stored objects.
- List or delete objects by key prefix.

Call flow:
- Try Google Cloud Storage client first.
- On missing credentials/client or request errors, transparently fall back to
  deterministic local paths under project `reports/` and `tmp_audio/`.

Assumptions:
- Local fallback paths are writable in runtime environment.
- `key` values are relative object keys (not absolute filesystem paths).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


_BASE_DIR = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _BASE_DIR / "reports"
_TMP_AUDIO_DIR = _BASE_DIR / "tmp_audio"


def _split_prefix(key: str) -> tuple[str, str]:
    """Split storage key into root prefix segment and remainder.

    Args:
        key: Object key, optionally with top-level directory prefix.

    Returns:
        Tuple of `(prefix, rest)` where `prefix` includes trailing slash for
        known directory-style prefixes. When key has no slash, prefix is empty.

    Raises:
        None: This helper does not intentionally raise.

    Side Effects:
        None.

    Latency:
        Constant-time string splitting.
    """
    parts = key.split("/", 1)
    if len(parts) == 1:
        return "", parts[0]
    return parts[0] + "/", parts[1]


def _local_path_for_key(key: str) -> Path:
    """Map an object key to deterministic local fallback filesystem path.

    Args:
        key: Object key used in storage operations.

    Returns:
        Absolute local `Path` under project root, `reports/`, or `tmp_audio/`.

    Raises:
        None: Pure local mapping helper.

    Side Effects:
        None.

    Latency:
        Constant-time path construction.
    """
    prefix, rest = _split_prefix(key)
    if prefix == "reports/":
        return _REPORTS_DIR / rest
    if prefix == "tmp_audio/":
        return _TMP_AUDIO_DIR / rest
    return _BASE_DIR / key


def _gcs_client() -> Any | None:
    """Create a Google Cloud Storage client when dependencies are available.

    Returns:
        Storage client instance, or `None` when import/credentials fail.

    Raises:
        None: Exceptions are converted into warning logs and `None`.

    Side Effects:
        Imports `google.cloud.storage` lazily and logs availability warnings.

    Latency:
        Small local import/client-construction cost.
    """
    try:
        from google.cloud import storage  # type: ignore[import-not-found]

        return storage.Client()
    except Exception as e:  # pragma: no cover
        logger.warning("[GCS] GCS client unavailable, using local fallback: %s", e)
        return None


def upload_bytes(bucket: str, key: str, data: bytes, content_type: str) -> str:
    """Store binary data and return retrievable object location.

    Args:
        bucket: GCS bucket name.
        key: Object key within bucket/fallback storage.
        data: Raw bytes to store.
        content_type: MIME type recorded on uploaded object.

    Returns:
        Signed URL (preferred) or public URL when GCS upload succeeds; local
        filesystem path string when fallback write is used.

    Raises:
        None: Upload errors degrade to local fallback writes.

    Side Effects:
        Network call to GCS on primary path.
        Creates parent directories and writes local files on fallback.

    Latency:
        Dominated by network upload/signing on GCS path; local disk I/O on fallback.
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
        logger.warning("[GCS] Upload failed, using local fallback: %s", e)
        path = _local_path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)


def download_bytes(bucket: str, key: str) -> bytes:
    """Fetch object bytes from storage with local fallback.

    Args:
        bucket: GCS bucket name.
        key: Object key to read.

    Returns:
        Raw object bytes.

    Raises:
        FileNotFoundError: If fallback local file does not exist.
        OSError: If local file read fails.

    Side Effects:
        Performs GCS download network call on primary path.
        Reads local filesystem on fallback path.

    Latency:
        Dominated by network round-trip for GCS path, disk I/O for fallback.
    """
    client = _gcs_client()
    if not client:
        return _local_path_for_key(key).read_bytes()

    try:
        blob = client.bucket(bucket).blob(key)
        return blob.download_as_bytes()
    except Exception as e:
        logger.warning("[GCS] Download failed, trying local fallback: %s", e)
        return _local_path_for_key(key).read_bytes()


def list_objects(bucket: str, prefix: str) -> List[Dict[str, Any]]:
    """List objects under a prefix from GCS or fallback local storage.

    Args:
        bucket: GCS bucket name.
        prefix: Object prefix (directory-like key prefix).

    Returns:
        List of dictionaries containing `name`, `size`, and `updated`.

    Raises:
        None: Listing errors degrade to local filesystem glob fallback.

    Side Effects:
        Performs GCS list API call on primary path.
        Creates local fallback base directory when missing.
        Reads filesystem metadata for each matched file.

    Latency:
        Depends on object count; network-bound on GCS path.
    """
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
        logger.warning("[GCS] List failed, using local fallback: %s", e)
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
    """Delete object from GCS or fallback local storage (best-effort).

    Args:
        bucket: GCS bucket name.
        key: Object key to delete.

    Returns:
        None.

    Raises:
        None: Delete failures are swallowed after warning logging.

    Side Effects:
        Performs GCS delete API call when client is available.
        Attempts filesystem unlink on fallback/error path.

    Latency:
        Small network call on GCS path; local filesystem unlink on fallback.
    """
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
        logger.warning("[GCS] Delete failed, trying local fallback: %s", e)
        try:
            _local_path_for_key(key).unlink(missing_ok=True)
        except Exception:
            pass
