"""V3 execution boundary tests: no CLI or output before usage proof."""
from pathlib import Path
import json
import subprocess

import pytest

from recommend_book.classification.runner import run_classification
from recommend_book.models import Evidence, WorkInput


def local_work():
    return WorkInput(work_id="guarded-work", title="Local fixture", original_language="en",
        evidence=[Evidence(evidence_id="fixture-evidence", kind="synopsis",
            text="A detective solves a murder.", source_url="https://example.invalid/work",
            rights_policy_id="fixture", llm_input_allowed=True,
            inherited_restrictions=["declared_allowed"], locator="summary")])


def forbid_cli(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("CLI discovery/version/invocation occurred before usage authorization")
    monkeypatch.setattr("recommend_book.classification.runner.resolve_executable", forbidden)
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", forbidden)


def test_missing_ledger_rejects_before_discovery_or_output(monkeypatch, tmp_path):
    forbid_cli(monkeypatch)
    output = tmp_path / "never-created"
    with pytest.raises(ValueError, match="(?i)usage.*ledger"):
        run_classification([local_work()], output, executable="never-resolved")
    assert not output.exists()


def test_missing_ledger_cannot_be_bypassed_with_empty_run(monkeypatch, tmp_path):
    forbid_cli(monkeypatch)
    output = tmp_path / "never-created"
    with pytest.raises(ValueError, match="(?i)usage.*ledger"):
        run_classification([], output, executable="never-resolved")
    assert not output.exists()


def test_missing_ledger_file_rejects_before_discovery_or_output(monkeypatch, tmp_path):
    forbid_cli(monkeypatch)
    output = tmp_path / "never-created"
    with pytest.raises((ValueError, FileNotFoundError), match="(?i)ledger"):
        run_classification([local_work()], output, executable="never-resolved",
            usage_ledger=tmp_path / "missing-usage-ledger.json")
    assert not output.exists()


def event_bytes(value):
    return (json.dumps({"type": "item.completed", "item": {
        "type": "agent_message", "text": json.dumps(value)}}) + "\n" +
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 12}}) + "\n").encode("utf-8")


def test_actual_last_agent_message_must_equal_final_value():
    from recommend_book.classification.runner import bind_final_message
    value = {"results": [{"work_id": "w", "relevance": None}]}
    binding = bind_final_message(event_bytes(value), json.dumps(value).encode("utf-8"))
    assert binding["value"] == value
    assert binding["events"]["usage"] == {"input_tokens": 12}
    assert binding["last_agent_message"] == json.dumps(value)


@pytest.mark.parametrize("events,final,reason", [
    (b'{"type":"turn.completed"}\n', b'{}', "agent message"),
    (event_bytes({"work_id": "a"}), b'{"work_id":"b"}', "differ"),
    (event_bytes({"value": 1}), b'{"value":1,"value":1}', "duplicate"),
    (event_bytes({"value": 1}), b'{"value":NaN}', "nonfinite"),
    (b'\xff', b'{}', "UTF-8"),
])
def test_protocol_rejects_unbound_or_malformed_native_output(events, final, reason):
    from recommend_book.classification.runner import bind_final_message
    with pytest.raises(ValueError, match=reason):
        bind_final_message(events, final)


def test_attempt_raw_artifacts_are_append_only(tmp_path):
    from recommend_book.classification.runner import immutable_bytes
    target = tmp_path / "actual.stdin.utf8"
    original = "実際の送信\r\nDATA".encode("utf-8")
    immutable_bytes(target, original)
    with pytest.raises(FileExistsError):
        immutable_bytes(target, b"replacement")
    assert target.read_bytes() == original


def allowed_fixture(tmp_path):
    from test_usage_guard import fixture_ledger
    work, ledger, raw, official, payload = fixture_ledger(tmp_path)
    binary = tmp_path / "native-fixture.exe"
    binary.write_bytes(b"actual unit-test executable artifact")
    approve_native_recipes(ledger,payload)
    return work, ledger, binary, payload


