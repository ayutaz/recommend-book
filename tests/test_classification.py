from copy import deepcopy
import json
import subprocess

import pytest

from recommend_book.models import Evidence, WorkInput
from recommend_book.classification import (
    build_prompt, input_hash, load_taxonomy, validate_result, read_results,
)
from recommend_book.classification.runner import run_classification


@pytest.fixture
def work():
    return WorkInput(work_id="work-1", title="Not a memory source", original_language="en",
        evidence=[Evidence(evidence_id="e1", kind="synopsis", text="A detective solves a murder.",
            source_url="https://example.org/work", rights_policy_id="fixture", llm_input_allowed=True, locator="summary"),
            Evidence(evidence_id="secret", kind="excerpt", text="WITHHELD TEXT", source_url="https://example.org",
                rights_policy_id="unresolved", llm_input_allowed=False)])


def result_for(work):
    return {
        "work_id": work.work_id, "expression_id": work.expression_id, "target_language": "en",
        "taxonomy_version": "1.0", "prompt_version": "1.0", "model": "gpt-6.1-sol", "cli_version": "fake-1",
        "input_hash": input_hash(work), "labels": [{"label_id": "genre.mystery", "axis": "genre",
            "decision": "present", "scope": "work", "expression_id": None,
            "relevance": 0.9, "raw_confidence": 0.8, "calibrated_confidence": None,
            "calibration_status": "uncalibrated", "calibration_version": None,
            "evidence": [{"evidence_id": "e1", "locator": "summary", "quote": "detective solves a murder",
                "summary": None, "directness": "direct"}], "missing_reason": None, "spoiler": "premise"}],
        "missing_axes": [{"axis": axis, "reason": "Insufficient evidence", "decision": "unknown"}
            for axis in ["theme", "structure", "protagonist", "style", "atmosphere"]],
        "conflicts": [], "needs_human_review": True,
    }


def test_taxonomy_six_axes_and_quality_subset():
    taxonomy = load_taxonomy()
    assert len(taxonomy["axes"]) == 6
    assert 60 <= len(taxonomy["labels"]) <= 100
    assert 20 <= sum(label["primary"] for label in taxonomy["labels"]) <= 30
    assert len({label["label_id"] for label in taxonomy["labels"]}) == len(taxonomy["labels"])
    assert all(label["definition"] and label["include"] and label["exclude"] and label["examples"] for label in taxonomy["labels"])


def test_prompt_filters_unapproved_content_and_requires_data_only(work):
    prompt = build_prompt(work, cli_version="fake-1")
    assert "WITHHELD TEXT" not in prompt
    assert "secret" not in prompt
    assert "A detective solves a murder" in prompt
    assert "no tools" in prompt.lower()
    assert "memory" in prompt.lower()


def test_valid_result_and_reference_verification(work):
    valid = result_for(work)
    assert validate_result(valid, work) == valid
    for mutate in [
        lambda x: x.update(work_id="invented"),
        lambda x: x["labels"][0].update(label_id="invented"),
        lambda x: x["labels"][0]["evidence"][0].update(evidence_id="secret"),
        lambda x: x["labels"][0]["evidence"][0].update(quote="fabricated quotation"),
        lambda x: x["labels"][0]["evidence"][0].update(locator="wrong"),
        lambda x: x["labels"][0].update(calibrated_confidence=0.9),
        lambda x: x.update(missing_axes=[]),
        lambda x: x["labels"][0].update(decision="unknown", relevance=0.0),
        lambda x: x["labels"][0].update(axis="style"),
        lambda x: x["labels"][0].update(evidence=[]),
    ]:
        invalid = deepcopy(valid)
        mutate(invalid)
        with pytest.raises(ValueError):
            validate_result(invalid, work)


