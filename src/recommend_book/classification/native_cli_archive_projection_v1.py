"""Read-only replay adapter for manifests made by an older CLI binary.

The frozen projection modules remain byte-for-byte untouched.  A dedicated
worker loads one of those modules from an in-memory AST clone and changes only
zero-argument ``read_bytes()`` calls to an injected ``read(path)`` function.
Only the exact absolute ``codex.exe`` binding from the manifest may resolve to
the content-addressed archive.  Every other path is read normally.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import types


_MODULES = {
    "scale-native-classification-export-v1":
        ("recommend_book.scale_classification_v1", "scale_classification_v1.py"),
    "scale-content-native-classification-export-v1":
        ("recommend_book.scale_content_classification_v1", "scale_content_classification_v1.py"),
    "scale-content-native-classification-export-v2":
        ("recommend_book.scale_content_classification_v2", "scale_content_classification_v2.py"),
    "scale-content-native-classification-export-v3":
        ("recommend_book.scale_content_classification_v3", "scale_content_classification_v3.py"),
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _absolute(path) -> Path:
    value = Path(path)
    if not value.is_absolute():
        raise ValueError("Projection artifact path must be absolute")
    return value


def _verify_pin(path, expected_sha):
    path = _absolute(path).resolve(strict=True)
    _regular(path)
    if not isinstance(expected_sha, str) or _sha(path.read_bytes()) != expected_sha:
        raise ValueError("Pinned adapter/helper bytes differ")
    return path


def _codex_expected(expected):
    if not isinstance(expected, dict):
        raise ValueError("Manifest executable artifact bindings are required")
    result = {}
    for name, digest in expected.items():
        path = _absolute(name)
        if path.name.lower() == "codex.exe":
            key = str(path.resolve(strict=False))
            if key in result:
                raise ValueError("Only one logical codex.exe binding is supported")
            result[key] = digest
    return result


def _regular(path: Path) -> None:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("CLI projection artifact is not a regular file")


def _root(path) -> Path:
    raw = Path(path).absolute()
    if raw.is_symlink():
        raise ValueError("CLI archive root must be a real directory")
    result = raw.resolve(strict=True)
    if not result.is_dir():
        raise ValueError("CLI archive root must be a real directory")
    return result


def _load_spec(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Unable to load pinned adapter: " + str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ReadContext:
    def __init__(self, expected, archive_root):
        if not isinstance(expected, dict):
            raise ValueError("Manifest executable artifact bindings are required")
        self.expected = dict(expected)
        self.archive_root = _root(archive_root)
        codex = []
        for name, digest in self.expected.items():
            raw_name = _absolute(name)
            if raw_name.name.lower() == "codex.exe":
                codex.append((str(raw_name.resolve(strict=False)), digest))
        if len(codex) > 1:
            raise ValueError("Only one logical codex.exe binding is supported")
        self.codex_path, self.codex_digest = codex[0] if codex else (None, None)
        if self.codex_path is not None:
            path = Path(self.codex_path)
            if not isinstance(self.codex_digest, str) or len(self.codex_digest) != 64:
                raise ValueError("Invalid logical codex.exe binding")
            if any(ch not in "0123456789abcdef" for ch in self.codex_digest):
                raise ValueError("Invalid logical codex.exe binding")
        self.current = {}
        self.archived = {}

    def read(self, path):
        path = Path(path)
        resolved = path.resolve(strict=True)
        if self.codex_path is None or str(resolved) != self.codex_path:
            return path.read_bytes()
        _regular(resolved)
        raw = resolved.read_bytes()
        identity = (resolved.stat().st_dev, resolved.stat().st_ino, resolved.stat().st_size)
        previous = self.current.get(resolved)
        if previous is not None and previous != (identity, raw):
            raise ValueError("Current CLI changed during projection verification")
        self.current.setdefault(resolved, (identity, raw))
        if _sha(raw) == self.codex_digest:
            return raw
        digest = self.codex_digest
        candidate_raw = self.archive_root / digest / "codex.exe"
        if candidate_raw.is_symlink():
            raise ValueError("Archived CLI entry is not a regular file")
        try:
            candidate = candidate_raw.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError("Archived CLI bytes are unavailable") from exc
        if not candidate.is_relative_to(self.archive_root):
            raise ValueError("Archived CLI entry escapes archive root")
        _regular(candidate)
        archived = candidate.read_bytes()
        if _sha(archived) != digest:
            raise ValueError("Archived CLI bytes differ from manifest binding")
        archived_identity = (candidate.stat().st_dev, candidate.stat().st_ino, candidate.stat().st_size)
        previous = self.archived.get(candidate)
        if previous is not None and previous != (archived_identity, archived, digest):
            raise ValueError("Archived CLI changed during projection verification")
        self.archived.setdefault(candidate, (archived_identity, archived, digest))
        return archived

    def close(self):
        for path, (identity, raw) in self.current.items():
            if (path.stat().st_dev, path.stat().st_ino, path.stat().st_size) != identity or path.read_bytes() != raw:
                raise ValueError("Current CLI changed during projection verification")
        for path, (identity, raw, digest) in self.archived.items():
            _regular(path)
            if ((path.stat().st_dev, path.stat().st_ino, path.stat().st_size) != identity
                    or path.read_bytes() != raw or _sha(path.read_bytes()) != digest):
                raise ValueError("Archived CLI changed during projection verification")


@contextmanager
def _read_scope(expected, archive_root):
    context = _ReadContext(expected, archive_root)
    try:
        yield context
    finally:
        context.close()


class _ReadBytesTransform(ast.NodeTransformer):
    def visit_Call(self, node):
        node = self.generic_visit(node)
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "read_bytes"
                and not node.args and not node.keywords):
            replacement = ast.Call(func=ast.Name(id="read", ctx=ast.Load()),
                                   args=[node.func.value], keywords=[])
            return ast.copy_location(replacement, node)
        return node


def _load_projection(module_name, source_path, read_context):
    source_path = Path(source_path).resolve(strict=True)
    source = source_path.read_bytes()
    tree = _ReadBytesTransform().visit(ast.parse(source, filename=str(source_path)))
    ast.fix_missing_locations(tree)
    module = types.ModuleType(module_name)
    module.__file__ = str(source_path)
    module.__package__ = module_name.rpartition(".")[0]
    module.__loader__ = None
    module.__spec__ = None
    module.__dict__["read"] = read_context.read
    sys.modules[module_name] = module
    parent_name, _, child_name = module_name.rpartition(".")
    parent = sys.modules.get(parent_name)
    if parent is not None:
        setattr(parent, child_name, module)
    exec(compile(tree, str(source_path), "exec"), module.__dict__)
    return module


class _SubprocessProxy:
    def __init__(self, original, child_factory):
        self._original = original
        self._child_factory = child_factory

    def run(self, args, **kwargs):
        if not isinstance(args, (list, tuple)) or len(args) < 3:
            raise ValueError("Frozen verifier subprocess command is not a Python -c child")
        if Path(str(args[0])).resolve() != Path(sys.executable).resolve() or args[1] != "-c":
            raise ValueError("Frozen verifier attempted an unapproved subprocess")
        return self._original.run(self._child_factory(list(args)), **kwargs)

    def __getattr__(self, name):
        return getattr(self._original, name)


def _module_bindings():
    root = Path(__file__).resolve().parents[3] / "src" / "recommend_book"
    return [(name, (root / filename).resolve(strict=True))
            for name, filename in _MODULES.values()]


def _module_binding(document, manifest_path):
    version = document.get("version")
    if version not in _MODULES:
        raise ValueError("Unsupported frozen projection version")
    module_name, filename = _MODULES[version]
    host = dict(_module_bindings())
    source = host[module_name]
    runtime = Path(document["runtime_preparation"]).resolve(strict=True) / "runtime" / "src/recommend_book"
    child_name = "recommend_book.scale_classification_v1" if module_name == "recommend_book.scale_classification_v1" else "recommend_book.scale_content_classification_v1"
    child_source = (runtime / ("scale_classification_v1.py" if child_name.endswith("scale_classification_v1") else "scale_content_classification_v1.py")).resolve(strict=True)
    return module_name, source, child_name, child_source


def _child_code(adapter_path, adapter_sha, helper_path, helper_sha, module_name, source_path, archive_root, expected, original):
    payload = json.dumps({"adapter": str(adapter_path), "helper": str(helper_path),
                          "adapter_sha": adapter_sha, "helper_sha": helper_sha,
                          "module": module_name, "source": str(source_path),
                          "archive": str(archive_root), "expected": expected,
                          "original": original}, ensure_ascii=True, separators=(",", ":"))
    return (
        "import importlib.util,json,sys\n"
        f"_p=json.loads({payload!r})\n"
        "import hashlib\n"
        "def _pin(p,h):\n"
        " r=open(p,'rb').read()\n"
        " if hashlib.sha256(r).hexdigest()!=h: raise ValueError('Pinned adapter/helper bytes differ')\n"
        "_pin(_p['adapter'],_p['adapter_sha']); _pin(_p['helper'],_p['helper_sha'])\n"
        "def _load(p,n):\n"
        " s=importlib.util.spec_from_file_location(n,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); return m\n"
        "_a=_load(_p['adapter'],'native_cli_archive_projection_child')\n"
        "_h=_load(_p['helper'],'native_cli_archive_helper_child')\n"
        "_a._verify_pin(_p['adapter'],_p['adapter_sha']); _a._verify_pin(_p['helper'],_p['helper_sha'])\n"
        "with _h.native_cli_archive_scope(_p['archive']):\n"
        " with _a._read_scope(_p['expected'],_p['archive']) as _c:\n"
        "  _m=_a._load_projection(_p['module'],_p['source'],_c)\n"
        "  exec(compile(_p['original'],'<frozen-verifier-child>','exec'),{'__name__':'__main__','__package__':None})\n"
    )


def _worker_verify(manifest_path, expected_sha256, archive_root, adapter_path, adapter_sha,
                   helper_path, helper_sha):
    manifest_path = _absolute(manifest_path).resolve(strict=True)
    raw = manifest_path.read_bytes()
    if _sha(raw) != expected_sha256:
        raise ValueError("Classification export manifest SHA differs")
    document = json.loads(raw)
    module_name, source_path, child_name, child_source = _module_binding(document, manifest_path)
    adapter_path = _verify_pin(adapter_path, adapter_sha)
    helper_path = _verify_pin(helper_path, helper_sha)
    _root(archive_root)
    helper = _load_spec(helper_path, "native_cli_archive_helper_host")
    original_subprocess = subprocess

    child_expected = _codex_expected(document.get("artifact_sha256", {}))

    def child_factory(args):
        return [args[0], "-c", _child_code(adapter_path, adapter_sha, helper_path, helper_sha,
                                             child_name, child_source, _root(archive_root),
                                             child_expected, args[2]), *args[3:]]

    with helper.native_cli_archive_scope(archive_root):
        with _read_scope(document.get("artifact_sha256", {}), archive_root) as context:
            modules = {}
            for name, path in _module_bindings():
                modules[name] = _load_projection(name, path, context)
                modules[name].subprocess = _SubprocessProxy(original_subprocess, child_factory)
            result = modules[module_name].verify_catalogue_classification(manifest_path, expected_sha256)
    return result


def _worker_entry():
    if len(sys.argv) != 8:
        raise ValueError("Invalid native CLI archive projection worker arguments")
    result = _worker_verify(*sys.argv[1:])
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False))


def verify_catalogue_classification(manifest_path, expected_sha256, archive_root):
    """Run a frozen projection verifier in a dedicated read-only worker."""
    manifest_path = _absolute(manifest_path).resolve(strict=True)
    adapter_path = Path(__file__).resolve(strict=True)
    helper_path = (Path(__file__).with_name("native_cli_archive_v1.py")).resolve(strict=True)
    _regular(adapter_path); _regular(helper_path); _root(archive_root)
    adapter_sha = _sha(adapter_path.read_bytes())
    helper_sha = _sha(helper_path.read_bytes())
    raw = manifest_path.read_bytes()
    if _sha(raw) != expected_sha256:
        raise ValueError("Classification export manifest SHA differs")
    code = ("import importlib.util,sys; p=sys.argv[1]; s=importlib.util.spec_from_file_location('native_cli_archive_projection_worker',p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); sys.argv=[sys.argv[0],*sys.argv[2:]]; m._worker_entry()")
    process = subprocess.run([sys.executable, "-c", code, str(adapter_path), str(manifest_path), expected_sha256,
                              str(_root(archive_root)), str(adapter_path), adapter_sha, str(helper_path), helper_sha],
                             cwd=str(Path(__file__).resolve().parents[3]), capture_output=True,
                             shell=False, timeout=900)
    if manifest_path.read_bytes() != raw:
        raise ValueError("Classification export manifest changed during verification")
    if _sha(adapter_path.read_bytes()) != adapter_sha:
        raise ValueError("Projection adapter changed during verification")
    if _sha(helper_path.read_bytes()) != helper_sha:
        raise ValueError("Projection helper changed during verification")
    if process.returncode:
        raise ValueError("Frozen projection verification failed: " + process.stderr.decode("utf-8", "replace")[-2000:])
    return json.loads(process.stdout)


if __name__ == "__main__":
    _worker_entry()


__all__ = ["verify_catalogue_classification"]
