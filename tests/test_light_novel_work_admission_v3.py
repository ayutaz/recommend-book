from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_light_novel_admission_v3 import source, claim
from recommend_book.light_novel_work_admission_v3 import (
    prepare_metadata, verify_metadata, check_duplicate_titles, SCHEMA,
)
from recommend_book.light_novel_classification_v1 import _entity


@pytest.fixture
def work_source(source):
    series = source[0]();write = source[1]
    entity = json.loads(Path(series['provenance']['raw_path']).read_bytes())['entities']['Q1']
    entity['claims']['P31'] = [claim('Q8261')]
    authorities = deepcopy(series['series_admission_proof']['type_authorities'])
    cp = {k: series['provenance'][k] for k in ('conditions_path', 'conditions_sha256')}
    authorities.pop('Q1667921')
    authorities['Q8261'] = {**write('novel.json', {'entities': {'Q8261': {'id': 'Q8261',
        'claims': {'P279': [claim(q) for q in ('Q87482979', 'Q3328821', 'Q676', 'Q116518880')]}}}}), **cp}
    def build(mutate=None):
        e = deepcopy(entity)
        if mutate:mutate(e)
        provenance = {**series['provenance'], **write('work.json', {'entities': {'Q1': e}})}
        return prepare_metadata('Q1', provenance, authorities)
    return build


def test_explicit_novel_genre_is_a_work_and_cannot_enter_series_pipeline(work_source):
    row = work_source()
    assert row['schema_version'] == SCHEMA and row['kind'] == 'work'
    assert verify_metadata(row)['id'] == 'Q1'
    with pytest.raises(ValueError, match='Only admitted Japanese novel series'):_entity(row)


@pytest.mark.parametrize('case', ['generic', 'genre_only_type', 'no_genre', 'wrong_language',
    'multiple_languages', 'edition', 'series', 'anime', 'manga', 'deprecated_novel'])
def test_work_conjunction_never_guesses_unit_or_language(work_source, case):
    def mutate(e):
        c = e['claims']
        if case == 'generic':c['P31'] = [claim('Q7725634')]
        elif case == 'genre_only_type':c['P31'] = [claim('Q747381')]
        elif case == 'no_genre':c.pop('P136')
        elif case == 'wrong_language':c['P407'] = [claim('Q1860')]
        elif case == 'multiple_languages':c['P407'].append(claim('Q1860'))
        elif case == 'edition':c['P629'] = [claim('Q9')]
        elif case in ('series', 'anime', 'manga'):
            c['P31'].append(claim({'series': 'Q1667921', 'anime': 'Q21198342', 'manga': 'Q8274'}[case]))
        else:c['P31'][0]['rank'] = 'deprecated'
    with pytest.raises(ValueError):work_source(mutate)


def test_cycle_members_remain_works_and_preserve_raw_relations(work_source):
    row = work_source(lambda e: e['claims'].update(P179=[claim('Q99')], P155=[claim('Q8')]))
    assert row['kind'] == 'work' and row['relations']['P179'] == ['Q99']
    assert row['relations']['P155'] == ['Q8']
    verify_metadata(row)


@pytest.mark.parametrize('mutation', ['authority_hash', 'linked_hash', 'projection', 'kind', 'rule'])
def test_source_pins_and_projection_are_replayed(work_source, mutation):
    row = work_source()
    if mutation == 'authority_hash':row['work_admission_proof']['type_authorities']['Q8261']['raw_sha256'] = '0'*64
    elif mutation == 'linked_hash':row['provenance']['linked_provenance']['Q2']['raw_sha256'] = '0'*64
    elif mutation == 'projection':row['authors'] = []
    elif mutation == 'kind':row['kind'] = 'series'
    else:row['work_admission_proof']['rule_version'] = 'unknown'
    with pytest.raises(ValueError):verify_metadata(row)


def test_duplicate_work_and_titleless_ndl_series_hold_without_merging(work_source):
    row = work_source()
    for existing in [
        {'entry_id': 'lns:wd:Q1', 'entry_kind': 'series', 'title': 'Different'},
        {'entry_id': 'lnw:wd:Q2', 'entry_kind': 'work', 'title': '作品・甲'},
        {'entry_id': 'lns:ndl:R1', 'entry_kind': 'series', 'record': {
            'anchor_edition_record': {'source_record': {'titles': [{'value': '作品甲 : 副題'}]}}}},
    ]:
        with pytest.raises(ValueError, match='crosswalk hold'):check_duplicate_titles(row, [existing])
    check_duplicate_titles(row, [{'entry_id': 'lnw:wd:Q1', 'entry_kind': 'work', 'title': row['title']}])
