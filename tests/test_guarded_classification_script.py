import hashlib
import json
import importlib.util
from pathlib import Path

import pytest

from test_classification_guarded import allowed_many_fixture
from recommend_book.jobs import canonical_json


def module():
    path=Path(__file__).resolve().parents[1]/'scripts/run_guarded_classification.py'
    spec=importlib.util.spec_from_file_location('guarded_script',path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


def setup(tmp_path,monkeypatch):
    m=module();monkeypatch.setattr(m,'OUTPUT_ROOT',tmp_path/'new-classification')
    works,ledger,binary,_=allowed_many_fixture(tmp_path)
    inputs=tmp_path/'inputs.jsonl';raw=''.join(canonical_json(w.model_dump(mode='json'))+'\n'for w in works).encode()
    inputs.write_bytes(raw)
    return m,works,ledger,inputs,raw


def test_prepare_freezes_raw_input_ledger_complete_prompt_and_context_without_cli(tmp_path,monkeypatch):
    m,works,ledger,inputs,raw=setup(tmp_path,monkeypatch)
    monkeypatch.setattr('recommend_book.classification.runner.subprocess.run',lambda *a,**k:pytest.fail('No CLI during prepare'))
    out=m.OUTPUT_ROOT/'fixed'
    report=m.prepare(inputs,ledger,out,cli_version='fake-1',batch_size=5)
    assert (out/'inputs.jsonl').read_bytes()==raw
    assert report['llm_started'] is False and report['cli_invocations']==0
    assert report['input_sha256']==hashlib.sha256(raw).hexdigest()
    assert report['usage_ledger_sha256']==hashlib.sha256(ledger.read_bytes()).hexdigest()
    assert len(list((out/'prompt-previews').glob('*.utf8')))==2
    assert report['config']['execution_spec']=='guarded-v3-baseline'
    assert set(report['root_context_artifacts'])=={'config/autonomous-next-v3.json','docs/source-rights-review-scope-v3.md'}
    assert m.verify_prepared(out)['input_sha256']==report['input_sha256']


@pytest.mark.parametrize('bad',['output_exists','wrong_namespace','duplicate_input','modified_ledger','modified_preview','modified_input'])
def test_prepare_and_resume_refuse_collision_or_changed_fixed_material(tmp_path,monkeypatch,bad):
    m,works,ledger,inputs,raw=setup(tmp_path,monkeypatch);out=m.OUTPUT_ROOT/'fixed'
    if bad=='output_exists':out.mkdir(parents=True)
    elif bad=='wrong_namespace':out=tmp_path/'historical-forbidden'
    elif bad=='duplicate_input':inputs.write_bytes(raw+raw.splitlines(keepends=True)[0])
    if bad in ('output_exists','wrong_namespace','duplicate_input'):
        with pytest.raises(ValueError):m.prepare(inputs,ledger,out,cli_version='fake-1')
        return
    m.prepare(inputs,ledger,out,cli_version='fake-1')
    if bad=='modified_ledger':ledger.write_bytes(ledger.read_bytes()+b' ')
    elif bad=='modified_input':(out/'inputs.jsonl').write_bytes(raw+b' ')
    else:
        path=next((out/'prompt-previews').glob('*.utf8'));path.write_bytes(path.read_bytes()+b' injected')
    with pytest.raises(ValueError):m.verify_prepared(out)
