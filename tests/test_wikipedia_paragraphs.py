import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from recommend_book.autonomous_enrichment import wikipedia_evidence
from recommend_book.database import DatasetDB
from recommend_book.models import SourceRecord, Title
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding
from recommend_book.wikipedia_paragraphs import VERSION, build_evidence, build_infobox_genre_evidence


CLEAN = 'An orphan returns to a distant town and discovers a missing sibling.'
MIXED = '== Plot ==\n' + CLEAN + '\n\n{{quote|A private externally quoted passage}}\n\n[https://example.org External prose]'


def fixture(tmp_path, text=MIXED):
    target = tmp_path / 'wd.json'
    target.write_text(json.dumps({'entities': {'Q1': {'id': 'Q1', 'labels': {'en': {'value': 'Book'}}, 'sitelinks': {'enwiki': {'title': 'Book'}}}}}), encoding='utf8')
    record = SourceRecord(source='wikidata', source_id='Q1', source_url='https://www.wikidata.org/wiki/Q1', retrieved_at='2026-10-15', provider_id='wikidata', record_kind='work', titles=[Title(value='Book')], raw_path=str(target), raw_sha256=hashlib.sha256(target.read_bytes()).hexdigest(), rights_policy_id='wikidata-cc0')
    page = {'pageid': 7, 'ns': 0, 'title': 'Book', 'pageprops': {'wikibase_item': 'Q1'}, 'revisions': [{'revid': 123, 'slots': {'main': {'contentmodel': 'wikitext', 'content': text}}}]}
    path = tmp_path / 'wp.json'
    path.write_text(json.dumps({'query': {'pages': [page]}}), encoding='utf8')
    artifact = SimpleNamespace(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), source_url='https://en.wikipedia.org/w/api.php')
    return record, page, artifact


def plot_evidence(tmp_path):
    record, page, artifact = fixture(tmp_path)
    evidence, ledger, outcomes = build_evidence(record, page, artifact, language='en')
    return record, page, artifact, next(ev for ev in evidence if ev.kind == 'synopsis'), ledger, outcomes


def test_new_recipe_selects_complete_clean_paragraph_and_preserves_old_rejection(tmp_path):
    record, page, artifact, ev, ledger, outcomes = plot_evidence(tmp_path)
    old, _, old_outcomes = wikipedia_evidence(record, 'old', 'canonical', page, artifact, 'en', set(), set(), 'test')
    assert not any(item.kind == 'synopsis' for item in old)
    assert old_outcomes['Plot']['reason'] == 'section_contains_rejected_paragraphs'
    assert ev.text == CLEAN
    assert ev.evidence_id.endswith(':' + VERSION)
    assert outcomes['Plot']['status'] == 'accepted'
    assert ev.attribution['paragraph_selection']['rejected_paragraphs'] == [{'index': 1, 'reason': 'quotation_template'}, {'index': 2, 'reason': 'external_link'}]
    assert ledger[0]['recipe_version'] == VERSION
    UsageGuard(tmp_path / 'unused.json')._wikipedia(ev, [record], [])
    forged_old = ev.model_copy(deep=True)
    forged_old.evidence_id = forged_old.evidence_id.removesuffix(VERSION) + 'autonomous-enrichment-v1'
    with pytest.raises(ValueError, match='complete clean section'):
        UsageGuard(tmp_path / 'unused.json')._wikipedia(forged_old, [record], [])


@pytest.mark.parametrize('notice', ['CC-notice', 'Source attribution', 'copyvio', 'copyrighted', 'EB1911', 'Imported text'])
def test_article_level_notice_blocks_even_when_outside_selected_section(tmp_path, notice):
    record, page, artifact = fixture(tmp_path, MIXED + '\n\n== References ==\n{{' + notice + '|source}}')
    evidence, _, outcomes = build_evidence(record, page, artifact, language='en')
    assert not evidence
    assert all(row['status'] == 'held' for row in outcomes.values())


@pytest.mark.parametrize('change', ['text', 'revision', 'credit', 'changes', 'omissions', 'locator', 'missing_locator', 'proof_type', 'identity', 'sharealike'])
def test_guard_rejects_modified_selection_or_provenance(tmp_path, change):
    record, _, _, ev, _, _ = plot_evidence(tmp_path)
    ev = ev.model_copy(deep=True)
    if change == 'text': ev.text += ' invented text'
    elif change == 'revision': ev.attribution['revision_id'] = 124
    elif change == 'credit': ev.attribution['credit'] = 'Wrong credit'
    elif change == 'changes': ev.attribution['changes'] = 'unmodified text'
    elif change == 'omissions': ev.attribution['paragraph_selection']['rejected_paragraphs'] = []
    elif change == 'locator': ev.locator += ';arbitrary'
    elif change == 'missing_locator': ev.locator = 'no-section'
    elif change == 'proof_type': ev.attribution['paragraph_selection']['accepted_paragraphs'] = True
    elif change == 'identity': ev.attribution['identity_proof']['wikidata_work_id'] = 'Q2'
    elif change == 'sharealike': ev.inherited_restrictions.remove('share_alike')
    with pytest.raises(ValueError):
        UsageGuard(tmp_path / 'unused.json')._wikipedia(ev, [record], [])