@pytest.mark.parametrize("decision", ["present", "absent"])
def test_supported_decision_keeps_unknown_relevance(work, decision):
    result = result_for(work)
    result["labels"][0].update(decision=decision, relevance=None)
    if decision == "absent":
        work.evidence[0].text = "There is explicitly no investigation or unresolved puzzle."
        result["input_hash"] = input_hash(work)
        result["labels"][0]["evidence"][0]["quote"] = work.evidence[0].text
    assert validate_result(result, work) == result
    assert result["labels"][0]["relevance"] is None
    assert "genre" not in {m["axis"] for m in result["missing_axes"]}


@pytest.mark.parametrize("invalid_field", ["evidence", "raw_confidence", "denied", "identity", "locator", "quote", "scope"])
def test_nullable_relevance_keeps_known_decision_guards(work, invalid_field):
    result = result_for(work)
    label = result["labels"][0]
    label["relevance"] = None
    if invalid_field == "evidence": label["evidence"] = []
    if invalid_field == "raw_confidence": label["raw_confidence"] = None
    if invalid_field == "denied": work.evidence[0].llm_input_allowed = False; result["input_hash"] = input_hash(work)
    if invalid_field == "identity": result["work_id"] = "different-work"
    if invalid_field == "locator": label["evidence"][0]["locator"] = "invented"
    if invalid_field == "quote": label["evidence"][0]["quote"] = "invented quotation"
    if invalid_field == "scope": label.update(scope="translation", expression_id="missing-expression")
    with pytest.raises(ValueError): validate_result(result, work)


def test_unknown_still_needs_null_relevance_and_reason(work):
    result = result_for(work)
    label = result["labels"][0]
    label.update(decision="unknown", relevance=None, missing_reason="Insufficient evidence")
    result["missing_axes"].append({"axis": "genre", "decision": "unknown", "reason": "Insufficient evidence"})
    assert validate_result(result, work) == result
    for update in [{"relevance": 0.5}, {"missing_reason": None}]:
        invalid = deepcopy(result)
        invalid["labels"][0].update(update)
        with pytest.raises(ValueError, match="Unknown needs null relevance and missing reason"):
            validate_result(invalid, work)


def test_public_run_config_is_offline_and_binds_validation_contract(monkeypatch, work):
    import hashlib
    import recommend_book.classification.runner as runner
    from recommend_book.classification import validation
    from recommend_book.jobs import job_key
    def prohibited(*args, **kwargs): raise AssertionError("No CLI discovery, execution or version query")
    monkeypatch.setattr(runner, "resolve_executable", prohibited)
    monkeypatch.setattr(runner, "cli_version", prohibited)
    monkeypatch.setattr(runner.subprocess, "run", prohibited)
    taxonomy = load_taxonomy()
    before = deepcopy(taxonomy)
    config = runner.build_run_config("fixture-model", "saved-cli-version", taxonomy, 5)
    assert taxonomy == before
    assert config == runner.build_run_config(model="fixture-model", cli_version="saved-cli-version", taxonomy=taxonomy, batch_size=5)
    assert config["validation_version"] == validation.VALIDATION_VERSION == "2.0"
    assert config["validation_hash"] == hashlib.sha256((runner.ROOT / "src/recommend_book/classification/validation.py").read_bytes()).hexdigest()
    assert config["prompt_version"] == "1.0"
    assert config["model"] == "fixture-model" and config["cli_version"] == "saved-cli-version"
    assert config["batch_size"] == 5
    old = {k: v for k, v in config.items() if k not in {"validation_version", "validation_hash"}}
    assert job_key(input_hash(work), old) != job_key(input_hash(work), config)


