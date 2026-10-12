import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from recommend_book.autonomous_enrichment import select_pilot, wikipedia_evidence, append_proposal
from recommend_book.models import SourceRecord, Title, WorkInput
from recommend_book.rights import RightsRegistry, USAGES


def fixture(tmp_path, text):
    raw = {'entities': {'Q1': {'id': 'Q1', 'sitelinks': {'enwiki': {'title': 'A novel'}}}}}
    path = tmp_path / 'wd.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    work = SourceRecord(source='wikidata', source_id='Q1', source_url='https://www.wikidata.org/wiki/Q1', retrieved_at='2026-10-13', provider_id='wikidata', record_kind='work', titles=[Title(value='A novel')], raw_path=str(path), raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), rights_policy_id='wikidata-cc0')
    page = {'pageid': 7, 'ns': 0, 'title': 'A novel', 'pageprops': {'wikibase_item': 'Q1'}, 'revisions': [{'revid': 123, 'timestamp': '2026-10-13T00:00:00Z', 'slots': {'main': {'contentmodel': 'wikitext', 'content': text}}}]}
    wp = tmp_path / 'wp.json'
    wp.write_text(json.dumps({'query': {'pages': [page]}}), encoding='utf-8')
    artifact = SimpleNamespace(path=str(wp), sha256=hashlib.sha256(wp.read_bytes()).hexdigest(), source_url='https://en.wikipedia.org/w/api.php', retrieved_at='2026-10-13')
    return work, page, artifact


def test_pilot_deterministic_stratified_and_featureless():
    rows = [{'work_id': f'w{i}', 'original_language': ['en', 'ja'][i % 2], 'first_publication_year': 1800 if i < 10 else 2020, 'features': {}, 'work_kind': 'novel'} for i in range(30)]
    assert select_pilot(rows, 8) == select_pilot(list(reversed(rows)), 8)
    assert len(select_pilot(rows, 8)) == 8
    assert {r['original_language'] for r in select_pilot(rows, 8)} == {'ja', 'en'}
    assert {r['first_publication_year'] for r in select_pilot(rows, 8)} == {1800, 2020}


def test_section_deny_is_not_converted_to_partial_permission(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.\n\n== Plot ==\nA person returns home.\n\n{{quote|copyrighted words}}')
    evidence, ledger, outcomes = wikipedia_evidence(work, 'version1', 'canonical1', page, artifact, 'en', set(), set(), 'pilot')
    assert [e.kind for e in evidence] == ['note']
    assert outcomes['Plot']['status'] == 'held'
    assert outcomes['Plot']['reason'] == 'section_contains_rejected_paragraphs'
    assert ledger[0]['identity_proof']['pageprops_wikibase_item'] == 'Q1'
    assert all(ledger[0]['usage_permitted'][usage] for usage in USAGES)


def test_raw_identity_and_exact_page_are_required(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.')
    page['pageprops']['wikibase_item'] = 'Q99'
    with pytest.raises(ValueError, match='identity'):
        wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), set(), 'pilot')


def test_denied_prose_and_existing_prose_never_rebound(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.')
    text_hash = hashlib.sha256(b'This novel follows the journey of a family.').hexdigest()
    evidence, _, outcomes = wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', {text_hash}, set(), 'pilot')
    assert not evidence and outcomes['lead']['reason'] == 'previously_denied_text'
    evidence, _, outcomes = wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), {text_hash}, 'pilot')
    assert not evidence and outcomes['lead']['reason'] == 'already_in_baseline'


def test_proposal_changes_only_evidence_preserves_baseline_refs(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.')
    new, _, _ = wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), set(), 'pilot')
    original = WorkInput(work_id='w1', title='A novel', source_record_ids=['v1'])
    record, proposal = append_proposal(work, original, new)
    assert record.model_dump(exclude={'evidence'}) == work.model_dump(exclude={'evidence'})
    assert proposal.model_dump(exclude={'evidence'}) == original.model_dump(exclude={'evidence'})
    assert proposal.source_record_ids == ['v1']
    assert record.evidence[0].rights_policy_id == 'wikipedia-screened-prose'


def test_imported_text_notice_anywhere_blocks_article(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.\n\n== Sources ==\n{{CC-notice|external source}}')
    evidence, _, outcomes = wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), set(), 'pilot')
    assert not evidence
    assert outcomes['lead']['reason'] == 'additional_attribution_or_import_notice'


