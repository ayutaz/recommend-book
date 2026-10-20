"""Fail-closed purpose checks over complete immutable inputs and provenance.

This is an admission guard, not a legal certification. A stored allowed flag or
an obligation's name never substitutes for source and fulfillment evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sqlite3
import importlib.util
from xml.etree import ElementTree
from datetime import datetime, timezone
from urllib.parse import quote

import mwparserfromhell

from pydantic import BaseModel, Field

from recommend_book.jobs import canonical_json
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.ingestion.wikipedia import section_text
from recommend_book.wikipedia_prose import screen_section
from recommend_book.ingestion.wikidata import claim_values, LANGUAGE_CODES

GUARD_VERSION = 'usage-guard-v3.7'
METADATA_FIELDS = ('title', 'authors', 'original_language', 'first_publication_year', 'region', 'series')


def _wikidata_title_values(entity):
    """Exact published names; rank does not establish a title's original role.

    Match WikidataAdapter's retained normal and preferred P1476 statements.
    Other factual properties continue to use claim_values' best-rank rule.
    """
    values = {(v['value'], v.get('language', language))
              for language, v in entity.get('labels', {}).items()}
    values.update((v['value'], v.get('language', language))
                  for language, group in entity.get('aliases', {}).items() for v in group)
    for statement in entity.get('claims', {}).get('P1476', []):
        snak = statement.get('mainsnak', {})
        if statement.get('rank') == 'deprecated' or snak.get('snaktype', 'value') != 'value':
            continue
        value = snak.get('datavalue', {}).get('value')
        if isinstance(value, dict) and isinstance(value.get('text'), str):
            values.add((value['text'], value.get('language')))
    return values


def model_hash(value) -> str:
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


class UsageDecision(BaseModel):
    allowed: bool = False
    usage: str
    guard_version: str = GUARD_VERSION
    ledger_path: str
    ledger_sha256: str | None = None
    input_hashes: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    material_decisions: list[dict] = Field(default_factory=list)
    prompt_sha256: str | None = None
    payload_sha256: str | None = None
    proof_hashes: list[str] = Field(default_factory=list)

    @property
    def input_hash(self):
        return self.input_hashes[0] if len(self.input_hashes) == 1 else None


class UsageDenied(ValueError):
    def __init__(self, decision):
        self.decision = decision
        super().__init__('Usage denied: ' + '; '.join(decision.reasons))


def _record(binding):
    if not Path(binding['raw_path']).is_absolute() or not Path(binding['database_path']).is_absolute():
        raise ValueError('source raw/database path must be absolute')
    connection = sqlite3.connect(Path(binding['database_path']).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        row = connection.execute('SELECT record_json, raw_path, raw_sha256 FROM source_records WHERE version_id=?', (binding['source_version_id'],)).fetchone()
    finally:
        connection.close()
    if row is None or model_hash(json.loads(row[0])) != binding['record_sha256']:
        raise ValueError('source version record missing or changed')
    if row[1] != binding['raw_path'] or row[2] != binding['raw_sha256']:
        raise ValueError('source raw binding changed')
    return SourceRecord.model_validate_json(row[0])


def source_binding(database_path, source_version_id):
    connection = sqlite3.connect(Path(database_path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        row = connection.execute('SELECT record_json,raw_path,raw_sha256 FROM source_records WHERE version_id=?', (source_version_id,)).fetchone()
    finally:
        connection.close()
    if row is None:
        raise ValueError('source version absent')
    if not Path(row[1]).is_absolute():
        raise ValueError('source raw path must be absolute')
    return {'database_path': str(Path(database_path).resolve()), 'source_version_id': source_version_id,
            'record_sha256': model_hash(json.loads(row[0])), 'raw_path': row[1], 'raw_sha256': row[2]}


def _values(record, field):
    if field == 'title':
        return [t.value for t in record.titles]
    if field == 'authors':
        return [c.name for c in record.contributors if c.role == 'author']
    if field == 'region':
        return record.region
    return [getattr(record, field)]


def build_input_entry(work: WorkInput, sources: dict, *, input_ref: dict | None = None):
    """Create bindings, not permission. The guard independently checks each use.

