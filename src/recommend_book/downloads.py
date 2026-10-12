"""Resume large published dumps, validating byte ranges before atomic completion."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from datetime import datetime, timezone

import httpx


def download_file(url: str, target: Path, *, client: httpx.Client | None = None) -> dict:
    target = Path(target)
    manifest_path = Path(str(target) + ".manifest.json")
    if target.exists() and manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["requested_url"] != url:
            raise ValueError("Existing snapshot is from a different URL")
        if target.stat().st_size == manifest["bytes"]:
            return manifest
        raise ValueError("Existing snapshot size does not match manifest")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(target) + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    owned = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(90, connect=30), headers={"User-Agent": "recommend-book/0.1 (https://github.com/ayutaz/recommend-book)", "Accept-Encoding": "identity"})
    checkpoint = Path(str(target) + ".checkpoint.json")
    prior = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {}
    if prior and prior.get("requested_url") != url:
        raise ValueError("Partial snapshot belongs to another URL")
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    if offset and prior.get("etag"):
        headers["If-Range"] = prior["etag"]
    started = time.monotonic()
    try:
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            total = None
            mode = "wb"
            if response.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("content-range", ""))
                if not match or int(match[1]) != offset or int(match[2]) < offset:
                    raise ValueError("Invalid resume byte range")
                total = int(match[3])
                mode = "ab"
            elif response.status_code == 200:
                offset = 0
                if response.headers.get("content-length"):
                    total = int(response.headers["content-length"])
            else:
                raise ValueError(f"Unexpected download status {response.status_code}")
            state = {"requested_url": url, "final_url": str(response.url), "etag": response.headers.get("etag"), "expected_bytes": total}
            checkpoint.write_text(json.dumps(state, indent=2), encoding="utf-8")
            done = offset
            last_progress = time.monotonic()
            with partial.open(mode) as stream:
                for chunk in response.iter_raw(chunk_size=1024 * 1024):
                    stream.write(chunk)
                    done += len(chunk)
                    if time.monotonic() - last_progress > 15:
                        stream.flush()
                        print(json.dumps({"file": target.name, "downloaded_bytes": done, "expected_bytes": total, "elapsed_seconds": round(time.monotonic() - started, 1)}), flush=True)
                        last_progress = time.monotonic()
            if total is not None and done != total:
                raise ValueError(f"Incomplete download: {done} bytes, expected {total}")
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        manifest = {**state, "bytes": done, "sha256": digest.hexdigest(), "completed_at": datetime.now(timezone.utc).isoformat()}
        partial.replace(target)
        temporary_manifest = Path(str(manifest_path) + ".tmp")
        temporary_manifest.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        temporary_manifest.replace(manifest_path)
        return manifest
    finally:
        if owned:
            client.close()
