from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.classification.validation import input_hash
from recommend_book.models import Evidence, WorkInput


def script():
    spec=importlib.util.spec_from_file_location('prepare_enrichment_classification',Path(__file__).parents[1]/'scripts/prepare_enrichment_classification.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def sha(raw):return hashlib.sha256(raw).hexdigest()


def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')


def fixture(tmp_path,remap=False):
    old=tmp_path/'old';new=tmp_path/'enriched';old.mkdir();new.mkdir()
    works=[WorkInput(work_id=f'work_{i}',title=f'Title {i}',original_language='en',source_record_ids=[f'src-{i}']) for i in range(4)]
    works[0].evidence=[Evidence(evidence_id='kept',kind='subject',text='Mystery',source_url='https://example.test/kept',rights_policy_id='fixture',llm_input_allowed=True)]
    old_lines=[(json.dumps(w.model_dump(mode='json'),indent=None,separators=(', ', ': '))+'\n').encode() for w in works]
    old_raw=b''.join(old_lines);(old/'ordered-inputs.jsonl').write_bytes(old_raw)
    save(old/'manifest.json',{'work_count':4,'ordered_inputs_sha256':sha(old_raw),'processing_context':{'legacy':'unknown'}})
    enriched=deepcopy(works);enriched[1].title='New title';enriched[1].source_record_ids.append('openlibrary-new')
    enriched[2].evidence=[Evidence(evidence_id='allowed',kind='synopsis',text='A detective solves a murder.',source_url='https://openlibrary.org/works/OL1W',rights_policy_id='openlibrary',llm_input_allowed=True),
        Evidence(evidence_id='terms',kind='note',text='DO NOT SEND RETRIEVAL CONDITIONS',source_url='https://openlibrary.org/terms',rights_policy_id='denied',llm_input_allowed=False)]
    if remap:enriched[2].work_id='work_new2'
    # Enriched producer order/serialization deliberately differs from the old ordered input.
    new_lines=[(w.model_dump_json()+'\n').encode() for w in enriched]
    new_raw=b''.join(reversed(new_lines));(new/'bibliographic-work-inputs.jsonl').write_bytes(new_raw)
    crosswalk=[{'old_canonical_id':a.work_id,'new_canonical_ids':[b.work_id],'old_work_node_ids':[a.work_id]} for a,b in zip(works,enriched)]
    save(new/'canonical-crosswalk.json',crosswalk);(new/'dataset.sqlite').write_bytes(b'fixture immutable DB bytes')
    save(new/'manifest.json',{'outputs_are_valid':True,'canonical_works_before':4,'canonical_works_after':4,'group_connected_partition_unchanged':True,'output_sha256':{name:sha((new/name).read_bytes()) for name in ('bibliographic-work-inputs.jsonl','canonical-crosswalk.json','dataset.sqlite')}})
    return old/'ordered-inputs.jsonl',new,works,enriched,old_lines,new_lines


def test_delta_only_order_protection_prompt_filter_and_ten_context_snapshots(tmp_path):
    old,new,works,enriched,old_lines,new_lines=fixture(tmp_path)
    before={p:p.read_bytes() for tree in (old.parent,new) for p in tree.rglob('*') if p.is_file()}
    out=tmp_path/'delta';report=script().prepare_enrichment_classification(old,new,out,expected_count=4,cli_version='fixture-cli')
    assert report['changed_count']==report['work_count']==2 and report['unchanged_count']==2
    assert report['changed_work_ids']==['work_1','work_2']
    assert (out/'ordered-inputs.jsonl').read_bytes()==new_lines[1]+new_lines[2]
    assert (out/'all-inputs.jsonl').read_bytes()==old_lines[0]+new_lines[1]+new_lines[2]+old_lines[3]
    assert report['unchanged_rows_byte_preserved'] is True
    assert report['execution_state']=='prepared_not_started' and report['llm_started'] is False
    assert report['execution_context']['artifact_count']==10
    for name,checksum in report['execution_context']['artifact_sha256'].items():
        assert sha((out/'artifacts'/name).read_bytes())==checksum
    prompts=[p.read_text() for p in (out/'prompts').glob('*.txt')]
    assert any('A detective solves a murder.' in p for p in prompts)
    assert all('DO NOT SEND RETRIEVAL CONDITIONS' not in p for p in prompts)
    assert report['evidence_comparison']['changed']['after']['excluded_count']==1
    assert report['canonical_crosswalk_path']=='canonical-crosswalk.source.json'
    assert (out/report['canonical_crosswalk_path']).read_bytes()==(new/'canonical-crosswalk.json').read_bytes()
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_identity_remap_is_new_input_only_with_provenance_not_old_result_rebind(tmp_path):
    old,new,works,enriched,old_lines,new_lines=fixture(tmp_path,remap=True);out=tmp_path/'delta'
    report=script().prepare_enrichment_classification(old,new,out,expected_count=4)
    assert report['identity_changed_count']==1 and report['classification_rebound'] is False
    assert report['changed_work_ids']==['work_1','work_new2']
    comparisons=json.loads((out/'input-comparison.json').read_text())
    change=next(row for row in comparisons if row['old_work_id']=='work_2')
    assert change['new_work_id']=='work_new2'
    assert change['old_input_hash']==input_hash(works[2]) and change['new_input_hash']==input_hash(enriched[2])
    assert (out/'ordered-inputs.jsonl').read_bytes()==new_lines[1]+new_lines[2]


@pytest.mark.parametrize('mode',['duplicate','split','unknown_target','missing_old','different_expression','input_sha','db_sha','db_sidecar','output_exists','nested_output','count'])
def test_bad_identity_manifest_or_output_boundary_is_rejected(tmp_path,mode):
    old,new,works,enriched,old_lines,new_lines=fixture(tmp_path);out=tmp_path/'delta'
    manifest=json.loads((new/'manifest.json').read_text());cross=json.loads((new/'canonical-crosswalk.json').read_text())
    if mode in ('split','unknown_target','missing_old'):
        if mode=='split':cross[0]['new_canonical_ids'].append('extra')
        elif mode=='unknown_target':cross[0]['new_canonical_ids']=['unknown']
        else:cross.pop()
        save(new/'canonical-crosswalk.json',cross);manifest['output_sha256']['canonical-crosswalk.json']=sha((new/'canonical-crosswalk.json').read_bytes());save(new/'manifest.json',manifest)
    elif mode in ('duplicate','different_expression'):
        if mode=='duplicate':enriched[1].work_id=enriched[0].work_id
        else:enriched[1].expression_id='translation'
        raw=b''.join((w.model_dump_json()+'\n').encode() for w in enriched);(new/'bibliographic-work-inputs.jsonl').write_bytes(raw)
        manifest['output_sha256']['bibliographic-work-inputs.jsonl']=sha(raw);save(new/'manifest.json',manifest)
    elif mode=='input_sha':old.write_bytes(old.read_bytes()+b' ')
    elif mode=='db_sha':(new/'dataset.sqlite').write_bytes(b'changed')
    elif mode=='db_sidecar':(new/'dataset.sqlite-wal').write_bytes(b'unlisted pending DB changes')
    elif mode=='output_exists':out.mkdir();(out/'keep').write_text('keep')
    elif mode=='nested_output':out=new/'delta'
    elif mode=='count':manifest['canonical_works_after']=5;save(new/'manifest.json',manifest)
    with pytest.raises(ValueError):script().prepare_enrichment_classification(old,new,out,expected_count=4)


def test_source_mutation_during_prompt_preparation_prevents_publication(tmp_path,monkeypatch):
    old,new,*_=fixture(tmp_path);module=script();original=module.build_prompt
    def mutate(*args,**kwargs):
        value=original(*args,**kwargs);old.write_bytes(old.read_bytes()+b' ');return value
    monkeypatch.setattr(module,'build_prompt',mutate)
    with pytest.raises(ValueError,match='changed during preparation'):
        module.prepare_enrichment_classification(old,new,tmp_path/'delta',expected_count=4)
    assert not (tmp_path/'delta').exists()
    assert not list(tmp_path.glob('*.prepare-*'))


def test_source_audit_proves_denied_retrieval_description_is_absent_without_mutating_snapshot(tmp_path):
    old,new,*_=fixture(tmp_path);denied=Evidence(evidence_id='denied',kind='description',text='DO NOT SEND RETRIEVAL CONDITIONS',source_url='https://openlibrary.org/terms',rights_policy_id='openlibrary-prose',llm_input_allowed=False)
    records=[{'record_kind':'work','raw_sha256':'0'*64,'evidence':[denied.model_dump(mode='json')]}]
    raw=b''.join((json.dumps(r)+'\n').encode() for r in records);(new/'accepted-records.jsonl').write_bytes(raw)
    manifest=json.loads((new/'manifest.json').read_text());manifest['output_sha256']['accepted-records.jsonl']=sha(raw);save(new/'manifest.json',manifest)
    out=tmp_path/'delta';module=script();module.prepare_enrichment_classification(old,new,out,expected_count=4)
    before={p:p.read_bytes() for p in out.rglob('*') if p.is_file()}
    report=module.audit_enrichment_sources(out,tmp_path/'source-audit.json')
    assert report['source_evidence']['denied_count']==1
    assert report['denied_evidence_rebound_to_input']==0 and report['input_denied_count']==1
    assert report['llm_started'] is False and report['raw_retrieval_performed'] is False
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_raw_cli_version_changes_config_and_prompt_metadata_only_without_rewriting_v1(tmp_path):
    old,new,*_=fixture(tmp_path);module=script();v1=tmp_path/'v1';v2=tmp_path/'v2'
    first=module.prepare_enrichment_classification(old,new,v1,expected_count=4,cli_version='0.162.1')
    before={p.relative_to(v1):p.read_bytes() for p in v1.rglob('*') if p.is_file()}
    second=module.prepare_enrichment_classification(old,new,v2,expected_count=4,cli_version='codex-cli 0.162.1')
    for name in ('ordered-inputs.jsonl','all-inputs.jsonl',*('artifacts/'+n for n in module.ARTIFACTS)):
        assert (v1/name).read_bytes()==(v2/name).read_bytes()
    old_config=deepcopy(first['execution_context']['run_config']);new_config=deepcopy(second['execution_context']['run_config'])
    assert old_config.pop('cli_version')=='0.162.1'
    assert new_config.pop('cli_version')=='codex-cli 0.162.1'
    assert old_config==new_config
    for prompt in (v1/'prompts').glob('*.txt'):
        expected=prompt.read_bytes().replace(b'"cli_version": "0.162.1"',b'"cli_version": "codex-cli 0.162.1"')
        assert expected==(v2/'prompts'/prompt.name).read_bytes()
    assert all((v1/name).read_bytes()==raw for name,raw in before.items())
