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
from .validation import ROOT, PROMPT_VERSION, VALIDATION_VERSION, input_hash, load_taxonomy, validate_result


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
    return observe_cli_version(command)[0]


def _bytes(value) -> bytes:
    # Native capture is bytes. Text is accepted solely for existing injected
    # unit-test CompletedProcess fixtures; no replacement decoding is used.
    return value.encode("utf-8") if isinstance(value, str) else value or b""


def observe_cli_version(command: list[str]) -> tuple[str, dict]:
    argv = [*command, "--version"]
    result = subprocess.run(argv, capture_output=True, timeout=30, shell=False)
    if result.returncode:
        raise RuntimeError("Codex version check failed")
    stdout, stderr = _bytes(result.stdout), _bytes(result.stderr)
    version = stdout.decode("utf-8").strip()
    if not version:
        raise ValueError("CLI version observation is empty")
    return version, {"argv": argv, "exit_code": result.returncode,
        "stdout_utf8": stdout.decode("utf-8"), "stderr_utf8": stderr.decode("utf-8"),
        "stdout_sha256": hashlib.sha256(stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(stderr).hexdigest()}


def immutable_bytes(path: Path, value: bytes) -> str:
    """An attempt artifact can be created once; overwrites are refused."""
    with Path(path).open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(value).hexdigest()


def immutable_json(path: Path, value) -> str:
    return immutable_bytes(path, (canonical_json(value) + "\n").encode("utf-8"))


def command_artifact_hashes(command: list[str]) -> dict:
    paths = [Path(command[0])]
    if len(command) > 1:
        paths.append(Path(command[1]))
    return {str(p.resolve(strict=True)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def _receipt(value):
    if isinstance(value, list):
        return [_receipt(v) for v in value]
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _observed_usage(value):
    if value is None:return {}
    if not isinstance(value,dict) or any(type(v) is not int or v<0 for v in value.values()):
        raise ValueError('Native observed usage must contain nonnegative integer counts')
    return value


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
            try:
                for key, value in _observed_usage(event.get('usage')).items():
                    usage[key] = usage.get(key, 0) + value
            except ValueError as exc:event_errors.append(str(exc))
        item = event.get("item") or {}
        if isinstance(item, dict) and item.get("type") and item.get("type") not in ("reasoning", "agent_message"):
            tools.append(item.get("type"))
        if event.get("type") in ("error", "turn.failed"):
            event_errors.append(str(event.get("message") or event.get("error") or event))
    return {"usage":usage, "tool_calls":tools, "event_errors":event_errors}


def _strict_json(text: str):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result
    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def bind_final_message(stdout: bytes, final_bytes: bytes) -> dict:
    """Bind the actual last completed agent message to the immutable final value.

    Strict decoding preserves corrupt bytes as a held response rather than
    silently replacing them. Duplicate keys and non-JSON numbers are refused.
    This verifies protocol identity, not classification correctness.
    """
    try:
        events_text = stdout.decode("utf-8")
        final_text = final_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid UTF-8 native response") from exc
    final_value = _strict_json(final_text)
    last_message = None
    for line in events_text.splitlines():
        if not line.strip():
            continue
        event = _strict_json(line)
        if not isinstance(event, dict):
            raise ValueError("Native JSONL event must be an object")
        if event.get("type") == "turn.completed":
            _observed_usage(event.get('usage'))
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict) and item.get("type") == "agent_message":
            if not isinstance(item.get("text"), str):
                raise ValueError("Native agent message text is missing")
            last_message = item["text"]
    if last_message is None:
        raise ValueError("Native last agent message is missing")
    if canonical_json(_strict_json(last_message)) != canonical_json(final_value):
        raise ValueError("Native last agent message and final value differ")
    return {"value": final_value, "last_agent_message": last_message,
        "last_agent_message_sha256": hashlib.sha256(last_message.encode("utf-8")).hexdigest(),
        "final_value_sha256": hashlib.sha256(canonical_json(final_value).encode("utf-8")).hexdigest(),
        "events": parse_events(events_text)}


def _error_category(text: str) -> tuple[str, bool]:
    lower = text.lower()
    if "invalid_json_schema" in lower:
        return "invalid_json_schema", False
    if any(word in lower for word in ("usage limit", "quota exceeded", "insufficient_quota", "weekly limit")):
        return "usage_limit", False
    if any(word in lower for word in ("unauthorized", "authentication", "invalid api key", "401")):
        return "authentication", False
    if any(word in lower for word in ("rate limit", "too many requests", "429")):
        return "rate_limit", True
    if any(word in lower for word in ("temporarily unavailable", "connection reset", "connection timeout",
            "network timeout", "502 bad gateway", "503 service unavailable", "504 gateway timeout")):
        return "transient_transport", True
    return "cli_exit", False


def _atomic_lines(path: Path, rows):
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text("".join(canonical_json(row)+"\n" for row in rows), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_controls(*, concurrency=2,timeout=900,limit=None,batch_size=1,
        max_run_seconds=None,max_observed_tokens=None):
    """Shared API/CLI bounds; zero dataset/budget limits deliberately stop work."""
    if type(concurrency) is not int or not 1 <= concurrency <= 8:
        raise ValueError("concurrency must be between 1 and 8")
    if type(batch_size) is not int or not 1 <= batch_size <= 10:
        raise ValueError("batch_size must be between 1 and 10")
    if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("limit must be a nonnegative integer")
    if max_run_seconds is not None and (isinstance(max_run_seconds,bool) or not isinstance(max_run_seconds,(int,float)) or not math.isfinite(max_run_seconds) or max_run_seconds < 0):
        raise ValueError("max_run_seconds must be finite and nonnegative")
    if max_observed_tokens is not None and (type(max_observed_tokens) is not int or max_observed_tokens < 0):
        raise ValueError("max_observed_tokens must be a nonnegative integer")


def build_run_config(model: str, cli_version: str, taxonomy: dict, batch_size: int, *, execution_spec: str | None=None) -> dict:
    """Build an offline config from supplied identity and local artifact bytes.

    This read-only helper performs no CLI discovery/version query, invocation,
    network request or output write. Migration can pass the archived CLI
    version and derive exactly the configuration used by a new run.
    """
    validate_controls(batch_size=batch_size)
    config = {"model": model, "taxonomy_version": taxonomy["version"], "prompt_version": PROMPT_VERSION,
            "taxonomy_hash": hashlib.sha256(canonical_json(taxonomy).encode()).hexdigest(),
            "schema_hash": hashlib.sha256((ROOT / "schemas/classification.schema.json").read_bytes()).hexdigest(),
            "prompt_hash": hashlib.sha256((ROOT / "src/recommend_book/classification/prompt.py").read_bytes()).hexdigest(),
            "validation_version": VALIDATION_VERSION,
            "validation_hash": hashlib.sha256((ROOT / "src/recommend_book/classification/validation.py").read_bytes()).hexdigest(),
            "cli_version": cli_version, "sandbox": "read-only", "reasoning_effort": "medium", "web_search": "disabled",
            "batch_size": batch_size}
    if execution_spec is not None:
        from .specs import get_spec
        from recommend_book.usage_guard import GUARD_VERSION
        spec = get_spec(execution_spec, root=ROOT)
        config.update(prompt_version=spec.prompt_version,
            prompt_hash=hashlib.sha256(spec.prompt_path.read_bytes()).hexdigest(),
            schema_hash=hashlib.sha256(spec.schema_path.read_bytes()).hexdigest(),
            execution_spec=execution_spec,
            spec_hash=hashlib.sha256((ROOT / "src/recommend_book/classification/specs.py").read_bytes()).hexdigest(),
            usage_guard_version=GUARD_VERSION,
            usage_guard_hash=hashlib.sha256((ROOT / "src/recommend_book/usage_guard.py").read_bytes()).hexdigest(),
            runner_version="guarded-runner-v3.2",runner_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
        dependencies = ("src/recommend_book/models.py", "src/recommend_book/jobs.py",
            "src/recommend_book/wikipedia_paragraphs.py", "src/recommend_book/wikipedia_prose.py",
            "src/recommend_book/autonomous_enrichment.py", "src/recommend_book/ingestion/wikipedia.py",
            "src/recommend_book/rights.py", "src/recommend_book/classification/prompt.py",
            "src/recommend_book/classification/prompt_v3.py", "src/recommend_book/classification/native_v3.py",
            "src/recommend_book/classification/native_attempt.py", "src/recommend_book/cli.py",
            "scripts/run_guarded_classification.py", "pyproject.toml", "uv.lock", "config/sources.json", "config/taxonomy.json")
        if execution_spec == "provenance-v4":
            dependencies = (*dependencies, "schemas/classification-provenance-v4.schema.json")
        config["dependency_sha256"] = {p:hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in dependencies}
        if execution_spec.startswith('native-'):
            codebook = 'data/autonomous-next-v3/reference/research-codebook/'
            config['native_codebook_sha256'] = {codebook+p:hashlib.sha256((ROOT / (codebook+p)).read_bytes()).hexdigest()
                for p in ('native-v3-source-contract.json','keywords.xml','book.pdf')}
    return config


def run_classification(inputs: list[WorkInput], output_dir: Path, *, concurrency: int=2,
        model: str="gpt-6.1-sol", executable: str | None=None, limit: int | None=None,
        timeout: float=900, max_run_seconds: float | None=None,
        max_observed_tokens: int | None=None, batch_size: int=1,
        stop_on_rate_limit: bool=False, usage_ledger: Path | None=None,
        execution_spec: str="guarded-v3-baseline",expected_run_config: dict | None=None,
        expected_usage_ledger_sha256: str | None=None) -> dict:
    """Classify a fixed snapshot with <=3 attempts, leases, cache and manifests.

    Budget bounds pause NEW submissions, allowing in-flight jobs to finish.
    CLI token usage is observed usage, not an API billing estimate. No fabricated
    all-unknown fallback is written when an invocation or validation fails.
    """
    validate_controls(concurrency=concurrency,timeout=timeout,limit=limit,batch_size=batch_size,
        max_run_seconds=max_run_seconds,max_observed_tokens=max_observed_tokens)
    from .specs import get_spec
    spec = get_spec(execution_spec)
    # New executions, including empty runs and cache-only resumes, require a
    # current, independently checked usage ledger before CLI discovery or IO.
    if usage_ledger is None:
        raise ValueError("A current usage ledger is required for classification")
    usage_ledger = Path(usage_ledger).resolve()
    if not usage_ledger.is_file():
        raise ValueError("Usage ledger file is missing")
    selected = inputs[:limit] if limit is not None else inputs
    identities = [(work.work_id, work.expression_id) for work in selected]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate work/expression inputs in one run")
    from recommend_book.usage_guard import UsageGuard, UsageDenied, GUARD_VERSION
    ledger_sha256 = hashlib.sha256(usage_ledger.read_bytes()).hexdigest()
    if expected_usage_ledger_sha256 is not None and ledger_sha256!=expected_usage_ledger_sha256:
        raise ValueError('Usage ledger differs from fixed execution context')
    usage_authorizer = UsageGuard(usage_ledger,expected_ledger_sha256=ledger_sha256)
    admission = usage_authorizer.assert_inputs_allowed(selected or inputs, usage="external_llm")
    output_dir = Path(output_dir).resolve()
    prior_manifest = output_dir / "manifest.json"
    prior = json.loads(prior_manifest.read_text(encoding='utf-8')) if prior_manifest.exists() else None
    input_identities=[{'work_id':w.work_id,'expression_id':w.expression_id,'input_hash':input_hash(w)}for w in selected]
    if prior_manifest.exists() and not json.loads(prior_manifest.read_text(encoding="utf-8")).get("config", {}).get("usage_guard_version"):
        raise ValueError("Historical unguarded run namespace cannot be modified; use a new namespace")
    if prior_manifest.exists() and json.loads(prior_manifest.read_text(encoding="utf-8")).get("execution_context",{}).get("usage_ledger_sha256") != ledger_sha256:
        raise ValueError("Frozen usage ledger changed; use a new approved namespace")
    if prior is not None and prior.get('input_identities')!=input_identities:
        raise ValueError('Existing namespace input snapshot differs; use a new namespace')
    taxonomy = load_taxonomy()
    command = resolve_executable(executable) if selected else []
    executable_sha256 = command_artifact_hashes(command) if selected else {}
    version, version_observation = observe_cli_version(command) if selected else ("not_invoked", None)
    config = build_run_config(model, version, taxonomy, batch_size, execution_spec=execution_spec)
    if expected_run_config is not None and config!=expected_run_config:
        raise ValueError('Observed CLI/code differs from fixed execution config')
    if prior is not None and prior.get('config')!=config:
        raise ValueError('Existing namespace execution config differs; use a new namespace')
    config_sha256 = hashlib.sha256(canonical_json(config).encode('utf-8')).hexdigest()
    execution_receipt = usage_authorizer.assert_execution_allowed(config_sha256,usage='external_llm') if selected else None
    resource_controls={'concurrency':concurrency,'batch_size':batch_size,'timeout':timeout,'limit':limit,
        'max_run_seconds':max_run_seconds,'max_observed_tokens':max_observed_tokens,'stop_on_rate_limit':stop_on_rate_limit}
    output_dir.mkdir(parents=True, exist_ok=True)
    for folder in ("attempts", "results", "workspace"):
        (output_dir / folder).mkdir(exist_ok=True)
    run_id = uuid.uuid4().hex
    immutable_json(output_dir / f"run.{run_id}.admission.json", {"run_id":run_id,
        "run_dir":str(output_dir),"input_identities":input_identities,
        "usage":"external_llm", "receipts":_receipt(admission), "config":config,
        "usage_ledger_path":str(usage_ledger),"usage_ledger_sha256":ledger_sha256,
        "execution_usage_receipt":_receipt(execution_receipt),"config_sha256":config_sha256,
        "resource_controls":resource_controls,
        "executable_sha256":executable_sha256, "version_observation":version_observation})
    schema_path = spec.schema_path
    batch_schema = {"type":"object", "additionalProperties":False, "required":["results"],
        "properties":{"results":{"type":"array", "items":json.loads(schema_path.read_text(encoding="utf-8"))}}}
    batch_schema_path = output_dir / "batch.schema.json"
    atomic_json(batch_schema_path, batch_schema)
    store = JobStore(output_dir)
    jobs = [(job_key(input_hash(work), config), work) for work in selected]
    started = time.monotonic()
    usage, durations, attempted = {}, [], []
    guard = threading.RLock()
    stop = threading.Event()
    throttled = threading.Event()
    reused = set()
    stop_reasons = set()
    usage_holds = {}
    usage_receipts = []
    frozen_proofs = {d.input_hashes[0]:sorted(d.proof_hashes) for d in admission}

    def request_stop(reason):
        with guard:
            stop_reasons.add(reason)
            stop.set()

    def budget_reason():
        observed = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        if max_run_seconds is not None and time.monotonic()-started >= max_run_seconds:
            return "time_budget"
        if max_observed_tokens is not None and observed >= max_observed_tokens:
            return "token_budget"
        return None

    def authorize(phase, pairs, operation):
        try:
            value = operation()
            if hasattr(value, "allowed") and not value.allowed:
                raise UsageDenied(value)
            for current in usage_authorizer.assert_inputs_allowed([w for _,w in pairs],usage="external_llm"):
                if sorted(current.proof_hashes) != frozen_proofs[current.input_hashes[0]]:
                    rejected = current.model_copy(update={"allowed":False,"reasons":["frozen source/policy proof set changed"]})
                    raise UsageDenied(rejected)
            with guard:
                usage_receipts.append({"phase":phase, "job_keys":[key for key, _ in pairs],
                    "receipt":_receipt(value)})
            return value
        except UsageDenied as exc:
            decision = _receipt(exc.decision)
            with guard:
                usage_holds.update({key:{"phase":phase,"decision":decision} for key, _ in pairs})
                reused.difference_update(key for key, _ in pairs)
                request_stop("usage_denied")
            immutable_json(output_dir / f"usage-hold.{run_id}.{uuid.uuid4().hex}.json",
                {"phase":phase,"job_keys":[key for key, _ in pairs],"decision":decision,
                 "native_cli_started":False,"run_id":run_id})
            return None

    def original_descriptor(state):
        proof = state.get('history', [])[-1]
        path = output_dir / proof['source_manifest_path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != proof['source_manifest_sha256']:
            raise ValueError('Native source manifest changed')
        return json.loads(path.read_bytes())

    def process(pairs):
        if authorize("batch_input", pairs, lambda: usage_authorizer.assert_inputs_allowed(
                [work for _, work in pairs], usage="external_llm")) is None:
            return
        pending = []
        for key, work in pairs:
            result_path = output_dir / "results" / f"{key}.json"
            cached = store.get(key)
            if cached.get("status") == "completed":
                if authorize("cache", [(key,work)], lambda: usage_authorizer.assert_cache_allowed(
                        work, cached.get("input_hash"), usage="external_llm")) is None:
                    return
                try:
                    cached_result = spec.validate_result(json.loads(result_path.read_text(encoding="utf-8")), work, taxonomy)
                    descriptor = original_descriptor(cached)
                    if authorize("cache_derived", [(key,work)], lambda: usage_authorizer.check_derived(
                            cached_result, descriptor['originating_input_hashes'], "external_llm",
                            cached_input_hash=input_hash(work),source_manifest=descriptor)) is None:
                        return
                    with guard:
                        reused.add(key)
                    continue
                except (ValueError, OSError, KeyError, IndexError):
                    store.invalidate(key)
            pending.append((key, work))
        while pending and not stop.is_set():
            if authorize("attempt_input", pending, lambda: usage_authorizer.assert_inputs_allowed(
                    [work for _, work in pending], usage="external_llm")) is None:
                return
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
            stdout_raw = stderr_raw = b""
            returncode = None
            category, error, retryable = None, None, True
            binding = None
            actual_cli_started = False
            launch_attempted = False
            prompt = spec.build_batch_prompt([w for _, w, _ in leases], model=model, cli_version=version, taxonomy=taxonomy) if batch else spec.build_prompt(work, model=model, cli_version=version, taxonomy=taxonomy)
            stdin_bytes = prompt.encode("utf-8")
            expected_bytes = spec.expected_prompt([w for _,w,_ in leases], model=model,
                cli_version=version, taxonomy=taxonomy).encode("utf-8")
            if stdin_bytes != expected_bytes:
                immutable_json(Path(str(stem)+".prompt-rejection.json"), {"run_id":run_id,
                    "actual_prompt_sha256":hashlib.sha256(stdin_bytes).hexdigest(),
                    "expected_prompt_sha256":hashlib.sha256(expected_bytes).hexdigest(),
                    "config":config,"actual_cli_started":False,"reason":"prompt_context_mismatch"})
                for item_key, _, item_lease in leases:
                    store.fail(item_key,"prompt_context_mismatch","Complete prompt differs from fixed execution spec",
                        retryable=False,lease_token=item_lease["lease_token"])
                request_stop("prompt_context_mismatch")
                return
            prompt_receipt = authorize("actual_prompt", [(k,w) for k,w,_ in leases],
                lambda: usage_authorizer.assert_prompt_allowed(stdin_bytes, [w for _,w,_ in leases], usage="external_llm",
                    expected_prompt_sha256=hashlib.sha256(expected_bytes).hexdigest()))
            if prompt_receipt is None:
                for item_key, _, item_lease in leases:
                    store.fail(item_key,"usage_denied","Usage proof expired before invocation",retryable=False,lease_token=item_lease["lease_token"])
                return
            if authorize('execution_recipe',[(k,w)for k,w,_ in leases],lambda:
                    usage_authorizer.assert_execution_allowed(config_sha256,usage='external_llm')) is None:
                for item_key,_,item_lease in leases:
                    store.fail(item_key,'usage_denied','Execution recipe expired',retryable=False,lease_token=item_lease['lease_token'])
                return
            stdin_sha = immutable_bytes(Path(str(stem)+".stdin.utf8"), stdin_bytes)
            immutable_json(Path(str(stem)+".request.json"), {"run_id":run_id,"argv":argv,
                "run_dir":str(output_dir),"attempt_stem":str(stem.relative_to(output_dir)),
                "config":config,"executable_sha256":executable_sha256,
                "resource_controls":resource_controls,
                "version_observation":version_observation,"stdin_sha256":stdin_sha,
                "usage_ledger_path":str(usage_ledger),"usage_ledger_sha256":ledger_sha256,
                "expected_prompt_sha256":hashlib.sha256(expected_bytes).hexdigest(),
                "config_sha256":hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest(),
                "input_hashes":[input_hash(w) for _,w,_ in leases],"job_keys":[k for k,_,_ in leases],
                "prompt_usage_receipt":_receipt(prompt_receipt),"shell":False,"text":False})
            try:
                if command_artifact_hashes(command) != executable_sha256:
                    raise ValueError("Executable artifact changed before invocation")
                if build_run_config(model, version, load_taxonomy(), batch_size, execution_spec=execution_spec) != config:
                    raise ValueError("Execution config artifacts changed before invocation")
                launch_attempted = True
                actual_cli_started = True
                completed = subprocess.run(argv, input=stdin_bytes,
                    capture_output=True, text=False, timeout=timeout, shell=False)
                stdout_raw, stderr_raw, returncode = _bytes(completed.stdout), _bytes(completed.stderr), completed.returncode
            except subprocess.TimeoutExpired as exc:
                stdout_raw, stderr_raw = _bytes(exc.stdout), _bytes(exc.stderr)
                category, error = "timeout", f"CLI exceeded {timeout} seconds"
            except OSError as exc:
                actual_cli_started = False
                category, error, retryable = "launch_error", str(exc), False
            except ValueError as exc:
                category, error, retryable = "execution_context_changed", str(exc), False
                request_stop(category)
            elapsed = time.monotonic()-before
            stdout_sha = immutable_bytes(Path(str(stem)+".stdout.jsonl"), stdout_raw)
            stderr_sha = immutable_bytes(Path(str(stem)+".stderr.txt"), stderr_raw)
            try:
                stdout, stderr = stdout_raw.decode("utf-8"), stderr_raw.decode("utf-8")
            except UnicodeDecodeError:
                stdout = stderr = ""
                category, error, retryable = "invalid_protocol", "Invalid UTF-8 native response", False
            events = parse_events(stdout)
            captured_last_message = None
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                    item = event.get("item") or {}
                    if event.get("type") == "item.completed" and item.get("type") == "agent_message" and isinstance(item.get("text"),str):
                        captured_last_message = item["text"]
                except (ValueError,TypeError,AttributeError):
                    pass
            if captured_last_message is not None:
                try:
                    immutable_bytes(Path(str(stem)+".last-agent-message.txt"), captured_last_message.encode("utf-8"))
                except UnicodeEncodeError:
                    captured_last_message=None
                    category,error,retryable='invalid_protocol','Agent text contains invalid Unicode surrogate',False
            if category is None and (returncode != 0 or events["event_errors"]):
                error = "\n".join(part for part in (stderr, *events["event_errors"]) if part) or f"CLI exited {returncode}"
                category, retryable = _error_category(error)
            if category is None and events["tool_calls"]:
                category, error, retryable = "tool_use", "Classification attempted tool calls", False
            if category is None and not final_path.exists():
                category, error, retryable = "missing_output", "CLI did not save the final message", False
            if category is None:
                try:
                    final_raw = final_path.read_bytes()
                    binding = bind_final_message(stdout_raw, final_raw)
                    decoded = binding["value"]
                    values = decoded["results"] if batch else [decoded]
                    if not isinstance(values, list) or not all(isinstance(v, dict) for v in values):
                        raise ValueError("Incorrect batch result shape")
                    result_index = {(r.get("work_id"),r.get("expression_id")):r for r in values}
                    expected = {(w.work_id,w.expression_id) for _, w, _ in leases}
                    if set(result_index) != expected or len(values) != len(expected):
                        raise ValueError("Incorrect or duplicate batch identities")
                    for _, item_work, _ in leases:
                        result = result_index[(item_work.work_id,item_work.expression_id)]
                        spec.validate_result(result, item_work, taxonomy)
                        if result["model"] != model or result["cli_version"] != version:
                            raise ValueError("Incorrect model/CLI metadata")
                except json.JSONDecodeError as exc:
                    category, error, retryable = "invalid_json", str(exc), False
                except (ValueError, KeyError, TypeError) as exc:
                    error = str(exc)
                    category = "invalid_schema" if error.startswith("schema:") else "invalid_result"
                    retryable = False
            immutable_json(Path(str(stem)+".binding.json"), {"actual_exit_code":returncode,
                "final_matches_last_agent_message":binding is not None,
                "final_sha256":hashlib.sha256(final_path.read_bytes()).hexdigest() if final_path.exists() else None,
                "last_agent_message_sha256":hashlib.sha256(captured_last_message.encode("utf-8")).hexdigest() if captured_last_message is not None else None,
                "final_value_sha256":binding["final_value_sha256"] if binding else None})
            protocol_attempt = {"run_id":run_id,
                "actual_cli_started":actual_cli_started,"launch_attempted":launch_attempted,"actual_exit_code":returncode,
                "stdin_sha256":stdin_sha,"stdout_sha256":stdout_sha,"stderr_sha256":stderr_sha,
                "final_sha256":hashlib.sha256(final_path.read_bytes()).hexdigest() if final_path.exists() else None,
                "final_value_sha256":binding["final_value_sha256"] if binding else None,
                "error_category":category,"error":error,"retryable":retryable,"seconds":elapsed,
                "usage":events["usage"],"job_keys":[k for k,_,_ in leases]}
            immutable_json(Path(str(stem)+".protocol-attempt.json"),protocol_attempt)
            descriptor = None
            descriptor_sha = None
            if category is None:
                suffixes = {'request':'.request.json','attempt':'.protocol-attempt.json','prompt':'.stdin.utf8',
                    'stdout':'.stdout.jsonl','stderr':'.stderr.txt','final':'.final.json'}
                descriptor = {'kind':'classification-native-attempt-v3','execution_spec':execution_spec,
                    'run_dir':str(output_dir),'run_id':run_id,'attempt_stem':str(stem.relative_to(output_dir)),
                    'config_sha256':hashlib.sha256(canonical_json(config).encode()).hexdigest(),
                    'usage_ledger_sha256':ledger_sha256,'originating_input_hashes':[input_hash(w) for _,w,_ in leases],
                    'artifacts':{name:{'path':str(stem)+suffix,
                        'sha256':hashlib.sha256(Path(str(stem)+suffix).read_bytes()).hexdigest()} for name,suffix in suffixes.items()}}
                admission_path=output_dir/f'run.{run_id}.admission.json'
                descriptor['artifacts']['admission']={'path':str(admission_path),'sha256':hashlib.sha256(admission_path.read_bytes()).hexdigest()}
                descriptor_sha = immutable_json(Path(str(stem)+'.source-manifest.json'),descriptor)
                adoption = authorize("result_adoption", [(k,w) for k,w,_ in leases],
                    lambda: usage_authorizer.check_derived(decoded,[input_hash(w) for _,w,_ in leases],
                        "external_llm",source_manifest=descriptor))
                immutable_json(Path(str(stem)+'.adoption.json'),{'run_id':run_id,
                    'source_manifest_sha256':descriptor_sha,'allowed':adoption is not None,
                    'receipt':_receipt(adoption) if adoption is not None else None})
                if adoption is None:
                    category,error,retryable = "usage_denied","Usage proof expired before result adoption",False
            immutable_json(Path(str(stem)+'.attempt.json'),{**protocol_attempt,
                'error_category':category,'error':error,'retryable':retryable})
            metric = {"seconds":elapsed,
                "returncode":returncode, "usage":events["usage"], "error_category":category,
                "events_path":str(Path(str(stem)+".stdout.jsonl").relative_to(output_dir)),
                "final_path":str(final_path.relative_to(output_dir)), "shared_batch_size":len(leases),
                "request_path":str(Path(str(stem)+".request.json").relative_to(output_dir)),
                "attempt_path":str(Path(str(stem)+".attempt.json").relative_to(output_dir)),
                "stdin_sha256":stdin_sha,"stdout_sha256":stdout_sha,"stderr_sha256":stderr_sha,
                "actual_cli_started":actual_cli_started}
            if descriptor is not None:
                metric.update(source_manifest_path=str(Path(str(stem)+'.source-manifest.json').relative_to(output_dir)),
                    source_manifest_sha256=descriptor_sha)
            with guard:
                if actual_cli_started:
                    attempted.extend(key for key, _, _ in leases)
                    durations.append(elapsed)
                for name, value in events["usage"].items():
                    usage[name] = usage.get(name, 0)+value
                reason = budget_reason()
                if reason:
                    request_stop(reason)
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
            if category in ("usage_limit", "authentication", "invalid_json_schema"):
                request_stop(category)
            if category == "rate_limit":
                throttled.set()
                if stop_on_rate_limit:
                    request_stop("rate_limit")
            if not retryable or not pending or stop.is_set():
                return
            # rate limits get a longer wait; malformed outputs remain separate.
            base = 15 if category == "rate_limit" else 1
            time.sleep(min(base * 2**(attempt-1), 120) * random.uniform(0.8, 1.2))

    try:
        cursor, active_concurrency = 0, concurrency
        while cursor < len(jobs) and not stop.is_set():
            reason = budget_reason()
            if reason:
                request_stop(reason)
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
                if authorize("result_reuse", [(key,work)], lambda: usage_authorizer.assert_cache_allowed(
                        work, states[key].get("input_hash"), usage="external_llm")) is None:
                    continue
                try:
                    current_result = spec.validate_result(json.loads((output_dir / "results" / f"{key}.json").read_text(encoding="utf-8")), work, taxonomy)
                    descriptor = original_descriptor(states[key])
                    if authorize("result_derived", [(key,work)], lambda: usage_authorizer.check_derived(
                            current_result,descriptor['originating_input_hashes'],"external_llm",
                            cached_input_hash=input_hash(work),source_manifest=descriptor)) is not None:
                        results.append(current_result)
                except (ValueError, OSError,KeyError,IndexError):
                    states[key] = {**states[key], "status":"invalid_cached_result"}
        _atomic_lines(output_dir / "results.jsonl", results)
        immutable_bytes(output_dir / f"results.{run_id}.jsonl", (output_dir / "results.jsonl").read_bytes())
        ordered = sorted(durations)
        def percentile(p):
            return ordered[max(0, math.ceil(len(ordered)*p)-1)] if ordered else None
        manifest = {"run_id":run_id, "config":config, "selected":len(jobs),
            "input_identities":input_identities,"run_dir":str(output_dir),
            "completed":len(results), "reused":len(reused),
            "failed":sum(s["status"] in ("held", "invalid_cached_result") or key in usage_holds for key,s in states.items()),
            "pending":sum(s["status"] in ("pending", "not_submitted", "running") and key not in usage_holds for key,s in states.items()),
            "attempts":len(attempted), "cli_invocations":len(durations), "retries":len(attempted)-len(set(attempted)),
            "usage":usage, "usage_source":"Codex CLI turn.completed events; not API billing",
            "elapsed_seconds":time.monotonic()-started, "p50_seconds":percentile(.5), "p95_seconds":percentile(.95),
            "initial_concurrency":concurrency, "final_concurrency":active_concurrency,
            "stopped":stop.is_set(), "stop_reasons":sorted(stop_reasons), "jobs":states,
            "usage_holds":usage_holds,"usage_receipts":usage_receipts,
            "execution_context":{"executable_sha256":executable_sha256,"version_observation":version_observation,
                "usage_ledger_path":str(usage_ledger),"usage_ledger_sha256":ledger_sha256,
                "input_proof_hashes":frozen_proofs},
            "results_snapshot_path":f"results.{run_id}.jsonl",
            "limits":{"timeout":timeout,"max_run_seconds":max_run_seconds,"max_observed_tokens":max_observed_tokens,
                "stop_on_rate_limit":stop_on_rate_limit,"in_flight_completion_may_exceed_budgets":True},
            "human_quality_verified":False}
        atomic_json(output_dir / "manifest.json", manifest)
        immutable_json(output_dir / f"manifest.{manifest['run_id']}.json", manifest)
    return manifest
