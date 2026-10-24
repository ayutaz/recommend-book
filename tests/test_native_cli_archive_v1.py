"""Synthetic checks for archived CLI receipt compatibility."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading

import pytest

from recommend_book.classification.native_artifact_snapshot_v1 import CommandArtifactSnapshot
from recommend_book.classification.native_validation_scope_v1 import NativeValidationScope, NativeValidationScopes
from recommend_book.classification.native_cli_archive_v1 import native_cli_archive_scope, resolve_cli_artifact


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path, current=b"cli-0.164", archived=b"cli-0.163"):
    current_path = tmp_path / "codex.exe"
    current_path.write_bytes(current)
    archive = tmp_path / "archive"
    old_hash = _digest(archived)
    (archive / old_hash).mkdir(parents=True)
    (archive / old_hash / "codex.exe").write_bytes(archived)
    command = [str(current_path)]
    return command, {str(current_path.resolve()): old_hash}, archive


def test_current_cli_keeps_existing_snapshot_and_restores_hook(tmp_path):
    current = b"cli-0.164"
    path = tmp_path / "codex.exe"
    path.write_bytes(current)
    command = [str(path)]
    expected = {str(path.resolve()): _digest(current)}
    original = NativeValidationScope.check_receipt
    owner = NativeValidationScopes()
    (tmp_path / "archive").mkdir()
    with native_cli_archive_scope(tmp_path / "archive"):
        with owner.validation():
            owner.current.check_receipt(command, expected)
    assert NativeValidationScope.check_receipt is original
    assert owner.reports[0]["executable_sha256"] == expected


def test_archived_cli_is_checked_by_old_logical_path_and_digest(tmp_path):
    command, expected, archive = _fixture(tmp_path)
    owner = NativeValidationScopes()
    with native_cli_archive_scope(archive):
        with owner.validation():
            owner.current.check_receipt(command, expected)
    report = owner.reports[0]
    assert report["executable_sha256"] == expected
    assert list(report["archived_paths"].values()) == list(expected.values())


def test_unknown_hash_and_archive_tamper_are_rejected(tmp_path):
    command, expected, archive = _fixture(tmp_path)
    unknown = dict(expected)
    unknown[next(iter(unknown))] = "0" * 64
    owner = NativeValidationScopes()
    with pytest.raises((ValueError, FileNotFoundError), match="Unknown|No such file"):
        with native_cli_archive_scope(archive):
            with owner.validation():
                owner.current.check_receipt(command, unknown)
    with pytest.raises(ValueError, match="changed"):
        with native_cli_archive_scope(archive):
            with owner.validation():
                owner.current.check_receipt(command, expected)
                archive_file = next(archive.rglob("codex.exe"))
                archive_file.write_bytes(b"tampered")


def test_scope_context_is_thread_local_and_restored_after_failure(tmp_path):
    command, expected, archive = _fixture(tmp_path)
    barrier = threading.Barrier(2)

    def run(_):
        owner = NativeValidationScopes()
        with native_cli_archive_scope(archive):
            with owner.validation():
                owner.current.check_receipt(command, expected)
                barrier.wait(timeout=5)
        return owner.reports[0]["receipt_count"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, range(2))) == [1, 1]
    assert NativeValidationScope.check_receipt is not None
    assert CommandArtifactSnapshot is not None


def test_archive_resolution_is_limited_to_codex_and_exact_content(tmp_path):
    command, expected, archive = _fixture(tmp_path)
    logical = Path(command[0])
    digest = next(iter(expected.values()))
    assert resolve_cli_artifact(logical, digest, archive) == archive / digest / "codex.exe"
    assert resolve_cli_artifact(logical, _digest(logical.read_bytes()), archive) == logical
    unrelated = tmp_path / "source.py"
    unrelated.write_bytes(b"current source")
    assert resolve_cli_artifact(unrelated, digest, archive) == unrelated
    (archive / digest / "codex.exe").write_bytes(b"bad archive")
    with pytest.raises(ValueError, match="hash"):
        resolve_cli_artifact(logical, digest, archive)


def test_shared_validation_hashes_archived_executable_once_per_scope(tmp_path, monkeypatch):
    from recommend_book.classification import native_cli_archive_v1 as helper
    command, expected, archive = _fixture(tmp_path)
    calls = []
    original = helper._ArchiveCommandSnapshot._archive_path
    def observed(self, path, digest):
        calls.append(digest)
        return original(self, path, digest)
    monkeypatch.setattr(helper._ArchiveCommandSnapshot, '_archive_path', observed)
    owner = NativeValidationScopes()
    with native_cli_archive_scope(archive):
        with owner.validation():
            for _ in range(20):
                owner.current.check_receipt(command, expected)
    assert calls == list(expected.values())
    assert owner.reports[0]['receipt_count'] == 20