def approve_native_recipes(ledger,payload):
    import hashlib
    from recommend_book.classification.runner import build_run_config,ROOT
    from recommend_book.classification.specs import SPEC_NAMES
    from recommend_book.classification import load_taxonomy
    from recommend_book.jobs import canonical_json
    verifier = ROOT / 'src/recommend_book/classification/native_attempt.py'
    payload['execution_recipes'] = {}
    for spec in SPEC_NAMES:
        for batch in (1,5):
            config = build_run_config('gpt-6.1-sol','fake-1',load_taxonomy(),batch,execution_spec=spec)
            payload['execution_recipes'][hashlib.sha256(canonical_json(config).encode()).hexdigest()] = {
                'verifier_path':str(verifier),'verifier_sha256':hashlib.sha256(verifier.read_bytes()).hexdigest()}
    ledger.write_text(json.dumps(payload),encoding='utf-8')


def allowed_many_fixture(tmp_path,count=2):
    """Real independent CC0 raw identities, immutable DB rows and per-field proof."""
    import hashlib
    from recommend_book.database import DatasetDB
    from recommend_book.models import SourceRecord,Title,Evidence
    from recommend_book.usage_guard import source_binding,build_input_entry,model_hash
    from test_usage_guard import fixture_ledger
    first = tmp_path / 'first';first.mkdir()
    _, _, _, _, payload = fixture_ledger(first)
    records = []
    for number in range(count):
        qid = 'Q'+str(number+101)
        title = 'Book '+str(number+1)
        raw = tmp_path / f'{qid}.json'
        raw.write_text(json.dumps({'entities':{qid:{'id':qid,'labels':{'en':{'value':title}},'claims':{}}}}))
        evidence = Evidence(evidence_id='wd:'+qid+':title',kind='bibliographic',text=title,
            source_url=f'https://www.wikidata.org/wiki/{qid}',rights_policy_id='wikidata-cc0',llm_input_allowed=True,locator='label:en')
        records.append(SourceRecord(source='wikidata',source_id=qid,source_url=evidence.source_url,
            retrieved_at='2026-10-13T00:00:00Z',provider_id='wikidata',record_kind='work',
            titles=[Title(value=title,language='en')],evidence=[evidence],raw_path=str(raw),
            raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),rights_policy_id='wikidata-cc0'))
    dbpath = tmp_path / 'source-many.sqlite'
    with DatasetDB(dbpath) as db:
        db.ingest(records);works = db.work_inputs()
    payload['inputs'] = {model_hash(w):build_input_entry(w,{ref:source_binding(dbpath,ref)for ref in w.source_record_ids})for w in works}
    ledger = tmp_path / 'ledger-many.json'
    approve_native_recipes(ledger,payload)
    binary = tmp_path / 'native-fixture.exe';binary.write_bytes(b'unit-test native binary')
    return works,ledger,binary,payload


def unknown_result(work, version="fake-1"):
    from recommend_book.classification import input_hash, load_taxonomy
    from recommend_book.classification.validation import target_language
    return {"work_id": work.work_id, "expression_id": work.expression_id,
        "target_language": target_language(work), "taxonomy_version": load_taxonomy()["version"],
        "prompt_version": "1.0", "model": "gpt-6.1-sol", "cli_version": version,
        "input_hash": input_hash(work), "labels": [],
        "missing_axes": [{"axis": a["axis_id"], "reason": "Bibliography has no narrative support", "decision": "unknown"}
            for a in load_taxonomy()["axes"]], "conflicts": [], "needs_human_review": True}


def install_native(monkeypatch, work, *, responder=None):
    calls = []
    def fake(argv, **kwargs):
        if "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, b"fake-1\n", b"")
        calls.append((argv, kwargs))
        if responder:
            returned = responder(argv, kwargs, len(calls))
            if returned is not None:
                return returned
        assert isinstance(kwargs["input"], bytes)
        assert kwargs.get("text", False) is False
        value = unknown_result(work)
        Path(argv[argv.index("-o") + 1]).write_bytes(json.dumps(value).encode("utf-8"))
        return subprocess.CompletedProcess(argv, 0, event_bytes(value), b"native stderr bytes\r\n")
    monkeypatch.setattr("recommend_book.classification.runner.subprocess.run", fake)
    monkeypatch.setattr("recommend_book.classification.runner.time.sleep", lambda _: None)
    return calls