def test_guard_rejects_article_notice_after_raw_rebinding(tmp_path):
    record, page, artifact, ev, _, _ = plot_evidence(tmp_path)
    page['revisions'][0]['slots']['main']['content'] += '\n\n== References ==\n{{CC-notice|third party}}'
    Path(artifact.path).write_text(json.dumps({'query': {'pages': [page]}}), encoding='utf8')
    ev.attribution['raw_sha256'] = hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='imported text attribution'):
        UsageGuard(tmp_path / 'unused.json')._wikipedia(ev, [record], [])


@pytest.mark.parametrize('recipe', ['paragraphs', 'infobox'])
def test_full_guard_admits_bound_new_recipe_with_current_official_conditions(tmp_path, recipe):
    if recipe == 'paragraphs':
        record, _, _, ev, _, _ = plot_evidence(tmp_path)
    else:
        record, _, _, evidence, _, _ = genre_evidence(tmp_path)
        ev = evidence[0]
    record.evidence = [ev]
    database_path = tmp_path / 'sources.sqlite'
    database = DatasetDB(database_path)
    database.ingest([record])
    work = database.work_inputs()[0]
    bindings = {ref: source_binding(database_path, ref) for ref in work.source_record_ids}
    registry = RightsRegistry.default()
    official = tmp_path / 'official.txt'
    official.write_text('CC0 attribution sharealike', encoding='utf8')
    policies = ('wikidata-cc0', 'wikipedia-screened-prose')
    ledger = {'version': 'usage-ledger-v3', 'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': {name: [{'raw_path': str(official), 'raw_sha256': hashlib.sha256(official.read_bytes()).hexdigest(), 'source_url': registry.policies[name].evidence_urls[0], 'verified_at': datetime.now(timezone.utc).isoformat()}] for name in policies},
              'inputs': {model_hash(work): build_input_entry(work, bindings)}, 'revoked_input_hashes': [], 'private_origins': {}}
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(ledger), encoding='utf8')
    decision = UsageGuard(path).check_input(work)
    assert decision.allowed, decision.reasons


GENRE = '{{Infobox animanga/Header|タイトル=Book|ジャンル=[[Fantasy|fantasy]]、[[Battle|battle]]<ref>{{Cite book|title=Source}}</ref>}}\n{{Infobox animanga/Novel|著者=Writer}}\n{{Infobox animanga/Footer}}'


def genre_evidence(tmp_path, text=GENRE):
    record, page, artifact = fixture(tmp_path, text)
    evidence, ledger, outcomes = build_infobox_genre_evidence(record, page, artifact, language='en')
    return record, page, artifact, evidence, ledger, outcomes


def test_infobox_genre_preserves_entire_assertion_and_work_scope(tmp_path):
    record, _, _, evidence, ledger, _ = genre_evidence(tmp_path)
    assert len(evidence) == 1
    ev = evidence[0]
    assert ev.text == 'Article-level declared genre (not exhaustive story annotation): fantasy、battle'
    assert ev.kind == 'note' and ev.expression_ref is None
    assert ev.attribution['genre_selection']['scope'] == 'article_level_work_assertion'
    assert ledger[0]['recipe_version'] == 'screened-infobox-genre-v1'
    UsageGuard(tmp_path / 'unused.json')._wikipedia(ev, [record], [])


@pytest.mark.parametrize('value', ['fantasy {{unknown complex|battle}}', '"quoted genre"', '[https://example.org external genre]', 'fantasy<br>battle', "''fantasy''"])
def test_complex_quoted_or_external_infobox_genre_is_held(tmp_path, value):
    text = '{{Infobox animanga/Header|ジャンル=' + value + '}}\n{{Infobox animanga/Novel}}'
    _, _, _, evidence, _, outcomes = genre_evidence(tmp_path, text)
    assert evidence == [] and outcomes['genre']['status'] == 'held'


@pytest.mark.parametrize('text', [GENRE.replace('/Novel', '/Manga'), GENRE + '\n{{Infobox animanga/Header|ジャンル=other}}', GENRE + '\n{{CC-notice|source}}', GENRE.replace('ジャンル=', 'ジャンル=other|ジャンル=')])
def test_infobox_scope_ambiguity_or_article_notice_is_held(tmp_path, text):
    _, _, _, evidence, _, _ = genre_evidence(tmp_path, text)
    assert not evidence


@pytest.mark.parametrize('change', ['genre', 'render', 'ref', 'context', 'credit'])
def test_guard_rejects_changed_infobox_genre_render_ref_or_scope(tmp_path, change):
    record, page, artifact, evidence, _, _ = genre_evidence(tmp_path)
    ev = evidence[0].model_copy(deep=True)
    if change == 'render': ev.text += ', invented horror'
    elif change == 'credit': ev.attribution['credit'] = 'other'
    else:
        text = page['revisions'][0]['slots']['main']['content']
        if change == 'genre': text = text.replace('Battle|battle', 'Battle|drama')
        elif change == 'ref': text = text.replace('title=Source', 'title=Changed source')
        elif change == 'context': text = text.replace('/Novel', '/Manga')
        page['revisions'][0]['slots']['main']['content'] = text
        Path(artifact.path).write_text(json.dumps({'query': {'pages': [page]}}), encoding='utf8')
        ev.attribution['raw_sha256'] = hashlib.sha256(Path(artifact.path).read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        UsageGuard(tmp_path / 'unused.json')._wikipedia(ev, [record], [])