Callers may use this helper for fixtures. Production binds an existing or newly
licensed snapshot via input_ref rather than duplicating held prose in the ledger.
"""
    records = {key: _record(binding) for key, binding in sources.items()}
    metadata = {}
    for field in METADATA_FIELDS:
        value = getattr(work, field)
        candidates = value if isinstance(value, list) else [value]
        metadata[field] = {'value_sha256': model_hash(value),
                           'origins': {str(index): [key for key, record in records.items() if item in _values(record, field)] for index, item in enumerate(candidates)},
                           'empty': value is None or value == [] or value == ''}
    evidence = {ev.evidence_id: {'model_sha256': model_hash(ev), 'source_versions': [key for key, record in records.items() if ev in record.evidence]} for ev in work.evidence}
    result = {'work_id': work.work_id, 'expression_id': work.expression_id, 'source_record_ids': work.source_record_ids,
              'sources': sources, 'metadata': metadata, 'evidence': evidence}
    result['input_ref' if input_ref else 'input_snapshot'] = input_ref or work.model_dump(mode='json')
    return result


def owned_observation_binding(database_path):
    """Private manual-import observations only; no API tables are read."""
    path = Path(database_path).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    try:
        records = [dict(r) for r in db.execute('''WITH ranked AS (
          SELECT o.*, i.pages_json, i.observed_at, i.imported_at,
          ROW_NUMBER() OVER(PARTITION BY o.ncode ORDER BY i.observed_at DESC,i.imported_at DESC,i.snapshot_id DESC) AS rank
          FROM rating_observations o JOIN imports i USING(snapshot_id))
          SELECT * FROM ranked WHERE rank=1 ORDER BY ncode''')]
    finally:
        db.close()
    rows, origins = [], {}
    for r in records:
        if type(r['my_rating']) not in (int, float) or not 1 <= r['my_rating'] <= 5:
            raise ValueError('owned observation rating invalid')
        code = r['ncode'].lower()
        rows.append({'work_id': 'owned:' + code, 'title': r['title'], 'rating': float(r['my_rating']),
                     'snapshot_id': r['snapshot_id'], 'features': {}, 'popularity': None, 'author_ids': [],
                     'series_ids': [], 'external_ids': [], 'read_status': 'unknown', 'feature_provenance': {}})
        pages = json.loads(r['pages_json'])
        pages = pages if isinstance(pages, list) else pages.get('pages', [])
        if not pages or any(p.get('source_url') for p in pages):
            raise ValueError('manual owner import provenance does not reproduce')
        origins['narou:' + code] = {'snapshot_id': r['snapshot_id'], 'source_page': r['source_page'],
                                  'row_number': r['row_number'], 'observation_sha256': model_hash(r),
                                  'original_html_rechecked': False}
    return {'database_path': str(path), 'database_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'input_sha256': model_hash(rows), 'origins': origins, 'rows_n': len(rows)}


class UsageGuard:
    def __init__(self, ledger_path, *, registry=None, registry_path=None, root=None, expected_ledger_sha256=None):
        self.ledger_path = Path(ledger_path).resolve()
        self.registry = registry
        self.registry_path = Path(registry_path).resolve() if registry_path is not None else None
        self.root = Path(root).resolve() if root is not None else Path.cwd().resolve()
        self.expected_ledger_sha256 = expected_ledger_sha256

    def _load(self):
        payload = self.ledger_path.read_bytes()
        if self.expected_ledger_sha256 is not None and hashlib.sha256(payload).hexdigest() != self.expected_ledger_sha256:
            raise ValueError('prelaunch frozen ledger hash changed')
        ledger = json.loads(payload)
        if ledger['version'] != 'usage-ledger-v3':
            raise ValueError('unsupported ledger version')
        registry_path = self.registry_path or (Path(ledger['registry_path']).resolve() if ledger.get('registry_path') else None)
        if registry_path:
            if self.registry_path and Path(ledger['registry_path']).resolve() != self.registry_path:
                raise ValueError('selected registry path differs from ledger')
            if hashlib.sha256(registry_path.read_bytes()).hexdigest() != ledger['registry_file_sha256']:
                raise ValueError('frozen source registry file changed')
            registry = RightsRegistry.from_file(registry_path)
        else:
            registry = self.registry or RightsRegistry.default()
        if ledger['registry_sha256'] != model_hash([p.model_dump(mode='json') for p in registry.policies.values()]):
            raise ValueError('policy registry changed')
        return ledger, registry, hashlib.sha256(payload).hexdigest()

    def assert_execution_allowed(self, config_sha256, usage='external_llm'):
        decision = UsageDecision(usage=usage, ledger_path=str(self.ledger_path), payload_sha256=config_sha256)
        try:
            ledger, _, decision.ledger_sha256 = self._load()
            if usage != 'external_llm' or not re.fullmatch('[0-9a-f]{64}', config_sha256):
                raise ValueError('invalid native execution purpose/config hash')
            from recommend_book.classification import native_attempt
            recipe = ledger['execution_recipes'][config_sha256]
            path = Path(native_attempt.__file__).resolve()
            if path != Path(recipe['verifier_path']).resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != recipe['verifier_sha256']:
                raise ValueError('preauthorized native verifier implementation changed')
            decision.proof_hashes = [recipe['verifier_sha256']]
            decision.allowed = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            decision.reasons.append(str(exc))
        if not decision.allowed:
            raise UsageDenied(decision)
        return decision

    def check_private_reference_aggregate(self, payload, origin_ids, *, context):
        decision = UsageDecision(usage='local_storage', ledger_path=str(self.ledger_path), payload_sha256=model_hash(payload))
        try:
            ledger, _, decision.ledger_sha256 = self._load()
            if context != 'private_local_owned_title_ranking':
                raise ValueError('unapproved private reference context')
            if set(payload) != {'mode', 'input_sha256', 'preregistration_sha256', 'aggregate_only'} or payload['mode'] != 'owned_title_fixed_ranking' or payload['aggregate_only'] is not True:
                raise ValueError('private aggregate precomputation payload differs')
            scope = ledger['owned_observation_scope']
            observed = owned_observation_binding(scope['database_path'])
            if observed != scope['binding'] or len(origin_ids) != len(set(origin_ids)) or set(origin_ids) != set(observed['origins']):
                raise ValueError('whole owner observation roster/source binding differs')
            if observed['rows_n'] != 335 or payload['input_sha256'] != observed['input_sha256']:
                raise ValueError('actual fixed private input bytes differ')
            for artifact in (scope['manual_source_document'], scope['importer_recipe'], scope['preregistration']):
                raw = Path(artifact['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                    raise ValueError('owner provenance/spec artifact changed')
                decision.proof_hashes.append(artifact['sha256'])
            readme = Path(scope['manual_source_document']['path']).read_text(encoding='utf-8')
            if not all(v in readme for v in ('自分が評価', 'HTML', '335', 'API')):
                raise ValueError('manual source document origin not reproduced')
            if payload['preregistration_sha256'] != scope['preregistration']['sha256']:
                raise ValueError('private result-independent specification differs')
            spec = json.loads(Path(scope['preregistration']['path']).read_bytes())
            if spec.get('api_fields_used') != [] or spec.get('annotations_reused') != 0 or spec.get('parameter_search_n') != 0:
                raise ValueError('private scope includes API or annotation material/selection')
            if scope['authorization_basis'] != 'explicit user instruction: readonly reuse of personal ratings in private local fixed-title ranking':
                raise ValueError('owner private authorization scope absent')
            decision.proof_hashes.append(observed['database_sha256'])
            decision.material_decisions = [{'material': 'owned manual-import observations', 'allowed': True,
                                           'context': context, 'n': 335, 'input_sha256': observed['input_sha256'],
                                           'origin_ids_sha256': model_hash(sorted(origin_ids)), 'origin_n': len(origin_ids),
                                           'raw_html_rechecked': False, 'api_fields_used': [],
                                           'external_llm_allowed': False, 'public_display_allowed': False,
                                           'basis': 'owner-authorized private lexical arithmetic; no historical AI permission claim'}]
            decision.allowed = True
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            decision.reasons.append(str(exc))
        return decision

    def _evaluate(self, work, usage, ledger, registry):
        key = model_hash(work)
        if usage not in USAGES:
            raise ValueError('unknown usage')
        if key in ledger.get('revoked_input_hashes', []):
            raise ValueError('complete input revoked')
        entry = ledger['inputs'][key]
        if entry['work_id'] != work.work_id or entry['expression_id'] != work.expression_id:
            raise ValueError('internal identity differs')
        if entry['source_record_ids'] != work.source_record_ids or set(entry['sources']) != set(work.source_record_ids):
            raise ValueError('complete source version references differ')
        if set(entry['metadata']) != set(METADATA_FIELDS):
            raise ValueError('complete metadata field bindings missing')
        if len({e.evidence_id for e in work.evidence}) != len(work.evidence) or set(entry['evidence']) != {e.evidence_id for e in work.evidence}:
            raise ValueError('complete evidence model bindings differ')
        records, proofs, reasons, materials = {}, [], [], []
        for ref, binding in entry['sources'].items():
            if binding['source_version_id'] != ref:
                raise ValueError('source version ID differs')
            record = _record(binding)
            raw = Path(record.raw_path).read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.raw_sha256:
                raise ValueError('raw source hash changed: ' + ref)
            self._raw_identity(record, raw)
            records[ref] = record
            proofs.extend([binding['record_sha256'], record.raw_sha256])
        for record in records.values():
            if record.source == 'standardebooks':
                bridge = record.extra['authority_bridge']
                if not any(r.source == 'wikidata' and r.provider_id == 'wikidata' and r.record_kind == 'work'
                           and r.source_id == bridge['wikidata_work_id'] and r.raw_path == bridge['target_raw_path']
                           and r.raw_sha256 == bridge['target_raw_sha256'] for r in records.values()):
                    raise ValueError('Standard Ebooks work authority not bound in complete input')
        for field in METADATA_FIELDS:
            value, binding = getattr(work, field), entry['metadata'][field]
            if model_hash(value) != binding['value_sha256']:
                raise ValueError('metadata full value hash differs: ' + field)
            empty = value is None or value == [] or value == ''
            if binding['empty'] != empty:
                raise ValueError('metadata empty claim differs: ' + field)
            if empty:
                materials.append({'material': 'metadata.'+field, 'allowed': True, 'basis': 'no supplied external value'})
                continue
            values = value if isinstance(value, list) else [value]
            if set(binding['origins']) != {str(i) for i in range(len(values))}:
                raise ValueError('metadata origin value coverage missing: ' + field)
            for index, item in enumerate(values):
                permitted = False
                candidates = binding['origins'][str(index)]
                for ref in candidates:
                    record = records[ref]
                    if item not in _values(record, field):
                        raise ValueError('metadata does not match immutable source field: ' + field)
                    # Wikipedia bibliographies copied from WD are not silently
                    # relicensed. A direct WD origin is needed for this field.
                    policy = registry.policies[record.rights_policy_id]
                    if policy.source != record.source or policy.provider_id not in (record.provider_id, '*'):
                        raise ValueError('metadata source/provider policy mismatch')
                    if 'bibliographic' in policy.fields and policy.usages.get(usage) == 'allowed':
                        raw_proof = self._raw_metadata(record, field, item, proofs)
                        self._official(policy, ledger, proofs)
                        permitted = True
                    elif policy.source in ('conssa', 'cligs-french') and 'bibliographic' in policy.fields and policy.usages.get(usage) == 'conditional':
                        if field != 'original_language' or item != {'conssa': 'es', 'cligs-french': 'fr'}[policy.source] or not record.evidence:
                            raise ValueError('corpus opaque metadata value not reproduced')
                        self._corpus(record.evidence[0], [record], ledger, usage, proofs)
                        raw_proof = {'source_id': record.source_id, 'raw_sha256': record.raw_sha256, 'locator': 'teiHeader.language', 'value_sha256': model_hash(item)}
                        permitted = True
                materials.append({'material': 'metadata.'+field+'['+str(index)+']', 'allowed': permitted, 'source_versions': candidates,
                                  'raw_reproduction': raw_proof if permitted else None})
                if not permitted:
                    reasons.append('metadata purpose unproved: ' + field)
        for evidence in work.evidence:
            binding = entry['evidence'][evidence.evidence_id]
            if binding['model_sha256'] != model_hash(evidence):
                raise ValueError('evidence full model hash differs: ' + evidence.evidence_id)
            if not binding['source_versions']:
                reasons.append('evidence immutable source absent: ' + evidence.evidence_id)
                continue
            for ref in binding['source_versions']:
                if evidence not in records[ref].evidence:
                    raise ValueError('evidence does not equal immutable source model')
            policy = registry.policies[evidence.rights_policy_id]
            allowed = False
            legacy = evidence.evidence_id.endswith(':wikipedia-prose-v2') or policy.source == 'wikisource'
            state = policy.usages.get(usage, 'unresolved')
            if legacy and usage in ('external_llm', 'display', 'redistribution', 'commercial'):
                reasons.append('legacy prose not reapproved: ' + evidence.evidence_id)
            elif evidence.kind not in policy.fields:
                reasons.append('field not covered by policy: ' + evidence.evidence_id)
            elif state == 'allowed':
                self._official(policy, ledger, proofs)
                # Mixed-source enriched records require the prose-specific
                # verifier below, never the enclosing record's CC0 policy.
                allowed = all(records[ref].source == policy.source and records[ref].provider_id == policy.provider_id and records[ref].rights_policy_id == evidence.rights_policy_id for ref in binding['source_versions'])
                if allowed:
                    for ref in binding['source_versions']:
                        self._raw_evidence(records[ref], evidence, proofs)
            elif state == 'conditional' and policy.policy_id == 'wikipedia-screened-prose' and evidence.evidence_id.endswith((':autonomous-enrichment-v1', ':screened-paragraphs-v1', ':screened-infobox-genre-v1')):
                try:
                    self._official(policy, ledger, proofs)
                    self._wikipedia(evidence, [records[ref] for ref in binding['source_versions']], proofs)
                    allowed = True
                except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
                    reasons.append('Wikipedia fulfillment unproved: ' + str(exc))
            elif state == 'conditional' and policy.policy_id in ('conssa-reviewed-original-v3', 'cligs-french-reviewed-original-v3'):
                try:
                    self._corpus(evidence, [records[ref] for ref in binding['source_versions']], ledger, usage, proofs)
                    allowed = True
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    reasons.append('Individual corpus fulfillment unproved: ' + str(exc))
            if not allowed:
                reasons.append('evidence purpose unproved: ' + evidence.evidence_id)
            materials.append({'material': evidence.evidence_id, 'allowed': allowed, 'policy_id': policy.policy_id,
                              'obligations': policy.obligations, 'source_versions': binding['source_versions'],
                              'license': evidence.license, 'attribution': evidence.attribution if allowed and policy.obligations else {}})
        return sorted(set(reasons)), materials, sorted(set(proofs))

    def _corpus(self, evidence, records, ledger, usage, proofs):
        if len(records) != 1:
            raise ValueError('individual corpus source version not unique')
        record = records[0]
        proof = ledger['corpus_proofs'][evidence.evidence_id]
        verifier = ledger['corpus_verifiers'][record.source]
        if record.source == 'conssa':
            from recommend_book import next_reference
            path = Path(next_reference.__file__).resolve()
            function = next_reference.verify_conssa_evidence
        else:
            path = self.root / 'data/autonomous-next-v3/reference/external-genre/jurisdiction-audit-v1/french_verifier.py'
            if path.resolve() != Path(verifier['path']).resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != verifier['sha256']:
                raise ValueError('frozen French corpus source verifier changed')
            spec = importlib.util.spec_from_file_location('v3_frozen_french_verifier', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            function = module.verify_french_evidence
        if path != Path(verifier['path']).resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != verifier['sha256']:
            raise ValueError('frozen corpus source verifier changed')
        reproduced = function(record, evidence, proof, root=self.root)
        if reproduced.get('reproduced') is not True:
            raise ValueError('individual original body source facts not reproduced')
        reproduced_hashes = reproduced['proof_hashes']
        proofs.extend(reproduced_hashes.values() if isinstance(reproduced_hashes, dict) else reproduced_hashes)
        proofs.append(verifier['sha256'])
        if record.source == 'conssa':
            reproduced['facts'] = self._conssa_authority(record, proof, reproduced['facts'], proofs)
        if usage not in ('acquire', 'local_storage', 'external_llm'):
            raise ValueError('private corpus receipt does not authorize commercial/public redistribution/display')
        facts = reproduced['facts']
        if facts['rights_reviewed_regions'] != ['Japan', 'United States'] or facts['worldwide_public_domain_claim'] is not False:
            raise ValueError('original text law review scope differs')
        years = [facts['author_death_year'], facts['first_publication_year'], facts['underlying_print_edition_year']]
        if record.source == 'conssa':
            invalid = (any(type(year) is not int for year in years) or
                       facts.get('country_is_citizenship') is not False or
                       type(years[1]) is int and type(years[2]) is int and years[2] < years[1])
            if not invalid:
                invalid = max(years) + 62 >= 2018 or max(years[1:]) + 95 >= 2026
        else:
            invalid = any(type(year) is not int or year > 1900 for year in years) or max(years) + 62 >= 2018 or facts['underlying_print_edition_year'] + 95 >= 1996
        if invalid:
            raise ValueError('conservative nonreviving JP/US original term bounds not established')
        review_path = Path(proof['rights_review']['path'])
        review_path = review_path if review_path.is_absolute() else self.root / review_path
        review_raw = review_path.read_bytes()
        if hashlib.sha256(review_raw).hexdigest() != proof['rights_review']['sha256']:
            raise ValueError('individual rights review SHA differs')
        proofs.append(proof['rights_review']['sha256'])
        reviews = [json.loads(row) for row in review_raw.decode('utf-8').splitlines()]
        review = next(row for row in reviews if row['reference_id'] == record.source_id)
        legal_urls = {'japan_terms': 'https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html',
                      'japan_wartime': 'https://www.jasrac.or.jp/aboutus/wartime/pdf/wartime.pdf',
                      'us_terms': 'https://www.copyright.gov/circs/circ38b.pdf',
                      'ccby_license': 'https://creativecommons.org/licenses/by/4.0/'}
        if record.source == 'conssa':
            checked = datetime.fromisoformat(review['reviewed_at'])
            now = datetime.now(timezone.utc)
            if checked.tzinfo is None or checked > now or (now - checked).days > 7 or review['regions_reviewed'] != ['Japan', 'United States'] or review['worldwide_public_domain_claim'] is not False:
                raise ValueError('individual corpus review current date/scope differs')
            legal_urls = {'japan_terms': legal_urls['japan_terms'], 'japan_wartime': legal_urls['japan_wartime'],
                'us_uraa': 'https://www.copyright.gov/circs/circ38b.pdf',
                'us_duration': 'https://www.copyright.gov/title17/92chap3.html',
                'us_edition_status': 'https://www.copyright.gov/circs/circ22.pdf',
                'ccby_license': 'https://creativecommons.org/licenses/by/4.0/legalcode.en',
                'pdm_declaration': 'https://creativecommons.org/publicdomain/mark/1.0/'}
        legal = {artifact['role']: artifact for artifact in review['legal_primary_artifacts']}
        if set(legal) != set(legal_urls):
            raise ValueError('individual term/legal/license primary artifacts incomplete')
        for role, url in legal_urls.items():
            artifact = legal[role]
            artifact_path = Path(artifact['path'])
            artifact_path = artifact_path if artifact_path.is_absolute() else review_path.parent / artifact_path
            raw = artifact_path.read_bytes()
            if artifact['url'] != url or hashlib.sha256(raw).hexdigest() != artifact['sha256'] or len(raw) != artifact['bytes']:
                raise ValueError('individual legal primary artifact URL/hash/bytes differ')
            proofs.append(artifact['sha256'])
        license_artifact = review['license_primary_artifact']
        license_path = review_path.parent / license_artifact['path']
        license_raw = license_path.read_bytes()
        expected_readme = record.source_url.rsplit('/master/master/', 1)[0] + '/README.md' if record.source == 'conssa' else record.source_url.rsplit('/tei/', 1)[0] + '/readme.md'
        if hashlib.sha256(license_raw).hexdigest() != license_artifact['sha256'] or license_artifact['url'] != expected_readme or 'cc by' not in license_raw.decode('utf-8').lower().replace('-', ' '):
            raise ValueError('corpus primary markup license not reproduced')
        proofs.append(license_artifact['sha256'])
        scope = ledger['corpus_review_scope']
        for artifact in (scope['policy'], scope['provider_observation']):
            raw = Path(artifact['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                raise ValueError('current scoped corpus admission basis changed')
            proofs.append(artifact['sha256'])
        observed = json.loads(Path(scope['provider_observation']['path']).read_bytes())
        if observed.get('auth_method') != 'chatgpt' or observed.get('model_provider') != 'openai' or observed.get('chatgpt_plan_type') != 'pro' or observed.get('configured_provider_override_present') is not False or observed.get('runtime_ignores_user_config') is not True:
            raise ValueError('actual corpus destination mode differs from reviewed scope')

    def _conssa_authority(self, record, proof, body_facts, proofs):
        """Independently reproduce authority IDs/death; never promote nationality."""
        descriptor = proof['author_primary']
        path = self.root / 'data/autonomous-next-v3/rights/conssa-individual-review-v1/authority_verifier.py'
        if path.resolve() != Path(descriptor['verifier']['path']).resolve() or hashlib.sha256(path.read_bytes()).hexdigest() != descriptor['verifier']['sha256']:
            raise ValueError('individual authority verifier SHA/path differs')
        def select(artifact, predicate):
            item = Path(artifact['path']); item = item if item.is_absolute() else self.root / item
            raw = item.read_bytes()
            if hashlib.sha256(raw).hexdigest() != artifact['sha256']:
                raise ValueError('individual authority sidecar SHA differs')
            proofs.append(artifact['sha256'])
            rows = [json.loads(row) for row in raw.decode('utf-8').splitlines()]
            matches = [row for row in rows if predicate(row)]
            if len(matches) != 1:
                raise ValueError('individual authority selector not unique')
            return matches[0]
        work = select(descriptor['work_crosswalk'], lambda row: row['reference_id'] == record.source_id)
        fact = select(descriptor['authors'], lambda row: row['author_group'] == work['author_group'])
        link = select(descriptor['id_links'], lambda row: row['author_group'] == work['author_group'])
        if work['tei_sha256'] != record.raw_sha256 or work['editions'] != body_facts['editions']:
            raise ValueError('author authority source/edition scope differs')
        spec = importlib.util.spec_from_file_location('v3_frozen_conssa_authority', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        reproduced = module.verify_authority(work, fact, link, root=self.root)
        if reproduced.get('reproduced') is not True or reproduced.get('permission_decision_made') is not False:
            raise ValueError('primary authority facts not independently reproduced')
        proofs.extend(reproduced['proof_hashes']); proofs.append(descriptor['verifier']['sha256'])
        for artifact in (fact['official_api_proof'], fact['official_license_proof']):
            item = Path(artifact['path']); item = item if item.is_absolute() else self.root / item
            if hashlib.sha256(item.read_bytes()).hexdigest() != artifact['sha256']:
                raise ValueError('primary authority API/licence proof SHA differs')
            proofs.append(artifact['sha256'])
        result = reproduced['facts']
        return {**result, 'rights_reviewed_regions': ['Japan', 'United States'], 'worldwide_public_domain_claim': False,
                'first_publication_year': body_facts['editions']['edition-first']['year'],
                'underlying_print_edition_year': body_facts['editions']['print-source']['year']}

    def _official(self, policy, ledger, proofs):
        artifacts = ledger.get('official_conditions', {}).get(policy.policy_id, [])
        if not artifacts:
            raise ValueError('current official conditions artifact absent: ' + policy.policy_id)
        if policy.policy_id == 'standardebooks-editorial-cc0':
            from recommend_book.ingestion.standardebooks import ABOUT_URL, MANUAL_URL
            if (policy.source, policy.provider_id, policy.license) != ('standardebooks', 'standardebooks', 'CC0-1.0') or set(policy.fields) != {'bibliographic', 'synopsis'}:
                raise ValueError('Standard Ebooks editorial policy scope differs')
            if {a['source_url'] for a in artifacts} != {ABOUT_URL, MANUAL_URL}:
                raise ValueError('both Standard Ebooks primary scope artifacts required')
        for artifact in artifacts:
            if artifact['source_url'] not in policy.evidence_urls or not artifact.get('verified_at'):
                raise ValueError('official source/date not bound to policy')
            verified = datetime.fromisoformat(artifact['verified_at'].replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if verified.tzinfo is None or verified > now or (now - verified).days > 7:
                raise ValueError('official condition check is not current timezone-qualified date')
            if artifact['raw_sha256'] in proofs and policy.policy_id != 'standardebooks-editorial-cc0':
                continue  # verified earlier in this same admission call
            path = Path(artifact['raw_path'])
            path = path if path.is_absolute() else self.root / path
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != artifact['raw_sha256']:
                raise ValueError('official conditions raw hash changed')
            text = payload.decode('utf-8').lower()
            clauses = {'wikidata-cc0': ('cc0',), 'openlibrary-metadata': ('public domain',),
                       'wikipedia-screened-prose': ('attribution', 'sharealike'),
                       'wikipedia-bibliography': ('attribution', 'sharealike')}.get(policy.policy_id)
            if policy.policy_id == 'standardebooks-editorial-cc0':
                clauses = (('content produced by or for standard ebooks', 'cc0 1.0 universal public domain dedication', 'https://creativecommons.org/publicdomain/zero/1.0/')
                           if artifact['source_url'] == ABOUT_URL else
                           ('dc:description', 'under no circumstances can a producer copy and paste from wikipedia', 'cc0 public domain dedication'))
            if clauses is None or not all(clause in text for clause in clauses):
                raise ValueError('required primary license clauses not reproduced')
            proofs.append(artifact['raw_sha256'])

    def _linked_label(self, record, qid, proofs):
        origin = record.extra.get('linked_label_provenance', {}).get(qid)
        if not origin:
            entity = json.loads(Path(record.raw_path).read_bytes()).get('entities', {}).get(qid)
            if entity and entity.get('id') == qid:
                return {v['value'] for v in entity.get('labels', {}).values()}
            raise ValueError('linked entity label raw proof absent; authority ID is not a name')
        raw = Path(origin['raw_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != origin['raw_sha256']:
            raise ValueError('linked author/language/region/series raw hash changed')
        entity = json.loads(raw)['entities'][qid]
        if entity.get('id') != qid:
            raise ValueError('linked entity identity differs')
        values = {v['value'] for v in entity.get('labels', {}).values()}
        if origin['label'] not in values:
            raise ValueError('linked label is not reproduced by raw entity')
        proofs.append(origin['raw_sha256'])
        return {origin['label']}

    def _raw_metadata(self, record, field, item, proofs):
        """Reproduce adopted factual values; immutable normalization is insufficient."""
        raw = Path(record.raw_path).read_bytes()
        if record.source == 'wikidata':
            entity = json.loads(raw)['entities'][record.source_id]
            def qids(prop):
                return [v['id'] for v in claim_values(entity, prop) if isinstance(v, dict) and 'id' in v]
            locator = {'title': 'labels;aliases;claims.P1476', 'authors': 'claims.P50;linked.labels',
                       'original_language': 'claims.P407', 'first_publication_year': 'claims.P577',
                       'region': 'claims.P495;linked.labels', 'series': 'claims.P179;linked.labels'}[field]
            if field == 'title':
                values = {text for text, language in _wikidata_title_values(entity)}
            elif field == 'authors':
                values = set()
                for contributor in record.contributors:
                    if contributor.role != 'author':
                        continue
                    ids = [i.value for i in contributor.identifiers if i.source == 'wikidata' and i.scope == 'agent']
                    if len(ids) != 1 or ids[0] not in qids('P50'):
                        raise ValueError('author authority does not occur in original P50')
                    if contributor.name not in self._linked_label(record, ids[0], proofs):
                        raise ValueError('author name not reproduced by authority raw labels')
                    values.add(contributor.name)
            elif field == 'original_language':
                refs = qids('P407')
                values = {LANGUAGE_CODES.get(refs[0])} if len(refs) == 1 else {None}
            elif field == 'first_publication_year':
                years = [int(v['time'][1:5]) for v in claim_values(entity, 'P577') if isinstance(v, dict) and v.get('precision', 0) >= 9 and re.match(r'^\+\d{4}-', v.get('time', ''))]
                values = {min(years)} if years else {None}
            elif field == 'region':
                values = set().union(*(self._linked_label(record, q, proofs) for q in qids('P495')))
            else:
                labels = [record.extra.get('linked_label_provenance', {}).get(q, {}).get('label', q) for q in qids('P179')]
                for q in qids('P179'):
                    self._linked_label(record, q, proofs)
                values = {'; '.join(labels)} if labels else {None}
        elif record.source == 'standardebooks':
            from recommend_book.ingestion.standardebooks import verify_record
            reproduced = verify_record(record, raw)
            if field not in ('title', 'authors'):
                raise ValueError('Standard Ebooks metadata scope supports title/author only')
            values = {reproduced.titles[0].value} if field == 'title' else {reproduced.contributors[0].name}
            locator = '/opf:package/opf:metadata/' + ('dc:title' if field == 'title' else 'dc:creator')
            proofs.append(record.extra['authority_bridge']['target_raw_sha256'])
        elif record.source == 'openlibrary':
            doc = json.loads(raw.rstrip(b'\r\n').split(b'\t', 4)[-1])
            locator = field
            if field == 'title':
                values = {doc.get('title'), *doc.get('alternative_titles', [])}
            elif field == 'first_publication_year':
                match = re.search(r'\b(\d{4})\b', doc.get('first_publish_date', ''))
                values = {int(match.group(1)) if match else None}
            elif field == 'authors':
                # Names require an independently bound author authority, never a label supplied by the work alone.
                raise ValueError('Open Library author raw authority proof absent')
            else:
                values = {None}
        else:
            raise ValueError('raw metadata reproduction unsupported: ' + record.source)
        if item not in values:
            raise ValueError('metadata value not reproduced by raw source: ' + field)
        return {'source_id': record.source_id, 'raw_sha256': record.raw_sha256, 'locator': locator, 'value_sha256': model_hash(item)}

    def _raw_evidence(self, record, evidence, proofs):
        if record.source == 'standardebooks':
            from recommend_book.ingestion.standardebooks import verify_record
            reproduced = verify_record(record, Path(record.raw_path).read_bytes())
            if evidence != reproduced.evidence[0]:
                raise ValueError('Standard Ebooks evidence not reproduced by editorial description')
            proofs.append(record.extra['authority_bridge']['target_raw_sha256'])
        elif record.source == 'wikidata':
            entity = json.loads(Path(record.raw_path).read_bytes())['entities'][record.source_id]
            def refs(prop):
                return [v['id'] for v in claim_values(entity, prop) if isinstance(v, dict) and 'id' in v]
            def label(qid):
                values = self._linked_label(record, qid, proofs)
                chosen = record.extra.get('linked_label_provenance', {}).get(qid, {}).get('label', qid)
                if chosen not in values:
                    raise ValueError('factual evidence label not reproduced')
                return chosen
            locator = evidence.locator or ''
            if locator.startswith('descriptions.'):
                expected = entity.get('descriptions', {}).get(locator.split('.')[1], {}).get('value')
            elif locator == 'claims.P136;claims.P921':
                expected = '; '.join(label(q) for q in refs('P136') + refs('P921'))
            elif locator in ('claims.P674', 'claims.P840', 'claims.P179'):
                prefix = {'claims.P674': 'Characters (not necessarily protagonists or narrators): ',
                          'claims.P840': 'Narrative locations (not origin regions): ', 'claims.P179': 'Series: '}[locator]
                expected = prefix + '; '.join(label(q) for q in refs(locator.split('.')[1]))
            elif locator == 'labels;claims.P50;claims.P31;claims.P7937':
                title = record.titles[0].value
                self._raw_metadata(record, 'title', title, proofs)
                for contributor in record.contributors:
                    prop = {'author': 'P50', 'translator': 'P655', 'editor': 'P98', 'illustrator': 'P110'}.get(contributor.role)
                    ids = [i.value for i in contributor.identifiers if i.source == 'wikidata' and i.scope == 'agent']
                    if prop is None or len(ids) != 1 or ids[0] not in refs(prop) or contributor.name not in self._linked_label(record, ids[0], proofs):
                        raise ValueError('bibliographic contributor not reproduced by raw authority')
                expected = f"Title: {title}; contributors: {'; '.join(c.name for c in record.contributors)}; Wikidata instance IDs: " + ', '.join(refs('P31')) + '; Creative work form IDs: ' + ', '.join(refs('P7937'))
            elif evidence.evidence_id == 'wd:' + record.source_id + ':title' and evidence.kind == 'bibliographic':
                self._raw_metadata(record, 'title', evidence.text, proofs)
                expected = evidence.text
            else:
                raise ValueError('Wikidata evidence raw recipe unsupported')
            if expected != evidence.text or evidence.source_url != record.source_url:
                raise ValueError('factual evidence text/source not reproduced by raw claims')
        elif record.source == 'openlibrary':
            doc = json.loads(Path(record.raw_path).read_bytes().rstrip(b'\r\n').split(b'\t', 4)[-1])
            if evidence.kind == 'subject' and evidence.text == '; '.join(doc.get('subjects', [])) and evidence.source_url == record.source_url:
                return
            raise ValueError('Open Library evidence raw recipe unsupported')
        else:
            raise ValueError('source evidence raw recipe unsupported')

    def _raw_identity(self, record, payload):
        if record.source == 'standardebooks':
            from recommend_book.ingestion.standardebooks import verify_record
            verify_record(record, payload)
        elif record.source == 'wikidata':
            entity = json.loads(payload).get('entities', {}).get(record.source_id)
            if not entity or entity.get('id') != record.source_id:
                raise ValueError('Wikidata raw identity differs')
            if 'identity_correction' in record.extra:
                from recommend_book.authority_identity_correction import verify_identity_correction
                verify_identity_correction(record)
        elif record.source == 'openlibrary':
            fields = payload.rstrip(b'\r\n').split(b'\t', 4)
            value = json.loads(fields[-1])
            if value.get('key') != record.source_id:
                raise ValueError('Open Library raw identity differs')
        elif record.source == 'wikipedia':
            pages = json.loads(payload)['query']['pages']
            pages = list(pages.values()) if isinstance(pages, dict) else pages
            if not any(p.get('pageid') == record.extra.get('page_id') and p.get('pageprops', {}).get('wikibase_item') == record.extra.get('wikibase_item_verified') for p in pages):
                raise ValueError('Wikipedia raw identity differs')
        elif record.source in ('conssa', 'cligs-french'):
            tree = ElementTree.fromstring(payload)
            namespace = {'tei': 'http://www.tei-c.org/ns/1.0'}
            ids = [''.join(node.itertext()).strip() for node in tree.findall('.//tei:teiHeader//tei:idno', namespace)]
            if record.source_id not in ids or record.provider_id != 'cligs':
                raise ValueError('corpus primary TEI identity differs')
        elif record.source not in ('wikisource',):
            raise ValueError('unsupported source identity verifier: ' + record.source)

    def _wikipedia(self, evidence, records, proofs):
        if len(records) != 1 or records[0].source != 'wikidata':
            raise ValueError('strict enrichment target is not unique immutable Wikidata source')
        record = records[0]
        attribution = evidence.attribution
        payload = Path(attribution['raw_path']).read_bytes()
        if hashlib.sha256(payload).hexdigest() != attribution['raw_sha256']:
            raise ValueError('prose raw hash changed')
        identity = attribution['identity_proof']
        if identity['wikidata_work_id'] != record.source_id or identity['target_raw_sha256'] != record.raw_sha256:
            raise ValueError('prose target raw identity differs')
        pages = json.loads(payload)['query']['pages']
        pages = list(pages.values()) if isinstance(pages, dict) else pages
        page = next(p for p in pages if p['pageid'] == attribution['page_id'])
        revision = page['revisions'][0]
        if page.get('ns') != 0 or page.get('pageprops', {}).get('wikibase_item') != record.source_id or 'disambiguation' in page.get('pageprops', {}):
            raise ValueError('prose page identity differs')
        if revision['revid'] != attribution['revision_id'] or identity['revision_id'] != revision['revid']:
            raise ValueError('prose immutable revision differs')
        entity = json.loads(Path(record.raw_path).read_bytes())['entities'][record.source_id]
        if entity['sitelinks'][evidence.language + 'wiki']['title'] != page['title']:
            raise ValueError('prose sitelink differs')
        text = revision['slots']['main'].get('content', revision['slots']['main'].get('*'))
        notices = re.compile(r'cc[ _-]?notice|source[ _-]?attribution|attribution|copyright|copyvio|copypaste|copy[ _-]?paste|1911|eb1911|dnb|public[ _-]?domain|pd[ _-]?notice|imported[ _-]?text|incorporates[ _-]?text|text[ _-]?incorporated', re.I)
        if any(notices.search(str(t.name)) for t in mwparserfromhell.parse(text).filter_templates(recursive=True)):
            raise ValueError('additional imported text attribution unproved')
        if evidence.evidence_id.endswith(':screened-infobox-genre-v1'):
            from recommend_book.wikipedia_paragraphs import verify_infobox_genre
            verify_infobox_genre(evidence, record, page)
            proofs.append(attribution['raw_sha256'])
            return
        paragraph_recipe = evidence.evidence_id.endswith(':screened-paragraphs-v1')
        if paragraph_recipe and ';section:' not in evidence.locator:
            raise ValueError('screened paragraph section locator missing')
        section = evidence.locator.split(';section:', 1)[1].split(';', 1)[0]
        screen = screen_section(section_text(text, section), article_wikitext=text, factual_titles=[page['title'], *[t.value for t in record.titles]])
        if paragraph_recipe:
            from recommend_book.wikipedia_paragraphs import VERSION, CHANGES, locator, selection_proof
            from recommend_book.autonomous_enrichment import SECTIONS
            if section not in SECTIONS.get(evidence.language, ['lead']):
                raise ValueError('screened paragraph section unsupported')
            if revision['slots']['main'].get('contentmodel', 'wikitext') != 'wikitext':
                raise ValueError('screened paragraph content model unsupported')
            if len(page['revisions']) != 1:
                raise ValueError('screened paragraph requires one immutable revision')
            if evidence.evidence_id != f'wp:{evidence.language}:{revision["revid"]}:{section}:{VERSION}' or evidence.locator != locator(revision['revid'], section):
                raise ValueError('screened paragraph recipe identity/locator differs')
            if evidence.kind != ('note' if section == 'lead' else 'synopsis'):
                raise ValueError('screened paragraph evidence kind differs')
            expected_identity = {'wikidata_work_id': record.source_id, 'pageprops_wikibase_item': page['pageprops']['wikibase_item'],
                                 'page_id': page['pageid'], 'revision_id': revision['revid'], 'language': evidence.language,
                                 'target_raw_path': record.raw_path, 'target_raw_sha256': record.raw_sha256,
                                 'target_raw_locator': 'entities.' + record.source_id}
            if canonical_json(identity) != canonical_json(expected_identity):
                raise ValueError('screened paragraph exact identity proof differs')
            if screen.article_blocked or screen.text != evidence.text or len(screen.text) < 20:
                raise ValueError('complete screened paragraphs not reproduced')
            if canonical_json(attribution.get('paragraph_selection')) != canonical_json(selection_proof(section_text(text, section), screen)):
                raise ValueError('screened paragraph omissions/proof differ')
            if attribution.get('changes') != CHANGES:
                raise ValueError('screened paragraph actual modification notice differs')
            if not {'attribution', 'share_alike', 'mark_changes', 'third_party_text_screened'} <= set(attribution.get('fulfilled_obligations', [])):
                raise ValueError('screened paragraph fulfilled obligations missing')
        elif screen.article_blocked or screen.rejected_paragraphs or screen.text != evidence.text or not screen.text:
            raise ValueError('complete clean section not reproduced')
        url = f'https://{evidence.language}.wikipedia.org/w/index.php?oldid={revision["revid"]}'
        history = f'https://{evidence.language}.wikipedia.org/w/index.php?title={quote(page["title"], safe="")}&action=history'
        if attribution['credit'] != 'Wikipedia contributors: ' + page['title'] or attribution['permalink'] != url or attribution['history_url'] != history or evidence.source_url != url:
            raise ValueError('exact revision attribution differs')
        if evidence.license != 'CC-BY-SA-4.0' or evidence.license_url != 'https://creativecommons.org/licenses/by-sa/4.0/':
            raise ValueError('licensed adaptation notice missing')
        if not paragraph_recipe and attribution['changes'] != 'selected complete clean section; references removed; wiki markup converted to plain text':
            raise ValueError('actual modification notice differs')
        if not {'attribution', 'share_alike', 'mark_changes', 'third_party_text_screened'} <= set(evidence.inherited_restrictions):
            raise ValueError('downstream restriction preservation missing')
        proofs.append(attribution['raw_sha256'])

    def check_input(self, work, usage='external_llm'):
        decision = UsageDecision(usage=usage, ledger_path=str(self.ledger_path), input_hashes=[model_hash(work)])
        try:
            ledger, registry, decision.ledger_sha256 = self._load()
            if usage not in USAGES:
                raise ValueError('unknown usage')
            reasons, materials, proofs = self._evaluate(work, usage, ledger, registry)
            decision.reasons, decision.material_decisions, decision.proof_hashes = reasons, materials, proofs
            decision.allowed = not reasons
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            decision.reasons.append(str(exc))
        return decision

    def assert_inputs_allowed(self, works, usage='external_llm'):
        if not works:
            raise UsageDenied(UsageDecision(usage=usage, ledger_path=str(self.ledger_path), reasons=['empty input batch']))
        decisions = [self.check_input(work, usage) for work in works]
        for decision in decisions:
            if not decision.allowed:
                raise UsageDenied(decision)
        return decisions

    def assert_cache_allowed(self, work, cached_input_hash, usage='external_llm'):
        decision = self.check_input(work, usage)
        if cached_input_hash != model_hash(work):
            decision.allowed = False
            decision.reasons.append('cache full input hash differs')
        if not decision.allowed:
            raise UsageDenied(decision)
        return decision

    def _snapshot(self, entry):
        if 'input_snapshot' in entry:
            return WorkInput.model_validate(entry['input_snapshot'])
        reference = entry['input_ref']
        path = Path(reference['path'])
        path = path if path.is_absolute() else self.root / path
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != reference['sha256']:
            raise ValueError('input snapshot artifact changed')
        lines = payload.decode('utf-8').splitlines()
        return WorkInput.model_validate_json(lines[reference['line_number'] - 1])

    def check_derived(self, payload, originating_input_hashes, usage, *, cached_input_hash=None, source_manifest=None, derivation_recipe=None):
        decision = UsageDecision(usage=usage, ledger_path=str(self.ledger_path), input_hashes=list(originating_input_hashes), payload_sha256=model_hash(payload))
        try:
            ledger, registry, decision.ledger_sha256 = self._load()
            if not originating_input_hashes:
                raise ValueError('derived output has no complete input lineage')
            content = payload.model_dump(mode='json') if hasattr(payload, 'model_dump') else payload
            works = []
            if isinstance(content, dict) and 'input_hash' in content and content['input_hash'] not in originating_input_hashes:
                raise ValueError('derived payload input hash is not an origin')
            for key in originating_input_hashes:
                work = self._snapshot(ledger['inputs'][key])
                works.append(work)
                if model_hash(work) != key:
                    raise ValueError('origin snapshot hash differs')
                if isinstance(content, dict) and 'work_id' in content and len(originating_input_hashes) == 1 and content['work_id'] != work.work_id:
                    raise ValueError('derived payload identity differs from origin')
                reasons, materials, proofs = self._evaluate(work, usage, ledger, registry)
                decision.reasons.extend(reasons)
                decision.material_decisions.extend(materials)
                decision.proof_hashes.extend(proofs)
            if source_manifest is not None:
                from recommend_book.classification import native_attempt
                config_sha = source_manifest['config_sha256']
                recipe = ledger['execution_recipes'][config_sha]
                if source_manifest['usage_ledger_sha256'] != decision.ledger_sha256:
                    raise ValueError('native attempt uses a different frozen admission ledger')
                verifier_path = Path(native_attempt.__file__).resolve()
                if verifier_path != Path(recipe['verifier_path']).resolve() or hashlib.sha256(verifier_path.read_bytes()).hexdigest() != recipe['verifier_sha256']:
                    raise ValueError('preauthorized native verifier implementation changed')
                native_content = content
                if derivation_recipe is not None:
                    from recommend_book import next_database
                    approved = ledger['feature_recipes'][derivation_recipe['extraction']]
                    implementation = Path(next_database.__file__).resolve()
                    if derivation_recipe['extraction'] != 'work-present-relevance-max-v1' or approved != {'path': str(implementation), 'sha256': hashlib.sha256(implementation.read_bytes()).hexdigest()} or any(derivation_recipe[k] != approved[k] for k in ('path', 'sha256')):
                        raise ValueError('feature extraction recipe is not preauthorized or changed')
                    final = json.loads(Path(source_manifest['artifacts']['final']['path']).read_bytes())
                    results = final.get('results', [final])
                    selected = [result for result in results if result['work_id'] == content['work_id']]
                    if len(selected) != 1 or model_hash(selected[0]) != derivation_recipe['native_result_sha256']:
                        raise ValueError('feature original native result differs')
                    native_content = selected[0]
                    selected_work = next(work for work in works if work.work_id == native_content['work_id'] and work.expression_id == native_content['expression_id'])
                    request = json.loads(Path(source_manifest['artifacts']['request']['path']).read_bytes())
                    job_key = request['job_keys'][originating_input_hashes.index(model_hash(selected_work))]
                    reproduced = next_database.extract_classification_features(selected_work, native_content, job_key, request['run_id'])
                    if reproduced != content:
                        raise ValueError('features and provenance are not reproduced from native labels')
                    decision.proof_hashes.append(approved['sha256'])
                native_attempt.verify_native_attempt(source_manifest, native_content, originating_input_hashes,
                                                     expected_config_sha256=config_sha, works=works)
                if cached_input_hash is not None and (cached_input_hash not in originating_input_hashes or content.get('input_hash') != cached_input_hash):
                    raise ValueError('native cache projected result lineage differs')
                decision.proof_hashes.append(recipe['verifier_sha256'])
                decision.proof_hashes.extend(a['sha256'] for a in source_manifest['artifacts'].values())
            else:
                descriptor = ledger['derived_artifacts'][model_hash(content)]
                if descriptor['originating_input_hashes'] != originating_input_hashes or descriptor['value_sha256'] != model_hash(content):
                    raise ValueError('derived artifact whole origin/value binding differs')
                raw = Path(descriptor['raw_path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != descriptor['raw_sha256']:
                    raise ValueError('typed derived original artifact changed')
                value = json.loads(raw.decode('utf-8').splitlines()[descriptor['line_number'] - 1]) if descriptor.get('format') == 'jsonl' else json.loads(raw)
                for token in descriptor['locator'].strip('/').split('/') if descriptor['locator'] else []:
                    token = token.replace('~1', '/').replace('~0', '~')
                    value = value[int(token)] if isinstance(value, list) else value[token]
                if value != content:
                    raise ValueError('typed derived values not reproduced by original artifact')
                recipe = descriptor['recipe']
                if hashlib.sha256(Path(recipe['path']).read_bytes()).hexdigest() != recipe['sha256']:
                    raise ValueError('typed derivation recipe changed')
                if descriptor['kind'] not in ('bibliographic_metadata', 'title_alias', 'classification', 'evaluation', 'delivery'):
                    raise ValueError('unregistered derived material kind')
                if descriptor['kind'] == 'bibliographic_metadata':
                    expected = {'work_id': works[0].work_id, **{field: getattr(works[0], field) for field in METADATA_FIELDS}}
                    if len(works) != 1 or content != expected:
                        raise ValueError('bibliographic projection is not reconstructed from complete source input')
                elif descriptor['kind'] == 'title_alias':
                    if len(works) != 1 or set(content) != {'work_id', 'title', 'language', 'role'} or content['work_id'] != works[0].work_id or content['title'] != works[0].title or content['role'] != 'unknown':
                        raise ValueError('title alias projection differs from source input')
                    source_models = [_record(b) for b in ledger['inputs'][originating_input_hashes[0]]['sources'].values()]
                    def title_language_bound(record):
                        if not any(t.value == content['title'] and t.language == content['language'] for t in record.titles):
                            return False
                        if record.source != 'wikidata':
                            return True
                        entity = json.loads(Path(record.raw_path).read_bytes())['entities'][record.source_id]
                        return (content['title'], content['language']) in _wikidata_title_values(entity)
                    if not content['language'] or not any(title_language_bound(r) for r in source_models):
                        raise ValueError('alias language not bound to immutable title authority')
                elif descriptor['kind'] == 'classification':
                    raise ValueError('classification requires typed native attempt manifest')
                decision.proof_hashes.extend([descriptor['raw_sha256'], recipe['sha256']])
                if cached_input_hash is not None and originating_input_hashes != [cached_input_hash]:
                    raise ValueError('cache lineage does not match complete input')
            decision.allowed = not decision.reasons
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            decision.reasons.append(str(exc))
        return decision

    def assert_prompt_allowed(self, prompt, works, usage='external_llm', *, expected_prompt_sha256=None):
        binary = prompt if isinstance(prompt, bytes) else prompt.encode('utf-8')
        decisions = self.assert_inputs_allowed(works, usage)
        decision = UsageDecision(allowed=True, usage=usage, ledger_path=str(self.ledger_path), ledger_sha256=decisions[0].ledger_sha256 if decisions else None,
                                 input_hashes=[model_hash(w) for w in works], prompt_sha256=hashlib.sha256(binary).hexdigest(), proof_hashes=[p for d in decisions for p in d.proof_hashes])
        try:
            if expected_prompt_sha256 is not None and hashlib.sha256(binary).hexdigest() != expected_prompt_sha256:
                raise ValueError('complete fixed-spec prompt bytes differ')
            data = json.loads(binary.decode('utf-8').split('BEGIN_LOCAL_DATA_JSON\n', 1)[1].rsplit('\nEND_LOCAL_DATA_JSON', 1)[0])
            allowed_keys = {'works', 'taxonomy'} if 'works' in data else {'work', 'output_identity', 'taxonomy'}
            if not isinstance(data, dict) or not set(data) <= allowed_keys:
                raise ValueError('unbound additional prompt top-level data')
            if 'works' in data and any(not isinstance(item, dict) or not set(item) <= {'work', 'output_identity'} for item in data['works']):
                raise ValueError('unbound additional batch prompt data')
            if expected_prompt_sha256 is None and set(data) != {'work'}:
                raise ValueError('full prompt recipe SHA is required for additional identity/taxonomy/batch data')
            actual = [item['work'] for item in data['works']] if 'works' in data else [data['work']]
            expected = []
            for work in works:
                item = work.model_dump(mode='json')
                item['evidence'] = [ev.model_dump(mode='json') for ev in work.evidence if ev.llm_input_allowed]
                expected.append(item)
            if actual != expected:
                raise ValueError('actual prompt work payload differs from guarded inputs')
        except (ValueError, KeyError, IndexError, UnicodeError) as exc:
            decision.allowed = False
            decision.reasons.append(str(exc))
            raise UsageDenied(decision) from exc
        return decision