def test_guarded_attempt_saves_actual_sent_and_captured_bytes(monkeypatch, tmp_path):
    import hashlib
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    calls = install_native(monkeypatch, work)
    output = tmp_path / "new-run"
    manifest = run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    assert manifest["completed"] == 1
    assert len(calls) == 1
    argv, kwargs = calls[0]
    sent = next((output / "attempts").glob("*.stdin.utf8"))
    assert sent.read_bytes() == kwargs["input"]
    assert next((output / "attempts").glob("*.stderr.txt")).read_bytes() == b"native stderr bytes\r\n"
    request = json.loads(next((output / "attempts").glob("*.request.json")).read_bytes())
    assert request["argv"] == argv
    assert request["stdin_sha256"] == hashlib.sha256(kwargs["input"]).hexdigest()
    assert request["executable_sha256"][str(binary.resolve())] == hashlib.sha256(binary.read_bytes()).hexdigest()
    assert request["prompt_usage_receipt"]["allowed"] is True
    binding = json.loads(next((output / "attempts").glob("*.binding.json")).read_bytes())
    assert binding["actual_exit_code"] == 0
    assert binding["final_matches_last_agent_message"] is True
    attempt = json.loads(next((output / "attempts").glob("*.attempt.json")).read_bytes())
    assert attempt["actual_exit_code"] == 0
    assert attempt["stdout_sha256"] == hashlib.sha256(event_bytes(unknown_result(work))).hexdigest()
    assert attempt["error_category"] is None


def test_unbound_response_is_held_without_transient_retry(monkeypatch, tmp_path):
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    def responder(argv, kwargs, count):
        Path(argv[argv.index("-o") + 1]).write_bytes(json.dumps(unknown_result(work)).encode())
        return subprocess.CompletedProcess(argv, 0, b'{"type":"turn.completed"}\n', b"")
    calls = install_native(monkeypatch, work, responder=responder)
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert manifest["completed"] == 0
    assert manifest["failed"] == 1
    assert manifest["cli_invocations"] == 1


def test_revoked_full_input_cannot_reuse_completed_cache(monkeypatch, tmp_path):
    from recommend_book.usage_guard import UsageDenied, model_hash
    work, ledger, binary, payload = allowed_fixture(tmp_path)
    calls = install_native(monkeypatch, work)
    output = tmp_path / "new-run"
    first = run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    before = (output / "results.jsonl").read_bytes()
    payload["revoked_input_hashes"] = [model_hash(work)]
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(UsageDenied):
        run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert (output / "results.jsonl").read_bytes() == before
    assert first["completed"] == 1


def test_retry_rechecks_usage_before_second_cli(monkeypatch, tmp_path):
    from recommend_book.usage_guard import model_hash
    work, ledger, binary, payload = allowed_fixture(tmp_path)
    def responder(argv, kwargs, count):
        payload["revoked_input_hashes"] = [model_hash(work)]
        ledger.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, b"", b"429 rate limit")
    calls = install_native(monkeypatch, work, responder=responder)
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert manifest["completed"] == 0
    assert manifest["stopped"] is True
    assert "usage_denied" in manifest["stop_reasons"]
    assert manifest["selected"] == manifest["completed"] + manifest["failed"] + manifest["pending"]


def test_usage_revoked_during_cli_cannot_adopt_successful_result(monkeypatch, tmp_path):
    from recommend_book.usage_guard import model_hash
    work, ledger, binary, payload = allowed_fixture(tmp_path)
    def responder(argv, kwargs, count):
        value = unknown_result(work)
        Path(argv[argv.index("-o") + 1]).write_bytes(json.dumps(value).encode())
        payload["revoked_input_hashes"] = [model_hash(work)]
        ledger.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0, event_bytes(value), b"")
    calls = install_native(monkeypatch, work, responder=responder)
    output = tmp_path / "new-run"
    manifest = run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert manifest["completed"] == 0
    assert manifest["failed"] == 1
    assert next(iter(manifest["jobs"].values()))["status"] == "held"
    assert (output / "results.jsonl").read_bytes() == b""


