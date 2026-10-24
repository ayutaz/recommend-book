"""Process-local compatibility for receipts made by an older CLI binary.

The request keeps its original logical command and executable digest.  When
the command at that path is still the current binary, the normal native
validation path is used.  When the current binary differs, this module can
resolve the expected digest from a content-addressed archive and validate
those archived bytes with the same close-time checks.

This module intentionally does not change source, prompt, usage, or model
validation.  It only supplies the executable bytes to the existing
``NativeValidationScope`` snapshot boundary for the duration of an explicit
process scope.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import importlib
from pathlib import Path
import stat
import threading
import weakref


_HOOK_LOCK = threading.RLock()
_HOOK_USERS = 0
_ORIGINAL_CHECK = None
_LOCAL = threading.local()


def _identity(path: Path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size


def _regular_file(path: Path) -> None:
    """Require an ordinary file, rejecting symlinked archive entries."""
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("CLI archive entry is not a regular file")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _archive_root(path) -> Path:
    raw = Path(path).absolute()
    if raw.is_symlink():
        raise ValueError("CLI archive root must be a real directory")
    root = raw.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("CLI archive root must be a real directory")
    return root


def resolve_cli_artifact(path, expected_sha256, archive_root):
    """Resolve only changed Codex executable pins; all other pins stay literal.

    The caller must still authenticate the returned file at its ordinary
    two-read artifact boundary. No source or evidence file has a fallback.
    """
    logical = Path(path)
    if logical.name.casefold() != "codex.exe":
        return logical
    if not logical.is_absolute():
        raise ValueError("CLI artifact path must be absolute")
    _regular_file(logical)
    with logical.open("rb") as stream:
        current = hashlib.file_digest(stream, "sha256").hexdigest()
    if current == expected_sha256:
        return logical
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        raise ValueError("Unknown archived CLI hash")
    root = _archive_root(archive_root)
    directory = root / expected_sha256
    candidate = directory / logical.name
    if directory.is_symlink() or candidate.is_symlink():
        raise ValueError("CLI archive entry is not a regular file")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("CLI archive entry escapes archive root")
    _regular_file(resolved)
    with resolved.open("rb") as stream:
        observed = hashlib.file_digest(stream, "sha256").hexdigest()
    if observed != expected_sha256:
        raise ValueError("Archived CLI hash differs from expected hash")
    return resolved


class _ArchiveCommandSnapshot:
    """The same contract as CommandArtifactSnapshot with an archive fallback."""

    def __init__(self, command, archive_root: Path):
        if not command:
            raise ValueError("A command is required")
        self.command = list(command)
        self.archive_root = _archive_root(archive_root)
        aliases = [Path(command[0]).absolute()]
        if len(command) > 1:
            aliases.append(Path(command[1]).absolute())
        self.resolved = {path: path.resolve(strict=True) for path in aliases}
        self.current = {}
        self.selected = {}
        self.expected = None
        self.receipt_count = 0
        self.closed = False
        for alias, path in self.resolved.items():
            _regular_file(path)
            raw = path.read_bytes()
            identity = _identity(path)
            if _identity(path) != identity:
                raise ValueError("Executable source changed during snapshot")
            self.current[alias] = (path, identity, raw, _hash(raw))
        self.hashes = {str(path): item[3] for path, item in self.current.items()}
        self.archive_observations = {}

    def _archive_path(self, logical_path: Path, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Unknown archived CLI hash")
        root = self.archive_root
        raw_candidate = root / digest / logical_path.name
        if raw_candidate.is_symlink():
            raise ValueError("CLI archive entry is not a regular file")
        try:
            candidate = raw_candidate.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ValueError("Unknown archived CLI hash") from exc
        if not candidate.is_relative_to(root):
            raise ValueError("CLI archive entry escapes archive root")
        _regular_file(candidate)
        raw = candidate.read_bytes()
        if _hash(raw) != digest:
            raise ValueError("Archived CLI bytes differ from expected hash")
        identity = _identity(candidate)
        if _identity(candidate) != identity:
            raise ValueError("Archived CLI changed during snapshot")
        self.archive_observations[candidate] = (identity, raw, digest)
        return candidate

    def check_receipt(self, command, expected):
        if self.closed:
            raise ValueError("Executable snapshot scope is closed")
        if list(command) != self.command or not isinstance(expected, dict) or set(expected) != set(self.hashes):
            raise ValueError("Executable receipt differs from fixed bytes")
        if self.expected is not None and expected != self.expected:
            raise ValueError("Executable receipt differs from fixed bytes")
        if self.expected is not None:
            # One validation operation already owns these exact bytes. Its
            # close boundary re-reads them before any result is accepted.
            self.receipt_count += 1
            return
        selected = {}
        for alias, (current_path, identity, raw, current_hash) in self.current.items():
            digest = expected[str(current_path)]
            if not isinstance(digest, str):
                raise ValueError("Unknown archived CLI hash")
            if digest == current_hash:
                selected[alias] = (current_path, identity, raw, digest, False)
            else:
                archive_path = self._archive_path(current_path, digest)
                selected[alias] = (archive_path, _identity(archive_path), archive_path.read_bytes(), digest, True)
        self.expected = dict(expected)
        self.selected = selected
        self.receipt_count += 1

    def close(self):
        if self.closed:
            raise ValueError("Executable snapshot scope is closed")
        self.closed = True
        if not self.receipt_count:
            raise ValueError("At least one expected receipt is required")
        for alias, (path, identity, raw, _) in self.current.items():
            if alias.resolve(strict=True) != path or _identity(path) != identity or path.read_bytes() != raw:
                raise ValueError("Executable source changed before batch completion")
        for path, (identity, raw, digest) in self.archive_observations.items():
            _regular_file(path)
            if _identity(path) != identity or path.read_bytes() != raw or _hash(path.read_bytes()) != digest:
                raise ValueError("Archived CLI changed before batch completion")
        report = {"version": "native-artifact-snapshot-v1",
                  "executable_sha256": dict(self.expected),
                  "receipt_count": self.receipt_count,
                  "content_reads": len(self.current) * 2 + len(self.archive_observations) * 2,
                  "content_bytes_read": sum(len(item[2]) for item in self.current.values()) * 2
                  + sum(len(item[1]) for item in self.archive_observations.values()) * 2,
                  "bytes_hashed": sum(len(item[2]) for item in self.current.values())
                  + sum(len(item[1]) for item in self.archive_observations.values()),
                  "archived_paths": {str(path): digest for path, (_, _, digest) in self.archive_observations.items()},
                  "result_adoption_authorized": False}
        self.current.clear()
        self.selected.clear()
        self.archive_observations.clear()
        return report


class _ArchiveContext:
    def __init__(self, archive_root):
        self.archive_root = _archive_root(archive_root)
        self.snapshots = weakref.WeakKeyDictionary()

    def check(self, scope, command, expected):
        if scope.closed:
            raise ValueError("Native validation scope already closed")
        snapshot = self.snapshots.get(scope)
        if snapshot is None:
            snapshot = _ArchiveCommandSnapshot(command, self.archive_root)
            self.snapshots[scope] = snapshot
            scope.snapshot = snapshot
        elif scope.snapshot is not snapshot:
            raise ValueError("Native validation scope snapshot changed")
        snapshot.check_receipt(command, expected)


def _patched_check(scope, command, expected):
    context = getattr(_LOCAL, "context", None)
    if context is None:
        return _ORIGINAL_CHECK(scope, command, expected)
    return context.check(scope, command, expected)


@contextmanager
def native_cli_archive_scope(archive_root):
    """Enable archived CLI resolution for the current thread and process call.

    The hook is installed only while at least one thread owns a scope.  Each
    thread receives its own archive context, while all other callers retain
    the original ``NativeValidationScope.check_receipt`` implementation.
    """
    global _HOOK_USERS, _ORIGINAL_CHECK
    context = _ArchiveContext(archive_root)
    module = importlib.import_module("recommend_book.classification.native_validation_scope_v1")
    scope_type = module.NativeValidationScope
    with _HOOK_LOCK:
        if _HOOK_USERS == 0:
            _ORIGINAL_CHECK = scope_type.check_receipt
            scope_type.check_receipt = _patched_check
        _HOOK_USERS += 1
    prior = getattr(_LOCAL, "context", None)
    _LOCAL.context = context
    try:
        yield context
    finally:
        if prior is None:
            try:
                del _LOCAL.context
            except AttributeError:
                pass
        else:
            _LOCAL.context = prior
        with _HOOK_LOCK:
            _HOOK_USERS -= 1
            if _HOOK_USERS == 0:
                scope_type.check_receipt = _ORIGINAL_CHECK
                _ORIGINAL_CHECK = None


# Descriptive aliases for frozen host integrations.
archive_cli_scope = native_cli_archive_scope
archived_cli_validation = native_cli_archive_scope


__all__ = ["native_cli_archive_scope", "archive_cli_scope", "archived_cli_validation", "resolve_cli_artifact"]
