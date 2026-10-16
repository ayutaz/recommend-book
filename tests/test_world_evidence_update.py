"""Existing work evidence additions preserve identity and prior admissions."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

spec = importlib.util.spec_from_file_location('evidence_update', Path(__file__).resolve().parents[1]/'scripts/build_world_evidence_update_v1.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def baseline(identity='existing'):
    return {'work_id': identity, 'title': 'Existing title', 'authors': ['Author'],
            'display_title_aliases': [], 'read_status': 'unknown', 'content_labels': ['genre.romance'],
            'score': None, 'explanations': [], 'score_status': 'unsupported'}


def supplement():
    return {'work_id': 'supplement', 'features': {'theme.family': .75, 'genre.romance': 1},
            'feature_provenance': {'theme.family': [{'scope': 'work', 'evidence_id': 'se:1'}]}}


def test_combination_uses_binary_union_and_keeps_both_admissions():
    old, added = baseline(), supplement()
    combined = update.combine_candidate(old, added, old_id='existing', supplement_id='supplement')
    assert combined['work_id'] == 'existing' and combined['title'] == old['title']
    assert combined['features'] == {'genre.romance': 1.0, 'theme.family': 1.0}
    assert combined['feature_provenance']['theme.family'] == added['feature_provenance']['theme.family']
    assert combined['feature_provenance']['genre.romance'][0]['admission'] == 'frozen-prior-public-delivery'
    assert old == baseline() and added == supplement()


@pytest.mark.parametrize('review', ['held', 'uncertain'])
def test_unaccepted_supplement_never_erases_prior_admissions(review):
    combined = update.combine_candidate(baseline(), supplement(), old_id='existing',
                                        supplement_id='supplement', review_status=review)
    assert combined['features'] == {'genre.romance': 1.0}
    assert 'theme.family' not in combined['feature_provenance']


def test_batch_replaces_only_explicit_existing_identities_and_refuses_duplicates():
    rows = [baseline('first'), baseline('second'), baseline('third')]
    original = {'ranking': rows, 'audit': {}}
    changed = dict(rows[0], content_labels=['theme.family'])
    result = update.replace_candidates(original, [changed])
    by_id = {row['work_id']: row for row in result['ranking']}
    assert len(by_id) == 3 and by_id['second'] == rows[1] and by_id['third'] == rows[2]
    assert result['audit']['prior_rows_unchanged_n'] == 2
    with pytest.raises(ValueError, match='Exactly one'):
        update.replace_candidates(original, [baseline('missing')])
    with pytest.raises(ValueError, match='Duplicate replacement'):
        update.replace_candidates(original, [changed, changed])


@pytest.mark.parametrize('url', ['http://standardebooks.org/ebooks/a', 'https://standardebooks.org.evil.test/',
    'https://user:pass@standardebooks.org/ebooks/a', 'file:///C:/private'])
def test_credit_links_reject_untrusted_urls(url):
    with pytest.raises(ValueError, match='Unapproved'):
        update.public_url(url)


def authority_database(tmp_path):
    database = tmp_path/'identity.sqlite'
    raw = tmp_path/'authority.json'
    raw.write_text('{}', encoding='utf-8')
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE external_ids(source,value,scope,entity_id,source_version_id);
            CREATE TABLE source_records(version_id,raw_path,raw_sha256);
            CREATE TABLE source_heads(source,source_id,version_id);
            CREATE TABLE autonomous_work_eligibility(work_id,eligible,reason);
            CREATE TABLE next_book_inventory(work_id,eligible);
        ''')
        connection.execute('INSERT INTO external_ids VALUES(?,?,?,?,?)',
                           ('wikidata','Q1','work','right-destination','current'))
        connection.execute('INSERT INTO external_ids VALUES(?,?,?,?,?)',
                           ('wikidata','Q2','work','wrong-existing-destination','other'))
        connection.execute('INSERT INTO source_records VALUES(?,?,?)', ('current',str(raw),update.sha(raw)))
        connection.execute('INSERT INTO source_heads VALUES(?,?,?)', ('wikidata','Q1','current'))
        connection.execute('INSERT INTO autonomous_work_eligibility VALUES(?,?,?)', ('right-destination',1,'verified work scope'))
        connection.execute('INSERT INTO next_book_inventory VALUES(?,?)', ('right-destination',1))
    return database, {'target_raw_path': str(raw), 'target_raw_sha256': update.sha(raw)}


def test_destination_authority_receipt_binds_existing_work_and_current_raw_head(tmp_path):
    database, bridge = authority_database(tmp_path)
    mapping = {'delivery_work_id': 'right-destination', 'wikidata_qid': 'Q1'}
    proof = update.destination_authority_proof(database, mapping, bridge)
    assert proof['identity_rows'][0]['entity_id'] == 'right-destination'
    assert proof['current_head']['raw_sha256'] == bridge['target_raw_sha256']
    with pytest.raises(ValueError, match='Destination lacks the exact work-scoped'):
        update.destination_authority_proof(database, dict(mapping, delivery_work_id='wrong-existing-destination'), bridge)
    with pytest.raises(ValueError, match='Destination lacks the exact work-scoped'):
        update.destination_authority_proof(database, dict(mapping, wikidata_qid='Q2'), bridge)
    with pytest.raises(ValueError, match='current source head differs'):
        update.destination_authority_proof(database, mapping, dict(bridge, target_raw_sha256='0'*64))


def test_edition_scoped_identity_cannot_authorize_existing_work_mapping(tmp_path):
    database, bridge = authority_database(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE external_ids SET scope='edition'")
    with pytest.raises(ValueError, match='Destination lacks the exact work-scoped'):
        update.destination_authority_proof(database,
            {'delivery_work_id': 'right-destination', 'wikidata_qid': 'Q1'}, bridge)


def novel_entity():
    return {'labels': {'en': {'value': 'New novel'}}, 'aliases': {},
            'claims': {'P7937': [{'mainsnak': {'datavalue': {'value': {'id':'Q8261'}}}}]}}


def test_new_public_candidate_requires_novel_eligibility_and_no_existing_identity(tmp_path):
    database, _ = authority_database(tmp_path)
    mapping = {'delivery_work_id': 'right-destination', 'wikidata_qid': 'Q1'}
    entity = novel_entity()
    proof = update.new_candidate_proof(database, mapping, entity, [baseline()])
    assert proof['existing_display_n'] == 1 and not proof['authority_duplicate_found']
    for existing_id in ('right-destination', 'declared-subclass:wd:Q1'):
        with pytest.raises(ValueError, match='already has a public delivery identity'):
            update.new_candidate_proof(database, mapping, entity, [baseline(existing_id)])
    with pytest.raises(ValueError, match='raw authority title/alias already displayed'):
        update.new_candidate_proof(database, mapping,
            dict(entity, aliases={'en':[{'value':'Existing title'}]}), [baseline()])
    with pytest.raises(ValueError, match='lacks direct novel type'):
        update.new_candidate_proof(database, mapping, dict(entity, claims={}), [baseline()])
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE next_book_inventory SET eligible=0')
    with pytest.raises(ValueError, match='lacks existing work/novel eligibility'):
        update.new_candidate_proof(database, mapping, entity, [baseline()])


def test_append_keeps_old_rows_and_refuses_repeated_public_identity():
    old = baseline()
    delivery = {'ranking':[old], 'audit':{}}
    new = baseline('new-public')
    result = update.append_candidates(delivery, [new])
    assert len(result['ranking']) == 2
    assert next(row for row in result['ranking'] if row['work_id']=='existing') == old
    assert delivery == {'ranking':[old], 'audit':{}}
    assert result['audit']['new_public_candidates_n'] == 1
    with pytest.raises(ValueError, match='duplicates a public identity'):
        update.append_candidates(delivery, [old])
