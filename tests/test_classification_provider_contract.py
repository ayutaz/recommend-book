"""Provider schema compatibility and permanent configuration failure boundaries."""
import json
from pathlib import Path
import shutil
import subprocess

from jsonschema import Draft202012Validator
import pytest

from recommend_book.classification.runner import run_classification
from recommend_book.classification.specs import get_spec
from test_classification_guarded import allowed_many_fixture, event_bytes
from test_classification_specs import native_result


@pytest.fixture
def native_codebook_runtime(tmp_path, monkeypatch):
    """Bind real guard/config code to synthetic sources in an isolated project."""
    from recommend_book.classification import runner

    original = runner.ROOT
    project = tmp_path / 'synthetic-project'
    for folder in ('src', 'schemas', 'config', 'scripts'):
        shutil.copytree(original / folder, project / folder,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copy2(original / name, project / name)
    codebook = project / 'data/autonomous-next-v3/reference/research-codebook'
    codebook.mkdir(parents=True)
    for name, raw in {
        'native-v3-source-contract.json': b'{"fixture_only":true}',
        'keywords.xml': b'<fixture-only/>',
        'book.pdf': b'%PDF-fixture-only-not-source-material',
    }.items():
        (codebook / name).write_bytes(raw)
    monkeypatch.setattr(runner, 'ROOT', project)
    return codebook


# Exact public diagnostic from the stopped native run, without work text or IDs.
PROVIDER_ERROR = {
    "type": "error",
    "error": {
        "type": "invalid_request_error",
        "code": "invalid_json_schema",
        "message": "Invalid schema for response_format 'codex_output_schema': "
            "In context=('properties', 'results', 'items', 'properties', 'task_version'), "
            "schema must have a 'type' key.",
        "param": "text.format.schema",
    },
    "status": 400,
}
NODES = (
    (("prompt_version",), "string"),
    (("task_version",), "string"),
    (("protagonist", "state"), "string"),
    (("age", "state"), "string"),
    (("age", "value"), ["string", "null"]),
    (("profession", "state"), "string"),
)


def node_at(schema, path):
    for name in path:
        schema = schema["properties"][name]
    return schema


def missing_types(value, path=""):
    missing = []
    if isinstance(value, dict):
        if ("enum" in value or "const" in value) and "type" not in value:
            missing.append(path)
        for name, child in value.items():
            missing.extend(missing_types(child, path + "/" + name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            missing.extend(missing_types(child, path + "/" + str(index)))
    return missing


@pytest.mark.parametrize("path,expected", NODES)
def test_native_provider_requires_explicit_enum_and_const_types(path, expected):
    schema = json.loads(get_spec("native-definitions-v3").schema_path.read_bytes())
    assert node_at(schema, path).get("type") == expected


def test_explicit_types_keep_enum_and_const_acceptance_unchanged():
    schema = json.loads(get_spec("native-definitions-v3").schema_path.read_bytes())
    for path, _ in NODES:
        current = node_at(schema, path)
        historical = {key: value for key, value in current.items() if key != "type"}
        permitted = current.get("enum", [current.get("const")])
        for value in [*permitted, None, True, 0, 2.5, "foreign", {}, []]:
            assert Draft202012Validator(current).is_valid(value) == Draft202012Validator(historical).is_valid(value)


def install_responder(monkeypatch, works, *, profile, failure=None, stderr=b"", strict_schema=False):
    calls = []
    byid = {work.work_id: work for work in works}

    def fake(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, b"fake-1\n", b"")
        calls.append((argv, kwargs))
        schema = json.loads(Path(argv[argv.index("--output-schema") + 1]).read_bytes())
        if strict_schema:
            assert missing_types(schema) == []
        if failure and len(calls) == 1:
            raw = (json.dumps({"type": "error", "message": json.dumps(failure)}) + "\n" +
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 37, "output_tokens": 2}}) + "\n").encode()
            return subprocess.CompletedProcess(argv, 1, raw, stderr)
        local = json.loads(kwargs["input"].decode().split("\nBEGIN_LOCAL_DATA_JSON\n")[1].split("\nEND_LOCAL_DATA_JSON")[0])
        requested = [row["work"]["work_id"] for row in local["works"]]
        value = {"results": [native_result(byid[work_id], profile) for work_id in requested]}
        Path(argv[argv.index("-o") + 1]).write_bytes(json.dumps(value).encode())
        return subprocess.CompletedProcess(argv, 0, event_bytes(value), b"")

    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    monkeypatch.setattr("recommend_book.classification.runner.time.sleep", lambda _: None)
    return calls


