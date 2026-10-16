import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from recommend_book.database import DatasetDB
from recommend_book.ingestion.standardebooks import (
    ABOUT_URL, MANUAL_URL, POLICY_ID, editorial_work_record, ingest_editorial_description, parse_opf,
)
from recommend_book.models import Contributor, Identifier, SourceRecord, Title, WorkInput
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding


URL = 'https://raw.githubusercontent.com/standardebooks/author_book/master/src/epub/content.opf'
OPF = '''<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">https://standardebooks.org/ebooks/author/book</dc:identifier>
<dc:title id="title">Book</dc:title><dc:creator id="author">Author</dc:creator>
<meta refines="#author" property="role">aut</meta>
<dc:language>en-GB</dc:language><dc:publisher>Standard Ebooks</dc:publisher>
<dc:rights>Contributors dedicate their contributions via CC0 1.0 https://creativecommons.org/publicdomain/zero/1.0/</dc:rights>
<dc:description>&lt;p&gt;A young traveler returns home and confronts family expectations.&lt;/p&gt;</dc:description>
<dc:subject>Externally sourced subject</dc:subject>
<link rel="schema:sameAs" href="https://en.wikipedia.org/wiki/Book"/>
<link refines="#author" rel="schema:sameAs" href="https://en.wikipedia.org/wiki/Author"/>
</metadata><manifest><item href="text/body.xhtml"/></manifest></package>'''

TYPED_TITLES = '''<dc:title id="title">Book</dc:title>
<meta property="title-type" refines="#title">main</meta>
<dc:title id="subtitle">A subtitle</dc:title>
<meta property="title-type" refines="#subtitle">subtitle</meta>
<dc:title id="fulltitle">Book: A subtitle</dc:title>
<meta property="title-type" refines="#fulltitle">expanded</meta>'''


def test_explicit_main_title_and_known_secondary_author_roles():
    text = OPF.replace('<dc:title id="title">Book</dc:title>', TYPED_TITLES)
    text = text.replace('</metadata>', '<meta refines="#author" property="role">wpr</meta><meta refines="#author" property="role">ann</meta><meta refines="#author" property="role">dto</meta></metadata>')
    result = parse_opf(text.encode())
    assert result['title'] == 'Book' and result['author'] == 'Author'


def test_unique_author_can_also_be_explicit_illustrator():
    text = OPF.replace('</metadata>', '<meta refines="#author" property="role">ill</meta></metadata>')
    assert parse_opf(text.encode())['author'] == 'Author'
    without_author = text.replace('<meta refines="#author" property="role">aut</meta>', '')
    with pytest.raises(ValueError, match='one explicit Standard Ebooks author'):
        parse_opf(without_author.encode())


@pytest.mark.parametrize('titles', [
    TYPED_TITLES.replace('>subtitle</meta>', '>main</meta>'),
    TYPED_TITLES.replace('<meta property="title-type" refines="#subtitle">subtitle</meta>', ''),
    TYPED_TITLES.replace('id="subtitle"', 'id="title"'),
    TYPED_TITLES.replace('>expanded</meta>', '>alternative</meta>'),
    TYPED_TITLES + '<meta property="title-type" refines="#title">expanded</meta>',
])
def test_multiple_titles_require_one_main_and_unique_explicit_types(titles):
    with pytest.raises(ValueError):
        parse_opf(OPF.replace('<dc:title id="title">Book</dc:title>', titles).encode())


@pytest.mark.parametrize('roles', ['wpr', 'aut aut', 'aut trl'])
def test_secondary_author_roles_cannot_replace_or_conflict_with_author(roles):
    text = OPF.replace('<meta refines="#author" property="role">aut</meta>', ''.join(
        f'<meta refines="#author" property="role">{role}</meta>' for role in roles.split()))
    with pytest.raises(ValueError):
        parse_opf(text.encode())


@pytest.mark.parametrize('qid,expected_title,held', [
    ('Q82464', 'The Picture of Dorian Gray', False),
    ('Q182961', 'Jane Eyre', True),
    ('Q274744', 'Sense and Sensibility', True),
])
def test_saved_phase10_shapes_keep_quotation_hold(qid, expected_title, held):
    fixture = Path(__file__).resolve().parents[1] / f'data/autonomous-next-v3/evaluation/phase10-standardebooks-candidates-v1/raw/{qid}-content.opf'
    if not fixture.exists():
        pytest.skip('local acquisition is not a distributed test fixture')
    if held:
        with pytest.raises(ValueError, match='third-party quotation'):
            parse_opf(fixture.read_bytes())
    else:
        assert parse_opf(fixture.read_bytes())['title'] == expected_title


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_fixture(tmp_path, text=OPF, entity_changes=None):
    raw = tmp_path / 'wd.json'
    entity = {'id': 'Q1', 'labels': {'en': {'value': 'Book'}},
              'claims': {'P50': [{'mainsnak': {'datavalue': {'value': {'id': 'Q2'}}}}]},
              'sitelinks': {'enwiki': {'title': 'Book'}}}
    entity.update(entity_changes or {})
    raw.write_text(json.dumps({'entities': {'Q1': entity, 'Q2': {'id': 'Q2', 'labels': {'en': {'value': 'Author'}}}}}), encoding='utf8')
    wd = SourceRecord(source='wikidata', source_id='Q1', source_url='https://www.wikidata.org/wiki/Q1',
                      provider_id='wikidata', retrieved_at=datetime.now(timezone.utc).isoformat(),
                      record_kind='work', rights_policy_id='wikidata-cc0', titles=[Title(value='Book')],
                      contributors=[Contributor(name='Author', identifiers=[Identifier(source='wikidata', value='Q2', scope='agent')])],
                      identifiers=[Identifier(source='wikidata', value='Q1', scope='work')],
                      raw_path=str(raw), raw_sha256=digest(raw))
    opf = tmp_path / 'book.opf'
    opf.write_text(text, encoding='utf8')
    return opf, wd


