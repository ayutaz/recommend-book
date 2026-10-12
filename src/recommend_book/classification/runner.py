"""Bounded Codex CLI scheduler. Events and final messages have separate files."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import threading
import time
import uuid

from recommend_book.jobs import JobStore, atomic_json, canonical_json, job_key
from recommend_book.models import WorkInput
from .prompt import build_prompt, build_batch_prompt
from .validation import ROOT, PROMPT_VERSION, input_hash, load_taxonomy, validate_result


def resolve_executable(executable: str | None=None) -> list[str]:
    """Avoid Windows .ps1/.cmd shims: resolve their observed npm native binary."""
    candidate = executable or shutil.which("codex")
    if not candidate:
        raise FileNotFoundError("Codex executable not found; pass executable explicitly")
    path = Path(candidate)
    if path.suffix.lower() in (".cmd", ".ps1") or (os.name == "nt" and path.name == "codex"):
        package = path.parent / "node_modules/@openai/codex"
        native = list(package.glob("node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"))
        if len(native) == 1:
            return [str(native[0].resolve())]
        script = package / "bin/codex.js"
        node = shutil.which("node")
        if script.exists() and node:
            return [node, str(script.resolve())]
        raise FileNotFoundError("Windows Codex shim has no native executable or Node entry")
    if path.suffix.lower() == ".js":
        node = shutil.which("node")
        if not node:
            raise FileNotFoundError("Node executable not found")
        return [node, str(path.resolve())]
    return [candidate]


def cli_version(command: list[str]) -> str:
    result = subprocess.run([*command, "--version"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=30, shell=False)
    if result.returncode:
        raise RuntimeError("Codex version check failed")
    return result.stdout.strip()


def parse_events(stdout: str) -> dict:
    usage, tools, event_errors = {}, [], []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed":
            for key, value in (event.get("usage") or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") and item.get("type") not in ("reasoning", "agent_message"):
            tools.append(item.get("type"))
        if event.get("type") in ("error", "turn.failed"):
            event_errors.append(str(event.get("message") or event.get("error") or event))
    return {"usage":usage, "tool_calls":tools, "event_errors":event_errors}


def _error_category(text: str) -> tuple[str, bool]:
    lower = text.lower()
    if any(word in lower for word in ("usage limit", "quota exceeded", "insufficient_quota", "weekly limit")):
        return "usage_limit", False
    if any(word in lower for word in ("unauthorized", "authentication", "invalid api key", "401")):
        return "authentication", False
    if any(word in lower for word in ("rate limit", "too many requests", "429")):
        return "rate_limit", True
    return "cli_exit", True


def _atomic_lines(path: Path, rows):
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("".join(canonical_json(row)+"\n" for row in rows), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run_classification(inputs: list[WorkInput], output_dir: Path, *, concurrency: int=2,
        model: str="gpt-6.1-sol", executable: str | None=None, limit: int | None=None,
        timeout: float=900, max_run_seconds: float | None=None,
        max_observed_tokens: int | None=None, batch_size: int=1) -> dict:
    """Classify a fixed snapshot with <=3 attempts, leases, cache and manifests.

    Budget bounds pause NEW submissions, allowing in-flight jobs to finish.
    CLI token usage is observed usage, not an API billing estimate. No fabricated
    all-unknown fallback is written when an invocation or validation fails.
    """
    if concurrency < 1 or timeout <= 0 or (limit is not None and limit < 0) or not 1 <= batch_size <= 10:
        raise ValueError("Invalid concurrency, timeout or limit")
    selected = inputs[:limit] if limit is not None else inputs
    identities = [(work.work_id, work.expression_id) for work in selected]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate work/expression inputs in one run")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for folder in ("attempts", "results", "workspace"):
        (output_dir / folder).mkdir(exist_ok=True)
    taxonomy = load_taxonomy()
    command = resolve_executable(executable) if selected else []
    version = cli_version(command) if selected else "not_invoked"
    config = {"model":model, "taxonomy_version":taxonomy["version"], "prompt_version":PROMPT_VERSION,
        "taxonomy_hash":hashlib.sha256(canonical_json(taxonomy).encode()).hexdigest(),
        "schema_hash":hashlib.sha256((ROOT / "schemas/classification.schema.json").read_bytes()).hexdigest(),
        "prompt_hash":hashlib.sha256((ROOT / "src/recommend_book/classification/prompt.py").read_bytes()).hexdigest(),
        "cli_version":version, "sandbox":"read-only", "reasoning_effort":"medium", "web_search":"disabled",
        "batch_size":batch_size}
    schema_path = ROOT / "schemas/classification.schema.json"
    batch_schema = {"type":"object", "additionalProperties":False, "required":["results"],
        "properties":{"results":{"type":"array", "items":json.loads(schema_path.read_text(encoding="utf-8"))}}}
    batch_schema_path = output_dir / "batch.schema.json"
    atomic_json(batch_schema_path, batch_schema)
    store = JobStore(output_dir)
    jobs = [(job_key(input_hash(work), config), work) for work in selected]
    started = time.monotonic()
    usage, durations, attempted = {}, [], []
    guard = threading.Lock()
    stop = threading.Event()
    throttled = threading.Event()
    reused = set()

    def budget_hit():
        observed = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        return (max_run_seconds is not None and time.monotonic()-started >= max_run_seconds) or (
            max_observed_tokens is not None and observed >= max_observed_tokens)

    def process(pairs):
        pending = []
        for key, work in pairs:
            result_path = output_dir / "results" / f"{key}.json"
            cached = store.get(key)
            if cached.get("status") == "completed":
                try:
                    validate_result(json.loads(result_path.read_text(encoding="utf-8")), work, taxonomy)
                    with guard:
                        reused.add(key)
                    continue
                except (ValueError, OSError):
                    store.invalidate(key)
            pending.append((key, work))
        while pending and not stop.is_set():
            leases = []
            for key, work in pending:
                lease = store.claim(key, lease_seconds=timeout+60, metadata={"work_id":work.work_id,
                    "expression_id":work.expression_id, "input_hash":input_hash(work), "config":config})
                if lease:
                    leases.append((key, work, lease))
            if not leases:
                return
            batch = len(leases) > 1
            key, work, lease = leases[0]
            attempt = max(lease["attempts"] for _, _, lease in leases)
            stem = output_dir / "attempts" / f"{key}.{attempt}.{uuid.uuid4().hex}"
            final_path = Path(str(stem)+".final.json")
            argv = [*command, "exec", "--model", model, "--sandbox", "read-only", "--ephemeral",
                "--ignore-user-config", "--skip-git-repo-check", "--json", "--color", "never",
                "-c", 'model_reasoning_effort="medium"', "-c", 'web_search="disabled"',
                "-c", "features.multi_agent=false", "--cd", str(output_dir / "workspace"),
                "--output-schema", str(batch_schema_path if batch else schema_path), "-o", str(final_path), "-"]
            before = time.monotonic()
            stdout = stderr = ""
            returncode = None
            category, error, retryable = None, None, True
            try:
                prompt = build_batch_prompt([w for _, w, _ in leases], model=model, cli_version=version, taxonomy=taxonomy) if batch else build_prompt(work, model=model, cli_version=version, taxonomy=taxonomy)
                completed = subprocess.run(argv, input=prompt,
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, shell=False)
                stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
                stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
                category, error = "timeout", f"CLI exceeded {timeout} seconds"
            except OSError as exc:
                category, error, retryable = "launch_error", str(exc), False
            elapsed = time.monotonic()-before
            Path(str(stem)+".stdout.jsonl").write_text(stdout, encoding="utf-8")
            Path(str(stem)+".stderr.txt").write_text(stderr, encoding="utf-8")
            events = parse_events(stdout)
            if category is None and (returncode != 0 or events["event_errors"]):
                error = stderr or "; ".join(events["event_errors"]) or f"CLI exited {returncode}"
                category, retryable = _error_category(error)
            if category is None and events["tool_calls"]:
                category, error, retryable = "tool_use", "Classification attempted tool calls", False
            if category is None and not final_path.exists():
                category, error = "missing_output", "CLI did not save the final message"
            if category is None:
                try:
                    decoded = json.loads(final_path.read_text(encoding="utf-8-sig"))
                    values = decoded["results"] if batch else [decoded]
                    if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
                        raise ValueError("Incorrect batch result shape")
                    result_index = {(r.get("work_id"),r.get("expression_id")):r for r in values}
                    expected = {(w.work_id,w.expression_id) for _, w, _ in leases}
                    if set(result_index) != expected or len(values) != len(expected):
                        raise ValueError("Incorrect or duplicate batch identities")
                    for _, item_work, _ in leases:
                        result = result_index[(item_work.work_id,item_work.expression_id)]
                        validate_result(result, item_work, taxonomy)
                        if result["model"] != model or result["cli_version"] != version:
                            raise ValueError("Incorrect model/CLI metadata")
                except json.JSONDecodeError as exc:
                    category, error = "invalid_json", str(exc)
                except (ValueError, KeyError, TypeError) as exc:
                    error = str(exc)
                    category = "invalid_schema" if error.startswith("schema:") else "invalid_result"
            metric = {"seconds":elapsed,
                "returncode":returncode, "usage":events["usage"], "error_category":category,
                "events_path":str(Path(str(stem)+".stdout.jsonl").relative_to(output_dir)),
                "final_path":str(final_path.relative_to(output_dir)), "shared_batch_size":len(leases)}
            with guard:
                attempted.extend(key for key, _, _ in leases)
                durations.append(elapsed)
                for name, value in events["usage"].items():
                    usage[name] = usage.get(name, 0)+value
                if budget_hit():
                    stop.set()
            if category is None:
                for item_key, item_work, item_lease in leases:
                    result_path = output_dir / "results" / f"{item_key}.json"
                    history = [*item_lease.get("history", []), {"attempt":item_lease["attempts"], **metric}]
                    store.complete(item_key, str(result_path.relative_to(output_dir)), lease_token=item_lease["lease_token"],
                        result=result_index[(item_work.work_id,item_work.expression_id)], history=history)
                return
            pending = []
            for item_key, item_work, item_lease in leases:
                history = [*item_lease.get("history", []), {"attempt":item_lease["attempts"], **metric}]
                store.fail(item_key, category, error, retryable=retryable, lease_token=item_lease["lease_token"], history=history)
                if store.get(item_key)["status"] == "pending":
                    pending.append((item_key,item_work))
            if category in ("usage_limit", "authentication"):
                stop.set()
            if category == "rate_limit":
                throttled.set()
            if not retryable or not pending or stop.is_set():
                return
            # rate limits get a longer wait; malformed outputs remain separate.
            base = 15 if category == "rate_limit" else 1
            time.sleep(min(base * 2**(attempt-1), 120) * random.uniform(0.8, 1.2))

    try:
        cursor, active_concurrency = 0, concurrency
        while cursor < len(jobs) and not stop.is_set():
            if budget_hit():
                stop.set()
                break
            wave = jobs[cursor:cursor+active_concurrency*batch_size]
            groups = [wave[offset:offset+batch_size] for offset in range(0,len(wave),batch_size)]
            with ThreadPoolExecutor(max_workers=active_concurrency) as pool:
                # Wave scheduling lets the next wave reduce concurrency after
                # observed throttling without queuing the whole dataset.
                list(pool.map(process, groups))
            cursor += len(wave)
            if throttled.is_set():
                active_concurrency = max(1, active_concurrency // 2)
                throttled.clear()
    finally:
        states = {key:store.get(key) or {"status":"not_submitted", "work_id":work.work_id,
                    "expression_id":work.expression_id} for key, work in jobs}
        results = []
        for key, work in jobs:
            if states[key]["status"] == "completed":
                try:
                    results.append(validate_result(json.loads((output_dir / "results" / f"{key}.json").read_text(encoding="utf-8")), work, taxonomy))
                except (ValueError, OSError):
                    states[key] = {**states[key], "status":"invalid_cached_result"}
        _atomic_lines(output_dir / "results.jsonl", results)
        ordered = sorted(durations)
        def percentile(p):
            return ordered[max(0, math.ceil(len(ordered)*p)-1)] if ordered else None
        manifest = {"run_id":uuid.uuid4().hex, "config":config, "selected":len(jobs),
            "completed":len(results), "reused":len(reused),
            "failed":sum(s["status"] in ("held", "invalid_cached_result") for s in states.values()),
            "pending":sum(s["status"] in ("pending", "not_submitted", "running") for s in states.values()),
            "attempts":len(attempted), "cli_invocations":len(durations), "retries":len(attempted)-len(set(attempted)),
            "usage":usage, "usage_source":"Codex CLI turn.completed events; not API billing",
            "elapsed_seconds":time.monotonic()-started, "p50_seconds":percentile(.5), "p95_seconds":percentile(.95),
            "initial_concurrency":concurrency, "final_concurrency":active_concurrency,
            "stopped":stop.is_set(), "jobs":states,
            "human_quality_verified":False}
        atomic_json(output_dir / "manifest.json", manifest)
        atomic_json(output_dir / f"manifest.{manifest['run_id']}.json", manifest)
    return manifest