@pytest.mark.parametrize("profile", ["native-definitions-v3", "native-context-v3"])
def test_actual_native_batch_schema_passes_strict_provider_type_requirement(monkeypatch, tmp_path, profile, native_codebook_runtime):
    works, ledger, binary, _ = allowed_many_fixture(tmp_path, count=5, execution_specs=(profile,))
    calls = install_responder(monkeypatch, works, profile=profile, strict_schema=True)
    manifest = run_classification(works, tmp_path / "run", concurrency=1, batch_size=5,
        executable=str(binary), usage_ledger=ledger, execution_spec=profile)
    assert manifest["completed"] == 5 and len(calls) == 1


@pytest.mark.parametrize("stderr", [b"", b"CLI progress: waiting for server\r\n"])
def test_provider_schema_rejection_stops_next_batch_and_preserves_actual_failure(monkeypatch, tmp_path, stderr, native_codebook_runtime):
    works, ledger, binary, _ = allowed_many_fixture(tmp_path, count=10, execution_specs=('native-definitions-v3',))
    calls = install_responder(monkeypatch, works, profile="native-definitions-v3",
        failure=PROVIDER_ERROR, stderr=stderr)
    output = tmp_path / "run"
    manifest = run_classification(works, output, concurrency=1, batch_size=5,
        executable=str(binary), usage_ledger=ledger, execution_spec="native-definitions-v3")
    assert len(calls) == 1  # No retry and no second wave after shared schema rejection.
    assert manifest["stopped"] is True and manifest["stop_reasons"] == ["invalid_json_schema"]
    assert manifest["cli_invocations"] == 1 and manifest["retries"] == 0
    assert manifest["completed"] == 0 and manifest["failed"] == 5 and manifest["pending"] == 5
    assert manifest["usage"] == {"input_tokens": 37, "output_tokens": 2}
    raw_path = next((output / "attempts").glob("*.stdout.jsonl"))
    assert json.loads(raw_path.read_bytes().splitlines()[0])["message"] == json.dumps(PROVIDER_ERROR)
    assert next((output / "attempts").glob("*.stderr.txt")).read_bytes() == stderr
    attempt = json.loads(next((output / "attempts").glob("*.attempt.json")).read_bytes())
    assert attempt["actual_exit_code"] == 1 and attempt["error_category"] == "invalid_json_schema"
    assert attempt["retryable"] is False
    assert (output / "results.jsonl").read_bytes() == b""
    assert sum(state["status"] == "held" for state in manifest["jobs"].values()) == 5
    assert sum(state["status"] == "not_submitted" for state in manifest["jobs"].values()) == 5


def test_general_cli_exit_remains_local_failure_and_does_not_stop_unrelated_batch(monkeypatch, tmp_path, native_codebook_runtime):
    works, ledger, binary, _ = allowed_many_fixture(tmp_path, count=10, execution_specs=('native-definitions-v3',))
    calls = install_responder(monkeypatch, works, profile="native-definitions-v3",
        failure={"message": "Unexpected child process exit; no provider schema rejection"})
    manifest = run_classification(works, tmp_path / "run", concurrency=1, batch_size=5,
        executable=str(binary), usage_ledger=ledger, execution_spec="native-definitions-v3")
    assert len(calls) == 2 and manifest["stopped"] is False
    assert manifest["completed"] == 5 and manifest["failed"] == 5 and manifest["pending"] == 0
    assert {state["error_category"] for state in manifest["jobs"].values() if state["status"] == "held"} == {"cli_exit"}
    assert manifest["retries"] == 0


def test_changed_synthetic_codebook_requires_new_execution_recipe(monkeypatch, tmp_path, native_codebook_runtime):
    from recommend_book.usage_guard import UsageDenied

    works, ledger, binary, _ = allowed_many_fixture(tmp_path, count=5,
        execution_specs=('native-definitions-v3',))
    (native_codebook_runtime / 'book.pdf').write_bytes(b'changed fixture source')
    calls = install_responder(monkeypatch, works, profile='native-definitions-v3')
    with pytest.raises(UsageDenied):
        run_classification(works, tmp_path / 'run', concurrency=1, batch_size=5,
            executable=str(binary), usage_ledger=ledger, execution_spec='native-definitions-v3')
    assert calls == []