def test_extracts_only_editorial_description_and_exact_work_bridge(tmp_path):
    opf, wd = source_fixture(tmp_path)
    rec = ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)
    assert rec.record_kind == 'edition'
    assert rec.evidence == [] and rec.language == 'en'
    rec = editorial_work_record(rec)
    assert rec.record_kind == 'work' and rec.language is None
    assert rec.subjects == [] and rec.original_language is None and rec.first_publication_year is None
    assert rec.work_refs == wd.identifiers
    assert rec.evidence[0].text == 'A young traveler returns home and confronts family expectations.'
    assert rec.evidence[0].kind == 'synopsis' and rec.evidence[0].language == 'en'
    assert rec.evidence[0].expression_ref is None
    assert rec.extra['authority_bridge']['target_raw_sha256'] == wd.raw_sha256


@pytest.mark.parametrize('text', [
    OPF.replace('wiki/Book"', 'wiki/Other"'),
    OPF.replace('<link rel="schema:sameAs" href="https://en.wikipedia.org/wiki/Book"/>', ''),
    OPF.replace('<dc:subject>', '<meta property="belongs-to-collection">Collection</meta><dc:subject>'),
    OPF.replace('&lt;p&gt;A young', '&lt;p&gt;&lt;q&gt;Third party&lt;/q&gt; A young'),
    OPF.replace('A young traveler', 'A reviewer wrote: “A young traveler”'),
    OPF.replace('<dc:title id="title">Book</dc:title>', '<dc:title>Book</dc:title><dc:title>Other</dc:title>'),
])
def test_rejects_ambiguous_identity_collection_or_quoted_material(tmp_path, text):
    opf, wd = source_fixture(tmp_path, text)
    with pytest.raises(ValueError):
        ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)


def test_rejects_changed_authority_raw_and_wrong_source_url(tmp_path):
    opf, wd = source_fixture(tmp_path)
    with pytest.raises(ValueError, match='source URL'):
        ingest_editorial_description(opf, source_url=URL.replace('author_book', 'someone_else'), retrieved_at=wd.retrieved_at, wikidata_record=wd)
    Path(wd.raw_path).write_text('{}')
    with pytest.raises(ValueError, match='hash'):
        ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)


@pytest.mark.parametrize('edition_class', ['Q3331189', 'Q53731850'])
def test_rejects_wikidata_edition_class_as_work_authority(tmp_path, edition_class):
    opf, wd = source_fixture(tmp_path, entity_changes={
        'claims': {'P31': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': {'id': edition_class}}}}]}})
    with pytest.raises(ValueError, match='edition assertion'):
        ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)


def admission_fixture(tmp_path, mutate=None):
    opf, wd = source_fixture(tmp_path)
    edition = ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)
    rec = editorial_work_record(edition)
    if mutate:
        mutate(rec)
    dbpath = tmp_path / 'source.sqlite'
    with DatasetDB(dbpath) as db:
        db.ingest([wd, edition, rec])
        versions = [row[0] for row in db.connection.execute('SELECT version_id FROM source_records ORDER BY source')]
    work = WorkInput(work_id='w1', title='Book', authors=['Author'], evidence=rec.evidence, source_record_ids=versions)
    registry_data = json.loads((Path(__file__).resolve().parents[1] / 'config/sources.json').read_text(encoding='utf8'))
    registry_data['rights_policies'].append(dict(policy_id=POLICY_ID, source='standardebooks', provider_id='standardebooks',
        fields=['bibliographic', 'synopsis'], license='CC0-1.0', usages={'external_llm': 'allowed'},
        verified_at=wd.retrieved_at, evidence_urls=[ABOUT_URL, MANUAL_URL], scope='SE-produced editorial descriptions only'))
    registry_path = tmp_path / 'registry.json'
    registry_path.write_text(json.dumps(registry_data), encoding='utf8')
    registry = RightsRegistry.from_file(registry_path)
    proofs = {}
    for policy, materials in [('wikidata-cc0', [(registry.policies['wikidata-cc0'].evidence_urls[0], 'CC0')]),
                             (POLICY_ID, [(ABOUT_URL, 'Content produced by or for Standard Ebooks CC0 1.0 Universal Public Domain Dedication https://creativecommons.org/publicdomain/zero/1.0/'),
                                          (MANUAL_URL, 'dc:description under no circumstances can a producer copy and paste from Wikipedia CC0 public domain dedication')])]:
        proofs[policy] = []
        for i, (url, text) in enumerate(materials):
            path = tmp_path / f'{policy}-{i}.html'
            path.write_text(text)
            proofs[policy].append(dict(raw_path=str(path), raw_sha256=digest(path), source_url=url, verified_at=wd.retrieved_at))
    ledger = dict(version='usage-ledger-v3', registry_path=str(registry_path), registry_file_sha256=digest(registry_path),
                  registry_sha256=model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
                  official_conditions=proofs,
                  inputs={model_hash(work): build_input_entry(work, {v: source_binding(dbpath, v) for v in versions})})
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(ledger))
    return work, path, ledger, rec