def test_raw_source_hash_corruption_is_rejected(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.')
    Path(artifact.path).write_bytes(b'{}')
    with pytest.raises(ValueError, match='raw hash'):
        wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), set(), 'pilot')


def test_article_prose_is_never_original_style_excerpt(tmp_path):
    work, page, artifact = fixture(tmp_path, 'This novel follows the journey of a family.\n\n== Plot ==\nAn orphan returns to a distant town and finds a missing sibling.')
    evidence, ledger, _ = wikipedia_evidence(work, 'v1', 'w1', page, artifact, 'en', set(), set(), 'pilot')
    assert {e.kind for e in evidence} == {'note', 'synopsis'}
    assert all(e.license == 'CC-BY-SA-4.0' and e.attribution['history_url'] for e in evidence)
    assert all(row['usages']['external_llm'] == 'conditional' for row in ledger)


def test_ndl_title_alone_and_isbn_without_full_author_authority_are_held(tmp_path):
    from recommend_book.autonomous_enrichment import assess_ndl_identity
    from recommend_book.models import Contributor, Identifier
    work, _, _ = fixture(tmp_path, 'This novel follows the journey of a family.')
    ndl = work.model_copy(update={'source': 'ndl', 'source_id': 'R100000002-I123', 'record_kind': 'edition', 'identifiers': [Identifier(source='isbn', value='9784101010137', scope='edition')], 'contributors': [Contributor(name='A writer')]})
    result = assess_ndl_identity(work, ndl)
    assert not result['accepted']
    assert 'isbn_not_explicitly_in_work_raw' in result['reasons']
    assert 'complete_author_authority_missing' in result['reasons']


def test_ndl_exact_raw_isbn_and_complete_author_authority_scope(tmp_path):
    from recommend_book.autonomous_enrichment import assess_ndl_identity
    from recommend_book.models import Contributor, Identifier
    work, _, _ = fixture(tmp_path, 'This novel follows the journey of a family.')
    claim = lambda value: {'mainsnak': {'snaktype': 'value', 'datavalue': {'value': value}}, 'rank': 'normal'}
    raw = json.loads(Path(work.raw_path).read_text())
    raw['entities']['Q1']['claims'] = {'P212': [claim('9784101010137')], 'P50': [claim({'id': 'Q2'})]}
    Path(work.raw_path).write_text(json.dumps(raw))
    author = tmp_path / 'author.json'
    author.write_text(json.dumps({'entities': {'Q2': {'id': 'Q2', 'claims': {'P349': [claim('00054222')]}}}}))
    work = work.model_copy(update={'raw_sha256': hashlib.sha256(Path(work.raw_path).read_bytes()).hexdigest(), 'contributors': [Contributor(name='Writer', identifiers=[Identifier(source='wikidata', value='Q2', scope='agent')])], 'extra': {'linked_label_provenance': {'Q2': {'raw_path': str(author), 'raw_sha256': hashlib.sha256(author.read_bytes()).hexdigest()}}}})
    from test_ingestion_ndl import _adapter, STRUCTURED
    ndl = _adapter(tmp_path / 'ndl').search_sru('isbn="9784101010137"', provider='iss-ndl-opac').records[0]
    assert assess_ndl_identity(work, ndl)['accepted']
    entity_uris = _adapter(tmp_path / 'ndl-entity', STRUCTURED.replace('/auth/ndlna/', '/auth/entity/')).search_sru('isbn="9784101010137"', provider='iss-ndl-opac').records[0]
    assert assess_ndl_identity(work, entity_uris)['accepted']
    assert not assess_ndl_identity(work, ndl.model_copy(update={'volume': '2'}))['accepted']
    injected = ndl.model_copy(update={'work_refs': [Identifier(source='wikidata', value='Q1', scope='work')]})
    assert not assess_ndl_identity(work, injected)['accepted']
    assert 'normalized_ndl_record_differs_from_raw' in assess_ndl_identity(work, injected)['reasons']
    raw['entities']['Q1']['claims']['P31'] = [claim({'id': 'Q3331189'})]
    Path(work.raw_path).write_text(json.dumps(raw))
    edition_target = work.model_copy(update={'raw_sha256': hashlib.sha256(Path(work.raw_path).read_bytes()).hexdigest()})
    assert 'target_wikidata_record_has_edition_scope' in assess_ndl_identity(edition_target, ndl)['reasons']
