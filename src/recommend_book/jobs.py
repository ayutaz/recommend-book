"""Per-job durable state with expiring leases and atomic replace writes.

OS file locks serialize claim/update across threads and processes. No worker
writes SQLite. Lease tokens prevent an expired worker overwriting a new owner.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def job_key(input_digest: str, config: dict) -> str:
    return hashlib.sha256(canonical_json({"input": input_digest, "config": config}).encode()).hexdigest()


def atomic_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical_json(value) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class JobStore:
    def __init__(self, directory: Path, *, clock=time.time, max_attempts: int=3):
        self.directory = Path(directory) / "jobs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.max_attempts = max_attempts
        self._thread_lock = threading.RLock()

    def _path(self, key):
        if not key or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in key):
            raise ValueError("Unsafe job key")
        return self.directory / f"{key}.json"

    @contextmanager
    def _locked(self, key):
        path = self._path(key).with_suffix(".lock")
        with self._thread_lock, path.open("a+b") as stream:
            if os.name == "nt":
                import msvcrt
                # Windows permits locking beyond EOF. Writing an initializer
                # before acquiring the lock races another owner's byte lock.
                # https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking
                while True:
                    stream.seek(0)
                    try:
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.01)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def get(self, key) -> dict:
        path = self._path(key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def claim(self, key, *, lease_seconds=930, metadata=None) -> dict | None:
        with self._locked(key):
            state = self.get(key)
            now = self.clock()
            if state.get("status") in ("completed", "held"):
                return None
            if state.get("status") == "running" and state.get("lease_until", 0) > now:
                return None
            if state.get("attempts", 0) >= self.max_attempts:
                state.update(status="held", lease_until=None, error_category=state.get("error_category", "lease_expired"))
                atomic_json(self._path(key), state)
                return None
            state.update(key=key, status="running", attempts=state.get("attempts", 0)+1,
                         lease_until=now+lease_seconds, lease_token=uuid.uuid4().hex, updated_at=now)
            if metadata:
                state.update(metadata)
            atomic_json(self._path(key), state)
            return state

    def _finish(self, key, updates, lease_token, *, result=None):
        with self._locked(key):
            state = self.get(key)
            if state.get("status") != "running" or (lease_token and state.get("lease_token") != lease_token):
                raise ValueError("Job lease no longer owned")
            if result is not None:
                root = self.directory.parent.resolve()
                path = (root / updates["result_path"]).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("Result path outside job directory")
                atomic_json(path, result)
            state.update(updates, lease_until=None, updated_at=self.clock())
            atomic_json(self._path(key), state)

    def complete(self, key, result_path, *, lease_token=None, result=None, **metrics):
        self._finish(key, {"status":"completed", "result_path":str(result_path), **metrics}, lease_token, result=result)

    def fail(self, key, category, message, *, retryable=True, lease_token=None, **metrics):
        state = self.get(key)
        status = "pending" if retryable and state.get("attempts", 0) < self.max_attempts else "held"
        self._finish(key, {"status":status, "error_category":category, "error":message, **metrics}, lease_token)

    def invalidate(self, key):
        """Reclassify a completed job whose durable result failed revalidation."""
        with self._locked(key):
            state = self.get(key)
            if state.get("status") == "completed":
                state.update(status="pending", attempts=0, error_category="invalid_cached_result")
                atomic_json(self._path(key), state)