def test_unrecognized_cli_error_is_held_without_retry(monkeypatch, tmp_path):
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    calls = install_native(monkeypatch, work, responder=lambda *args:
        subprocess.CompletedProcess(args[0], 2, b"", b"unsupported command option"))
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert manifest["failed"] == 1


def test_changed_artifact_blocks_retry_before_actual_cli(monkeypatch, tmp_path):
    import recommend_book.classification.runner as runner
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    original = runner.build_run_config
    changed = False
    def config(*args, **kwargs):
        result = original(*args, **kwargs)
        if changed:
            result["runner_hash"] = "changed-artifact"
        return result
    monkeypatch.setattr(runner, "build_run_config", config)
    def responder(argv, kwargs, count):
        nonlocal changed
        changed = True
        return subprocess.CompletedProcess(argv, 1, b"", b"429 rate limit")
    calls = install_native(monkeypatch, work, responder=responder)
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert "execution_context_changed" in manifest["stop_reasons"]
    attempts = [json.loads(p.read_bytes()) for p in (tmp_path / "new-run" / "attempts").glob("*.attempt.json")]
    assert any(a["actual_cli_started"] is False for a in attempts)


@pytest.mark.parametrize("controls", [{"concurrency": True}, {"concurrency": 9}, {"batch_size": True},
    {"timeout": True}, {"limit": True}, {"max_observed_tokens": True}, {"max_run_seconds": False}])
def test_resource_controls_reject_bool_and_shared_cap_before_ledger(controls, tmp_path):
    with pytest.raises(ValueError,match=next(iter(controls))):
        run_classification([local_work()], tmp_path / "new-run", **controls)


@pytest.mark.parametrize("alteration", ["annotation", "suffix", "identity", "taxonomy"])
def test_whole_prompt_binding_rejects_unapproved_extra_data(monkeypatch, tmp_path, alteration):
    from recommend_book.classification.specs import ExecutionSpec
    original = ExecutionSpec.build_prompt
    def injected(self, *args, **kwargs):
        text = original(self, *args, **kwargs)
        if alteration == "suffix":
            return text + "\nsecret injected outside local data"
        prefix, body = text.split("\nBEGIN_LOCAL_DATA_JSON\n", 1)
        raw, suffix = body.rsplit("\nEND_LOCAL_DATA_JSON", 1)
        payload = json.loads(raw)
        if alteration == "annotation":
            payload["annotations"] = {"secret": "unauthorized additional input"}
        elif alteration == "identity":
            payload["output_identity"]["cli_version"] = "foreign-cli"
        else:
            payload["taxonomy"]["extra"] = "unauthorized additional input"
        return prefix + "\nBEGIN_LOCAL_DATA_JSON\n" + json.dumps(payload, ensure_ascii=False) + "\nEND_LOCAL_DATA_JSON" + suffix
    monkeypatch.setattr(ExecutionSpec, "build_prompt", injected)
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    calls = install_native(monkeypatch, work)
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert calls == []
    assert manifest["failed"] == 1
    assert manifest["cli_invocations"] == 0
    assert "prompt_context_mismatch" in manifest["stop_reasons"]


def test_same_namespace_refuses_changed_ledger_even_when_still_allowed(monkeypatch, tmp_path):
    work, ledger, binary, payload = allowed_fixture(tmp_path)
    calls = install_native(monkeypatch, work)
    output = tmp_path / "new-run"
    run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    before = (output / "manifest.json").read_bytes()
    payload["note"] = "separately approved update requires a new namespace"
    ledger.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="ledger"):
        run_classification([work], output, executable=str(binary), usage_ledger=ledger)
    assert (output / "manifest.json").read_bytes() == before
    assert len(calls) == 1


