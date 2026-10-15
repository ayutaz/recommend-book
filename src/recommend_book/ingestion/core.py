"""Immutable raw bytes, normalized JSONL, and serialized HTTP with bounded retries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import threading
import time
from typing import Callable, Iterable, Literal

import httpx

from recommend_book.models import SourceRecord


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class RawArtifact:
    sha256: str
    path: str
    size: int
    retrieved_at: str
    source_url: str


class RawStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def save(self, payload: bytes, *, source: str, url: str, metadata: dict | None = None) -> RawArtifact:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", source):
            raise ValueError("source must be a safe directory identifier")
        digest = hashlib.sha256(payload).hexdigest()
        path = self.root / source / digest[:2] / (digest + ".bin")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves the first copy; concurrent identical writes are harmless.
        try:
            with path.open("xb") as output:
                output.write(payload)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"raw object hash mismatch: {path}")
        artifact = RawArtifact(digest, str(path), len(payload), utc_now(), url)
        with self._lock, (self.root / "manifest.jsonl").open("a", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps({**artifact.__dict__, "source": source, "metadata": metadata or {}}, ensure_ascii=False) + "\n")
        return artifact


@dataclass
class FetchResult:
    status: Literal["success", "failed"]
    artifact: RawArtifact | None = None
    attempts: int = 0
    status_code: int | None = None
    error: str | None = None
    content: bytes = b""


@dataclass
class CollectionResult:
    records: list[SourceRecord] = field(default_factory=list)
    status: Literal["complete", "truncated", "failed"] = "complete"
    total_available: int | None = None
    next_token: str | None = None
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class HTTPFetcher:
    """A shared SQLite transaction serializes network calls across local worker processes.

    All workers must use the same RawStore root to share the rate gate. NDL requests
    to a host are serialized even while waiting for the response, not merely spaced.
    """
    def __init__(self, store: RawStore, *, client: httpx.Client | None = None,
                 user_agent: str = "recommend-book/0.1 research", min_interval: float = 1.0,
                 max_attempts: int = 3, sleep: Callable[[float], None] = time.sleep,
                 host_intervals: dict[str, float] | None = None):
        if min_interval < 0 or max_attempts < 1 or any(v < 0 for v in (host_intervals or {}).values()):
            raise ValueError("invalid HTTP limits")
        self.store, self.min_interval, self.max_attempts, self.sleep = store, min_interval, max_attempts, sleep
        self.host_intervals = dict(host_intervals or {})
        self.client = client or httpx.Client(timeout=60, follow_redirects=True)
        self.user_agent = user_agent
        self.gate_path = store.root / "http-rate-gate.sqlite3"
        with sqlite3.connect(self.gate_path, timeout=120) as db:
            db.execute("CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, last_request REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS cooldowns (host TEXT PRIMARY KEY, not_before REAL NOT NULL)")

    def _wait(self, seconds: float, *, host: str, reason: str, attempt: int):
        if seconds <= 0:
            return
        started = time.time()
        entry = {"host": host, "reason": reason, "attempt": attempt,
                 "requested_seconds": seconds, "started_at_epoch": started, "state": "pending"}
        wait_log = self.store.root / "http-waits.jsonl"
        with wait_log.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry) + "\n")
        self.sleep(seconds)
        entry.update(state="completed", observed_seconds=time.time() - started)
        with wait_log.open("a", encoding="utf-8") as output:
            output.write(json.dumps(entry) + "\n")

    def get(self, url: str, *, source: str, params: dict | None = None, headers: dict | None = None) -> FetchResult:
        host = httpx.URL(url).host
        request_headers = {"User-Agent": self.user_agent, **(headers or {})}
        error = None
        artifact = None
        code = None
        for attempt in range(1, self.max_attempts + 1):
            response = None
            try:
                with sqlite3.connect(self.gate_path, timeout=120) as db:
                    db.execute("BEGIN IMMEDIATE")
                    row = db.execute("SELECT last_request FROM hosts WHERE host=?", (host,)).fetchone()
                    cooldown = db.execute("SELECT not_before FROM cooldowns WHERE host=?", (host,)).fetchone()
                    interval = max(self.min_interval, self.host_intervals.get(host, 0))
                    not_before = max(row[0] + interval if row else 0, cooldown[0] if cooldown else 0)
                    self._wait(max(0, not_before - time.time()), host=host, reason="host_gate", attempt=attempt)
                    response = self.client.get(url, params=params, headers=request_headers)
                    db.execute("INSERT OR REPLACE INTO hosts VALUES (?,?)", (host, time.time()))
                    db.commit()
                code = response.status_code
                artifact = self.store.save(response.content, source=source, url=str(response.url), metadata={"status_code": code, "attempt": attempt, "retry_after": response.headers.get("Retry-After"), "configured_host_interval": interval})
                if 200 <= code < 300:
                    return FetchResult("success", artifact, attempt, code, content=response.content)
                error = f"HTTP {code}"
                if code not in {408, 429, 500, 502, 503, 504}:
                    break
            except (httpx.HTTPError, sqlite3.Error) as exc:
                error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts or response is not None and response.headers.get("Retry-After"):
                delay = min(60.0, 2 ** (attempt - 1) + random.random())
                if response is not None and response.headers.get("Retry-After"):
                    value = response.headers["Retry-After"]
                    try:
                        delay = max(0.0, float(value))
                    except ValueError:
                        try:
                            delay = max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
                        except (ValueError, TypeError):
                            pass
                # Persist server cooldown before sleeping, so interrupted/restarted
                # collectors using this raw root cannot accidentally skip Retry-After.
                with sqlite3.connect(self.gate_path, timeout=120) as db:
                    db.execute("INSERT INTO cooldowns VALUES (?,?) ON CONFLICT(host) DO UPDATE SET not_before=MAX(not_before, excluded.not_before)", (host, time.time() + delay))
                if attempt < self.max_attempts:
                    self._wait(delay, host=host, reason="retry_after" if response is not None and response.headers.get("Retry-After") else "retry_backoff", attempt=attempt)
        failed = FetchResult("failed", artifact, attempt, code, error)
        with (self.store.root / "failures.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps({"url": str(httpx.URL(url, params=params)), "source": source, "attempts": attempt, "status_code": code, "error": error, "at": utc_now()}) + "\n")
        return failed


def write_jsonl(records: Iterable[SourceRecord], path: str | Path) -> int:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    count = 0
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(record.model_dump_json() + "\n")
                count += 1
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return count


def read_jsonl(path: str | Path) -> Iterable[SourceRecord]:
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield SourceRecord.model_validate_json(line)
