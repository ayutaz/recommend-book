from __future__ import annotations
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

SPEC = importlib.util.spec_from_file_location('lncollect', Path(__file__).parents[1] / 'scripts/collect_light_novel_metadata_v1.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def entity(qid='Q5357270', types=('Q104213567',), langs=('Q5287',)):
    def statement(prop, value):
        return {'rank':'normal','mainsnak':{'property':prop,'snaktype':'value','datavalue':{'value':{'id':value,'entity-type':'item'}}}}
    return {'id':qid,'lastrevid':123,'labels':{'ja':{'value':'魔法科高校の劣等生'}},'claims':{'P31':[statement('P31',x) for x in types], 'P407':[statement('P407',x) for x in langs]}}


def test_query_includes_series_and_single_works_without_language_selection_bias():
    q = M.discovery_query(limit=100, offset=200)
    assert 'Q104213567' in q and 'Q747381' in q
    assert 'wdt:P7937 wd:Q104213567' in q
    assert 'P407' not in q
    assert 'LIMIT 100 OFFSET 200' in q


def test_series_is_distinct_from_single_volume_and_adaptation():
    rec=M.normalize_entity(entity(), {}, {'raw_path':'a','raw_sha256':'b'}, 'conditions.json')
    assert rec['kind']=='series' and rec['admission']['accepted']
    assert rec['id']=='Q5357270'
    assert M.normalize_entity(entity(types=('Q747381',)), {}, {}, '')['kind']=='work'
    assert not M.normalize_entity(entity(types=('Q104213567','Q5398426')), {}, {}, '')['admission']['accepted']
    assert M.normalize_entity(entity(types=('Q104213567','Q7725310')), {}, {}, '')['admission']['accepted']
    assert not M.normalize_entity(entity(types=('Q747381','Q136747113')), {}, {}, '')['admission']['accepted']


def test_unknown_language_is_held_not_japanese_by_label():
    rec=M.normalize_entity(entity(langs=()), {}, {}, '')
    assert not rec['admission']['accepted']
    assert 'japanese_original_language_unproved' in rec['admission']['reasons']


def test_deprecated_type_is_not_admitted():
    e=entity(); e['claims']['P31'][0]['rank']='deprecated'
    assert not M.normalize_entity(e, {}, {}, '')['admission']['accepted']


def test_request_cache_reuses_success_and_holds_failed_request(tmp_path):
    class Fetcher:
        def __init__(self): self.calls=0
        def get(self,*args,**kwargs):
            self.calls+=1
            return SimpleNamespace(status='failed', artifact=None,error='HTTP 429', status_code=429)
    f=Fetcher(); store=M.CheckpointFetcher(tmp_path, f)
    assert store.get('https://example.test',source='test',params={'q':'x'}) is None
    assert store.get('https://example.test',source='test',params={'q':'x'}) is None
    assert f.calls==1
    assert len(list((tmp_path/'requests').glob('*.json')))==1

def test_ndl_partitions_require_facet_sum_and_never_invent_missing_years():
    assert M.ndl_year_partitions(600, {'2020':300,'2021':300}) == [('2020',300),('2021',300)]
    assert M.ndl_year_partitions(600, {'2020':300}) is None
    assert M.ndl_year_partitions(600, {'unknown':600}) is None


def test_ndl_literal_cannot_escape_provider_query():
    import pytest
    with pytest.raises(ValueError): M.ndl_label_query('x" OR dpid="other')
    assert M.ndl_label_query('電撃文庫','2024') == 'anywhere="電撃文庫" AND from="2024-01-01" AND until="2024-12-31" AND dpid="iss-ndl-opac"'

def test_description_resolution_requires_explicit_japanese_literary_origin():
    assert M.description_origin('Japanese light novel series')
    assert M.description_origin('Japanese fantasy light novel series written by X')
    assert not M.description_origin('anime series based on a Japanese light novel')
    assert not M.description_origin('Chinese light novel series translated into Japanese')
    assert not M.description_origin('novel series by a Japanese author')


def test_ndl_namespaced_facets_partition_and_cache_are_auditable(tmp_path, monkeypatch):
    import hashlib
    ns = 'http://www.loc.gov/zing/srw/'
    def payload(total, ids, facets=''):
        records = ''.join(f'''<record><recordData><rdf:RDF
            xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
            xmlns:dcndl="http://ndl.go.jp/dcndl/terms/"
            xmlns:dcterms="http://purl.org/dc/terms/">
            <dcndl:BibResource rdf:about="https://ndlsearch.ndl.go.jp/books/R100000002-I{i}#material">
            <dcterms:title>Fixture {i}</dcterms:title>
            <dcterms:language>jpn</dcterms:language>
            </dcndl:BibResource></rdf:RDF></recordData></record>''' for i in ids)
        return f'<searchRetrieveResponse xmlns="{ns}"><numberOfRecords>{total}</numberOfRecords>{facets}<records>{records}</records></searchRetrieveResponse>'.encode()
    class Fetcher:
        calls = 0
        def get(self, url, *, source, params):
            self.calls += 1
            assert params['query'].endswith('dpid="iss-ndl-opac"')
            if '2020-01-01' in params['query']:
                raw = payload(250, range(1, 251))
            elif '2021-01-01' in params['query']:
                raw = payload(251, range(251, 502))
            else:
                raw = payload(501, range(1, 501), '<extraResponseData><lst name="ISSUED_DATE"><int name="2020">250</int><int name="2021">251</int></lst></extraResponseData>')
            path = tmp_path / f'fixture-{self.calls}.xml'
            path.write_bytes(raw)
            return raw, {'raw_path': str(path), 'raw_sha256': hashlib.sha256(raw).hexdigest(), 'completed_at': '2026-01-01T00:00:00+00:00', 'url': url}
    fetcher = Fetcher()
    monkeypatch.setattr(M, 'make_fetcher', lambda root: fetcher)
    condition_path = tmp_path / 'conditions.json'
    condition_path.write_text('{}')
    monkeypatch.setattr(M, 'conditions', lambda root, fetcher: condition_path)
    roster = tmp_path / 'roster.json'
    roster.write_text(json.dumps([{'id': 'label:fixture', 'label': 'Fixture', 'query_terms': ['Fixture'], 'scope': 'core'}]))
    report = M.collect_ndl(tmp_path / 'acquisition', roster=roster)
    assert report == {'labels': 1, 'records': 501, 'complete': True}
    assert fetcher.calls == 3
    M.collect_ndl(tmp_path / 'acquisition', roster=roster)
    assert fetcher.calls == 3
    index = M.load(tmp_path / 'acquisition' / 'ndl' / 'index.json')
    term = index['labels']['label:fixture']['terms']['Fixture']
    assert term['root']['year_facets'] == {'2020': 250, '2021': 251}
    assert len(term['partitions']) == 2
    assert all(p['receipt']['raw_sha256'] for p in term['partitions'])
