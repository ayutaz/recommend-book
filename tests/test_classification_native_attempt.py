import hashlib
import json
from pathlib import Path

import pytest

from recommend_book.classification.runner import run_classification
from test_classification_guarded import allowed_fixture, install_native, unknown_result


def attempt_fixture(tmp_path, monkeypatch):
    work, ledger, binary, _ = allowed_fixture(tmp_path)
    install_native(monkeypatch,work)
    output = tmp_path / "new-run"
    report = run_classification([work],output,executable=str(binary),usage_ledger=ledger)
    files = next((output / "attempts").glob("*.request.json"))
    stem = str(files)[:-len(".request.json")]
    suffixes = {"request":".request.json","attempt":".protocol-attempt.json","prompt":".stdin.utf8",
        "stdout":".stdout.jsonl","stderr":".stderr.txt","final":".final.json"}
    from pathlib import Path
    descriptor = {"kind":"classification-native-attempt-v3","execution_spec":"guarded-v3-baseline",
        "run_dir":str(output.resolve()),"run_id":report['run_id'],"attempt_stem":str(Path(stem).relative_to(output)),
        "usage_ledger_sha256":report['execution_context']['usage_ledger_sha256'],
        "originating_input_hashes":[unknown_result(work)["input_hash"]],
        "config_sha256":hashlib.sha256(__import__('recommend_book.jobs',fromlist=['canonical_json']).canonical_json(report['config']).encode()).hexdigest(),
        "artifacts":{k:{"path":stem+s,"sha256":hashlib.sha256(Path(stem+s).read_bytes()).hexdigest()} for k,s in suffixes.items()}}
    admission=output/f"run.{report['run_id']}.admission.json"
    descriptor['artifacts']['admission']={'path':str(admission),'sha256':hashlib.sha256(admission.read_bytes()).hexdigest()}
    return work,descriptor,unknown_result(work)


def test_generated_native_descriptor_reproduces_actual_result(tmp_path,monkeypatch):
    from recommend_book.classification.native_attempt import verify_native_attempt
    work,descriptor,result = attempt_fixture(tmp_path,monkeypatch)
    receipt = verify_native_attempt(descriptor,result,descriptor['originating_input_hashes'],
        expected_config_sha256=descriptor['config_sha256'],works=[work])
    assert receipt['allowed'] is True
    assert receipt['actual_exit_code'] == 0


@pytest.mark.parametrize('change',['result','stdin','exit','config','origin','artifact_sha','event_error','tool'])
def test_native_descriptor_refuses_counterfeit_or_incomplete_proof(tmp_path,monkeypatch,change):
    from recommend_book.classification.native_attempt import verify_native_attempt
    from pathlib import Path
    work,descriptor,result = attempt_fixture(tmp_path,monkeypatch)
    if change == 'result':result['needs_human_review'] = False
    elif change == 'config':descriptor['config_sha256'] = 'foreign'
    elif change == 'origin':descriptor['originating_input_hashes'] = ['foreign']
    elif change == 'artifact_sha':descriptor['artifacts']['final']['sha256'] = 'foreign'
    else:
        key = 'prompt' if change == 'stdin' else 'attempt' if change == 'exit' else 'stdout'
        path = Path(descriptor['artifacts'][key]['path'])
        if change == 'stdin':path.write_bytes(path.read_bytes()+b'\nunauthorized material')
        elif change == 'exit':
            value = json.loads(path.read_bytes());value['actual_exit_code']=1;path.write_text(json.dumps(value))
        else:path.write_bytes(path.read_bytes()+json.dumps({'type':'error','message':'failed'} if change == 'event_error' else {'type':'item.completed','item':{'type':'command_execution'}}).encode()+b'\n')
        descriptor['artifacts'][key]['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):verify_native_attempt(descriptor,result,[result['input_hash']],
        expected_config_sha256=hashlib.sha256(__import__('recommend_book.jobs',fromlist=['canonical_json']).canonical_json(json.loads(Path(descriptor['artifacts']['request']['path']).read_bytes())['config']).encode()).hexdigest(),works=[work])


@pytest.mark.parametrize('change',['outside_copy','foreign_run'])
def test_native_descriptor_rejects_same_bytes_outside_original_run(tmp_path,monkeypatch,change):
    from recommend_book.classification.native_attempt import verify_native_attempt
    work,descriptor,result=attempt_fixture(tmp_path,monkeypatch)
    if change=='outside_copy':
        source=Path(descriptor['artifacts']['prompt']['path']);copy=tmp_path/'outside.stdin.utf8';copy.write_bytes(source.read_bytes())
        descriptor['artifacts']['prompt']['path']=str(copy)
    else:descriptor['run_id']='foreign-run'
    with pytest.raises(ValueError,match='namespace|run|attempt'):
        verify_native_attempt(descriptor,result,descriptor['originating_input_hashes'],
            expected_config_sha256=descriptor['config_sha256'],works=[work])