def test_real_guard_admits_bound_description_with_frozen_registry(tmp_path):
    work, path, _, rec = admission_fixture(tmp_path)
    decision = UsageGuard(path).check_input(work)
    assert decision.allowed, decision.reasons
    assert rec.raw_sha256 in decision.proof_hashes
    assert rec.extra['authority_bridge']['target_raw_sha256'] in decision.proof_hashes


@pytest.mark.parametrize('mutation', [
    lambda r: setattr(r.evidence[0], 'kind', 'subject'),
    lambda r: setattr(r.evidence[0], 'locator', 'manifest/text/body.xhtml'),
    lambda r: setattr(r.evidence[0], 'source_url', 'https://example.com/'),
    lambda r: r.extra['authority_bridge'].update(target_raw_sha256='0' * 64),
    lambda r: r.extra['authority_bridge'].update(wikidata_work_id='Q2'),
    lambda r: setattr(r, 'work_refs', [Identifier(source='wikidata', value='Q2', scope='work')]),
    lambda r: setattr(r, 'contents', [Identifier(source='wikidata', value='Q2', scope='work')]),
    lambda r: setattr(r, 'source_url', URL.replace('author_book', 'someone_else')),
])
def test_guard_reextracts_instead_of_trusting_normalized_record(tmp_path, mutation):
    work, path, *_ = admission_fixture(tmp_path, mutation)
    assert not UsageGuard(path).check_input(work).allowed


def test_guard_requires_both_primary_policy_artifacts(tmp_path):
    work, path, ledger, _ = admission_fixture(tmp_path)
    ledger['official_conditions'][POLICY_ID].pop()
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_guard_requires_authority_record_in_same_complete_input(tmp_path):
    work, path, ledger, _ = admission_fixture(tmp_path)
    bindings = ledger['inputs'][model_hash(work)]['sources']
    se_ref = next(ref for ref, binding in bindings.items() if binding['raw_path'].endswith('.opf'))
    work.source_record_ids = [se_ref]
    ledger['inputs'] = {model_hash(work): build_input_entry(work, {se_ref: bindings[se_ref]})}
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_shared_official_proof_hash_cannot_bypass_manual_clauses(tmp_path):
    work, path, ledger, _ = admission_fixture(tmp_path)
    about, manual = ledger['official_conditions'][POLICY_ID]
    manual.update(raw_path=about['raw_path'], raw_sha256=about['raw_sha256'])
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_guard_rechecks_raw_opf_bytes(tmp_path):
    work, path, _, rec = admission_fixture(tmp_path)
    Path(rec.raw_path).write_text(OPF.replace('family expectations', 'unverified external prose'))
    assert not UsageGuard(path).check_input(work).allowed


def test_real_saved_opf_is_supported(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'data/autonomous-next-v3/evaluation/phase9-standardebooks-reference-v1/raw/age-of-innocence.opf'
    if not fixture.exists():
        pytest.skip('local acquisition is not a distributed test fixture')
    opf, wd = source_fixture(tmp_path, fixture.read_text(encoding='utf8'), {'sitelinks': {'enwiki': {'title': 'The Age of Innocence'}}})
    rec = editorial_work_record(ingest_editorial_description(opf, source_url=URL.replace('author_book', 'edith-wharton_the-age-of-innocence'), retrieved_at=wd.retrieved_at, wikidata_record=wd))
    assert len(rec.evidence[0].text) > 600


def test_database_projects_one_work_synopsis_and_preserves_edition(tmp_path):
    opf, wd = source_fixture(tmp_path)
    edition = ingest_editorial_description(opf, source_url=URL, retrieved_at=wd.retrieved_at, wikidata_record=wd)
    editorial = editorial_work_record(edition)
    with DatasetDB(tmp_path / 'projection.sqlite') as db:
        db.ingest([wd, edition, editorial])
        inputs = db.work_inputs()
        assert len(inputs) == 1
        assert inputs[0].expression_id is None
        assert inputs[0].evidence == editorial.evidence
        assert len(inputs[0].source_record_ids) == 3
        assert db.connection.execute('SELECT COUNT(*) FROM editions').fetchone()[0] == 1
        assert db.connection.execute('SELECT COUNT(*) FROM evidence WHERE expression_id IS NOT NULL').fetchone()[0] == 0
