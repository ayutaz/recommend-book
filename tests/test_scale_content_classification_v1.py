"""New content native boundaries; no CLI or network in focused tests."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from recommend_book.models import Evidence, WorkInput
from recommend_book.scale_content_classification_v1 import (
    actual_content_evidence_ids, admit_native_labels, display_source_attributions,
    select_review_work_ids, source_genre_terms, verify_catalogue_classification)
from recommend_book.scale_content_control_v1 import actual_gate, verify_control_receipt
from test_scale_classification_v1 import label, record

ROOT = Path(__file__).resolve().parents[1]


def content():
    return Evidence(evidence_id='content:revision:1', kind='synopsis',
        text='A family leaves its home and struggles with displacement.',
        source_url='https://example.test/article?oldid=1', rights_policy_id='content-test',
        llm_input_allowed=True, locator='/plot', license='CC-BY-SA-4.0',
        license_url='https://creativecommons.org/licenses/by-sa/4.0/',
        attribution={'author': 'Contributors', 'revision_id': 1})


def native(work, labels):
    return {'work_id': work.work_id, 'labels': labels, 'conflicts': []}


def test_mixed_provider_raw_never_masquerades_as_wikidata(tmp_path):
    wd = record(tmp_path).model_dump(mode='json')
    path = tmp_path / 'content.json'
    path.write_text(json.dumps({'article': 'fiction; fantasy; never a WD entity'}))
    other = {'source': 'wikipedia', 'raw_path': str(path),
             'raw_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    pinned = []
    assert source_genre_terms([wd, other], lambda *args: pinned.append(args)) == {'fantasy'}
    assert (str(path), other['raw_sha256']) in pinned


def test_only_exact_retained_non_wikidata_narrative_counts_as_content():
    evidence = content()
    work = WorkInput(work_id='w', title='Title', evidence=[evidence])
    source = {'source': 'wikipedia', 'evidence': [evidence.model_dump(mode='json')]}
    assert actual_content_evidence_ids(work, [source]) == {evidence.evidence_id}
    altered = work.model_copy(update={'evidence': [evidence.model_copy(update={'text': 'different'})]})
    assert not actual_content_evidence_ids(altered, [source])
    # An enriched WD record may carry separately licensed, guard-bound prose.
    assert actual_content_evidence_ids(work, [{**source, 'source': 'wikidata'}]) == {evidence.evidence_id}
    cc0 = evidence.model_copy(update={'rights_policy_id': 'wikidata-cc0'})
    assert not actual_content_evidence_ids(work.model_copy(update={'evidence': [cc0]}),
        [{'source': 'wikidata', 'evidence': [cc0.model_dump(mode='json')]}])
    descriptor = evidence.model_copy(update={'kind': 'description'})
    assert not actual_content_evidence_ids(work.model_copy(update={'evidence': [descriptor]}),
        [{'source': 'wikipedia', 'evidence': [descriptor.model_dump(mode='json')]}])


def test_unreviewed_non_genre_stays_held_and_review_preserves_native_values():
    evidence = content()
    work = WorkInput(work_id='w', title='Title', evidence=[evidence])
    item = label('theme.migration', 'theme', evidence.evidence_id, evidence.locator)
    result = native(work, [item]); before = deepcopy(result)
    assert admit_native_labels(work, result)['admitted_labels'] == []
    admitted = admit_native_labels(work, result, review_cleared_label_ids=['theme.migration'])
    assert admitted['admitted_labels'] == [item]
    assert admitted['admitted_labels'][0]['relevance'] is None
    assert result == before
    bad = deepcopy(item); bad['evidence'][0]['locator'] = '/other'
    with pytest.raises(ValueError, match='exact source citations'):
        admit_native_labels(work, native(work, [bad]), review_cleared_label_ids=['theme.migration'])


def test_review_selection_covers_all_non_genre_and_content_only_genres():
    works, results = [], []
    for i in range(20):
        evidence = Evidence(evidence_id=f'wd:{i}', kind='subject', text='fantasy',
            source_url='https://www.wikidata.org/wiki/Q1', rights_policy_id='wikidata-cc0',
            llm_input_allowed=True, locator='claims.P136;claims.P921')
        work = WorkInput(work_id=f'w{i}', title='Title', evidence=[evidence, content()])
        item = label(evidence_id=evidence.evidence_id)
        if i == 0:
            item = label('theme.migration', 'theme', content().evidence_id, content().locator)
        if i == 1:
            item = label('genre.fantasy', 'genre', content().evidence_id, content().locator)
        works.append(work); results.append(native(work, [item]))
    chosen = select_review_work_ids(works, results)
    assert {'w0', 'w1'} <= set(chosen)
    assert 5 <= len(chosen) < len(works)
    assert chosen == select_review_work_ids(works, list(reversed(results)))
    assert set(select_review_work_ids(works, results, full=True)) == {w.work_id for w in works}


def test_public_content_attribution_retains_license_revision_and_exact_id():
    evidence = content(); evidence.attribution['raw_path'] = 'private/source/path'
    work = WorkInput(work_id='w', title='Title', evidence=[evidence])
    rows = display_source_attributions(work, {evidence.evidence_id})
    assert rows[0]['evidence_ids'] == [evidence.evidence_id]
    assert rows[0]['revision'] == 1 and rows[0]['license'] == 'CC-BY-SA-4.0'
    assert rows[0]['quote_allowed'] is True
    assert 'raw_path' not in rows[0]['attribution']
    rows[0]['attribution']['author'] = 'changed'
    assert evidence.attribution['author'] == 'Contributors'


def test_selected_review_adapter_refuses_duplicate_scope_without_model():
    path = ROOT / 'scripts/review_scale_content_v1.py'
    spec = importlib.util.spec_from_file_location('selected_content_review', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with pytest.raises(ValueError, match='Distinct nonempty'):
        module.load_review(['w', 'w'])
    loaded = module.load_review(['w'])
    assert loaded.SELECTED_WORK_IDS == {'w'}


def test_new_verifier_rejects_legacy_or_tampered_manifest_before_subprocess(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps({'version': 'scale-native-classification-export-v1'}))
    monkeypatch.setattr('recommend_book.scale_content_classification_v1.subprocess.run',
                        lambda *a, **k: pytest.fail('Unknown or tampered version cannot launch replay'))
    with pytest.raises(ValueError, match='Unknown classification export version'):
        verify_catalogue_classification(path, hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match='manifest SHA'):
        verify_catalogue_classification(path, '0' * 64)


def test_content_gate_uses_actual_accepted_display_and_non_genre_counts():
    assert actual_gate(100, 95, 70, 1)['passed'] is True
    assert actual_gate(100, 94, 90, 1)['passed'] is False
    assert actual_gate(100, 100, 69, 1)['passed'] is False
    assert actual_gate(100, 100, 100, 0)['reasons'] == [
        'no_actual_content_cited_non_genre_adoption_return_to_content']
    assert actual_gate(100, 100, 100, 10, stopped=True)['passed'] is False
    with pytest.raises(ValueError, match='Invalid actual'):
        actual_gate(100, 80, 90, 10)


def test_compact_control_checks_process_counts_without_loading_old_corpus(tmp_path):
    def write(name, value):
        path = tmp_path / name; path.write_text(json.dumps(value))
        return {'path': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    export = write('export.json', {'version': 'scale-content-native-classification-export-v1',
        'gate': {'passed': True}, 'coordinator_sha256': 'coordinator',
        'artifact_sha256': {'intentionally-not-loaded-old-corpus': 'not-permission'}})
    run = write('run.json', {'selected': 100, 'run_id': 'run', 'stop_reasons': []})
    report = write('import.json', {'stage': 'actual_catalogue_import_complete',
        'actual_process': {'actual_exit_code': 0, 'process_exit_observed': True, 'pid': 123},
        'classification_manifest_sha256': export['sha256'],
        'result': {'failed': 0, 'raw_classified': 100, 'adopted': 90}})
    receipt = {'version': 'scale-content-window-control-v1', 'run_id': 'run',
        'coordinator_sha256': 'coordinator', 'control_artifacts': {'export': export, 'run': run, 'import_report': report},
        'gate': actual_gate(100, 100, 90, 50)}
    pin = write('control.json', receipt)
    assert verify_control_receipt(pin['path'], pin['sha256']) == receipt
    Path(run['path']).write_text(json.dumps({'selected': 99}))
    with pytest.raises(ValueError, match='process-control artifact changed'):
        verify_control_receipt(pin['path'], pin['sha256'])


def test_new_source_wrapper_separates_purposes_and_retains_content():
    path = ROOT / 'scripts/prepare_scale_content_sources_v1.py'
    spec = importlib.util.spec_from_file_location('content_source_prep', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    loaded = module.native_preparer(excluded_qids=set(), run_name='fresh', window_index=0)
    assert loaded.VERSION == 'scale-content-classification-source-v1'
    work = WorkInput(work_id='w', title='Title', evidence=[content()])
    assert loaded.scale_evidence_input(work).evidence == work.evidence
    assert set(loaded.REQUIRED_USAGES) == {'acquire', 'local_storage', 'external_llm', 'display'}


def test_content_sidecar_only_narrows_two_purposes_and_cannot_expand_grants(tmp_path):
    path = ROOT / 'scripts/prepare_scale_content_sources_v1.py'
    spec = importlib.util.spec_from_file_location('narrow_content_source_prep', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original = json.loads((ROOT / 'config/sources.json').read_bytes())
    target = tmp_path / 'narrowed.json'
    module.narrowed_registry(ROOT / 'config/sources.json', target)
    actual = json.loads(target.read_bytes()); expected = deepcopy(original)
    policy = next(p for p in expected['rights_policies'] if p['policy_id'] == 'wikipedia-screened-prose')
    policy['usages'].update(acquire='conditional', local_storage='conditional')
    assert actual == expected
    assert module.narrowed_registry(ROOT / 'config/sources.json', target) == target
    tampered = deepcopy(actual)
    next(p for p in tampered['rights_policies'] if p['policy_id'] == 'wikipedia-screened-prose')['usages']['external_llm'] = 'allowed'
    target.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match='sidecar differs'):
        module.narrowed_registry(ROOT / 'config/sources.json', target)
