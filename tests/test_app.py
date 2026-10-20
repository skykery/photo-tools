import os
import sys
import json
import tempfile
import pytest
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module


class TestScanUnconvertedLogic:
    """Test the scan_unconverted function."""

    def test_empty_directory(self, tmp_path):
        """Folder with no files should return empty."""
        candidates = app_module.scan_unconverted(str(tmp_path))
        assert candidates == []

    def test_only_dng_files(self, tmp_path):
        """Folder with only .dng files is not a candidate."""
        (tmp_path / "image1.dng").touch()
        (tmp_path / "image2.dng").touch()
        candidates = app_module.scan_unconverted(str(tmp_path))
        assert candidates == []

    def test_only_raw_files(self, tmp_path):
        """Folder with only .orf/.nef files is a candidate for conversion."""
        (tmp_path / "image1.orf").touch()
        (tmp_path / "image2.nef").touch()
        candidates = app_module.scan_unconverted(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["path"] == str(tmp_path)
        assert candidates[0]["raw_count"] == 2
        assert "image1.orf" in candidates[0]["raw_files"]
        assert "image2.nef" in candidates[0]["raw_files"]

    def test_mixed_orf_and_dng(self, tmp_path):
        """Folder with .orf and .dng files is NOT a candidate for conversion."""
        (tmp_path / "image1.orf").touch()
        (tmp_path / "image2.dng").touch()
        candidates = app_module.scan_unconverted(str(tmp_path))
        assert candidates == []

    def test_nested_folders_unconverted(self, tmp_path):
        """Nested unconverted folders should be detected separately."""
        # Root folder with RAW only
        (tmp_path / "root.orf").touch()

        # Subfolder with RAW only
        subfolder = tmp_path / "subfolder"
        subfolder.mkdir()
        (subfolder / "sub.nef").touch()

        candidates = app_module.scan_unconverted(str(tmp_path))
        assert len(candidates) == 2

    def test_macos_metadata_files_ignored(self, tmp_path):
        """macOS metadata files (._*) should not be counted."""
        (tmp_path / "image.orf").touch()
        (tmp_path / "._image.orf").touch()

        candidates = app_module.scan_unconverted(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["raw_count"] == 1
        assert "._image.orf" not in candidates[0]["raw_files"]


class TestScanLogic:
    """Test the scan_candidates function."""

    def test_empty_directory(self, tmp_path):
        """Folder with no files should return empty."""
        candidates = app_module.scan_candidates(str(tmp_path))
        assert candidates == []

    def test_only_dng_files(self, tmp_path):
        """Folder with only .dng files is not a candidate."""
        (tmp_path / "image1.dng").touch()
        (tmp_path / "image2.dng").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert candidates == []

    def test_only_raw_files(self, tmp_path):
        """Folder with only .orf/.nef files is not a candidate."""
        (tmp_path / "image1.orf").touch()
        (tmp_path / "image2.nef").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert candidates == []

    def test_mixed_orf_and_dng(self, tmp_path):
        """Folder with .orf and .dng files is a candidate."""
        (tmp_path / "image1.orf").touch()
        (tmp_path / "image2.dng").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["path"] == str(tmp_path)
        assert candidates[0]["raw_count"] == 1
        assert candidates[0]["dng_count"] == 1
        assert "image1.orf" in candidates[0]["raw_files"]

    def test_mixed_nef_and_dng(self, tmp_path):
        """Folder with .nef and .dng files is a candidate."""
        (tmp_path / "photo.nef").touch()
        (tmp_path / "photo.dng").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["raw_count"] == 1
        assert candidates[0]["dng_count"] == 1

    def test_mixed_orf_nef_and_dng(self, tmp_path):
        """Folder with .orf, .nef and .dng files lists all raw files."""
        (tmp_path / "image1.orf").touch()
        (tmp_path / "image2.nef").touch()
        (tmp_path / "image1.dng").touch()
        (tmp_path / "image2.dng").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["raw_count"] == 2
        assert candidates[0]["dng_count"] == 2

    def test_case_insensitive_extensions(self, tmp_path):
        """Extensions should be matched case-insensitively."""
        (tmp_path / "image.ORF").touch()
        (tmp_path / "photo.DNG").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1

    def test_nested_folders(self, tmp_path):
        """Nested folders should be detected separately."""
        # Root folder with mixed files
        (tmp_path / "root.orf").touch()
        (tmp_path / "root.dng").touch()

        # Subfolder with mixed files
        subfolder = tmp_path / "subfolder"
        subfolder.mkdir()
        (subfolder / "sub.orf").touch()
        (subfolder / "sub.dng").touch()

        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 2
        paths = {c["path"] for c in candidates}
        assert str(tmp_path) in paths
        assert str(subfolder) in paths

    def test_macos_metadata_files_ignored(self, tmp_path):
        """macOS metadata files (._*) should not be counted."""
        (tmp_path / "image.orf").touch()
        (tmp_path / "image.dng").touch()
        # Add macOS metadata files
        (tmp_path / "._image.dng").touch()
        (tmp_path / "._image.orf").touch()
        (tmp_path / "._other.dng").touch()

        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["raw_count"] == 1  # Only image.orf
        assert candidates[0]["dng_count"] == 1  # Only image.dng
        assert "._image.orf" not in candidates[0]["raw_files"]
        assert "._image.dng" not in str(candidates[0])


class TestDeleteLogic:
    """Test the delete_raw_files function."""

    def test_delete_orf_files(self, tmp_path, monkeypatch):
        """Delete .orf files from directory."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        (tmp_path / "image.orf").touch()
        (tmp_path / "image.dng").touch()
        deleted, errors = app_module.delete_raw_files(str(tmp_path))
        assert "image.orf" in deleted
        assert len(errors) == 0
        assert (tmp_path / "image.orf").exists() is False
        assert (tmp_path / "image.dng").exists() is True

    def test_delete_nef_files(self, tmp_path, monkeypatch):
        """Delete .nef files from directory."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        (tmp_path / "photo.nef").touch()
        (tmp_path / "photo.dng").touch()
        deleted, errors = app_module.delete_raw_files(str(tmp_path))
        assert "photo.nef" in deleted
        assert (tmp_path / "photo.nef").exists() is False
        assert (tmp_path / "photo.dng").exists() is True

    def test_delete_multiple_raw_files(self, tmp_path, monkeypatch):
        """Delete multiple raw files."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        (tmp_path / "img1.orf").touch()
        (tmp_path / "img2.nef").touch()
        (tmp_path / "img3.dng").touch()
        deleted, errors = app_module.delete_raw_files(str(tmp_path))
        assert len(deleted) == 2
        assert "img1.orf" in deleted
        assert "img2.nef" in deleted

    def test_nonexistent_directory(self, tmp_path, monkeypatch):
        """Deleting from nonexistent directory returns error."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        nonexistent = str(tmp_path / "does_not_exist")
        deleted, errors = app_module.delete_raw_files(nonexistent)
        assert len(deleted) == 0
        assert len(errors) > 0

    def test_path_traversal_protection(self, tmp_path):
        """Path outside BASE_DIR should raise ValueError."""
        outside_path = "/etc/passwd"
        with pytest.raises(ValueError, match="outside base directory"):
            app_module.delete_raw_files(outside_path)


class TestConvertLogic:
    """Test the convert_folder function."""

    def test_convert_with_no_raw_files(self, tmp_path, monkeypatch):
        """Convert folder with no RAW files returns 0."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        converted, errors = app_module.convert_folder(str(tmp_path))
        assert converted == 0
        assert len(errors) == 0

    def test_convert_nonexistent_directory(self, tmp_path, monkeypatch):
        """Converting nonexistent directory returns error."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        nonexistent = str(tmp_path / "does_not_exist")
        converted, errors = app_module.convert_folder(nonexistent)
        assert converted == 0
        assert len(errors) > 0

    def test_convert_path_traversal_protection(self, tmp_path):
        """Path outside BASE_DIR should raise ValueError."""
        outside_path = "/etc/passwd"
        with pytest.raises(ValueError, match="outside base directory"):
            app_module.convert_folder(outside_path)

    def test_convert_uses_macos_app(self, tmp_path, monkeypatch):
        """On macOS with local app installed, should use native executable."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
        (tmp_path / "image.orf").touch()
        (tmp_path / "._image.orf").touch()

        original_isfile = os.path.isfile
        def mock_isfile(path):
            if path == app_module.MACOS_DNG_EXECUTABLE:
                return True
            return original_isfile(path)
        monkeypatch.setattr(app_module.os.path, 'isfile', mock_isfile)

        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        converted, errors = app_module.convert_folder(str(tmp_path))

        assert len(calls) == 1
        cmd = calls[0]
        assert cmd[0] == app_module.MACOS_DNG_EXECUTABLE
        assert "-dng1.7" in cmd
        assert str(tmp_path / "image.orf") in cmd
        assert "._image.orf" not in str(cmd)
        assert converted == 1

    def test_convert_macos_falls_back_to_docker(self, tmp_path, monkeypatch):
        """On macOS without local app, should fall back to Docker."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
        (tmp_path / "image.orf").touch()

        original_isfile = os.path.isfile
        def mock_isfile(path):
            if path == app_module.MACOS_DNG_EXECUTABLE:
                return False
            return original_isfile(path)
        monkeypatch.setattr(app_module.os.path, 'isfile', mock_isfile)

        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(cmd, list) and "inspect" in cmd:
                return type('Result', (), {'returncode': 0})()
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        converted, errors = app_module.convert_folder(str(tmp_path))

        docker_run_calls = [c for c in calls if isinstance(c, list) and c[0] == "docker" and "run" in c]
        assert len(docker_run_calls) == 1
        assert converted == 1

    def test_convert_calls_docker_on_linux(self, tmp_path, monkeypatch):
        """On Linux, should always use Docker."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        monkeypatch.setattr(app_module.sys, 'platform', 'linux')
        (tmp_path / "image.orf").touch()
        (tmp_path / "._image.orf").touch()

        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(cmd, list) and "inspect" in cmd:
                return type('Result', (), {'returncode': 0})()
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        converted, errors = app_module.convert_folder(str(tmp_path))

        docker_run_calls = [c for c in calls if isinstance(c, list) and c[0] == "docker" and "run" in c]
        assert len(docker_run_calls) == 1
        docker_run_call = docker_run_calls[0]
        assert "-v" in docker_run_call
        assert f"{tmp_path}:/work" in docker_run_call[docker_run_call.index("-v") + 1]
        assert "/work/image.orf" in docker_run_call
        assert "._image.orf" not in str(docker_run_call)
        assert converted == 1

    def test_convert_uses_local_converter_when_configured(self, tmp_path, monkeypatch):
        """Inside the app image, DNG_CONVERTER wrapper is called directly, no Docker."""
        photos = tmp_path / "photos"
        photos.mkdir()
        wrapper = tmp_path / "convert-dng"
        wrapper.touch()
        monkeypatch.setattr(app_module, 'BASE_DIR', str(photos))
        monkeypatch.setattr(app_module.sys, 'platform', 'linux')
        monkeypatch.setattr(app_module, 'LOCAL_DNG_CONVERTER', str(wrapper))
        (photos / "image.orf").touch()

        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        converted, errors = app_module.convert_folder(str(photos))

        assert len(calls) == 1
        assert calls[0][0] == str(wrapper)
        assert str(photos / "image.orf") in calls[0]
        assert "docker" not in calls[0]
        assert converted == 1
        assert errors == []

    def test_convert_docker_failure(self, tmp_path, monkeypatch):
        """Docker failure should return error with stderr details."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        monkeypatch.setattr(app_module.sys, 'platform', 'linux')
        (tmp_path / "image.orf").touch()

        def mock_run(cmd, **kwargs):
            if isinstance(cmd, list) and "inspect" in cmd:
                return type('Result', (), {'returncode': 0})()
            if isinstance(cmd, list) and "run" in cmd:
                raise app_module.subprocess.CalledProcessError(
                    1, cmd, stderr="wine crashed"
                )
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        converted, errors = app_module.convert_folder(str(tmp_path))
        assert converted == 0
        assert len(errors) == 1
        assert "wine crashed" in errors[0]

    def test_ensure_docker_image_pulls_if_missing(self, tmp_path, monkeypatch):
        """ensure_docker_image should pull from registry when image is missing."""
        calls = []
        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if isinstance(cmd, list) and "inspect" in cmd:
                return type('Result', (), {'returncode': 1})()
            return type('Result', (), {'returncode': 0})()

        monkeypatch.setattr(app_module.subprocess, 'run', mock_run)

        app_module.ensure_docker_image()

        assert len(calls) == 2  # inspect, docker pull
        assert "inspect" in calls[0]
        assert calls[1][0] == "docker" and "pull" in calls[1]
        assert app_module.DOCKER_IMAGE in calls[1]


class TestExpandedRawFormats:
    """Smoke tests proving the expanded RAW_EXTENSIONS set is honored end-to-end."""

    def test_scan_unconverted_picks_up_fuji_raf(self, tmp_path):
        """A folder with .raf should be detected as needing conversion."""
        (tmp_path / "image.raf").touch()
        candidates = app_module.scan_unconverted(str(tmp_path))
        assert len(candidates) == 1
        assert "image.raf" in candidates[0]["raw_files"]

    def test_scan_candidates_picks_up_canon_cr3_with_dng(self, tmp_path):
        """A folder with .cr3 + .dng should be a cleanup candidate."""
        (tmp_path / "shot.cr3").touch()
        (tmp_path / "shot.dng").touch()
        candidates = app_module.scan_candidates(str(tmp_path))
        assert len(candidates) == 1
        assert candidates[0]["raw_count"] == 1
        assert candidates[0]["dng_count"] == 1

    def test_delete_removes_sony_arw(self, tmp_path, monkeypatch):
        """delete_raw_files should remove .arw files too."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        (tmp_path / "a.arw").touch()
        (tmp_path / "a.dng").touch()
        deleted, errors = app_module.delete_raw_files(str(tmp_path))
        assert "a.arw" in deleted
        assert (tmp_path / "a.arw").exists() is False


class TestLightroomUpload:
    """Unit tests for Lightroom upload helpers."""

    @pytest.fixture(autouse=True)
    def isolate_state_file(self, tmp_path, monkeypatch):
        """Redirect state file to tmp so tests don't pollute real state."""
        monkeypatch.setattr(
            app_module, 'LIGHTROOM_STATE_FILE', str(tmp_path / "state.json")
        )

    def test_state_key_strips_extension(self):
        key = app_module._state_key("/foo/bar/IMG_001.ORF")
        assert key == "/foo/bar/IMG_001"

    def test_mark_uploaded_persists(self, tmp_path):
        f = tmp_path / "photo.orf"
        f.touch()
        app_module.mark_uploaded(str(f), "asset-xyz")
        state = app_module.load_upload_state()
        key = app_module._state_key(str(f))
        assert key in state
        assert state[key]["asset_id"] == "asset-xyz"
        assert state[key]["original_path"] == str(f)

    def test_mark_baseline_records_seen(self, tmp_path):
        f = tmp_path / "photo.orf"
        f.touch()
        marked = app_module.mark_baseline(str(f))
        assert marked is True
        state = app_module.load_upload_state()
        key = app_module._state_key(str(f))
        assert state[key]["asset_id"] is None
        assert state[key]["baseline"] is True

    def test_mark_baseline_idempotent(self, tmp_path):
        f = tmp_path / "photo.orf"
        f.touch()
        assert app_module.mark_baseline(str(f)) is True
        assert app_module.mark_baseline(str(f)) is False  # already recorded

    def test_scan_for_upload_finds_new_files(self, tmp_path):
        (tmp_path / "a.orf").touch()
        (tmp_path / "b.raf").touch()
        new = app_module.scan_for_upload(str(tmp_path))
        assert len(new) == 2
        names = {os.path.basename(p) for p in new}
        assert names == {"a.orf", "b.raf"}

    def test_scan_for_upload_ignores_macos_metadata(self, tmp_path):
        (tmp_path / "real.orf").touch()
        (tmp_path / "._real.orf").touch()
        new = app_module.scan_for_upload(str(tmp_path))
        assert len(new) == 1
        assert new[0].endswith("real.orf")

    def test_scan_for_upload_skips_already_uploaded(self, tmp_path):
        f = tmp_path / "a.orf"
        f.touch()
        app_module.mark_uploaded(str(f), "asset-1")
        new = app_module.scan_for_upload(str(tmp_path))
        assert new == []

    def test_scan_for_upload_skips_baseline(self, tmp_path):
        f = tmp_path / "a.orf"
        f.touch()
        app_module.mark_baseline(str(f))
        new = app_module.scan_for_upload(str(tmp_path))
        assert new == []

    def test_scan_skips_dng_when_orf_uploaded(self, tmp_path):
        """If IMG_001.ORF is uploaded, IMG_001.DNG in same folder is skipped
        (DNG isn't in RAW_EXTENSIONS but stem-key dedup is future-proof)."""
        orf = tmp_path / "IMG_001.orf"
        orf.touch()
        app_module.mark_uploaded(str(orf), "asset-1")
        # Create the converted sibling and also a second ORF with same stem
        (tmp_path / "IMG_001.dng").touch()
        (tmp_path / "IMG_001.cr3").touch()  # Even if re-imported as Canon raw same stem
        new = app_module.scan_for_upload(str(tmp_path))
        assert new == []  # stem-key dedup prevents re-upload

    def test_baseline_upload_dir_marks_all_existing(self, tmp_path):
        (tmp_path / "a.orf").touch()
        (tmp_path / "b.raf").touch()
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.nef").touch()
        count = app_module.baseline_upload_dir(str(tmp_path))
        assert count == 3
        # Second run: nothing new to baseline
        assert app_module.baseline_upload_dir(str(tmp_path)) == 0

    def test_upload_to_lightroom_success(self, tmp_path, monkeypatch):
        f = tmp_path / "photo.orf"
        f.write_bytes(b"fake raw data")

        calls = []

        class MockResp:
            def __init__(self, code, text='{"id": "cat-abc"}'):
                self.status_code = code
                self.text = text

        def mock_get(url, **kwargs):
            calls.append(("GET", url, kwargs))
            return MockResp(200, '{"id": "cat-abc"}')

        def mock_put(url, **kwargs):
            calls.append(("PUT", url, kwargs))
            return MockResp(201)

        monkeypatch.setattr(app_module.requests, 'get', mock_get)
        monkeypatch.setattr(app_module.requests, 'put', mock_put)

        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="cid", access_token="tok"
        )
        assert errors == []
        assert asset_id is not None
        assert len(asset_id) == 32
        # 1 GET /catalog + 2 PUTs (metadata + master)
        assert sum(1 for c in calls if c[0] == "GET") == 1
        assert sum(1 for c in calls if c[0] == "PUT") == 2
        # Verify auth headers were sent
        for _, _, kwargs in calls:
            assert kwargs["headers"]["Authorization"] == "Bearer tok"
            assert kwargs["headers"]["X-API-Key"] == "cid"

    def test_upload_to_lightroom_uses_provided_catalog(self, tmp_path, monkeypatch):
        f = tmp_path / "photo.orf"
        f.write_bytes(b"data")

        get_called = []

        def mock_get(url, **kwargs):
            get_called.append(url)
            return type('R', (), {'status_code': 200, 'text': '{"id": "x"}'})()

        def mock_put(url, **kwargs):
            return type('R', (), {'status_code': 201})()

        monkeypatch.setattr(app_module.requests, 'get', mock_get)
        monkeypatch.setattr(app_module.requests, 'put', mock_put)

        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="cid", access_token="tok", catalog_id="cat-preset"
        )
        assert asset_id is not None
        assert get_called == []  # no /catalog call when id provided

    def test_upload_to_lightroom_auth_failure(self, tmp_path, monkeypatch):
        f = tmp_path / "photo.orf"
        f.write_bytes(b"data")

        def mock_put(url, **kwargs):
            return type('R', (), {'status_code': 401, 'text': 'unauthorized'})()

        monkeypatch.setattr(app_module.requests, 'put', mock_put)

        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="cid", access_token="bad", catalog_id="cat-1"
        )
        assert asset_id is None
        assert any("401" in e for e in errors)

    def test_upload_to_lightroom_duplicate_412(self, tmp_path, monkeypatch):
        f = tmp_path / "photo.orf"
        f.write_bytes(b"duplicate raw bytes")

        put_calls = []

        def mock_put(url, **kwargs):
            put_calls.append(url)
            # Fail the metadata PUT with 412; master PUT must never be reached.
            return type('R', (), {'status_code': 412, 'text': 'duplicate'})()

        monkeypatch.setattr(app_module.requests, 'put', mock_put)

        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="cid", access_token="tok", catalog_id="cat-1"
        )
        assert errors == []
        assert asset_id is not None
        assert asset_id.startswith("duplicate:")
        # Only one PUT (metadata). Master upload must be skipped.
        assert len(put_calls) == 1
        assert "/master" not in put_calls[0]

    def test_upload_to_lightroom_sends_sha256_and_filesize(self, tmp_path, monkeypatch):
        data = b"hash-this-please"
        f = tmp_path / "photo.orf"
        f.write_bytes(data)

        meta_payload = {}

        def mock_put(url, **kwargs):
            if "/master" not in url:
                meta_payload["body"] = json.loads(kwargs["data"])
            return type('R', (), {'status_code': 201})()

        monkeypatch.setattr(app_module.requests, 'put', mock_put)

        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="cid", access_token="tok", catalog_id="cat-1"
        )
        assert asset_id is not None
        assert errors == []
        import_source = meta_payload["body"]["payload"]["importSource"]
        assert import_source["fileSize"] == len(data)
        assert len(import_source["sha256"]) == 64  # hex digest length
        # Verify it's the actual sha256 of the file contents
        import hashlib as _h
        assert import_source["sha256"] == _h.sha256(data).hexdigest()

    def test_upload_to_lightroom_missing_file(self):
        asset_id, errors = app_module.upload_to_lightroom(
            "/nonexistent/file.orf", client_id="c", access_token="t"
        )
        assert asset_id is None
        assert any("not found" in e.lower() for e in errors)

    def test_upload_to_lightroom_missing_credentials(self, tmp_path):
        f = tmp_path / "photo.orf"
        f.touch()
        asset_id, errors = app_module.upload_to_lightroom(
            str(f), client_id="", access_token=""
        )
        assert asset_id is None
        assert any("access_token" in e for e in errors)


