"""Build separate full-input rights bindings and purpose-safe projections.

Uses existing immutable SQLite/source data. Official policy pages alone may be
retrieved with --verify-official; no article/dump/API/LLM collection occurs.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import httpx

from recommend_book.models import WorkInput
from recommend_book.models import SourceRecord
from recommend_book.database import DatasetDB
from recommend_book.rights import RightsRegistry, RightsPolicy, USAGES
from recommend_book.usage_guard import UsageGuard, UsageDecision, build_input_entry, model_hash, source_binding, owned_observation_binding, METADATA_FIELDS, _record


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def build_owned(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    database = Path('data/reference-code/narou-personal-recommender/data/narou.sqlite').resolve()
    readme = Path('data/reference-code/narou-personal-recommender/README.md').resolve()
    importer = Path('data/reference-code/narou-personal-recommender/src/narou_personal_recommender/db.py').resolve()
    spec = Path('data/autonomous-next-v3/evaluation/preregistration-owned-title-v3.json').resolve()
    def artifact(path):
        return {'path': str(path), 'sha256': sha(path)}
    binding = owned_observation_binding(database)
    registry = RightsRegistry.default()
    ledger = {'version': 'usage-ledger-v3', 'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'inputs': {}, 'revoked_input_hashes': [], 'official_conditions': {},
              'owned_observation_scope': {'database_path': str(database), 'binding': binding,
                 'manual_source_document': artifact(readme), 'importer_recipe': artifact(importer),
                 'preregistration': artifact(spec),
                 'authorization_basis': 'explicit user instruction: readonly reuse of personal ratings in private local fixed-title ranking',
                 'scope': 'Manual-import observation titles/ratings only. Original HTML files unavailable; no API/annotations, no retrospective AI authorization, no public/reference-title display.'}}
    path = output / 'usage-ledger.json'
    write(path, ledger)
    payload = {'mode': 'owned_title_fixed_ranking', 'input_sha256': binding['input_sha256'],
               'preregistration_sha256': sha(spec), 'aggregate_only': True}
    receipt = UsageGuard(path, expected_ledger_sha256=sha(path)).check_private_reference_aggregate(payload, list(binding['origins']), context='private_local_owned_title_ranking')
    write(output / 'admission-receipt.json', receipt.model_dump(mode='json'))
    write(output / 'manifest.json', {'ledger_sha256': sha(path), 'receipt_sha256': sha(output / 'admission-receipt.json'),
                                    'rows_n': binding['rows_n'], 'allowed': receipt.allowed,
                                    'old_source_unchanged': sha(database) == binding['database_sha256'],
                                    'new_api_calls': 0, 'new_llm_calls': 0, 'original_html_rechecked': False})
    print(json.dumps({'rows_n': binding['rows_n'], 'allowed': receipt.allowed, 'ledger_sha256': sha(path), 'reasons': receipt.reasons}))


def build_french(output):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    base = Path('data/autonomous-next-v3/reference/external-genre/jurisdiction-audit-v1').resolve()
    registry = RightsRegistry.from_file('data/autonomous-next-v3/rights/registry-v3.json')
    policies = [p.model_dump(mode='json') for p in registry.policies.values()]
    for policy in policies:
        if policy['source'] in ('cligs-french', 'conssa'):
            policy['fields'] = ['bibliographic', 'excerpt']
            policy['usages'] = {usage: 'conditional' if usage in ('acquire', 'local_storage', 'external_llm') else 'denied' for usage in USAGES}
            policy['scope'] = 'Individual author/edition/original-body and JP/US primary term review, metadata/markup license, attribution/changes and actual OpenAI ChatGPT Pro mode required. Unknown other processing regions retained as uncertainty; no global PD or US-only processing claim. Private local PoC source review only; commercial/public display/redistribution not admitted.'
    registry_path = output / 'registry-v3-scope-v2.json'
    write(registry_path, {'version': 'sources-v3.2', 'rights_policies': policies})
    registry = RightsRegistry.from_file(registry_path)
    records_path = base / 'source-records-candidate-v2.jsonl'
    inputs_path = base / 'work-inputs-candidate-v2.jsonl'
    proofs_path = base / 'body-proof-descriptors.jsonl'
    originals = {str(p): sha(p) for p in (records_path, inputs_path, proofs_path)}
    records = [SourceRecord.model_validate_json(row) for row in records_path.read_text(encoding='utf8').splitlines()]
    works = [WorkInput.model_validate_json(row) for row in inputs_path.read_text(encoding='utf8').splitlines()]
    descriptors = [json.loads(row) for row in proofs_path.read_text(encoding='utf8').splitlines()]
    corpus_proofs = {ev.evidence_id: next(d for d in descriptors if d['reference_id'] == rec.source_id) for rec in records for ev in rec.evidence}
    database = output / 'source-bindings.sqlite'
    db = DatasetDB(database)
    for record in records:
        record.raw_path = str(Path(record.raw_path).resolve())
    db.ingest(records)
    connection = __import__('sqlite3').connect(database)
    versions = dict(connection.execute('SELECT source_id,version_id FROM source_heads'))
    connection.close()
    for work in works:
        ident = work.work_id.rsplit(':', 1)[1]
        work.source_record_ids = [versions[ident]]
    def artifact(path):
        return {'path': str(Path(path).resolve()), 'sha256': sha(path)}
    source_verifier = base / 'french_verifier.py'
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path), 'registry_file_sha256': sha(registry_path),
       'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]), 'inputs': {},
       'official_conditions': {}, 'revoked_input_hashes': [], 'corpus_proofs': corpus_proofs,
       'corpus_verifiers': {'cligs-french': artifact(source_verifier)},
       'corpus_review_scope': {'policy': artifact('docs/source-rights-review-scope-v3.md'),
          'provider_observation': artifact('data/autonomous-next-v3/preparation/provider-observation-v1/safe-auth-observation.json')}}
    for work in works:
        bindings = {ref: source_binding(database, ref) for ref in work.source_record_ids}
        ledger['inputs'][model_hash(work)] = build_input_entry(work, bindings)
    ledger_path = output / 'candidate-usage-ledger.json'
    write(ledger_path, ledger)
    guard = UsageGuard(ledger_path, expected_ledger_sha256=sha(ledger_path))
    decisions = [guard.check_input(work, 'external_llm') for work in works]
    write(output / 'candidate-admission-receipts.json', [d.model_dump(mode='json') for d in decisions])
    accepted = []
    for work, decision in zip(works, decisions):
        if decision.allowed:
            record = next(record for record in records if record.source_id == work.work_id.rsplit(':', 1)[1]).model_copy(deep=True)
            for evidence in record.evidence:
                evidence.llm_input_allowed = True
            db.ingest([record])
            connection = __import__('sqlite3').connect(database)
            ref = connection.execute('SELECT version_id FROM source_heads WHERE source_id=?', (record.source_id,)).fetchone()[0]
            connection.close()
            approved = work.model_copy(deep=True)
            approved.evidence = record.evidence
            approved.source_record_ids = [ref]
            accepted.append(approved)
            ledger['inputs'][model_hash(approved)] = build_input_entry(approved, {ref: source_binding(database, ref)})
    final_path = output / 'usage-ledger.json'
    write(final_path, ledger)
    final_guard = UsageGuard(final_path, expected_ledger_sha256=sha(final_path))
    final_decisions = [final_guard.check_input(work) for work in accepted]
    if not all(d.allowed for d in final_decisions):
        raise ValueError('Admitted immutable version did not pass complete model/source proof recheck')
    (output / 'approved-inputs.jsonl').write_text(''.join(work.model_dump_json() + '\n' for work in accepted), encoding='utf8')
    write(output / 'admission-receipts.json', [d.model_dump(mode='json') for d in final_decisions])
    if originals != {path: sha(path) for path in originals}:
        raise ValueError('Original French proposals changed')
    write(output / 'manifest.json', {'candidates_n': len(works), 'admitted_private_external_llm_n': len(accepted), 'new_llm_calls': 0,
       'root_live_gate_granted': False, 'worldwide_public_domain_claim': False,
       'other_processing_jurisdictions': 'not individually verified; retained residual uncertainty, not fabricated US-only routing',
       'commercial_display_redistribution_admitted_n': 0, 'originals_unchanged': True, 'original_artifacts': originals,
       'ledger_sha256': sha(final_path), 'inputs_sha256': sha(output / 'approved-inputs.jsonl'),
       'guard_code_sha256': sha('src/recommend_book/usage_guard.py')})
    print(json.dumps({'candidates_n': len(works), 'admitted_private_external_llm_n': len(accepted), 'reasons': [d.reasons for d in decisions]}))


def project_verified_input(work, sources, guard, ledger, registry):
    """Preserve metadata/identity; select reproduced origins and hold bad evidence.

    Choosing a direct, independently verified field origin is explicit lineage,
    not a change to either the historical input or the source record's licence.
    The complete supplied source-reference roster remains bound and verified.
    """
    original_hash = model_hash(work)
    clean = work.model_copy(deep=True)
    clean.evidence = []
    entry = build_input_entry(clean, sources)
    records = {ref: _record(binding) for ref, binding in sources.items()}
    audit = {'work_id': work.work_id, 'original_input_hash': original_hash,
             'metadata_unchanged': True, 'metadata_origin_selections': [],
             'excluded_evidence': [], 'accepted_evidence_ids': []}
    for field in METADATA_FIELDS:
        if entry['metadata'][field]['empty']:
            continue
        value = getattr(clean, field)
        values = value if isinstance(value, list) else [value]
        for index, item in enumerate(values):
            attempts, selected = [], None
            candidates = entry['metadata'][field]['origins'][str(index)]
            for ref in sorted(candidates, key=lambda r: (records[r].source != 'wikidata', r)):
                record = records[ref]
                try:
                    policy = registry.policies[record.rights_policy_id]
                    if policy.source != record.source or policy.provider_id not in (record.provider_id, '*') or 'bibliographic' not in policy.fields or policy.usages.get('external_llm') != 'allowed':
                        raise ValueError('direct bibliographic purpose not permitted')
                    proofs = []
                    reproduction = guard._raw_metadata(record, field, item, proofs)
                    guard._official(policy, ledger, proofs)
                    selected = ref
                    attempts.append({'source_version_id': ref, 'selected': True,
                                     'raw_reproduction': reproduction, 'proof_hashes': proofs})
                    break
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    attempts.append({'source_version_id': ref, 'selected': False, 'reason': str(exc)})
            audit['metadata_origin_selections'].append({'field': field, 'index': index,
                'value_sha256': model_hash(item), 'candidate_source_versions': candidates, 'attempts': attempts})
            if selected is None:
                raise ValueError('metadata origin could not be reproduced: ' + field)
            entry['metadata'][field]['origins'][str(index)] = [selected]
    ledger['inputs'][model_hash(clean)] = entry
    errors, _, _ = guard._evaluate(clean, 'external_llm', ledger, registry)
    if errors:
        raise ValueError('metadata projection held: ' + '; '.join(errors))
    for evidence in work.evidence:
        single = clean.model_copy(deep=True)
        single.evidence = [evidence]
        single_entry = build_input_entry(single, sources)
        single_entry['metadata'] = entry['metadata']
        ledger['inputs'][model_hash(single)] = single_entry
        try:
            errors, _, _ = guard._evaluate(single, 'external_llm', ledger, registry)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors = [str(exc)]
        if errors:
            audit['excluded_evidence'].append({'evidence_id': evidence.evidence_id,
                'model_sha256': model_hash(evidence), 'kind': evidence.kind,
                'rights_policy_id': evidence.rights_policy_id, 'reasons': errors})
        else:
            clean.evidence.append(evidence)
            audit['accepted_evidence_ids'].append(evidence.evidence_id)
    final_entry = build_input_entry(clean, sources)
    final_entry['metadata'] = entry['metadata']
    ledger['inputs'][model_hash(clean)] = final_entry
    errors, _, _ = guard._evaluate(clean, 'external_llm', ledger, registry)
    if errors:
        raise ValueError('complete projected input held: ' + '; '.join(errors))
    audit['projected_input_hash'] = model_hash(clean)
    return clean, final_entry, audit


def prepare_pilot(directory, output, execution_config=None):
    directory, output = Path(directory).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    database = directory / 'dataset.sqlite'
    registry_path = Path('data/autonomous-next-v3/rights/french-admission-v2/registry-v3-scope-v2.json').resolve()
    registry = RightsRegistry.from_file(registry_path)
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path), 'registry_file_sha256': sha(registry_path),
        'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
        'official_conditions': json.loads(Path('data/autonomous-next-v3/rights/build-v1/official-conditions.json').read_text(encoding='utf8')),
        'inputs': {}, 'derived_artifacts': {}, 'execution_recipes': {}, 'revoked_input_hashes': []}
    raw_sources = {}
    def bind(work):
        sources = {}
        for ref in work.source_record_ids:
            if ref not in raw_sources:
                raw_sources[ref] = source_binding(database, ref)
            sources[ref] = raw_sources[ref]
        return sources
    classification_path = directory / 'classification-inputs.jsonl'
    candidates = [WorkInput.model_validate_json(row) for row in classification_path.read_text(encoding='utf8').splitlines()]
    guard_path = output / 'usage-ledger.json'
    guard = UsageGuard(guard_path)
    narrative, failures, projections, entries = [], [], [], {}
    ordered = sorted(candidates, key=lambda w: (0 if any(e.kind == 'synopsis' and e.language == 'ja' for e in w.evidence) else 1 if any(e.kind == 'synopsis' and e.language == 'en' for e in w.evidence) else 2, w.work_id))
    for work in ordered:
        if not any(e.rights_policy_id == 'wikipedia-screened-prose' and e.kind == 'synopsis' for e in work.evidence):
            continue
        try:
            projected, entry, audit = project_verified_input(work, bind(work), guard, ledger, registry)
            projections.append(audit)
            reasons = [] if any(e.kind == 'synopsis' and e.rights_policy_id == 'wikipedia-screened-prose' for e in projected.evidence) else ['licensed synopsis not admitted in safe projection']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reasons = [str(exc)]
        if not reasons:
            narrative.append(projected)
            entries[model_hash(projected)] = entry
        else:
            failures.append({'work_id': work.work_id, 'input_hash': model_hash(work), 'reasons': reasons})
    # Prioritize a bounded cross-language pilot, without a coverage claim.
    selected = []
    for language, limit in [('ja', 2), ('en', 1), ('other', 2)]:
        matches = [w for w in narrative if any(e.kind == 'synopsis' and e.rights_policy_id == 'wikipedia-screened-prose' and (e.language not in ('ja', 'en') if language == 'other' else e.language == language) for e in w.evidence)]
        selected.extend(matches[:limit])
    selected = list({w.work_id: w for w in selected}.values())
    selected.extend(w for w in narrative if w.work_id not in {x.work_id for x in selected} and len(selected) < 5)
    metadata_path = directory / 'bibliographic-inputs.jsonl'
    metadata = []
    for line in metadata_path.read_text(encoding='utf8').splitlines():
        work = WorkInput.model_validate_json(line)
        if work.work_id in {w.work_id for w in selected}:
            continue
        try:
            projected, entry, audit = project_verified_input(work, bind(work), guard, ledger, registry)
            projections.append(audit)
            reasons = []
        except (OSError, ValueError, KeyError, TypeError) as exc:
            reasons = [str(exc)]
        if not reasons:
            metadata.append(projected)
            entries[model_hash(projected)] = entry
        if len(metadata) == 5:
            break
    write(output / 'narrative-candidate-holds.json', failures)
    write(output / 'projection-audit.json', projections)
    if len(selected) != 5 or len(metadata) != 5:
        write(output / 'manifest.json', {'state': 'pilot_incomplete', 'narrative_n': len(selected),
            'bibliographic_only_n': len(metadata), 'held_narrative_candidates_n': len(failures),
            'guard_sha256': sha('src/recommend_book/usage_guard.py'), 'new_llm_calls': 0, 'root_live_gate_granted': False})
        raise ValueError('A fully verified 5 narrative +5 bibliographic pilot was not obtained')
    works = selected + metadata
    input_path = output / 'pilot-inputs.jsonl'
    input_path.write_text(''.join(work.model_dump_json() + '\n' for work in works), encoding='utf8')
    ledger['inputs'] = {model_hash(work): entries[model_hash(work)] for work in works}
    for index, work in enumerate(works, 1):
        entry = ledger['inputs'][model_hash(work)]
        entry.pop('input_snapshot', None)
        entry['input_ref'] = {'path': str(input_path), 'sha256': sha(input_path), 'line_number': index}
    if execution_config:
        config = json.loads(Path(execution_config).read_bytes())
        verifier_path = Path('src/recommend_book/classification/native_attempt.py').resolve()
        ledger['execution_recipes'][model_hash(config)] = {'verifier_path': str(verifier_path), 'verifier_sha256': sha(verifier_path)}
    write(guard_path, ledger)
    frozen_guard = UsageGuard(guard_path, expected_ledger_sha256=sha(guard_path))
    receipts = frozen_guard.assert_inputs_allowed(works)
    write(output / 'admission-receipts.json', [decision.model_dump(mode='json') for decision in receipts])
    write(output / 'narrative-candidate-holds.json', failures)
    write(output / 'manifest.json', {'n': 10, 'narrative_n': 5, 'bibliographic_only_n': 5,
       'verified_narrative_candidates_n': len(narrative), 'held_narrative_candidates_n': len(failures),
       'source_database_path': str(database), 'source_database_sha256': sha(database),
       'input_sha256': sha(input_path), 'ledger_sha256': sha(guard_path), 'guard_sha256': sha('src/recommend_book/usage_guard.py'),
       'new_llm_calls': 0, 'root_live_gate_granted': False, 'execution_recipe_registered': bool(execution_config),
       'narrative_language_counts': dict(Counter(e.language for w in selected for e in w.evidence if e.kind == 'synopsis' and e.rights_policy_id == 'wikipedia-screened-prose'))})
    print(json.dumps({'n': 10, 'narrative_n': 5, 'bibliographic_only_n': 5, 'verified_narrative_candidates_n': len(narrative),
                      'held_narrative_candidates_n': len(failures), 'ledger_sha256': sha(guard_path), 'execution_recipe_registered': bool(execution_config)}))


def build_safe_projections(directory, output):
    """Inventory every canonical candidate and keep purpose holds explicit.

    Input files retain the entire baseline cohort. A DB bridge admits only the
    purpose-passing subset; a failed field is never erased to inflate coverage.
    This offline review does not execute or approve a native model recipe.
    """
    directory, output = Path(directory).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry_path = Path('data/autonomous-next-v3/rights/french-admission-v2/registry-v3-scope-v2.json').resolve()
    registry = RightsRegistry.from_file(registry_path)
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path), 'registry_file_sha256': sha(registry_path),
        'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
        'official_conditions': json.loads(Path('data/autonomous-next-v3/rights/build-v1/official-conditions.json').read_bytes()),
        'inputs': {}, 'derived_artifacts': {}, 'execution_recipes': {}, 'revoked_input_hashes': []}
    database = directory / 'dataset.sqlite'
    groups = [('bibliographic', 'bibliographic-inputs.jsonl'), ('classification', 'classification-inputs.jsonl'), ('alias', 'aliases-inputs.jsonl')]
    protected = {str(path): sha(path) for path in [database, *(directory / name for _, name in groups), Path('src/recommend_book/usage_guard.py').resolve(), Path(__file__).resolve()]}
    guard = UsageGuard(output / 'usage-ledger.json')
    source_cache, lineage, outcomes, counts, files = {}, [], [], {}, {}
    original_by_kind = {kind: [WorkInput.model_validate_json(row) for row in (directory/name).read_text(encoding='utf8').splitlines()] for kind, name in groups}
    bibliography_hashes = {work.work_id: model_hash(work) for work in original_by_kind['bibliographic']}
    for kind, filename in groups:
        path = output / filename
        files[kind] = str(path)
        group_counts, kind_counts = Counter(), Counter()
        with path.open('w', encoding='utf8') as stream:
            for index, original in enumerate(original_by_kind[kind], 1):
                sources = {}
                for ref in original.source_record_ids:
                    if ref not in source_cache:
                        source_cache[ref] = source_binding(database, ref)
                    sources[ref] = source_cache[ref]
                try:
                    projected, entry, audit = project_verified_input(original, sources, guard, ledger, registry)
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    projected = original.model_copy(deep=True)
                    entry = build_input_entry(projected, sources)
                    audit = {'work_id': original.work_id, 'original_input_hash': model_hash(original),
                        'projected_input_hash': model_hash(projected), 'metadata_unchanged': True,
                        'metadata_origin_selections': [], 'excluded_evidence': [], 'projection_hold_reasons': [str(exc)]}
                ledger['inputs'][model_hash(projected)] = entry
                purposes = {}
                for purpose in ('external_llm', 'display'):
                    try:
                        errors, materials, hashes = guard._evaluate(projected, purpose, ledger, registry)
                    except (OSError, ValueError, KeyError, TypeError) as exc:
                        errors, materials, hashes = [str(exc)], [], []
                    purposes[purpose] = {'allowed': not errors, 'reasons': errors}
                    group_counts[purpose + (':allowed' if not errors else ':held')] += 1
                    outcomes.append({'kind': kind, 'input_hash': model_hash(projected), 'usage': purpose,
                        'allowed': not errors, 'reasons': errors, 'material_decisions': materials, 'proof_hashes': hashes})
                audit.update(kind=kind, input_hash=model_hash(projected), base_input_hash=model_hash(original),
                    base_sourcekind=kind, original_bibliographic_hash=bibliography_hashes[original.work_id],
                    source_evidence_keys={ev.evidence_id: entry['evidence'][ev.evidence_id]['source_versions'] for ev in projected.evidence},
                    excluded_refs=[], metadata_origin_bindings=entry['metadata'], purposes=purposes, line_number=index)
                lineage.append(audit)
                if purposes['external_llm']['allowed']:
                    kind_counts.update(ev.kind for ev in projected.evidence)
                    if any(ev.kind == 'synopsis' for ev in projected.evidence):
                        group_counts['external_llm:with_synopsis'] += 1
                    if any(ev.kind == 'excerpt' for ev in projected.evidence):
                        group_counts['external_llm:with_original_excerpt'] += 1
                    if not projected.evidence:
                        group_counts['external_llm:metadata_only'] += 1
                stream.write(projected.model_dump_json() + '\n')
                if index % 100 == 0:
                    print(json.dumps({'kind': kind, 'processed': index, 'counts': dict(group_counts)}), flush=True)
        for row in lineage:
            if row['kind'] == kind:
                entry = ledger['inputs'][row['input_hash']]
                entry.pop('input_snapshot', None)
                entry['input_ref'] = {'path': str(path), 'sha256': sha(path), 'line_number': row['line_number']}
        counts[kind] = {'candidate_n': len(original_by_kind[kind]), 'purpose_counts': dict(group_counts), 'allowed_evidence_kind_counts': dict(kind_counts)}
        write(output / 'progress-counts.json', counts)
    if protected != {path: sha(path) for path in protected}:
        raise ValueError('Frozen source/inputs changed during safe projection review')
    ledger_path = output / 'usage-ledger.json'
    write(ledger_path, ledger)
    (output/'lineage.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in lineage), encoding='utf8')
    (output/'admission-outcomes.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in outcomes), encoding='utf8')
    write(output/'manifest.json', {'version': 'world-purpose-safe-projections-v3', 'base_database_path': str(database),
        'base_database_sha256': sha(database), 'inputs': {kind: {'path': path, 'sha256': sha(path)} for kind, path in files.items()},
        'usage_ledger_path': str(ledger_path), 'usage_ledger_sha256': sha(ledger_path),
        'guard_version': __import__('recommend_book.usage_guard', fromlist=['GUARD_VERSION']).GUARD_VERSION,
        'guard_sha256': sha('src/recommend_book/usage_guard.py'), 'counts': counts, 'record_scope': {'inventory_canonical_n': len(original_by_kind['bibliographic'])},
        'purpose_reviewed': ['external_llm', 'display'], 'root_live_gate_granted': False,
        'new_llm_calls': 0, 'new_network_requests': 0, 'originals_unchanged': True, 'protected_artifacts': protected})
    print(json.dumps(counts), flush=True)


def build_proposals(directory, output):
    """Evaluate new immutable DB proposals without admitting historical results."""
    directory, output = Path(directory).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    registry_path = Path('data/autonomous-next-v3/rights/registry-v3.json').resolve()
    if not registry_path.exists():
        policies = list(RightsRegistry.default().policies.values())
        for source, policy_id in [('conssa', 'conssa-reviewed-original-v3'), ('cligs-french', 'cligs-french-reviewed-original-v3')]:
            policies.append(RightsPolicy(policy_id=policy_id, source=source, provider_id='cligs', fields=['excerpt'],
                license='CC-BY-4.0 markup; original text separately reviewed', usages={usage: 'conditional' for usage in USAGES},
                obligations=['per_work_authority', 'per_edition_rights', 'jurisdiction_scope', 'attribution', 'mark_changes', 'original_body_scope'],
                verified_at=datetime.now(timezone.utc).isoformat(), evidence_urls=['https://creativecommons.org/licenses/by/4.0/'],
                scope='No blanket corpus approval. Individual original text, edition, author authority, JP/US and actual processing jurisdiction, raw TEI identity/body transformation and attribution proofs mandatory; annotation answers excluded.'))
        write(registry_path, {'version': 'sources-v3.1', 'rights_policies': [p.model_dump(mode='json') for p in policies]})
    registry = RightsRegistry.from_file(registry_path)
    original_official = Path('data/autonomous-next-v3/rights/build-v1/official-conditions.json')
    ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_path), 'registry_file_sha256': sha(registry_path),
              'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': json.loads(original_official.read_text(encoding='utf-8')), 'inputs': {},
              'revoked_input_hashes': [], 'derived_artifacts': {}, 'execution_recipes': {}}
    database = directory / 'dataset.sqlite'
    files = ['bibliographic-inputs.jsonl', 'classification-inputs.jsonl', 'aliases-inputs.jsonl']
    snapshots = {str(database): sha(database), **{str(directory / f): sha(directory / f) for f in files}}
    source_cache, groups = {}, {}
    for file in files:
        path = directory / file
        works = [WorkInput.model_validate_json(row) for row in path.read_text(encoding='utf-8').splitlines()]
        groups[file] = works
        for index, work in enumerate(works, 1):
            for ref in work.source_record_ids:
                if ref not in source_cache:
                    source_cache[ref] = source_binding(database, ref)
            ledger['inputs'][model_hash(work)] = build_input_entry(work, {ref: source_cache[ref] for ref in work.source_record_ids},
                input_ref={'path': str(path), 'sha256': sha(path), 'line_number': index})
    derived = directory / 'derivations.jsonl'
    for index, row in enumerate(derived.read_text(encoding='utf-8').splitlines(), 1):
        descriptor = json.loads(row)
        ledger['derived_artifacts'][descriptor['value_sha256']] = {key: descriptor[key] for key in ['kind', 'originating_input_hashes', 'value_sha256', 'recipe']}
        ledger['derived_artifacts'][descriptor['value_sha256']].update(raw_path=str(derived), raw_sha256=sha(derived), format='jsonl', line_number=index, locator='/payload')
    path = output / 'usage-ledger.json'
    write(path, ledger)
    guard = UsageGuard(path, expected_ledger_sha256=sha(path))
    # This offline review uses one frozen ledger snapshot, independently rereads
    # all source/proof material per input, and checks the ledger hash at the end.
    # Live admission APIs deliberately reload it before every call instead.
    ledger, registry, ledger_sha = guard._load()
    outcomes, counts = [], {}
    for file, works in groups.items():
        for usage in ('external_llm', 'display'):
            group_counts, reasons = Counter(), Counter()
            for index, work in enumerate(works, 1):
                decision = UsageDecision(usage=usage, ledger_path=str(path), ledger_sha256=ledger_sha, input_hashes=[model_hash(work)])
                try:
                    errors, materials, proofs = guard._evaluate(work, usage, ledger, registry)
                    decision.reasons, decision.material_decisions, decision.proof_hashes = errors, materials, proofs
                    decision.allowed = not errors
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    decision.reasons = [str(exc)]
                group_counts['allowed' if decision.allowed else 'held'] += 1
                reasons.update(decision.reasons)
                outcomes.append({'group': file, 'input_hash': model_hash(work), **decision.model_dump(mode='json')})
                if index % 250 == 0:
                    print(f'{file} {usage} {index}/{len(works)}', flush=True)
            counts[file + ':' + usage] = {'counts': dict(group_counts), 'reasons': dict(reasons)}
    write(output / 'counts.json', counts)
    (output / 'admission-outcomes.jsonl').write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in outcomes), encoding='utf-8')
    if snapshots != {path: sha(path) for path in snapshots}:
        raise ValueError('Proposal/original database changed during admission review')
    if sha(path) != ledger_sha:
        raise ValueError('Frozen ledger changed during offline review')
    write(output / 'manifest.json', {'state': 'candidate_admission_review; root live gate not granted', 'ledger_sha256': sha(path),
          'original_artifacts': snapshots, 'originals_unchanged': True, 'new_llm_calls': 0,
          'guard_code_sha256': sha('src/recommend_book/usage_guard.py'), 'counts_path': str(output / 'counts.json')})


def official_conditions(output, registry, verify):
    destination = output / 'official-conditions.json'
    if destination.exists():
        existing = json.loads(destination.read_text(encoding='utf-8'))
        for items in existing.values():
            for item in items:
                if sha(item['raw_path']) != item['raw_sha256']:
                    raise ValueError('Existing official artifact changed')
        return existing
    if not verify:
        raise ValueError('No current official artifacts; first use requires --verify-official')
    raw = output / 'official'
    raw.mkdir()
    urls = {}
    # Unapproved WS text has no admission path; retaining its old input does not
    # require pretending its per-work conditions have now been met.
    for policy in registry.policies.values():
        if policy.source in ('wikidata', 'openlibrary', 'wikipedia'):
            for url in policy.evidence_urls:
                if url in ('https://www.wikidata.org/wiki/Wikidata:Licensing', 'https://openlibrary.org/developers/licensing',
                           'https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use', 'https://creativecommons.org/licenses/by-sa/4.0/'):
                    urls.setdefault(url, None)
    with httpx.Client(timeout=45, follow_redirects=True, headers={'User-Agent': 'recommend-book/0.1 private rights audit (https://github.com/ayutaz/recommend-book)'}) as client:
        for index, url in enumerate(urls):
            if index:
                time.sleep(1.1)
            response = client.get(url)
            response.raise_for_status()
            path = raw / (hashlib.sha256(url.encode()).hexdigest() + '.html')
            path.write_bytes(response.content)
            urls[url] = {'source_url': url, 'final_url': str(response.url), 'raw_path': str(path.resolve()),
                         'raw_sha256': sha(path), 'verified_at': datetime.now(timezone.utc).isoformat(),
                         'status_code': response.status_code, 'response_headers': dict(response.headers),
                         'verification_scope': 'Current primary general policy; not a blanket third-party-text or per-work clearance.'}
    result = {key: [urls[url] for url in policy.evidence_urls if url in urls] for key, policy in registry.policies.items() if policy.source in ('wikidata', 'openlibrary', 'wikipedia')}
    write(destination, result)
    return result


def build(database, inputs, output, *, verify_official=False, quarantine=None):
    database, inputs, output = Path(database).resolve(), Path(inputs).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'manifest.json').exists():
        raise ValueError('Completed namespace is immutable; select a new output directory')
    protected_before = {str(p): sha(p) for p in (database, inputs)}
    registry = RightsRegistry.default()
    official = official_conditions(output, registry, verify_official)
    original = [WorkInput.model_validate_json(line) for line in inputs.read_text(encoding='utf-8').splitlines()]
    source_refs = sorted({ref for work in original for ref in work.source_record_ids})
    sources = {ref: source_binding(database, ref) for ref in source_refs}
    ledger = {'version': 'usage-ledger-v3', 'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
              'official_conditions': official, 'inputs': {}, 'revoked_input_hashes': [], 'private_origins': {},
              'created_at': datetime.now(timezone.utc).isoformat(), 'no_prelaunch_retroactive_approval': True}
    for index, work in enumerate(original):
        ledger['inputs'][model_hash(work)] = build_input_entry(work, {ref: sources[ref] for ref in work.source_record_ids},
                                                            input_ref={'path': str(inputs), 'sha256': protected_before[str(inputs)], 'line_number': index + 1})
    ledger_path = output / 'usage-ledger.json'
    write(ledger_path, ledger)
    guard = UsageGuard(ledger_path)
    historical_outcomes, metadata, content, material_held = [], [], [], Counter()
    quarantined = set()
    if quarantine:
        q = json.loads(Path(quarantine).read_text(encoding='utf-8'))
        # The audit format explicitly enumerates canonical IDs.
        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get('work_id'), str):
                    quarantined.add(value['work_id'])
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(q.get('quarantines', q.get('quarantined_works', [])))
    for index, work in enumerate(original):
        reasons, materials, proofs = guard._evaluate(work, 'external_llm', ledger, registry)
        historical_outcomes.append({'work_id': work.work_id, 'input_hash': model_hash(work), 'allowed': not reasons,
                                    'reasons': reasons, 'materials': [{k: v for k, v in item.items() if k != 'attribution'} for item in materials]})
        accepted_ids = {item['material'] for item in materials if item['allowed'] and item['material'] in {e.evidence_id for e in work.evidence}}
        for item in materials:
            if not item['allowed'] and not item['material'].startswith('metadata.'):
                material_held[item['policy_id']] += 1
        if work.work_id in quarantined:
            continue
        clean = work.model_copy(deep=True)
        clean.evidence = []
        metadata.append(clean)
        rich = work.model_copy(deep=True)
        rich.evidence = [e for e in work.evidence if e.evidence_id in accepted_ids]
        content.append(rich)
        if (index + 1) % 100 == 0:
            print(json.dumps({'checked': index + 1, 'total': len(original)}, ensure_ascii=True), flush=True)
    paths = {'metadata': output / 'metadata-only-inputs.jsonl', 'content': output / 'permitted-material-inputs.jsonl'}
    for category, works in (('metadata', metadata), ('content', content)):
        paths[category].write_text(''.join(json.dumps(w.model_dump(mode='json'), ensure_ascii=False) + '\n' for w in works), encoding='utf-8')
        snapshot_sha = sha(paths[category])
        for index, work in enumerate(works):
            ledger['inputs'][model_hash(work)] = build_input_entry(work, {ref: sources[ref] for ref in work.source_record_ids},
                                                                input_ref={'path': str(paths[category]), 'sha256': snapshot_sha, 'line_number': index + 1})
    write(ledger_path, ledger)
    outcomes_path = output / 'historical-input-usage-outcomes.jsonl'
    outcomes_path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in historical_outcomes), encoding='utf-8')
    counts = {}
    for category, works in (('metadata', metadata), ('content', content)):
        purpose = {}
        for usage in USAGES:
            n = sum(not guard._evaluate(work, usage, ledger, registry)[0] for work in works)
            purpose[usage] = {'allowed_n': n, 'held_n': len(works) - n}
        counts[category] = purpose
    assert {str(p): sha(p) for p in (database, inputs)} == protected_before
    result = {'version': 'usage-ledger-v3-build-v1', 'original_n': len(original), 'quarantined_n': len(quarantined),
              'projected_n': len(metadata), 'historical_external_llm_allowed_n': sum(row['allowed'] for row in historical_outcomes),
              'held_evidence_by_policy': dict(material_held), 'projected_usage_counts': counts,
              'content_evidence_by_policy': dict(Counter(e.rights_policy_id for w in content for e in w.evidence)),
              'nonempty_content_n': sum(bool(w.evidence) for w in content),
              'raw_database_input_unchanged': True, 'original_artifacts': protected_before,
              'new_llm_calls': 0, 'old_results_reapproved_or_reused': False,
              'outputs': {str(path): sha(path) for path in [ledger_path, outcomes_path, *paths.values()]}}
    write(output / 'manifest.json', result)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', default='data/autonomous-poc-v1/database/final-v1/dataset.sqlite')
    parser.add_argument('--inputs', default='data/autonomous-poc-v1/database/final-v1/ordered-inputs.jsonl')
    parser.add_argument('--output', default='data/autonomous-next-v3/rights/build-v1')
    parser.add_argument('--quarantine', default='data/autonomous-poc-v1/enrichment/work-scope-quarantine.json')
    parser.add_argument('--verify-official', action='store_true')
    parser.add_argument('--owned-private', action='store_true')
    parser.add_argument('--proposal-directory')
    parser.add_argument('--french-corpus', action='store_true')
    parser.add_argument('--prepare-pilot', action='store_true')
    parser.add_argument('--safe-projections', action='store_true')
    parser.add_argument('--execution-config')
    args = parser.parse_args()
    if args.safe_projections:
        build_safe_projections(args.proposal_directory, args.output)
    elif args.prepare_pilot:
        prepare_pilot(args.proposal_directory, args.output, args.execution_config)
    elif args.french_corpus:
        build_french(args.output)
    elif args.proposal_directory:
        build_proposals(args.proposal_directory, args.output)
    elif args.owned_private:
        build_owned(args.output)
    else:
        build(args.database, args.inputs, args.output, verify_official=args.verify_official, quarantine=args.quarantine)
