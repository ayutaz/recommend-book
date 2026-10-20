import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from recommend_book.database import DatasetDB
from recommend_book.models import Contributor, Identifier, Evidence, SourceRecord, Title, WorkInput
from recommend_book.rights import RightsRegistry
from recommend_book.usage_guard import UsageDenied, UsageGuard, build_input_entry, source_binding, model_hash


def conssa_term_fixture(tmp_path, monkeypatch, *, death=1920, first=1889, printed=1889, citizenship=False):
    from recommend_book import next_reference
    root = tmp_path
    legal_urls = {'japan_terms': 'https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html',
                  'japan_wartime': 'https://www.jasrac.or.jp/aboutus/wartime/pdf/wartime.pdf',
                  'us_uraa': 'https://www.copyright.gov/circs/circ38b.pdf',
                  'us_duration': 'https://www.copyright.gov/title17/92chap3.html',
                  'us_edition_status': 'https://www.copyright.gov/circs/circ22.pdf',
                  'ccby_license': 'https://creativecommons.org/licenses/by/4.0/legalcode.en',
                  'pdm_declaration': 'https://creativecommons.org/publicdomain/mark/1.0/'}
    def artifact(name, text):
        path = root / name
        path.write_text(text, encoding='utf8')
        return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': len(path.read_bytes())}
    legal = [{**artifact(role, 'primary fixture ' + role), 'role': role, 'url': url} for role, url in legal_urls.items()]
    url = 'https://raw.githubusercontent.com/cligs/conssa/commit/master/master/ne0002.xml'
    license_artifact = {**artifact('README.md', 'Original text public domain; XML markup CC BY 4.0'), 'url': url.rsplit('/master/master/', 1)[0] + '/README.md'}
    review_path = root/'reviews.jsonl'
    review_path.write_text(json.dumps({'reference_id': 'ne0002', 'reviewed_at': datetime.now(timezone.utc).isoformat(), 'regions_reviewed': ['Japan', 'United States'], 'worldwide_public_domain_claim': False, 'legal_primary_artifacts': legal, 'license_primary_artifact': license_artifact})+'\n')
    facts = {'author_death_year': death, 'first_publication_year': first, 'underlying_print_edition_year': printed,
             'rights_reviewed_regions': ['Japan', 'United States'], 'worldwide_public_domain_claim': False,
             'country_is_citizenship': citizenship, 'source_reported_country_differs': True}
    monkeypatch.setattr(next_reference, 'verify_conssa_evidence', lambda *a, **k: {'reproduced': True, 'proof_hashes': [], 'facts': {}})
    guard = UsageGuard(root/'ledger.json', root=root)
    monkeypatch.setattr(guard, '_conssa_authority', lambda *a, **k: facts, raising=False)
    ev = Evidence(evidence_id='ne0002:definitions-v3:body:1', kind='excerpt', text='Original body', source_url=url, rights_policy_id='conssa-reviewed-original-v3', llm_input_allowed=False)
    record = SourceRecord(source='conssa', source_id='ne0002', source_url=url, retrieved_at='2026-10-13T00:00:00Z', provider_id='cligs', record_kind='edition', raw_path=str(root/'source.xml'), raw_sha256='a'*64, rights_policy_id=ev.rights_policy_id, evidence=[ev])
    scope = {'policy': artifact('policy.md', 'scoped policy'), 'provider_observation': artifact('auth.json', json.dumps({'auth_method':'chatgpt','model_provider':'openai','chatgpt_plan_type':'pro','configured_provider_override_present':False,'runtime_ignores_user_config':True}))}
    ledger = {'corpus_proofs': {ev.evidence_id: {'rights_review': {'path': str(review_path), 'sha256': hashlib.sha256(review_path.read_bytes()).hexdigest()}}},
              'corpus_verifiers': {'conssa': {'path': str(__import__('pathlib').Path(next_reference.__file__).resolve()), 'sha256': hashlib.sha256(__import__('pathlib').Path(next_reference.__file__).read_bytes()).hexdigest()}}, 'corpus_review_scope': scope}
    return guard, ev, record, ledger


def test_individual_conssa_term_scope_admits_private_external_after_reproduction(tmp_path, monkeypatch):
    guard, ev, record, ledger = conssa_term_fixture(tmp_path, monkeypatch)
    guard._corpus(ev, [record], ledger, 'external_llm', [])


@pytest.mark.parametrize('changes', [{'death': 1956}, {'printed': 1931}, {'death': None}, {'first': 1905, 'printed': 1890}, {'citizenship': True}])
def test_conssa_term_scope_rejects_unsupported_or_conflicting_facts(tmp_path, monkeypatch, changes):
    guard, ev, record, ledger = conssa_term_fixture(tmp_path, monkeypatch, **changes)
    with pytest.raises(ValueError):
        guard._corpus(ev, [record], ledger, 'external_llm', [])


