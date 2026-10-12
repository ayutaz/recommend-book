import importlib.util
from pathlib import Path

import pytest


def module():
    spec = importlib.util.spec_from_file_location('resolve_prose_wikidata', Path(__file__).parents[1] / 'scripts/resolve_prose_wikidata.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def claim(value):
    return {'rank': 'normal', 'mainsnak': {'datavalue': {'value': value}}}


def entity(qid, label, **props):
    return {'id': qid, 'labels': {'en': {'value': label}}, 'claims': {p: [claim(v) for v in values] for p, values in props.items()}}


def fixture():
    entities = {
        'Q1': entity('Q1', 'Example', P31=[{'id': 'Q8261'}], P50=[{'id': 'Q3'}], P407=[{'id': 'Q1860'}], P577=[{'time': '+1900-01-01T00:00:00Z', 'precision': 9}]),
        'Q2': entity('Q2', 'Example (1901)', P31=[{'id': 'Q3331189'}], P629=[{'id': 'Q1'}], P577=[{'time': '+1901-01-01T00:00:00Z', 'precision': 9}]),
        'Q3': entity('Q3', 'A Writer'), 'Q8261': entity('Q8261', 'novel'),
        'Q3331189': entity('Q3331189', 'version, edition or translation'), 'Q1860': entity('Q1860', 'English'),
    }
    entities['Q2']['sitelinks'] = {'enwikisource': {'title': 'Example (1901)'}}
    candidate = {'candidate_id': 'ws:en:1', 'title': 'Example', 'author': 'A Writer', 'first_publication_year': 1900, 'edition_year': 1901, 'edition_wikidata_candidate': 'Q2', 'wikisource_title': 'Example (1901)'}
    return candidate, entities


def test_verified_machine_identity_is_not_human_identity():
    c, es = fixture()
    result = module().resolve_candidate(c, es)
    assert result['status'] == 'matched'
    assert result['work_qid'] == 'Q1'
    assert result['entity_role'] == 'edition_or_version'
    assert result['human_verified'] is False
    assert result['original_language_qids'] == ['Q1860']


def test_p629_alone_never_proves_published_edition():
    c, es = fixture()
    es['Q2']['claims'].pop('P31')
    result = module().resolve_candidate(c, es)
    assert result['status'] == 'uncertain'
    assert result['entity_role'] == 'unknown'


@pytest.mark.parametrize('change', ['author', 'year', 'language', 'sitelink', 'missing_work', 'multiple_works'])
def test_conflict_and_missing_metadata_stay_uncertain(change):
    c, es = fixture()
    if change == 'author': c['author'] = 'Another Writer'
    if change == 'year': c['edition_year'] = 1902
    if change == 'language': es['Q1']['claims'].pop('P407')
    if change == 'sitelink': es['Q2']['sitelinks']['enwikisource']['title'] = 'Other'
    if change == 'missing_work': es.pop('Q1')
    if change == 'multiple_works': es['Q2']['claims']['P629'].append(claim({'id': 'Q9'}))
    assert module().resolve_candidate(c, es)['status'] == 'uncertain'


def test_entity_api_is_fixed_bounded_and_maxlag_protected():
    m = module()
    assert m.API_URL == 'https://www.wikidata.org/w/api.php'
    with pytest.raises(ValueError): m.request_params(ids=['Q1'] * 51)
    assert m.request_params(ids=['Q1'])['maxlag'] == 5


def test_revised_version_is_preserved():
    c, es = fixture()
    es['Q3331189']['labels']['en']['value'] = 'revised version'
    assert module().resolve_candidate(c, es)['entity_role'] == 'revised_version'


def test_saved_author_qid_can_verify_pen_name_and_english_variant_is_retained():
    c, es = fixture()
    c['author'] = 'Full legal name absent from Wikidata aliases'
    c['author_wikidata_candidate'] = 'Q3'
    es['Q1']['claims']['P407'] = [claim({'id': 'Q7979'})]
    es['Q7979'] = entity('Q7979', 'British English')
    result = module().resolve_candidate(c, es)
    assert result['status'] == 'matched'
    assert result['original_language_qids'] == ['Q7979']
    assert result['author_comparison_basis'] == 'saved Wikisource Author page wikibase_item'
