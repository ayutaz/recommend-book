from copy import deepcopy
import hashlib
import json

import pytest

from recommend_book.light_novel_admission_v3 import (
    RULE, CONFLICT_TYPES, prepare_metadata, verify_metadata, check_duplicate_titles, metadata_artifact_pins,
    stored_title_closure, duplicate_title_key,
)
from recommend_book.light_novel_classification_v1 import _entity, build_series_metadata_evidence, freeze_runtime


def claim(qid, *, rank='normal'):
    return {'rank': rank, 'mainsnak': {'snaktype': 'value',
        'datavalue': {'value': {'id': qid}}}}


@pytest.fixture
def source(tmp_path):
    def write(name, data):
        p = tmp_path / name
        p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
        return {'raw_path': str(p), 'raw_sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
    license_pin = write('license.json', {'official': 'snapshot'})
    conditions = write('conditions.json', {'version': 'light-novel-source-conditions-v1',
        'snapshots': [{'url': 'https://www.wikidata.org/wiki/Wikidata:Licensing', **license_pin}],
        'wikidata': {'license': 'CC0', 'prose_excluded': True,
            'usages': dict.fromkeys(('local_storage', 'metadata_display', 'metadata_recommendation'), True)}})
    cp = {'conditions_path': conditions['raw_path'], 'conditions_sha256': conditions['raw_sha256']}
    authorities = {}
    for qid, parents in [('Q1667921', ['Q277759', 'Q15980953']), ('Q747381', ['Q8261'])]:
        authorities[qid] = {**write(qid + '.json', {'entities': {qid: {'id': qid,
            'claims': {'P279': [claim(p) for p in parents]}}}}), **cp}
    linked = write('linked.json', {'entities': {
        'Q2': {'id': 'Q2', 'labels': {'ja': {'value': '著者甲'}}},
        'Q747381': {'id': 'Q747381', 'labels': {'ja': {'value': 'ライトノベル'}}}}})
    entity = {'id': 'Q1', 'lastrevid': 7, 'labels': {'ja': {'value': '作品甲'}},
        'aliases': {'ja': [{'value': '別題甲'}]}, 'claims': {'P31': [claim('Q1667921')],
        'P136': [claim('Q747381')], 'P407': [claim('Q5287')], 'P50': [claim('Q2')]}}
    def build(mutate=None):
        e = deepcopy(entity)
        if mutate:
            mutate(e)
        pin = write('entity.json', {'entities': {'Q1': e}})
        provenance = {**pin, **cp, 'url': 'https://www.wikidata.org/wiki/Q1', 'entity_revision': 7,
            'linked_provenance': {'Q2': linked, 'Q747381': linked}}
        return prepare_metadata('Q1', provenance, authorities)
    return build, write, authorities


def test_complete_conjunction_replays_and_provides_metadata_evidence(source):
    row = source[0]()
    assert row['admission']['version'] == RULE
    assert verify_metadata(row)['id'] == _entity(row)['id'] == 'Q1'
    assert row['authors'] == [{'id': 'Q2', 'label': '著者甲'}]
    evidence, receipts = build_series_metadata_evidence(row)
    assert len(evidence) == len(receipts) == 1
    assert evidence[0].kind == 'bibliographic'


@pytest.mark.parametrize('mutation', ['generic_work', 'no_genre', 'no_language', 'multiple_languages',
    'edition', 'deprecated_unit', 'deprecated_genre', 'deprecated_language', 'nonvalue_unit'])
def test_missing_conjunct_never_admits(source, mutation):
    def change(e):
        claims = e['claims']
        if mutation == 'generic_work': claims['P31'] = [claim('Q7725634')]
        elif mutation == 'no_genre': claims.pop('P136')
        elif mutation == 'no_language': claims.pop('P407')
        elif mutation == 'multiple_languages': claims['P407'].append(claim('Q1860'))
        elif mutation == 'edition': claims['P629'] = [claim('Q3')]
        elif mutation.startswith('deprecated_'):
            prop = {'deprecated_unit': 'P31', 'deprecated_genre': 'P136', 'deprecated_language': 'P407'}[mutation]
            claims[prop][0]['rank'] = 'deprecated'
        else: claims['P31'][0]['mainsnak']['snaktype'] = 'somevalue'
    with pytest.raises(ValueError): source[0](change)


@pytest.mark.parametrize('conflict', sorted(CONFLICT_TYPES))
def test_each_existing_adaptation_conflict_remains_disqualifying(source, conflict):
    with pytest.raises(ValueError, match='scope conflicts'):
        source[0](lambda e: e['claims']['P31'].append(claim(conflict)))


@pytest.mark.parametrize('field', ['title', 'aliases', 'authors', 'genres', 'subjects', 'publishers',
    'types', 'countries', 'original_languages', 'publication_dates', 'sitelinks', 'relations'])
def test_complete_projection_cannot_be_forged(source, field):
    row = source[0]()
    row[field] = 'forged'
    with pytest.raises(ValueError, match='projection differs'): verify_metadata(row)


@pytest.mark.parametrize('mutation', ['rule', 'authority_hash', 'authority_identity', 'authority_claims',
    'authority_conditions', 'conditions_hash', 'linked_hash', 'missing_linked', 'licensing_hash'])
def test_all_authority_and_conditions_pins_replayed(source, mutation):
    row = source[0]();write = source[1]
    proof = row['series_admission_proof'];pin = proof['type_authorities']['Q1667921']
    if mutation == 'rule': proof['rule_version'] = 'unknown'
    elif mutation == 'authority_hash': pin['raw_sha256'] = '0' * 64
    elif mutation in ('authority_identity', 'authority_claims'):
        entity = {'id': 'Q3' if mutation == 'authority_identity' else 'Q1667921', 'claims': {}}
        pin.update(write('wrong-authority.json', {'entities': {'Q1667921': entity}}))
    elif mutation == 'authority_conditions': pin['conditions_sha256'] = '0' * 64
    elif mutation == 'conditions_hash': row['provenance']['conditions_sha256'] = '0' * 64
    elif mutation == 'linked_hash': row['provenance']['linked_provenance']['Q2']['raw_sha256'] = '0' * 64
    elif mutation == 'missing_linked': row['provenance']['linked_provenance'].pop('Q2')
    else:
        from pathlib import Path
        conditions = json.loads(Path(row['provenance']['conditions_path']).read_bytes())
        conditions['snapshots'][0]['raw_sha256'] = '0' * 64
        replacement = write('bad-conditions.json', conditions)
        row['provenance'].update(conditions_path=replacement['raw_path'], conditions_sha256=replacement['raw_sha256'])
    with pytest.raises(ValueError): _entity(row)


def test_absent_author_is_preserved_as_unknown(source):
    row = source[0](lambda e: e['claims'].pop('P50'))
    assert row['authors'] == [] and verify_metadata(row)


def test_v3_does_not_relax_existing_schema_admission(source):
    row = source[0]()
    for schema in ('light-novel-metadata-v1', 'light-novel-metadata-v2'):
        old = deepcopy(row);old['schema_version'] = schema
        with pytest.raises(ValueError, match='Raw series form'):
            _entity(old)


def test_duplicate_alias_blocks_ndl_story_and_groups_but_not_edition_or_self(source):
    row = source[0]()
    for kind in ('series', 'publisher_group'):
        with pytest.raises(ValueError, match='crosswalk hold'):
            check_duplicate_titles(row, [{'entry_id': 'lns:ndl:R1', 'entry_kind': kind,
                'title': '別 題甲', 'aliases': []}])
    check_duplicate_titles(row, [{'entry_id': 'lne:ndl:R2', 'entry_kind': 'edition', 'title': '作品甲'},
        {'entry_id': 'lns:wd:Q1', 'entry_kind': 'series', 'title': '作品甲'}])
    with pytest.raises(ValueError, match='finite limit'):
        check_duplicate_titles(row, [{'entry_id': 'lns:wd:Q2', 'entry_kind': 'series', 'title': '別作品'}], limit=0)
    with pytest.raises(ValueError, match='existing Wikidata work identity'):
        check_duplicate_titles(row, [{'entry_id': 'lnw:wd:Q1', 'entry_kind': 'work',
            'title': 'Entirely different saved title'}])


def test_structured_pin_closure_includes_types_genre_conditions_and_licensing(source):
    row = source[0]()
    pins = metadata_artifact_pins(row)
    expected = [row['provenance'], *row['provenance']['linked_provenance'].values(),
        *row['series_admission_proof']['type_authorities'].values()]
    from pathlib import Path
    for pin in expected:
        assert pins[str(Path(pin['raw_path']).resolve())] == pin['raw_sha256']
    assert row['provenance']['conditions_path'] in pins
    conditions = json.loads(Path(row['provenance']['conditions_path']).read_bytes())
    assert conditions['snapshots'][0]['raw_path'] in pins


def test_finite_preparer_blocks_saved_alias_and_duplicate_selection_before_output(source, tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('v3_prepare',
        Path(__file__).resolve().parents[1] / 'scripts/prepare_light_novel_metadata_v3.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    row = source[0]();write = source[1]
    selected = {'qid': row['id'], 'provenance': row['provenance'],
        'type_authorities': row['series_admission_proof']['type_authorities']}
    selection = write('selection.json', {'version': 'light-novel-v3-finite-selection-v1', 'series': [selected]})
    existing = write('existing.json', [{'entry_id': 'lns:ndl:R1', 'entry_kind': 'series',
        'title': '別 題甲', 'aliases': []}])
    output = tmp_path / 'prepared'
    with pytest.raises(ValueError, match='crosswalk hold'):
        module.prepare(selection['raw_path'], selection['raw_sha256'], existing['raw_path'],
            existing['raw_sha256'], output)
    assert not output.exists()
    selection = write('repeated.json', {'version': 'light-novel-v3-finite-selection-v1', 'series': [selected, selected]})
    with pytest.raises(ValueError, match='duplicated'):
        module.prepare(selection['raw_path'], selection['raw_sha256'], existing['raw_path'],
            existing['raw_sha256'], output)


def test_new_verifier_is_inside_frozen_runtime_and_hash_pinned(tmp_path):
    runtime = freeze_runtime(tmp_path / 'runtime')
    relative = 'src/recommend_book/light_novel_admission_v3.py'
    manifest = json.loads((runtime / 'light-novel-runtime-snapshot.json').read_bytes())
    assert manifest['files'][relative] == hashlib.sha256((runtime / relative).read_bytes()).hexdigest()
    assert 'verify_metadata' in (runtime / relative).read_text(encoding='utf8')


def test_titleless_ndl_anchor_closure_preserves_exact_main_forms_and_readings(source):
    row = source[0]()
    # Same stored shape as ordinary publisher/NDL series: no top-level title.
    witness = {'schema_version': 'light-novel-ndl-series-v1',
        'anchor_edition_record': {'source_record': {'title': '作品・甲 : 副題',
            'titles': [{'value': '作品・甲 : 副題'}, {'value': '別題・甲 = Alias'}],
            'extra': {'title_readings': ['サクヒン コウ']}}}}
    closure = stored_title_closure(witness)
    assert '作品・甲' in closure and '別題・甲' in closure and 'サクヒン コウ' in closure
    assert '作品・甲 : 副題' in closure
    assert duplicate_title_key('さくひん・こう') == duplicate_title_key('サクヒン コウ')
    with pytest.raises(ValueError, match='crosswalk hold'):
        check_duplicate_titles(row, [{'entry_id': 'lns:ndl:R1', 'entry_kind': 'series', 'record': witness}])
    # Conservative closure never invents a series by deleting the volume.
    only_volume = {'anchor_edition_record': {'source_record': {'titles': [{'value': '作品甲. 1'}]}}}
    assert stored_title_closure(only_volume) == ['作品甲. 1']


def test_actual_wortenia_saved_ndl_title_closure_blocks_duplicate_wd_series(source):
    # Exact bibliographic title fields retained by existing NDL series
    # R100000002-I026722300; no dependency on private DB or raw files.
    witness = {'schema_version': 'light-novel-ndl-series-v1',
        'anchor_edition_record': {'source_record': {'titles': [
            {'kind': 'preferred', 'language': None,
             'value': 'ウォルテニア戦記 = RECORD OF WORTENIA WAR. 1'},
            {'kind': 'preferred', 'language': None, 'value': 'ウォルテニア戦記'}],
            'extra': {'title_readings': ['ウォルテニア センキ']}}}}
    row = source[0](lambda e: e['labels']['ja'].update(value='ウォルテニア戦記'))
    with pytest.raises(ValueError, match='R100000002-I026722300'):
        check_duplicate_titles(row, [{'entry_id': 'lns:ndl:R100000002-I026722300',
            'entry_kind': 'series', 'record': witness}])


@pytest.mark.parametrize('witness_kind', ['titleless_ndl', 'same_qid_work'])
def test_catalogue_ingest_rejects_saved_identity_witness_before_new_v3_insert(source, tmp_path, witness_kind):
    from recommend_book.light_novel_catalogue_v1 import LightNovelCatalogue
    row = source[0]()
    cat = LightNovelCatalogue(tmp_path / 'catalogue.sqlite')
    if witness_kind == 'titleless_ndl':
        identity, kind = 'lns:ndl:R100000002-I1', 'series'
        witness = {'schema_version': 'light-novel-ndl-series-v1',
            'anchor_edition_record': {'source_record': {'titles': [{'value': '作品・甲 : 副題'}],
                'extra': {'title_readings': ['サクヒン コウ']}}}}
    else:
        identity, kind = 'lnw:wd:Q1', 'work'
        witness = {'title': 'Distinct saved title', 'aliases': []}
    encoded = json.dumps(witness, ensure_ascii=False)
    with cat._connect() as db:
        db.execute('INSERT INTO entries VALUES(?,?,?,?)',
            (identity, kind, encoded, hashlib.sha256(encoded.encode()).hexdigest()))
    result = cat.ingest([row])
    assert result['inserted'] == 0 and result['failed'] == 1
    assert 'crosswalk hold' in result['failures'][0]['reason']
    with cat._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 1
        assert db.execute('SELECT record_json FROM entries WHERE entry_id=?', (identity,)).fetchone()[0] == encoded
