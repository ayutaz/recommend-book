"""Synthetic, read-only tests for frozen projection archive replay."""
import hashlib
import importlib.util
from pathlib import Path
import json
import shutil
import subprocess

from recommend_book.classification.native_cli_archive_projection_v1 import (
    _ReadBytesTransform,
    _ReadContext,
    _SubprocessProxy,
    _child_code,
    _load_projection,
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def archive_fixture(tmp_path):
    current = tmp_path / "codex.exe"
    current.write_bytes(b"cli-0.164")
    old = b"cli-0.163"
    old_hash = digest(old)
    archive = tmp_path / "archive"
    (archive / old_hash).mkdir(parents=True)
    (archive / old_hash / "codex.exe").write_bytes(old)
    return current, archive, old_hash


def test_ast_clone_injects_archive_read_without_touching_source(tmp_path):
    current, archive, old_hash = archive_fixture(tmp_path)
    source = tmp_path / "projection.py"
    source.write_text("def value(path):\n    return path.read_bytes()\n", encoding="utf-8")
    before = source.read_bytes()
    context = _ReadContext({str(current.resolve()): old_hash}, archive)
    module = _load_projection("synthetic_projection", source, context)
    assert module.value(current) == b"cli-0.163"
    context.close()
    assert source.read_bytes() == before


def test_codex_only_archive_and_unknown_hash_rejection(tmp_path):
    current, archive, old_hash = archive_fixture(tmp_path)
    python = tmp_path / "python.exe"
    python.write_bytes(b"current-python")
    context = _ReadContext({str(current.resolve()): old_hash,
                            str(python.resolve()): digest(b"old-python")}, archive)
    assert context.read(python) == b"current-python"
    context.close()
    unknown = _ReadContext({str(current.resolve()): "0" * 64}, archive)
    try:
        unknown.read(current)
    except ValueError as error:
        assert "Archived CLI" in str(error) or "archive" in str(error).lower()
    else:
        raise AssertionError("unknown archive hash was accepted")


def test_subprocess_proxy_delegates_only_python_c_children_with_preamble(tmp_path):
    calls = []

    class FakeSubprocess:
        def run(self, args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, b"[]", b"")

    original = "print('projection child')"
    child = _child_code(tmp_path / "adapter.py", "a" * 64,
                        tmp_path / "helper.py", "b" * 64,
                        "recommend_book.synthetic", tmp_path / "projection.py",
                        tmp_path, {"/tmp/codex.exe": "a" * 64}, original)
    proxy = _SubprocessProxy(FakeSubprocess(), lambda args: [args[0], "-c", child, *args[3:]])
    result = proxy.run([str(Path(__import__('sys').executable)), "-c", original, "arg"])
    assert result.returncode == 0
    assert "importlib.util" in calls[0][0][2]
    assert "projection child" in calls[0][0][2]


def test_dedicated_worker_and_python_child_replay_old_cli_and_reject_tamper(tmp_path):
    repo = tmp_path / "repo"
    package = repo / "src" / "recommend_book"
    classification = package / "classification"
    classification.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (classification / "__init__.py").write_text("", encoding="utf-8")
    (package / "classification" / "native_validation_scope_v1.py").write_text(
        "class NativeValidationScope:\n    def check_receipt(self,*args): pass\n", encoding="utf-8")

    adapter = classification / "native_cli_archive_projection_v1.py"
    helper = classification / "native_cli_archive_v1.py"
    source_adapter = Path(__file__).parents[1] / "src/recommend_book/classification/native_cli_archive_projection_v1.py"
    source_helper = Path(__file__).parents[1] / "src/recommend_book/classification/native_cli_archive_v1.py"
    shutil.copyfile(source_adapter, adapter)
    shutil.copyfile(source_helper, helper)
    spec = importlib.util.spec_from_file_location("synthetic_archive_adapter", adapter)
    synthetic_adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic_adapter)

    current = tmp_path / "codex.exe"
    current.write_bytes(b"cli-0.164")
    old = b"cli-0.163"
    old_hash = digest(old)
    archive = tmp_path / "archive"
    (archive / old_hash).mkdir(parents=True)
    archive_file = archive / old_hash / "codex.exe"
    archive_file.write_bytes(old)

    # All four host modules are present; the selected verifier launches a
    # genuine Python child which imports the runtime v1 clone.
    host_code = """
import json, subprocess, sys
from pathlib import Path
CODEX = __CODEX__
def verify_catalogue_classification(manifest_path, expected_sha256):
    code = 'import json,sys; from recommend_book.scale_classification_v1 import authenticated_rows; print(json.dumps(authenticated_rows(*sys.argv[1:]), sort_keys=True))'
    process = subprocess.run([sys.executable, '-c', code, str(manifest_path), expected_sha256], capture_output=True, shell=False)
    if process.returncode:
        raise ValueError(process.stderr.decode())
    return json.loads(process.stdout)
""".replace("__CODEX__", repr(str(current.resolve())))
    for name in ("scale_classification_v1.py", "scale_content_classification_v1.py",
                 "scale_content_classification_v2.py", "scale_content_classification_v3.py"):
        (package / name).write_text(host_code, encoding="utf-8")

    runtime_root = repo / "data" / "autonomous-next-v3" / "preparation" / "fixture" / "runtime"
    runtime = runtime_root / "src" / "recommend_book"
    runtime.mkdir(parents=True)
    child = runtime / "scale_classification_v1.py"
    child.write_text("""
import hashlib, json
from pathlib import Path
CODEX = __CODEX__
def authenticated_rows(*_args):
    return [{'qid': 'Qfixture', 'cli_sha256': hashlib.sha256(Path(CODEX).read_bytes()).hexdigest()}]
""".replace("__CODEX__", repr(str(current.resolve()))), encoding="utf-8")
    # The manifest path is read by the host adapter and the runtime path is
    # used only to establish the child source namespace.
    manifest = tmp_path / "manifest.json"
    pins = {str(current.resolve()): old_hash}
    # The old implementation serialized every historical artifact pin into
    # the child command line.  Keep a large manifest here to exercise the
    # bounded codex-only child payload on Windows.
    pins.update({str((tmp_path / f"unused-{index:05d}.json").resolve()): "0" * 64
                 for index in range(2200)})
    manifest.write_text(json.dumps({
        "version": "scale-native-classification-export-v1",
        "runtime_preparation": str(runtime_root.parent),
        "artifact_sha256": pins,
    }), encoding="utf-8")
    manifest_sha = digest(manifest.read_bytes())
    assert len(manifest.read_bytes()) > 32768
    rows = synthetic_adapter.verify_catalogue_classification(manifest, manifest_sha, archive)
    assert rows == [{"qid": "Qfixture", "cli_sha256": old_hash}]
    archive_file.write_bytes(b"tampered")
    try:
        synthetic_adapter.verify_catalogue_classification(manifest, manifest_sha, archive)
    except ValueError as error:
        assert "Archived CLI" in str(error) or "projection verification failed" in str(error)
    else:
        raise AssertionError("tampered archive was accepted")