def test_validation_artifact_change_is_a_new_job(monkeypatch, tmp_path, work):
    import recommend_book.classification.runner as runner
    from recommend_book.jobs import job_key
    for relative in ["schemas/classification.schema.json", "src/recommend_book/classification/prompt.py", "src/recommend_book/classification/validation.py"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((runner.ROOT / relative).read_bytes())
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    taxonomy = load_taxonomy()
    first = runner.build_run_config("fixture-model", "saved-cli", taxonomy, 1)
    path = tmp_path / "src/recommend_book/classification/validation.py"
    path.write_bytes(path.read_bytes() + b"\n# Changed validation artifact\n")
    second = runner.build_run_config("fixture-model", "saved-cli", taxonomy, 1)
    assert first["validation_hash"] != second["validation_hash"]
    assert {k: v for k, v in first.items() if k != "validation_hash"} == {k: v for k, v in second.items() if k != "validation_hash"}
    assert job_key(input_hash(work), first) != job_key(input_hash(work), second)


def test_original_style_rejects_translated_excerpt(work):
    work.evidence[0].kind = "excerpt"
    work.evidence[0].expression_ref = "translated-1"
    valid = result_for(work)
    label = valid["labels"][0]
    label.update(label_id="style.concise", axis="style", scope="original")
    valid["missing_axes"] = [x for x in valid["missing_axes"] if x["axis"] != "style"]
    valid["missing_axes"].append({"axis": "genre", "reason": "missing", "decision": "unknown"})
    with pytest.raises(ValueError, match="original|expression"):
        validate_result(valid, work)


# Runtime tests use raw/SQL/rights-bound inputs; the loose narrative fixture
# above exercises only historical prompt and grounding semantics.
from pathlib import Path
from test_classification_guarded import allowed_many_fixture, unknown_result, event_bytes


def native_many(monkeypatch,works,responder=None):
    calls=[]
    def fake(argv,**kwargs):
        if '--version' in argv:return subprocess.CompletedProcess(argv,0,b'fake-1\n',b'')
        calls.append(argv)
        if responder:
            returned=responder(argv,kwargs,len(calls))
            if returned is not None:return returned
        data=json.loads(kwargs['input'].decode().split('BEGIN_LOCAL_DATA_JSON\n',1)[1].rsplit('\nEND_LOCAL_DATA_JSON',1)[0])
        entries=data.get('works',[data]);results=[]
        for entry in entries:
            work=next(w for w in works if w.work_id==entry['work']['work_id'])
            results.append(unknown_result(work))
        value={'results':results} if len(results)>1 else results[0]
        Path(argv[argv.index('-o')+1]).write_bytes(json.dumps(value).encode())
        return subprocess.CompletedProcess(argv,0,event_bytes(value),b'')
    monkeypatch.setattr('recommend_book.classification.runner.subprocess.run',fake)
    monkeypatch.setattr('recommend_book.classification.runner.time.sleep',lambda _:None)
    return calls


def test_runner_resume_and_raw_events_separate(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path,1)
    calls=native_many(monkeypatch,works);out=tmp_path/'run'
    first=run_classification(works,out,executable=str(binary),usage_ledger=ledger)
    second=run_classification(works,out,executable=str(binary),usage_ledger=ledger)
    assert first['completed']==1 and first['usage']['input_tokens']==12
    assert second['reused']==1 and second['cli_invocations']==0 and second['usage']=={}
    from recommend_book.classification.runner import build_run_config
    assert first['config']==build_run_config('gpt-6.1-sol','fake-1',load_taxonomy(),1,execution_spec='guarded-v3-baseline')
    assert len(calls)==1 and len(read_results(out))==1
    assert len(list((out/'attempts').glob('*.stdout.jsonl')))==1


def test_failure_never_fabricates_result_and_invalid_not_retried(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path,1)
    calls=native_many(monkeypatch,works,lambda argv,kw,n:subprocess.CompletedProcess(argv,0,b'{"type":"turn.completed"}\n',b''))
    report=run_classification(works,tmp_path/'run',executable=str(binary),usage_ledger=ledger)
    assert report['failed']==1 and report['attempts']==1 and len(calls)==1
    assert read_results(tmp_path/'run')==[]
    assert next(iter(report['jobs'].values()))['error_category']=='missing_output'


def test_changed_input_requires_new_whole_provenance(monkeypatch,tmp_path):
    from recommend_book.usage_guard import UsageDenied
    works,ledger,binary,_=allowed_many_fixture(tmp_path,1)
    calls=native_many(monkeypatch,works)
    run_classification(works,tmp_path/'run',executable=str(binary),usage_ledger=ledger)
    old=input_hash(works[0]);works[0].title+=' Changed without raw proof'
    assert old!=input_hash(works[0])
    with pytest.raises(UsageDenied):run_classification(works,tmp_path/'run',executable=str(binary),usage_ledger=ledger)
    assert len(calls)==1


def test_small_batch_exact_ids_and_full_cache_reuse(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    calls=native_many(monkeypatch,works);out=tmp_path/'run'
    first=run_classification(works,out,executable=str(binary),usage_ledger=ledger,batch_size=5)
    second=run_classification(works,out,executable=str(binary),usage_ledger=ledger,batch_size=5)
    assert first['completed']==2 and first['cli_invocations']==1 and len(calls)==1
    assert first['usage']['input_tokens']==12 and second['reused']==2 and second['cli_invocations']==0


@pytest.mark.parametrize('bad',['tool','duplicate'])
def test_invalid_batch_is_held_whole_without_partial_recovery(monkeypatch,tmp_path,bad):
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    def response(argv,kw,n):
        value={'results':[unknown_result(works[0]),unknown_result(works[0])]} if bad=='duplicate' else {'results':[unknown_result(w)for w in works]}
        Path(argv[argv.index('-o')+1]).write_bytes(json.dumps(value).encode())
        events=event_bytes(value)
        if bad=='tool':events+=b'{"type":"item.completed","item":{"type":"command_execution"}}\n'
        return subprocess.CompletedProcess(argv,0,events,b'')
    calls=native_many(monkeypatch,works,response)
    report=run_classification(works,tmp_path/'run',executable=str(binary),usage_ledger=ledger,batch_size=5)
    assert report['failed']==2 and report['cli_invocations']==1 and len(calls)==1
    assert read_results(tmp_path/'run')==[]


def test_usage_limit_stops_new_work(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    calls=native_many(monkeypatch,works,lambda argv,kw,n:subprocess.CompletedProcess(argv,1,b'',b'usage limit reached'))
    report=run_classification(works,tmp_path/'run',executable=str(binary),usage_ledger=ledger,concurrency=1)
    assert report['failed']==1 and report['pending']==1 and report['attempts']==1 and report['stopped'] and len(calls)==1


def test_token_budget_stop_and_resume_reuses_completed(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    calls=native_many(monkeypatch,works);out=tmp_path/'run'
    stopped=run_classification(works,out,executable=str(binary),usage_ledger=ledger,concurrency=1,max_observed_tokens=12)
    assert stopped['completed']==1 and stopped['pending']==1 and stopped['stop_reasons']==['token_budget']
    resumed=run_classification(works,out,executable=str(binary),usage_ledger=ledger,concurrency=1)
    assert resumed['completed']==2 and resumed['reused']==1 and len(calls)==2


def test_rate_stop_preserves_retryable_job_for_resume(monkeypatch,tmp_path):
    works,ledger,binary,_=allowed_many_fixture(tmp_path,1)
    calls=native_many(monkeypatch,works,lambda argv,kw,n:subprocess.CompletedProcess(argv,1,b'',b'429 rate limit') if n==1 else None)
    out=tmp_path/'run'
    stopped=run_classification(works,out,executable=str(binary),usage_ledger=ledger,stop_on_rate_limit=True)
    assert stopped['pending']==1 and stopped['failed']==0 and stopped['stop_reasons']==['rate_limit']
    resumed=run_classification(works,out,executable=str(binary),usage_ledger=ledger,stop_on_rate_limit=True)
    assert resumed['completed']==1 and next(iter(resumed['jobs'].values()))['attempts']==2 and len(calls)==2
