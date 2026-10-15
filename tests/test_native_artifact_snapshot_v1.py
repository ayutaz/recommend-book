import hashlib
import os
from pathlib import Path

import pytest

from recommend_book.classification.native_artifact_snapshot_v1 import verify_command_receipts


def test_batch_reads_fixed_bytes_twice_and_hashes_once(tmp_path, monkeypatch):
    executable = tmp_path / "tool"
    executable.write_bytes(b"original bytes")
    expected = {str(executable.resolve()): hashlib.sha256(executable.read_bytes()).hexdigest()}
    original = Path.read_bytes
    reads = []
    def observe(path):
        reads.append(path)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", observe)
    result = verify_command_receipts([str(executable)], [expected] * 12)
    assert len(reads) == 2
    assert result["receipt_count"] == 12
    assert result["bytes_hashed"] == len(b"original bytes")
    assert result["result_adoption_authorized"] is False


@pytest.mark.parametrize("change", ["same_size_mtime", "removed", "replaced", "foreign_receipt"])
def test_batch_refuses_changed_source_or_receipt(tmp_path, change):
    executable = tmp_path / "tool"
    executable.write_bytes(b"original")
    before = executable.stat()
    expected = {str(executable.resolve()): hashlib.sha256(b"original").hexdigest()}
    def receipts():
        yield expected
        if change == "same_size_mtime":
            executable.write_bytes(b"modified")
            os.utime(executable, ns=(before.st_atime_ns, before.st_mtime_ns))
        elif change == "removed":
            executable.unlink()
        elif change == "replaced":
            replacement = tmp_path / "replacement"
            replacement.write_bytes(b"original")
            replacement.replace(executable)
        yield {} if change == "foreign_receipt" else expected
    with pytest.raises((ValueError, FileNotFoundError)):
        verify_command_receipts([str(executable)], receipts())


def test_no_cache_survives_invocation(tmp_path):
    executable = tmp_path / "tool"
    executable.write_bytes(b"original")
    expected = {str(executable.resolve()): hashlib.sha256(b"original").hexdigest()}
    verify_command_receipts([str(executable)], [expected])
    executable.write_bytes(b"modified")
    with pytest.raises(ValueError, match="receipt differs"):
        verify_command_receipts([str(executable)], [expected])


def test_wrapper_and_script_bytes_are_both_bound(tmp_path):
    wrapper, script = tmp_path / "wrapper", tmp_path / "script"
    wrapper.write_bytes(b"wrapper")
    script.write_bytes(b"script")
    expected = {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [wrapper, script]}
    result = verify_command_receipts([str(wrapper), str(script)], [expected] * 3)
    assert result["content_reads"] == 4
    assert result["bytes_hashed"] == len(b"wrapperscript")


def test_changed_command_path_resolution_rejects_batch(tmp_path, monkeypatch):
    executable, replacement = tmp_path / "tool", tmp_path / "replacement"
    executable.write_bytes(b"original")
    replacement.write_bytes(b"original")
    expected = {str(executable.resolve()): hashlib.sha256(b"original").hexdigest()}
    resolve = Path.resolve
    redirected = False
    def resolve_alias(path, *args, **kwargs):
        return replacement if redirected and path == executable else resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve_alias)
    def receipts():
        nonlocal redirected
        yield expected
        redirected = True
    with pytest.raises(ValueError, match="alias changed"):
        verify_command_receipts([str(executable)], receipts())
