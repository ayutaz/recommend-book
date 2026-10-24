from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('lncoverage', Path(__file__).parents[1] / 'scripts/audit_light_novel_coverage_v1.py')
M = importlib.util.module_from_spec(SPEC)


def load_module():
    SPEC.loader.exec_module(M)


def label(key='label:a'):
    return {'id': key, 'label': key, 'publisher': 'Publisher', 'scope': 'core', 'is_story_series': False}


def partition(ids=('NDL:1', 'NDL:2'), expected=2):
    return {'id': 'all-years', 'expected_total': expected, 'pages': [{'start': 1, 'record_ids': list(ids), 'raw_sha256': 'a' * 64}], 'terminal': True}


def query(parts=None):
    return {'label_id': 'label:a', 'term': 'label:a', 'partitioning_complete': True, 'partitions': parts or [partition()]}


def test_missing_page_and_duplicate_records_are_not_complete():
    load_module()
    p = partition(expected=4)
    p['pages'].append({'start': 4, 'record_ids': ['NDL:2', 'NDL:3'], 'raw_sha256': 'b' * 64})
    report = M.audit([label()], [query([p])])
    assert report['labels'][0]['retrieval_status'] == 'incomplete'
    assert 'page_gap_or_overlap' in report['labels'][0]['issues']
    assert 'unique_record_count_mismatch' in report['labels'][0]['issues']


def test_complete_label_does_not_prove_national_or_series_coverage():
    load_module()
    report = M.audit([label()], [query()])
    assert report['retrieval_complete_labels'] == 1
    assert report['retrieved_unique_edition_ids'] == 2
    assert report['japanese_national_coverage_proved'] is False
    assert report['series_coverage'] is None


def test_unqueried_female_and_historical_labels_stay_visible():
    load_module()
    rows = [label(), {**label('label:female'), 'scope': 'web_book'}, {**label('label:old'), 'scope': 'historical'}]
    report = M.audit(rows, [query()])
    assert report['retrieval_complete_labels'] == 1
    assert report['unqueried_labels'] == ['label:female', 'label:old']
    assert report['scope_counts']['historical']['unqueried'] == 1


def test_coverage_uses_evidence_backed_identity_map_not_title_equality():
    load_module()
    reference = [{'reference_id': 'publisher:series1', 'kind': 'series', 'source_url': 'https://publisher.test/series1'}, {'reference_id': 'publisher:series2', 'kind': 'series', 'source_url': 'https://publisher.test/series2'}]
    mappings = [{'reference_id': 'publisher:series1', 'catalogue_id': 'Q1', 'kind': 'series', 'relation_evidence': ['https://publisher.test/series1'], 'verified': True}, {'reference_id': 'publisher:series2', 'catalogue_id': 'Q2', 'kind': 'series', 'relation_evidence': [], 'verified': True}]
    report = M.audit([label()], [query()], reference, mappings)
    assert report['series_coverage']['reference_series'] == 2
    assert report['series_coverage']['matched_series'] == 1
    assert report['series_coverage']['unmatched_reference_ids'] == ['publisher:series2']
    assert report['japanese_national_coverage_proved'] is False


def test_unfinished_partition_tree_is_not_complete_even_with_full_leaf():
    load_module()
    q = query()
    q['partitioning_complete'] = False
    report = M.audit([label()], [q])
    assert report['retrieval_complete_labels'] == 0
    assert 'partitioning_incomplete' in report['labels'][0]['issues']


def test_empty_results_require_proved_terminal_query():
    load_module()
    p = partition(ids=(), expected=0)
    report = M.audit([label()], [query([p])])
    assert report['retrieval_complete_labels'] == 1
    p['terminal'] = False
    assert M.audit([label()], [query([p])])['retrieval_complete_labels'] == 0