def test_private_conssa_receipt_does_not_authorize_public_display(tmp_path, monkeypatch):
    guard, ev, record, ledger = conssa_term_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        guard._corpus(ev, [record], ledger, 'display', [])


def fixture_ledger(tmp_path, *, policy='wikidata-cc0', kind='bibliographic', allowed_flag=True, normalized_title='Book', normalized_language='en', raw_entity=None, record_fields=None, raw_path_override=None):
    raw = tmp_path / 'raw.json'
    raw.write_text(json.dumps({'entities': {'Q1': raw_entity or {'id': 'Q1', 'labels': {'en': {'value': 'Book'}}, 'claims': {}}}}))
    ev = Evidence(evidence_id='wd:Q1:title', kind=kind, text='Book', source_url='https://www.wikidata.org/wiki/Q1', rights_policy_id=policy, llm_input_allowed=allowed_flag)
    rec = SourceRecord(source='wikidata', source_id='Q1', source_url=ev.source_url, retrieved_at='2026-10-13T00:00:00Z', provider_id='wikidata', record_kind='work', titles=[Title(value=normalized_title, language=normalized_language)], evidence=[ev], raw_path=raw_path_override or str(raw), raw_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(), rights_policy_id='wikidata-cc0', **(record_fields or {}))
    dbpath = tmp_path / 'data.sqlite'
    db = DatasetDB(dbpath)
    db.ingest([rec])
    works = db.work_inputs()
    work = works[0]
    # Exercise the complete supplied material even when the DB convenience
    # projection omits llm_input_allowed=False evidence.
    work.evidence = [ev]
    bindings = {ref: source_binding(dbpath, ref) for ref in work.source_record_ids}
    entry = build_input_entry(work, bindings)
    registry = RightsRegistry.default()
    official = tmp_path / 'official.html'
    official.write_text('Official structured data licensing: CC0')
    policy_model = registry.policies['wikidata-cc0']
    ledger = {'version': 'usage-ledger-v3', 'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': {'wikidata-cc0': [{'raw_path': str(official), 'raw_sha256': hashlib.sha256(official.read_bytes()).hexdigest(), 'source_url': policy_model.evidence_urls[0], 'verified_at': datetime.now(timezone.utc).isoformat()}]},
              'inputs': {model_hash(work): entry}, 'revoked_input_hashes': [], 'private_origins': {}}
    path = tmp_path / 'ledger.json'
    path.write_text(json.dumps(ledger))
    return work, path, raw, official, ledger


