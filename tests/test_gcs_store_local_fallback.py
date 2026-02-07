"""Tests for local-filesystem fallback behavior in `app.utils.gcs_store`.

These tests force the storage helper into no-GCS-client mode and verify that
upload/download/list/delete and key-to-path mapping still work deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.utils import gcs_store


def test_local_fallback_upload_download_list_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify full CRUD-style storage operations in local fallback mode."""
    # Force local fallback mode (no GCS client).
    monkeypatch.setattr(gcs_store, "_gcs_client", lambda: None)
    monkeypatch.setattr(gcs_store, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(gcs_store, "_REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(gcs_store, "_TMP_AUDIO_DIR", tmp_path / "tmp_audio")

    key = "reports/example.pdf"
    content = b"%PDF-1.7\n"
    written = gcs_store.upload_bytes("unused-bucket", key, content, "application/pdf")

    written_path = Path(written)
    assert written_path.exists()
    assert gcs_store.download_bytes("unused-bucket", key) == content

    objects = gcs_store.list_objects("unused-bucket", "reports/")
    assert any(obj.get("name") == "reports/example.pdf" for obj in objects)

    gcs_store.delete_object("unused-bucket", key)
    assert not written_path.exists()


def test_local_path_for_key_prefix_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Ensure known key prefixes map to expected local fallback directories."""
    monkeypatch.setattr(gcs_store, "_BASE_DIR", tmp_path)
    monkeypatch.setattr(gcs_store, "_REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(gcs_store, "_TMP_AUDIO_DIR", tmp_path / "tmp_audio")

    report_path = gcs_store._local_path_for_key("reports/file.pdf")
    audio_path = gcs_store._local_path_for_key("tmp_audio/sample.m4a")
    other_path = gcs_store._local_path_for_key("misc/data.json")

    assert report_path == (tmp_path / "reports" / "file.pdf")
    assert audio_path == (tmp_path / "tmp_audio" / "sample.m4a")
    assert other_path == (tmp_path / "misc" / "data.json")