class TestAlbumBuilder:
    """Unit tests for create_album()."""

    def test_create_album_happy_path(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        for name in ("a", "b", "c"):
            (base / name).mkdir()
            (base / name / "photo.jpg").write_bytes(b"x")
        dest = tmp_path / "albums"
        dest.mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        album_path, links = app_module.create_album(
            str(dest), "trip-2024", [str(base / "a"), str(base / "b"), str(base / "c")]
        )

        assert album_path == str(dest / "trip-2024")
        assert os.path.isdir(album_path)
        assert len(links) == 3
        for link in links:
            link_path = os.path.join(album_path, link["name"])
            assert os.path.islink(link_path)
            assert os.path.realpath(link_path) == link["source"]
            assert os.path.isdir(link_path)  # symlink resolves to a real dir

    def test_create_album_collision_appends_suffix(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        (base / "p1").mkdir()
        (base / "p1" / "shared").mkdir()
        (base / "p2").mkdir()
        (base / "p2" / "shared").mkdir()
        dest = tmp_path / "out"
        dest.mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        album_path, links = app_module.create_album(
            str(dest), "mix", [str(base / "p1" / "shared"), str(base / "p2" / "shared")]
        )
        names = [l["name"] for l in links]
        assert names == ["shared", "shared-2"]
        assert os.path.islink(os.path.join(album_path, "shared"))
        assert os.path.islink(os.path.join(album_path, "shared-2"))

    def test_create_album_rejects_source_outside_base(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        dest = tmp_path / "out"
        dest.mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        with pytest.raises(ValueError, match="outside base"):
            app_module.create_album(str(dest), "album", [str(outside)])
        # Target must NOT have been created.
        assert not (dest / "album").exists()

    def test_create_album_rejects_nonexistent_source(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        dest = tmp_path / "out"
        dest.mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        with pytest.raises(ValueError):
            app_module.create_album(str(dest), "album", [str(base / "missing")])
        assert not (dest / "album").exists()

    def test_create_album_rejects_unsafe_name(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        (base / "src").mkdir()
        dest = tmp_path / "out"
        dest.mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        for bad in ("", "foo/bar", "..", ".", "back\\slash"):
            with pytest.raises(ValueError):
                app_module.create_album(str(dest), bad, [str(base / "src")])

    def test_create_album_rejects_existing_target(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        (base / "src").mkdir()
        dest = tmp_path / "out"
        dest.mkdir()
        (dest / "taken").mkdir()
        (dest / "taken" / "sentinel").write_text("dont-touch")

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        with pytest.raises(ValueError, match="already exists"):
            app_module.create_album(str(dest), "taken", [str(base / "src")])
        # Pre-existing folder untouched.
        assert (dest / "taken" / "sentinel").read_text() == "dont-touch"

    def test_create_album_rejects_missing_dest(self, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        (base / "src").mkdir()

        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))
        with pytest.raises(ValueError, match="not an accessible directory"):
            app_module.create_album(
                str(tmp_path / "does-not-exist"), "album", [str(base / "src")]
            )


class TestFlaskApp:
    """Integration tests for Flask routes."""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        """Create a Flask test client."""
        app_module.app.config['TESTING'] = True
        # Redirect config + state files to tmp so tests never touch real files
        monkeypatch.setattr(app_module, 'CONFIG_FILE', str(tmp_path / "config.json"))
        monkeypatch.setattr(
            app_module, 'LIGHTROOM_STATE_FILE', str(tmp_path / "lightroom_uploaded.json")
        )
        monkeypatch.setattr(app_module, 'LIGHTROOM_CONFIG', {})
        # Clear job state before each test
        app_module.job_states.clear()
        app_module.job_queue.clear()
        with app_module.app.test_client() as client:
            yield client
        # Clean up after test
        app_module.job_states.clear()
        app_module.job_queue.clear()

    def test_home_route(self, client):
        """GET / should return homepage with tool list."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'Photo Tools' in response.data
        assert b'RAW Cleaner' in response.data
        assert b'Lightroom Uploader' in response.data
        assert b'Album Builder' in response.data

    def test_raw_cleaner_route(self, client):
        """GET /tools/raw-cleaner should return RAW Cleaner UI."""
        response = client.get('/tools/raw-cleaner')
        assert response.status_code == 200
        assert b'RAW Cleaner' in response.data

    def test_scan_route(self, client, tmp_path, monkeypatch):
        """GET /tools/raw-cleaner/api/scan should return JSON list when base_dir exists."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        response = client.get('/tools/raw-cleaner/api/scan')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_scan_route_missing_base_dir(self, client, tmp_path, monkeypatch):
        """Scan endpoints should return 400 with a helpful error when BASE_DIR doesn't exist."""
        missing = str(tmp_path / "does-not-exist")
        monkeypatch.setattr(app_module, 'BASE_DIR', missing)

        response = client.get('/tools/raw-cleaner/api/scan')
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data and missing in data["error"]
        assert data["base_dir"] == missing

        response = client.get('/tools/raw-cleaner/api/scan/unconverted')
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data and missing in data["error"]

    def test_queue_job_valid(self, client, tmp_path, monkeypatch):
        """POST /api/queue with valid path should create job."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        folder = tmp_path / "test_folder"
        folder.mkdir()

        response = client.post('/tools/raw-cleaner/api/queue',
                              json={"path": str(folder)},
                              content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert "job_id" in data
        assert data["status"] == "pending"

    def test_queue_job_missing_path(self, client):
        """POST /api/queue without path should return 400."""
        response = client.post('/tools/raw-cleaner/api/queue',
                              json={},
                              content_type='application/json')
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_queue_job_path_traversal(self, client):
        """POST /api/queue with path outside BASE_DIR should return 403."""
        response = client.post('/tools/raw-cleaner/api/queue',
                              json={"path": "/etc/passwd"},
                              content_type='application/json')
        assert response.status_code == 403

    def test_get_jobs_empty(self, client):
        """GET /api/jobs with no jobs should return empty list."""
        response = client.get('/tools/raw-cleaner/api/jobs')
        assert response.status_code == 200
        data = response.get_json()
        assert data == []

    def test_get_jobs_with_pending(self, client, tmp_path, monkeypatch):
        """GET /api/jobs should return job status."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        folder = tmp_path / "test"
        folder.mkdir()

        # Create a job
        queueResp = client.post('/tools/raw-cleaner/api/queue',
                               json={"path": str(folder)},
                               content_type='application/json')
        job_id = queueResp.get_json()["job_id"]

        # Get jobs
        response = client.get('/tools/raw-cleaner/api/jobs')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 1
        assert data[0]["id"] == job_id
        assert data[0]["status"] == "pending"

    def test_cancel_pending_job(self, client, tmp_path, monkeypatch):
        """DELETE /api/queue/<id> should cancel pending job."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        folder = tmp_path / "test"
        folder.mkdir()

        # Create a job
        queueResp = client.post('/tools/raw-cleaner/api/queue',
                               json={"path": str(folder)},
                               content_type='application/json')
        job_id = queueResp.get_json()["job_id"]

        # Cancel it
        response = client.delete(f'/tools/raw-cleaner/api/queue/{job_id}')
        assert response.status_code == 200

        # Verify it's gone
        jobsResp = client.get('/tools/raw-cleaner/api/jobs')
        jobs = jobsResp.get_json()
        job_ids = [j["id"] for j in jobs]
        assert job_id not in job_ids

    def test_cancel_nonexistent_job(self, client):
        """DELETE /api/queue/<id> for nonexistent job should return 404."""
        response = client.delete('/tools/raw-cleaner/api/queue/nonexistent-id')
        assert response.status_code == 404

    def test_worker_processes_job(self, client, tmp_path, monkeypatch):
        """Background worker should process pending jobs."""
        import time

        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        folder = tmp_path / "test"
        folder.mkdir()
        (folder / "image.orf").touch()
        (folder / "image.dng").touch()

        # Create a job
        queueResp = client.post('/tools/raw-cleaner/api/queue',
                               json={"path": str(folder)},
                               content_type='application/json')
        job_id = queueResp.get_json()["job_id"]

        # Wait for worker to process
        time.sleep(2)

        # Check job status
        jobsResp = client.get('/tools/raw-cleaner/api/jobs')
        jobs = jobsResp.get_json()
        job = next((j for j in jobs if j["id"] == job_id), None)

        assert job is not None
        assert job["status"] == "done"
        assert "image.orf" in job["deleted"]
        assert (folder / "image.orf").exists() is False
        assert (folder / "image.dng").exists() is True

    def test_get_config(self, client):
        """GET /api/config should return current base_dir."""
        response = client.get('/tools/raw-cleaner/api/config')
        assert response.status_code == 200
        data = response.get_json()
        assert "base_dir" in data
        assert isinstance(data["base_dir"], str)

    def test_set_config_valid_path(self, client, tmp_path, monkeypatch):
        """POST /api/config with valid path should save it."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))

        response = client.post('/tools/raw-cleaner/api/config',
                              json={"base_dir": str(tmp_path)},
                              content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert data["base_dir"] == str(tmp_path)
        assert app_module.BASE_DIR == str(tmp_path)

    def test_set_config_nonexistent_path(self, client):
        """POST /api/config with nonexistent path should return 400."""
        response = client.post('/tools/raw-cleaner/api/config',
                              json={"base_dir": "/nonexistent/path/12345"},
                              content_type='application/json')
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data

    def test_set_config_missing_path(self, client):
        """POST /api/config without base_dir should return 400."""
        response = client.post('/tools/raw-cleaner/api/config',
                              json={},
                              content_type='application/json')
        assert response.status_code == 400

    def test_scan_unconverted_route(self, client, tmp_path, monkeypatch):
        """GET /api/scan/unconverted should return unconverted folders."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))

        # Create a RAW-only folder
        raw_only = tmp_path / "raw_only"
        raw_only.mkdir()
        (raw_only / "image.orf").touch()

        response = client.get('/tools/raw-cleaner/api/scan/unconverted')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data) == 1
        assert data[0]["path"] == str(raw_only)
        assert data[0]["raw_count"] == 1

    def test_convert_route_valid(self, client, tmp_path, monkeypatch):
        """POST /api/convert with valid path should create convert job."""
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        folder = tmp_path / "test_folder"
        folder.mkdir()

        response = client.post('/tools/raw-cleaner/api/convert',
                              json={"path": str(folder)},
                              content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert "job_id" in data
        assert data["status"] == "pending"

        # Verify job is in job_states with correct type
        assert data["job_id"] in app_module.job_states
        job = app_module.job_states[data["job_id"]]
        assert job["type"] == "convert"

    def test_convert_route_missing_path(self, client):
        """POST /api/convert without path should return 400."""
        response = client.post('/tools/raw-cleaner/api/convert',
                              json={},
                              content_type='application/json')
        assert response.status_code == 400

    def test_convert_route_path_traversal(self, client):
        """POST /api/convert with path outside BASE_DIR should return 403."""
        response = client.post('/tools/raw-cleaner/api/convert',
                              json={"path": "/etc/passwd"},
                              content_type='application/json')
        assert response.status_code == 403

    def test_browse_valid_directory(self, client, tmp_path):
        """POST /api/browse with valid directory should list subdirectories."""
        # Create test subdirectories
        (tmp_path / "folder1").mkdir()
        (tmp_path / "folder2").mkdir()
        (tmp_path / ".hidden").mkdir()  # Should be ignored
        (tmp_path / "file.txt").touch()  # Should be ignored

        response = client.post('/tools/raw-cleaner/api/browse',
                              json={"path": str(tmp_path)},
                              content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert data["current_path"] == str(tmp_path)
        assert len(data["entries"]) == 2
        names = [e["name"] for e in data["entries"]]
        assert "folder1" in names
        assert "folder2" in names
        assert ".hidden" not in names

    def test_browse_nonexistent_path(self, client):
        """POST /api/browse with nonexistent path should return 400."""
        response = client.post('/tools/raw-cleaner/api/browse',
                              json={"path": "/nonexistent/path/12345"},
                              content_type='application/json')
        assert response.status_code == 400

    def test_browse_parent_path(self, client, tmp_path):
        """POST /api/browse should return parent_path for navigation."""
        subfolder = tmp_path / "subfolder"
        subfolder.mkdir()

        response = client.post('/tools/raw-cleaner/api/browse',
                              json={"path": str(subfolder)},
                              content_type='application/json')
        assert response.status_code == 200
        data = response.get_json()
        assert "parent_path" in data
        assert data["parent_path"] == str(tmp_path)

    # -----------------------------
    # Lightroom Uploader routes
    # -----------------------------

    def test_home_lists_lightroom(self, client):
        """Homepage should list both RAW Cleaner and Lightroom Uploader."""
        response = client.get('/')
        assert response.status_code == 200
        assert b'RAW Cleaner' in response.data
        assert b'Lightroom Uploader' in response.data

    def test_lightroom_uploader_route(self, client):
        """GET /tools/lightroom-uploader should return the tool UI."""
        response = client.get('/tools/lightroom-uploader')
        assert response.status_code == 200
        assert b'Lightroom Uploader' in response.data

    def test_lr_config_get_empty(self, client):
        """GET /tools/lightroom-uploader/api/config returns empty defaults."""
        response = client.get('/tools/lightroom-uploader/api/config')
        assert response.status_code == 200
        data = response.get_json()
        assert data["upload_dir"] == ""
        assert data["has_access_token"] is False

    def test_lr_config_set_get_roundtrip(self, client, tmp_path, monkeypatch):
        """POST config then GET returns the same upload_dir; token is masked."""
        upload_dir = tmp_path / "usb-imports"
        upload_dir.mkdir()

        response = client.post(
            '/tools/lightroom-uploader/api/config',
            json={
                "upload_dir": str(upload_dir),
                "client_id": "adobe-cid",
                "access_token": "very-secret-token-12345",
                "catalog_id": "cat-xyz",
            },
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["upload_dir"] == str(upload_dir)
        assert data["client_id"] == "adobe-cid"
        assert data["has_access_token"] is True
        # Token should be masked, never echoed raw
        assert "very-secret-token-12345" not in str(data)
        assert "***" in data["access_token_masked"] or "..." in data["access_token_masked"]

    def test_lr_config_rejects_bad_upload_dir(self, client):
        response = client.post(
            '/tools/lightroom-uploader/api/config',
            json={"upload_dir": "/nonexistent/xyz/12345"},
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_lr_trigger_without_config(self, client):
        """Trigger with no upload_dir configured returns 400."""
        response = client.post(
            '/tools/lightroom-uploader/api/trigger',
            json={},
            content_type='application/json',
        )
        assert response.status_code == 400

    def test_lr_trigger_queues_jobs(self, client, tmp_path, monkeypatch):
        """Trigger should enqueue one upload job per new RAW file."""
        # Disable worker from actually processing during this test
        monkeypatch.setattr(app_module, 'LIGHTROOM_CONFIG', {
            "upload_dir": str(tmp_path),
            "client_id": "",  # empty so even if worker runs, upload fails cleanly
            "access_token": "",
        })
        (tmp_path / "one.orf").touch()
        (tmp_path / "two.raf").touch()
        (tmp_path / "ignored.txt").touch()

        response = client.post(
            '/tools/lightroom-uploader/api/trigger',
            json={"path": str(tmp_path)},
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["queued"] == 2
        assert len(data["job_ids"]) == 2

    def test_lr_trigger_skips_already_uploaded(self, client, tmp_path, monkeypatch):
        """A second trigger after a successful upload should queue 0 new jobs."""
        monkeypatch.setattr(app_module, 'LIGHTROOM_CONFIG', {
            "upload_dir": str(tmp_path),
        })
        f = tmp_path / "photo.orf"
        f.touch()
        # Simulate prior upload
        app_module.mark_uploaded(str(f), "prev-asset-id")

        response = client.post(
            '/tools/lightroom-uploader/api/trigger',
            json={"path": str(tmp_path)},
            content_type='application/json',
        )
        assert response.status_code == 200
        assert response.get_json()["queued"] == 0

    def test_lr_baseline_snapshot(self, client, tmp_path, monkeypatch):
        """Baseline should mark existing files; subsequent trigger queues nothing."""
        monkeypatch.setattr(app_module, 'LIGHTROOM_CONFIG', {
            "upload_dir": str(tmp_path),
        })
        (tmp_path / "old1.orf").touch()
        (tmp_path / "old2.raf").touch()

        response = client.post('/tools/lightroom-uploader/api/baseline')
        assert response.status_code == 200
        assert response.get_json()["marked"] == 2

        # New trigger should queue nothing
        trigger_resp = client.post(
            '/tools/lightroom-uploader/api/trigger',
            json={"path": str(tmp_path)},
            content_type='application/json',
        )
        assert trigger_resp.get_json()["queued"] == 0

        # But a new file arriving after baseline is queued
        (tmp_path / "new.orf").touch()
        trigger_resp = client.post(
            '/tools/lightroom-uploader/api/trigger',
            json={"path": str(tmp_path)},
            content_type='application/json',
        )
        assert trigger_resp.get_json()["queued"] == 1

    def test_lr_status_returns_uploads_and_baseline(self, client, tmp_path, monkeypatch):
        """Status endpoint returns uploaded list, baseline count, and jobs."""
        monkeypatch.setattr(app_module, 'LIGHTROOM_CONFIG', {
            "upload_dir": str(tmp_path),
        })
        f1 = tmp_path / "uploaded.orf"
        f1.touch()
        app_module.mark_uploaded(str(f1), "asset-1")
        f2 = tmp_path / "baseline.orf"
        f2.touch()
        app_module.mark_baseline(str(f2))

        response = client.get('/tools/lightroom-uploader/api/status')
        assert response.status_code == 200
        data = response.get_json()
        assert len(data["uploaded"]) == 1
        assert data["uploaded"][0]["asset_id"] == "asset-1"
        assert data["baseline_count"] == 1

    # -- Album Builder routes -----------------------------------------------

    def test_album_builder_route(self, client):
        """GET /tools/album-builder should return the album builder UI."""
        response = client.get('/tools/album-builder')
        assert response.status_code == 200
        assert b'Album Builder' in response.data

    def test_album_config_returns_base_dir(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        response = client.get('/tools/album-builder/api/config')
        assert response.status_code == 200
        data = response.get_json()
        assert data["base_dir"] == str(tmp_path)

    def test_album_browse_returns_subdirs(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        (tmp_path / "alpha").mkdir()
        (tmp_path / "beta").mkdir()
        (tmp_path / "afile.txt").write_text("not a dir")
        (tmp_path / "snap1.jpg").write_bytes(b"\xff\xd8jpegdata")
        (tmp_path / "snap2.JPEG").write_bytes(b"\xff\xd8more")

        response = client.post(
            '/tools/album-builder/api/browse',
            json={"path": str(tmp_path)},
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        names = [e["name"] for e in data["entries"]]
        assert "alpha" in names and "beta" in names
        assert "afile.txt" not in names

        preview_names = [p["name"] for p in data["preview_files"]]
        assert "snap1.jpg" in preview_names
        assert "snap2.JPEG" in preview_names
        assert "afile.txt" not in preview_names
        # preview entries should also carry absolute paths
        for p in data["preview_files"]:
            assert os.path.isabs(p["path"])

    def test_album_file_serves_jpeg(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        jpeg_bytes = b"\xff\xd8\xff\xe0test-jpeg-data"
        f = tmp_path / "photo.jpg"
        f.write_bytes(jpeg_bytes)

        response = client.get(
            '/tools/album-builder/api/file',
            query_string={"path": str(f)},
        )
        assert response.status_code == 200
        assert response.mimetype == "image/jpeg"
        assert response.data == jpeg_bytes

    def test_album_file_rejects_outside_base(self, client, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "elsewhere.jpg"
        outside.write_bytes(b"\xff\xd8nope")
        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))

        response = client.get(
            '/tools/album-builder/api/file',
            query_string={"path": str(outside)},
        )
        assert response.status_code == 403
        assert "error" in response.get_json()

    def test_album_file_rejects_non_jpeg(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        f = tmp_path / "raw.orf"
        f.write_bytes(b"raw-bytes")

        response = client.get(
            '/tools/album-builder/api/file',
            query_string={"path": str(f)},
        )
        assert response.status_code == 400
        assert "JPEG" in response.get_json()["error"]

    def test_album_file_missing(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))

        response = client.get(
            '/tools/album-builder/api/file',
            query_string={"path": str(tmp_path / "missing.jpg")},
        )
        assert response.status_code == 404
        assert "error" in response.get_json()

    def test_album_browse_missing_path(self, client, tmp_path, monkeypatch):
        monkeypatch.setattr(app_module, 'BASE_DIR', str(tmp_path))
        response = client.post(
            '/tools/album-builder/api/browse',
            json={"path": str(tmp_path / "nope")},
            content_type='application/json',
        )
        assert response.status_code == 400
        assert "error" in response.get_json()

    def test_album_create_success(self, client, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        (base / "src1").mkdir()
        (base / "src2").mkdir()
        dest = tmp_path / "albums"
        dest.mkdir()
        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))

        response = client.post(
            '/tools/album-builder/api/create',
            json={
                "album_dir": str(dest),
                "album_name": "my-album",
                "sources": [str(base / "src1"), str(base / "src2")],
            },
            content_type='application/json',
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data["album_path"] == str(dest / "my-album")
        assert len(data["links"]) == 2
        assert os.path.isdir(data["album_path"])
        for link in data["links"]:
            assert os.path.islink(os.path.join(data["album_path"], link["name"]))

    def test_album_create_validation_error(self, client, tmp_path, monkeypatch):
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        dest = tmp_path / "albums"
        dest.mkdir()
        monkeypatch.setattr(app_module, 'BASE_DIR', str(base))

        response = client.post(
            '/tools/album-builder/api/create',
            json={
                "album_dir": str(dest),
                "album_name": "bad",
                "sources": [str(outside)],
            },
            content_type='application/json',
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "error" in data
        assert not (dest / "bad").exists()