def test_bound_complete_input_passes_and_serializes(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    decision = UsageGuard(path).check_input(work, 'external_llm')
    assert decision.allowed
    assert decision.model_dump(mode='json')['input_hashes'] == [model_hash(work)]


def test_normalized_title_cannot_override_same_raw_identity(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path, normalized_title='Injected title')
    assert not UsageGuard(path).check_input(work).allowed


@pytest.mark.parametrize('rank,expected', [('normal', True), ('deprecated', False)])
def test_published_title_normal_rank_remains_usable_beside_preferred(tmp_path, rank, expected):
    def statement(text, statement_rank):
        return {'rank': statement_rank, 'mainsnak': {'snaktype': 'value',
            'datavalue': {'value': {'text': text, 'language': 'ja'}}}}
    entity = {'id': 'Q1', 'labels': {'en': {'value': 'Book'}},
              'claims': {'P1476': [statement('Preferred', 'preferred'), statement('Published', rank)]}}
    work, path, *_ = fixture_ledger(tmp_path, normalized_title='Published', raw_entity=entity)
    assert UsageGuard(path).check_input(work).allowed is expected


@pytest.mark.parametrize('language,allowed', [('fr', True), ('en', False)])
def test_title_alias_language_requires_exact_raw_title_assertion(tmp_path, language, allowed):
    entity = {'id': 'Q1', 'labels': {'en': {'value': 'Book'}}, 'claims': {'P1476': [
        {'rank': 'normal', 'mainsnak': {'snaktype': 'value',
         'datavalue': {'value': {'text': 'Published', 'language': 'fr'}}}}]}}
    work, path, raw, official, ledger = fixture_ledger(tmp_path, normalized_title='Published', normalized_language=language, raw_entity=entity)
    payload = {'work_id': work.work_id, 'title': work.title, 'language': language, 'role': 'unknown'}
    derived = tmp_path/'derived.json'
    derived.write_text(json.dumps(payload))
    recipe = tmp_path/'recipe.py'
    recipe.write_text('typed alias derivation')
    ledger['derived_artifacts'] = {model_hash(payload): {'kind': 'title_alias', 'raw_path': str(derived),
        'raw_sha256': hashlib.sha256(derived.read_bytes()).hexdigest(), 'locator': '',
        'originating_input_hashes': [model_hash(work)], 'value_sha256': model_hash(payload),
        'recipe': {'path': str(recipe), 'sha256': hashlib.sha256(recipe.read_bytes()).hexdigest()}}}
    path.write_text(json.dumps(ledger))
    assert UsageGuard(path).check_derived(payload, [model_hash(work)], 'display').allowed is allowed


@pytest.mark.parametrize('field,value', [('first_publication_year', 1999), ('original_language', 'ja'), ('region', ['Japan']), ('series', 'Fake series')])
def test_normalized_nonempty_metadata_requires_original_claims(tmp_path, field, value):
    work, path, *_ = fixture_ledger(tmp_path, record_fields={field: value})
    assert not UsageGuard(path).check_input(work).allowed


@pytest.mark.parametrize('field,prop,value', [('contributors', 'P50', [Contributor(name='Q2', identifiers=[Identifier(source='wikidata', value='Q2', scope='agent')])]), ('region', 'P495', ['Q2']), ('series', 'P179', 'Q2')])
def test_unresolved_author_region_series_id_is_not_verified_name(tmp_path, field, prop, value):
    claims = {prop: [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': {'id': 'Q2'}}}}]}
    work, path, *_ = fixture_ledger(tmp_path, raw_entity={'id': 'Q1', 'labels': {'en': {'value': 'Book'}}, 'claims': claims}, record_fields={field: value})
    assert not UsageGuard(path).check_input(work).allowed


def test_prompt_extra_unbound_annotation_rejected(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    prompt = ('BEGIN_LOCAL_DATA_JSON\n' + json.dumps({'work': work.model_dump(mode='json'), 'hidden_annotation': 'not bound'}) + '\nEND_LOCAL_DATA_JSON').encode()
    with pytest.raises(UsageDenied):
        UsageGuard(path).assert_prompt_allowed(prompt, [work])


def test_source_raw_relative_path_is_rejected_at_binding(tmp_path):
    with pytest.raises(ValueError, match='absolute'):
        fixture_ledger(tmp_path, raw_path_override='raw.json')


def test_author_label_requires_raw_authority_and_rechecks_auxiliary_sha(tmp_path):
    author_raw = tmp_path/'author.json'
    author_raw.write_text(json.dumps({'entities': {'Q2': {'id': 'Q2', 'labels': {'en': {'value': 'Author'}}}}}))
    claims = {'P50': [{'rank': 'normal', 'mainsnak': {'datavalue': {'value': {'id': 'Q2'}}}}]}
    work, path, *_ = fixture_ledger(tmp_path, raw_entity={'id': 'Q1', 'labels': {'en': {'value': 'Book'}}, 'claims': claims},
        record_fields={'contributors': [Contributor(name='Author', identifiers=[Identifier(source='wikidata', value='Q2', scope='agent')])],
          'extra': {'linked_label_provenance': {'Q2': {'label': 'Author', 'raw_path': str(author_raw), 'raw_sha256': hashlib.sha256(author_raw.read_bytes()).hexdigest()}}}})
    guard = UsageGuard(path)
    assert guard.check_input(work).allowed
    author_raw.write_text('{}')
    assert not guard.check_input(work).allowed


def test_ledger_pin_rejects_inserted_allowed_entries(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    guard = UsageGuard(path, expected_ledger_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert guard.check_input(work).allowed
    ledger['revoked_input_hashes'] = []
    ledger['unexpected'] = True
    path.write_text(json.dumps(ledger))
    assert not guard.check_input(work).allowed


def test_official_proof_requires_real_iso_date_and_primary_clause(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    ledger['official_conditions']['wikidata-cc0'][0]['verified_at'] = 'x'
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed
    ledger['official_conditions']['wikidata-cc0'][0]['verified_at'] = datetime.now(timezone.utc).isoformat()
    official.write_text('Unrelated page with no license statement')
    ledger['official_conditions']['wikidata-cc0'][0]['raw_sha256'] = hashlib.sha256(official.read_bytes()).hexdigest()
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_private_owner_aggregate_does_not_accept_self_declared_payload(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    decision = UsageGuard(path).check_private_reference_aggregate(
        {'mode': 'owned_title_fixed_ranking', 'input_sha256': 'a'*64,
         'preregistration_sha256': 'b'*64, 'aggregate_only': True},
        ['narou:n0001aa'], context='private_local_owned_title_ranking')
    assert not decision.allowed


def test_unregistered_execution_recipe_is_denied_before_native_call(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    with pytest.raises(UsageDenied):
        UsageGuard(path).assert_execution_allowed('a'*64)


def test_missing_ledger_is_fail_closed(tmp_path):
    work = WorkInput(work_id='w1', title='Book')
    with pytest.raises(UsageDenied):
        UsageGuard(tmp_path/'missing.json').assert_inputs_allowed([work])


def test_metadata_change_rejects_full_input_hash(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    work.title = 'Changed'
    assert not UsageGuard(path).check_input(work).allowed


def test_false_evidence_is_not_exempt_from_provenance(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path, allowed_flag=False)
    work.evidence[0].text = 'Injected'
    assert not UsageGuard(path).check_input(work).allowed


def test_raw_tamper_revokes_existing_guard_instance(tmp_path):
    work, path, raw, *_ = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    assert guard.check_input(work).allowed
    raw.write_text('{}')
    assert not guard.check_input(work).allowed


def test_policy_or_official_proof_tamper_is_rejected(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    official.write_text('Changed')
    assert not UsageGuard(path).check_input(work).allowed


def test_conditional_declarations_cannot_fulfill_obligations(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path, policy='wikipedia-screened-prose', kind='note')
    work.evidence[0].inherited_restrictions = ['attribution', 'share_alike', 'mark_changes', 'third_party_text_screened']
    ledger['inputs'] = {model_hash(work): build_input_entry(work, ledger['inputs'].popitem()[1]['sources'])}
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_evidence_modelhash_and_metadata_origin_required(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    entry = ledger['inputs'][model_hash(work)]
    entry['metadata'].pop('title')
    path.write_text(json.dumps(ledger))
    assert not UsageGuard(path).check_input(work).allowed


def test_ledger_revocation_read_on_every_call(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    assert guard.check_input(work).allowed
    ledger['revoked_input_hashes'] = [model_hash(work)]
    path.write_text(json.dumps(ledger))
    assert not guard.check_input(work).allowed


def test_cache_requires_same_full_input(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    assert guard.assert_cache_allowed(work, model_hash(work)).allowed
    with pytest.raises(UsageDenied):
        guard.assert_cache_allowed(work, '0'*64)


def test_derived_rechecks_all_origin_not_quoted_ids(tmp_path):
    work, path, raw, official, ledger = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    payload = {'work_id': work.work_id, **{field: getattr(work, field) for field in ('title', 'authors', 'original_language', 'first_publication_year', 'region', 'series')}}
    assert not guard.check_derived(payload, [model_hash(work)], 'external_llm').allowed
    source = tmp_path/'derived.json'
    source.write_text(json.dumps(payload))
    recipe = tmp_path/'recipe.json'
    recipe.write_text('typed fixture derivation')
    ledger['derived_artifacts'] = {model_hash(payload): {'kind': 'bibliographic_metadata', 'raw_path': str(source),
         'raw_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'locator': '',
         'originating_input_hashes': [model_hash(work)], 'value_sha256': model_hash(payload),
         'recipe': {'path': str(recipe), 'sha256': hashlib.sha256(recipe.read_bytes()).hexdigest()}}}
    path.write_text(json.dumps(ledger))
    assert guard.check_derived(payload, [model_hash(work)], 'external_llm').allowed
    assert not guard.check_derived({'labels': []}, [model_hash(work), '0'*64], 'external_llm').allowed
    raw.write_text('{}')
    assert not guard.check_derived({'labels': []}, [model_hash(work)], 'external_llm').allowed


def test_actual_prompt_bytes_reject_different_metadata(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    guard = UsageGuard(path)
    payload = work.model_dump(mode='json')
    payload['evidence'] = [e.model_dump(mode='json') for e in work.evidence if e.llm_input_allowed]
    prompt = ('instruction\nBEGIN_LOCAL_DATA_JSON\n'+json.dumps({'work':payload})+'\nEND_LOCAL_DATA_JSON').encode()
    receipt = guard.assert_prompt_allowed(prompt, [work])
    assert receipt.prompt_sha256 == hashlib.sha256(prompt).hexdigest()
    payload['authors'] = ['Unproven person']
    with pytest.raises(UsageDenied):
        guard.assert_prompt_allowed(('BEGIN_LOCAL_DATA_JSON\n'+json.dumps({'work':payload})+'\nEND_LOCAL_DATA_JSON').encode(), [work])


def test_derived_payload_cannot_relabel_old_input_as_new_origin(tmp_path):
    work, path, *_ = fixture_ledger(tmp_path)
    assert not UsageGuard(path).check_derived({'input_hash': '0'*64, 'labels': []}, [model_hash(work)], 'external_llm').allowed