def test_ledger_bytes_update_during_transient_error_stops_new_cli(monkeypatch, tmp_path):
    work, ledger, binary, payload = allowed_fixture(tmp_path)
    def responder(argv, kwargs, count):
        payload["note"] = "changes no permissions but replaces the frozen ledger"
        ledger.write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.CompletedProcess(argv, 1, b"", b"429 rate limit")
    calls = install_native(monkeypatch, work, responder=responder)
    manifest = run_classification([work], tmp_path / "new-run", executable=str(binary), usage_ledger=ledger)
    assert len(calls) == 1
    assert "usage_denied" in manifest["stop_reasons"]


def test_unregistered_execution_recipe_refused_before_output_or_exec(monkeypatch,tmp_path):
    from recommend_book.usage_guard import UsageDenied
    work,ledger,binary,payload=allowed_fixture(tmp_path)
    payload['execution_recipes']={};ledger.write_text(json.dumps(payload))
    calls=install_native(monkeypatch,work);out=tmp_path/'new-run'
    with pytest.raises(UsageDenied):run_classification([work],out,executable=str(binary),usage_ledger=ledger)
    assert calls==[] and not out.exists()


def test_failed_process_launch_is_not_reported_as_started_cli(monkeypatch,tmp_path):
    work,ledger,binary,_=allowed_fixture(tmp_path)
    def failed(*args):raise OSError('Cannot create process')
    install_native(monkeypatch,work,responder=failed)
    out=tmp_path/'new-run';report=run_classification([work],out,executable=str(binary),usage_ledger=ledger)
    attempt=json.loads(next((out/'attempts').glob('*.attempt.json')).read_bytes())
    assert attempt['actual_cli_started'] is False
    assert attempt['launch_attempted'] is True
    assert report['cli_invocations']==0 and report['failed']==1


def test_fixed_prepared_cli_identity_must_match_before_exec(monkeypatch,tmp_path):
    from recommend_book.classification.runner import build_run_config
    from recommend_book.classification import load_taxonomy
    work,ledger,binary,_=allowed_fixture(tmp_path);calls=install_native(monkeypatch,work)
    expected=build_run_config('gpt-6.1-sol','foreign-version',load_taxonomy(),1,execution_spec='guarded-v3-baseline')
    out=tmp_path/'new-run'
    with pytest.raises(ValueError,match='fixed execution config'):
        run_classification([work],out,executable=str(binary),usage_ledger=ledger,expected_run_config=expected)
    assert not out.exists() and calls==[]


@pytest.mark.parametrize('change',['roster','order','spec','batch_size'])
def test_resume_requires_exact_config_and_ordered_whole_input_snapshot(monkeypatch,tmp_path,change):
    from test_classification import native_many
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    calls=native_many(monkeypatch,works);out=tmp_path/'new-run'
    run_classification(works,out,executable=str(binary),usage_ledger=ledger)
    before=(out/'manifest.json').read_bytes()
    selected=works[:1] if change=='roster' else works[::-1] if change=='order' else works
    options={'execution_spec':'definitions-v3'} if change=='spec' else {'batch_size':5} if change=='batch_size' else {}
    with pytest.raises(ValueError,match='snapshot|config'):
        run_classification(selected,out,executable=str(binary),usage_ledger=ledger,**options)
    assert (out/'manifest.json').read_bytes()==before and len(calls)==2


def test_malformed_usage_container_is_protocol_value_error():
    from recommend_book.classification.runner import bind_final_message
    events=event_bytes({'ok':True})+b'{"type":"turn.completed","usage":"not-an-object"}\n'
    with pytest.raises(ValueError,match='usage'):
        bind_final_message(events,b'{"ok":true}')


def test_escaped_surrogate_agent_text_is_held_without_uncaught_error(monkeypatch,tmp_path):
    work,ledger,binary,_=allowed_fixture(tmp_path)
    def response(argv,kw,n):
        Path(argv[argv.index('-o')+1]).write_bytes(json.dumps(unknown_result(work)).encode())
        return subprocess.CompletedProcess(argv,0,b'{"type":"item.completed","item":{"type":"agent_message","text":"\\ud800"}}\n',b'')
    calls=install_native(monkeypatch,work,responder=response)
    report=run_classification([work],tmp_path/'new-run',executable=str(binary),usage_ledger=ledger)
    assert report['failed']==1 and report['completed']==0 and len(calls)==1
