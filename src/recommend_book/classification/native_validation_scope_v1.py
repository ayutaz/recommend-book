"""Explicit invocation-local native validation scopes, never a global cache."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import threading

from .native_artifact_snapshot_v1 import CommandArtifactSnapshot


def _runtime_sources(root):
    return {p.relative_to(root).as_posix() for p in [*(root / 'src/recommend_book').rglob('*.py'), *(root / 'scripts').glob('*.py')]}


class NativeValidationScope:
    def __init__(self):
        self.snapshot = None
        self.closed = False
        self.report = None
        self.source_bytes = None
        self.source_root = None

    def check_source_receipts(self, root, expected):
        if self.closed:
            raise ValueError('Native validation scope already closed')
        root = Path(root).resolve(strict=True)
        if self.source_bytes is None:
            if _runtime_sources(root) != set(expected):
                raise ValueError('Native runtime source inventory differs')
            self.source_root = root
            self.source_bytes = {}
            for relative, digest in expected.items():
                alias = root / relative
                path = alias.resolve(strict=True)
                if not path.is_relative_to(root):
                    raise ValueError('Native runtime source escapes frozen root')
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != digest:
                    raise ValueError('Native runtime source receipt differs')
                self.source_bytes[relative] = (alias,path,raw,digest)
        elif expected != {key:value[3] for key,value in self.source_bytes.items()}:
            raise ValueError('Native runtime source context differs within scope')

    def check_receipt(self, command, expected):
        if self.closed:
            raise ValueError('Native validation scope already closed')
        if self.snapshot is None:
            self.snapshot = CommandArtifactSnapshot(command)
        self.snapshot.check_receipt(command, expected)

    def close(self):
        if self.closed:
            raise ValueError('Native validation scope already closed')
        self.closed = True
        self.report = self.snapshot.close() if self.snapshot is not None else None
        for alias,path,raw,_ in (self.source_bytes or {}).values():
            if alias.resolve(strict=True) != path or path.read_bytes() != raw:
                raise ValueError('Native runtime source changed before publication')
        if self.source_bytes is not None and _runtime_sources(self.source_root) != set(self.source_bytes):
            raise ValueError('Native runtime source inventory changed before publication')
        self.source_bytes = None
        return self.report


class NativeValidationScopes:
    """Owned by one guard, with separate concurrent worker state."""
    def __init__(self):
        self.local = threading.local()
        self.reports = []
        self.lock = threading.Lock()

    @property
    def current(self):
        value = getattr(self.local, 'value', None)
        if value is None or value.closed:
            raise ValueError('An active native validation scope is required')
        return value

    @contextmanager
    def validation(self):
        prior = getattr(self.local, 'value', None)
        if prior is not None:
            if prior.closed:
                raise ValueError('Native validation scope already closed')
            yield prior
            return
        scope = NativeValidationScope()
        self.local.value = scope
        try:
            yield scope
            report = scope.report if scope.closed else scope.close()
            if report is not None:
                with self.lock:
                    self.reports.append(report)
        finally:
            self.local.value = None
