import importlib.util
import json
from pathlib import Path
from recommend_book.models import SourceRecord,Title,Identifier,Evidence


def load_script():
    spec=importlib.util.spec_from_file_location('collect_prose_poc',Path(__file__).parents[1]/'scripts/collect_prose_poc.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_resume_reuses_checkpoint_and_does_not_request_completed_text(tmp_path,monkeypatch):
    module=load_script()
    class NoNetwork:
        def __init__(self,*args):pass
        def pages(self,*args):raise AssertionError('resume must not re-request completed metadata')
        def query(self,*args,**kwargs):raise AssertionError('resume must not re-request completed prose')
    monkeypatch.setattr(module,'WikisourceAdapter',NoNetwork)
    metadata={'pages':{'Sense and Sensibility':{},'Author:Jane Austen':{}},'authors':{}}
    (tmp_path/'metadata.json').write_text(json.dumps(metadata))
    (tmp_path/'works').mkdir()
    record=SourceRecord(source='wikisource',source_id='42',source_url='https://example.test',retrieved_at='2026-10-12',provider_id='wikisource',record_kind='work',titles=[Title(value='Sense and Sensibility')],identifiers=[Identifier(source='wikisource',value='ws:en:1028',scope='work')],raw_sha256='a'*64,raw_path='raw.bin',rights_policy_id='wikisource-reviewed-original',evidence=[Evidence(evidence_id='ws:42',kind='excerpt',text='Original prose',source_url='https://example.test',rights_policy_id='wikisource-reviewed-original',llm_input_allowed=True)])
    (tmp_path/'works/01.json').write_text(record.model_dump_json())
    manifest=module.collect(tmp_path,1)
    assert manifest['completed']==1 and manifest['excerpt_count']==1
    assert json.loads((tmp_path/'work-inputs.jsonl').read_text())['work_id']=='ws:en:1028'
    assert not manifest['llm_classification_started']