def test_ndl_adapter_replays_raw_ids_and_rejects_hash_or_index_tampering(tmp_path):
    load_module()
    raw = b'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>1</numberOfRecords><records><record><recordData><RDF xmlns="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><BibAdminResource xmlns="http://ndl.go.jp/dcndl/terms/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" rdf:about="https://ndlsearch.ndl.go.jp/books/NDL:1" /></RDF></recordData></record></records></searchRetrieveResponse>'
    path = tmp_path / 'raw.xml'
    path.write_bytes(raw)
    page = {'expected_total': 1, 'returned_record_ids': ['NDL:1'], 'requested_position': 1, 'complete': True, 'receipt': {'raw_path': str(path), 'raw_sha256': hashlib.sha256(raw).hexdigest()}}
    index = {'labels': {'label:a': {'terms': {'label:a': {'root': page, 'partitions': [], 'complete': True}}}}}
    queries = M.adapt_ndl_index(index)
    assert M.audit([label()], queries)['retrieval_complete_labels'] == 1
    page['returned_record_ids'] = ['NDL:forged']
    assert M.audit([label()], M.adapt_ndl_index(index))['retrieval_complete_labels'] == 0
    path.write_bytes(raw + b' ')
    assert M.audit([label()], M.adapt_ndl_index(index))['retrieval_complete_labels'] == 0


def test_ndl_adapter_rejects_missing_partition_year(tmp_path):
    load_module()
    # The parent's total cannot prove coverage when its year facet is not exhausted.
    root = {'expected_total': 600, 'year_facets': {'2020': 300, '2021': 300}}
    index = {'labels': {'label:a': {'terms': {'label:a': {'root': root, 'partitions': [], 'complete': False}}}}}
    adapted = M.adapt_ndl_index(index)
    assert adapted[0]['partitioning_complete'] is False


def test_duplicate_identity_across_year_partitions_is_incomplete():
    load_module()
    first = partition(ids=('NDL:1',), expected=1)
    first['id'] = '2020'
    second = partition(ids=('NDL:1',), expected=1)
    second['id'] = '2021'
    report = M.audit([label()], [query([first, second])])
    assert report['retrieval_complete_labels'] == 0
    assert 'overlapping_partitions' in report['labels'][0]['issues']


def test_ndl_monthly_split_requires_all_months_and_replayed_annual_parent(tmp_path):
    load_module()
    def page(name, total, ids, facets=None):
        extra = '<extraResponseData><facets><lst name="ISSUED_DATE">' + ''.join(f'<int name="{key}">{value}</int>' for key,value in (facets or {}).items()) + '</lst></facets></extraResponseData>'
        body = ''.join(f'<record><recordData><dcndl:BibAdminResource xmlns:dcndl="http://ndl.go.jp/dcndl/terms/" xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" rdf:about="https://ndlsearch.ndl.go.jp/books/{value}"/></recordData></record>' for value in ids)
        raw = f'<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/"><numberOfRecords>{total}</numberOfRecords>{extra}<records>{body}</records></searchRetrieveResponse>'.encode()
        path=tmp_path / f'{name}.xml';path.write_bytes(raw)
        return {'partition':name,'expected_total':total,'returned_record_ids':ids,'requested_position':1,
                'complete':len(ids)==total,'receipt':{'raw_path':str(path),'raw_sha256':hashlib.sha256(raw).hexdigest()},'year_facets':facets or {}}
    ids=[f'NDL:{n}' for n in range(600)]
    root=page('all',600,ids[:500],{'2020':600})
    annual=page('2020',600,ids[:500])
    months=[page(f'2020-{month:02}',50,ids[(month-1)*50:month*50]) for month in range(1,13)]
    state={'root':root,'partitions':months,'year_parent_pages':[annual],'complete':True}
    index={'labels':{'label:a':{'terms':{'label:a':state}}}}
    assert M.audit([label()],M.adapt_ndl_index(index))['retrieval_complete_labels']==1
    state['partitions']=months[:-1]
    assert M.audit([label()],M.adapt_ndl_index(index))['retrieval_complete_labels']==0
