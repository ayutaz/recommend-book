"""Freeze new Wikidata classification inputs and six-purpose source receipts.

Offline preparation only. Historical databases are queried read-only for QIDs;
they are never copied, hashed in full, or used to rebind old classifications.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

from recommend_book.database import DatasetDB
from recommend_book.ingestion.wikidata import claim_values
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.next_database import build_next_database, get_next_inputs
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding
from recommend_book.scale_classification_v1 import GENRE_TERMS

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'scale-classification-source-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def historical_qids(databases, mappings=()):
    """Read exact source IDs, without scanning historical raw bodies or results."""
    qids, proofs = set(), []
    for item in databases:
        path = Path(item).resolve(strict=True)
        before = path.stat()
        with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            values = {row[0] for row in db.execute(
                "SELECT DISTINCT value FROM external_ids WHERE source='wikidata' AND scope='work'")}
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('Historical authority database changed during QID query')
        qids.update(values)
        proofs.append({'path': str(path), 'qid_count': len(values), 'size': after.st_size,
                       'mtime_ns': after.st_mtime_ns, 'full_database_hashed': False})
    for item in mappings:
        path = Path(item).resolve(strict=True)
        document = json.loads(path.read_bytes())
        values = document if isinstance(document, list) else document.get('work_mapping', document.get('mappings', document.get('cases', [])))
        for row in values:
            qid = row.get('wikidata_qid', row.get('source_id'))
            if isinstance(qid, str) and re.fullmatch(r'Q[1-9][0-9]*', qid):
                qids.add(qid)
        proofs.append({'path': str(path), 'sha256': sha(path), 'kind': 'saved-work-mapping'})
    return qids, proofs


def scope_issues(record, entity):
    def values(prop):
        return {s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id')
                for s in entity.get('claims', {}).get(prop, [])
                if s.get('rank') != 'deprecated' and
                isinstance(s.get('mainsnak', {}).get('datavalue', {}).get('value'), dict)}
    kinds = values('P31') | values('P7937')
    issues = []
    if record.source != 'wikidata' or record.provider_id != 'wikidata' or record.record_kind != 'work':
        issues.append('not_wikidata_work')
    if record.work_kind not in ('novel', 'light_novel') or not kinds & {'Q8261', 'Q747381'}:
        issues.append('explicit_individual_novel_assertion_absent')
    if values('P629') or 'Q3331189' in values('P31'):
        issues.append('explicit_edition_assertion')
    if kinds & {'Q49084', 'Q277759', 'Q1667921', 'Q53731850', 'Q1279564',
                'Q105420', 'Q2668072', 'Q104213567', 'Q5398426', 'Q11424'}:
        issues.append('short_story_collection_series_or_adaptation_scope_conflict')
    if not record.titles or re.fullmatch(r'Q[1-9][0-9]*', record.titles[0].value):
        issues.append('human_title_absent')
    if any(re.fullmatch(r'Q[1-9][0-9]*', c.name) for c in record.contributors):
        issues.append('linked_contributor_label_unresolved')
    if not record.evidence or not any(e.llm_input_allowed and e.kind in ('subject', 'description') for e in record.evidence):
        issues.append('no_subject_or_description_evidence')
    return issues


def choose_records(path, excluded_qids, *, limit=1000):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('New fixed classification pool must contain 1..1000 works')
    candidates, excluded, raw_cache, seen = [], [], {}, set()
    for number, line in enumerate(Path(path).read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        record = SourceRecord.model_validate_json(line)
        if record.source_id in seen:
            raise ValueError('Duplicate source QID in candidate snapshot')
        seen.add(record.source_id)
        if record.source_id in excluded_qids:
            excluded.append({'source_id': record.source_id, 'reasons': ['historical_work_excluded']})
            continue
        raw_path = Path(record.raw_path).resolve(strict=True)
        cache_key = (str(raw_path), record.raw_sha256)
        if cache_key not in raw_cache:
            raw = raw_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != record.raw_sha256:
                raise ValueError('Candidate raw SHA differs: ' + record.source_id)
            raw_cache[cache_key] = json.loads(raw)
        entity = raw_cache[cache_key].get('entities', {}).get(record.source_id)
        if not entity or entity.get('id') != record.source_id:
            raise ValueError('Candidate raw identity differs')
        issues = scope_issues(record, entity)
        if issues:
            excluded.append({'source_id': record.source_id, 'reasons': issues})
            continue
        # Prefer actual P136 genre claims; never derive content from a title.
        genres = [v['id'] for v in claim_values(entity, 'P136') if isinstance(v, dict) and 'id' in v]
        explicit_terms = {_label.casefold().strip() for qid in genres
                          if isinstance(_label := record.extra.get('linked_label_provenance', {}).get(qid, {}).get('label'), str)}
        strong = bool(explicit_terms & set().union(*GENRE_TERMS.values()))
        candidates.append((0 if strong else (1 if genres else 2), number, record, genres))
    candidates.sort(key=lambda row: (row[0], row[1]))
    selected = candidates[:limit]
    if len(selected) != limit:
        raise ValueError(f'Only {len(selected)} new eligible candidates for requested {limit}')
    return selected, excluded


def scale_evidence_input(work):
    descriptions = [e for e in work.evidence if e.llm_input_allowed and e.kind == 'description']
    def language(ev):
        return ev.language or ((ev.locator or '').split('.', 1)[1] if (ev.locator or '').startswith('descriptions.') else '')
    descriptions.sort(key=lambda e: ({'en': 0, 'ja': 1}.get(language(e), 2), language(e), e.evidence_id))
    selected = descriptions[:2]
    selected_ids = {e.evidence_id for e in selected}
    evidence = [e for e in work.evidence if e.llm_input_allowed and (e.kind == 'subject' or e.evidence_id in selected_ids)]
    return work.model_copy(update={'evidence': evidence}, deep=True)


def seal_scale_evidence(output, source, works, ledger, bind):
    """Use the existing exact-subset seal; complete SourceRecord rows survive."""
    from recommend_book.sealed_projection import seal_next_projections
    from recommend_book.usage_guard import GUARD_VERSION
    base = output / 'base-next-source'
    build_next_database(source_database=source, output_dir=base)
    proposals = get_next_inputs(base / 'dataset.sqlite')
    originals = {w.work_id: w for w in proposals['classification']}
    if set(originals) != {w.work_id for w in works}:
        raise ValueError('New source projection coverage differs')
    inputs = output / 'evidence-subset'
    inputs.mkdir()
    groups = {'classification': works, 'bibliographic': proposals['bibliographic'], 'aliases': proposals['aliases']}
    paths, lineages = {}, []
    with sqlite3.connect(source) as db:
        source_evidence = {ref: SourceRecord.model_validate_json(raw).evidence for ref, raw in db.execute('SELECT version_id,record_json FROM source_records')}
    for kind, values in groups.items():
        path = inputs / (kind + '-inputs.jsonl')
        path.write_text(''.join(w.model_dump_json() + '\n' for w in values), encoding='utf-8', newline='\n')
        paths[kind] = path
        bind(values, base / 'dataset.sqlite', path)
        old_values = {model_hash(w): w for w in proposals[kind]} if kind != 'classification' else None
        for number, work in enumerate(values, 1):
            original = originals[work.work_id] if kind == 'classification' else old_values[model_hash(work)]
            retained = {e.evidence_id for e in work.evidence}
            lineages.append({'kind': 'alias' if kind == 'aliases' else kind, 'input_hash': model_hash(work),
                'base_input_hash': model_hash(original), 'line_number': number, 'excluded_refs': [],
                'source_evidence_keys': {e.evidence_id: sorted(ref for ref in work.source_record_ids if e in source_evidence[ref]) for e in work.evidence},
                'excluded_evidence': [{'evidence_id': e.evidence_id, 'model_sha256': model_hash(e),
                    'kind': e.kind, 'rights_policy_id': e.rights_policy_id,
                    'reasons': ['new_scale_input_keeps_subjects_and_two_preferred_descriptions; full_SourceRecord_retained']}
                    for e in original.evidence if e.evidence_id not in retained],
                'purposes': {'external_llm' if kind == 'classification' else 'display': {'allowed': True}}})
    lineage = inputs / 'lineage.jsonl'
    lineage.write_text(''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in lineages), encoding='utf-8', newline='\n')
    admitted_ledger = inputs / 'usage-ledger.json'
    save(admitted_ledger, ledger)
    manifest = inputs / 'projection-manifest.json'
    save(manifest, {'base_database_sha256': sha(base / 'dataset.sqlite'), 'usage_ledger_sha256': sha(admitted_ledger),
        'guard_version': GUARD_VERSION, 'lineage_path': str(lineage), 'lineage_sha256': sha(lineage),
        'record_scope': {'inventory_canonical_n': len(works)},
        'inputs': {('alias' if kind == 'aliases' else kind): {'path': str(path), 'sha256': sha(path)} for kind, path in paths.items()}})
    target = output / 'next-source'
    seal_next_projections(base_database=base / 'dataset.sqlite', bibliography_inputs=paths['bibliographic'],
        classification_inputs=paths['classification'], alias_inputs=paths['aliases'], projection_manifest=manifest,
        usage_ledger=admitted_ledger, output_dir=target)
    return target


def prepare_sources(records_path, output, *, historical_databases, official_conditions,
                    historical_mappings=(), limit=1000, registry_path=None, resume=False):
    records_path = Path(records_path).resolve(strict=True)
    official_conditions = Path(official_conditions).resolve(strict=True)
    output = Path(output).resolve()
    if (output.exists() and not resume) or output.is_relative_to(records_path.parent):
        raise ValueError('Fresh source preparation outside acquisition tree required')
    if not historical_databases:
        raise ValueError('Actual historical authority database required for exclusion')
    excluded_qids, old_proofs = historical_qids(historical_databases, historical_mappings)
    source_sha, condition_sha = sha(records_path), sha(official_conditions)
    selected, excluded = choose_records(records_path, excluded_qids, limit=limit)
    registry_copy = output / 'source-registry.json'
    source = output / 'sources/source-bindings.sqlite'
    ordered = output / 'ordered-inputs.jsonl'
    initial = output / 'usage-ledger-inputs.json'
    if resume:
        checkpoint = json.loads((output / 'admission-interrupted-checkpoint.v1.json').read_bytes())
        if checkpoint.get('selected') != limit or checkpoint.get('stopped_before_model_calls') is not True:
            raise ValueError('Explicit stopped pre-model source checkpoint required')
        for path, digest in checkpoint['files'].items():
            if sha(path) != digest:
                raise ValueError('Source admission checkpoint changed: ' + path)
        works = [WorkInput.model_validate_json(line) for line in ordered.read_bytes().splitlines()]
        ledger = json.loads(initial.read_bytes())
        with sqlite3.connect(source.resolve().as_uri() + '?mode=ro&immutable=1', uri=True) as db:
            qids = [db.execute('SELECT source_id FROM source_records WHERE version_id=?', (work.source_record_ids[0],)).fetchone()[0] for work in works]
        if qids != [row[2].source_id for row in selected]:
            raise ValueError('Checkpoint ordered authorities differ from frozen selection')
    else:
        # This existing projection resolves proven regions/unsupported language
        # codes only. Complete acquisition records/raw remain available.
        if str(ROOT / 'scripts') not in sys.path:
            sys.path.insert(0, str(ROOT / 'scripts'))
        from prepare_editorial_acquisitions_v1 import normalize_supported_metadata
        records = [normalize_supported_metadata(row[2]) for row in selected]
        output.mkdir(parents=True)
        registry_path = Path(registry_path or ROOT / 'config/sources.json').resolve(strict=True)
        registry_copy.write_bytes(registry_path.read_bytes())
        registry = RightsRegistry.from_file(registry_copy)
        source.parent.mkdir()
        with DatasetDB(source) as db:
            db.ingest(records)
            full = [w for w in db.work_inputs() if w.expression_id is None]
            by_qid = {}
            for work in full:
                qids = {db.connection.execute('SELECT source_id FROM source_records WHERE version_id=?', (ref,)).fetchone()[0] for ref in work.source_record_ids}
                if len(qids) != 1:
                    raise ValueError('Distinct new authorities merged unexpectedly')
                by_qid[next(iter(qids))] = work
        with sqlite3.connect(source) as db:
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.execute('PRAGMA journal_mode=DELETE')
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('New source database integrity failed')
        works = [scale_evidence_input(by_qid[row[2].source_id]) for row in selected]
        ordered.write_text(''.join(w.model_dump_json() + '\n' for w in works), encoding='utf-8', newline='\n')
        ledger = {'version': 'usage-ledger-v3', 'registry_path': str(registry_copy),
                  'registry_file_sha256': sha(registry_copy),
                  'registry_sha256': model_hash([p.model_dump(mode='json') for p in registry.policies.values()]),
                  'official_conditions': json.loads(official_conditions.read_bytes()), 'inputs': {}, 'revoked_input_hashes': [],
                  'derived_artifacts': {}, 'execution_recipes': {}}
    if len(works) != limit or len({w.work_id for w in works}) != limit:
        raise ValueError('New work identities do not form a distinct fixed pool')
    conditions = json.loads(official_conditions.read_bytes())
    if set(conditions) != {'wikidata-cc0'}:
        raise ValueError('Native source conditions require exact Wikidata policy binding')
    if ledger['official_conditions'] != conditions:
        raise ValueError('Frozen native conditions differ from current source ledger')

    def bind(values, database, path):
        digest = sha(path)
        for number, work in enumerate(values, 1):
            refs = {ref: source_binding(database, ref) for ref in work.source_record_ids}
            ledger['inputs'][model_hash(work)] = build_input_entry(work, refs,
                input_ref={'path': str(path), 'sha256': digest, 'line_number': number})

    if not resume:
        bind(works, source, ordered)
        save(initial, ledger)
    guard = UsageGuard(initial, expected_ledger_sha256=sha(initial))
    receipts = {usage: [guard.check_input(work, usage).model_dump(mode='json') for work in works] for usage in USAGES}
    save(output / ('admission-receipts-resumed.v1.json' if resume else 'admission-receipts.json'), receipts)
    failures = [{'usage': usage, 'work_id': works[i].work_id, 'reasons': row['reasons']}
                for usage, items in receipts.items() for i, row in enumerate(items) if not row['allowed']]
    if failures:
        save(output / 'admission-failures.json', failures)
        raise ValueError('New source per-purpose admission held; inspect admission-failures.json')
    next_dir = seal_scale_evidence(output, source, works, ledger, bind)
    groups = get_next_inputs(next_dir / 'dataset.sqlite')
    if {model_hash(w): w for w in groups['classification']} != {model_hash(w): w for w in works}:
        raise ValueError('Actual next classification projection differs from fixed input')
    for kind in ('classification', 'bibliographic', 'aliases'):
        bind(groups[kind], next_dir / 'dataset.sqlite', next_dir / (kind + '-inputs.jsonl'))
    packets = next_dir / 'derivations.jsonl'
    packet_sha = sha(packets)
    packet_values = [json.loads(line) for line in packets.read_text(encoding='utf-8').splitlines()]
    for number, packet in enumerate(packet_values, 1):
        ledger['derived_artifacts'][model_hash(packet['payload'])] = {
            'kind': packet['kind'], 'value_sha256': packet['value_sha256'],
            'originating_input_hashes': packet['originating_input_hashes'], 'raw_path': str(packets),
            'raw_sha256': packet_sha, 'format': 'jsonl', 'line_number': number,
            'locator': '/payload', 'recipe': packet['recipe']}
    final_ledger = output / 'usage-ledger-projections.json'
    save(final_ledger, ledger)
    mapping = [{'work_id': w.work_id, 'input_work_id': w.work_id, 'wikidata_qid': row[2].source_id,
                'catalogue_work_id': 'wd:' + row[2].source_id, 'genre_qids': row[3],
                'input_sha256': model_hash(w), 'source_record_ids': w.source_record_ids}
               for w, row in zip(works, selected)]
    save(output / 'work-mapping.json', mapping)
    save(output / 'excluded-candidates.json', excluded)
    chunks = []
    for start in range(0, len(works), 100):
        chunk_dir = output / 'chunks' / f'{start + 1:04d}-{min(start + 100, len(works)):04d}'
        chunk_dir.mkdir(parents=True)
        path = chunk_dir / 'ordered-inputs.jsonl'
        path.write_text(''.join(w.model_dump_json() + '\n' for w in works[start:start + 100]), encoding='utf-8', newline='\n')
        save(chunk_dir / 'manifest.json', {'ordered_inputs_sha256': sha(path), 'work_count': len(works[start:start + 100])})
        # Runtime admission reads only this window's ledger, while the exact
        # source versions/whole inputs remain shared and unchanged. This avoids
        # parsing every unrelated alias on every per-use admission.
        chunk_ids = {w.work_id for w in works[start:start + 100]}
        chunk_ledger = {key: deepcopy(value) for key, value in ledger.items() if key not in ('inputs', 'derived_artifacts')}
        chunk_ledger.update(inputs={}, derived_artifacts={})
        for kind in ('classification', 'bibliographic', 'aliases'):
            values = (works[start:start + 100] if kind == 'classification' else
                      [w for w in groups[kind] if w.work_id in chunk_ids])
            projection_path = path if kind == 'classification' else chunk_dir / (kind + '-inputs.jsonl')
            if kind != 'classification':
                projection_path.write_text(''.join(w.model_dump_json() + '\n' for w in values), encoding='utf-8', newline='\n')
            projection_sha = sha(projection_path)
            for line_number, work in enumerate(values, 1):
                entry = deepcopy(ledger['inputs'][model_hash(work)])
                entry['input_ref'] = {'path': str(projection_path), 'sha256': projection_sha, 'line_number': line_number}
                chunk_ledger['inputs'][model_hash(work)] = entry
        selected_packets = [packet for packet in packet_values if set(packet['originating_input_hashes']) <= set(chunk_ledger['inputs'])]
        chunk_packets = chunk_dir / 'derivations.jsonl'
        chunk_packets.write_text(''.join(json.dumps(packet, ensure_ascii=False, sort_keys=True) + '\n' for packet in selected_packets), encoding='utf-8', newline='\n')
        chunk_packet_sha = sha(chunk_packets)
        for line_number, packet in enumerate(selected_packets, 1):
            entry = deepcopy(ledger['derived_artifacts'][model_hash(packet['payload'])])
            entry.update(raw_path=str(chunk_packets), raw_sha256=chunk_packet_sha, line_number=line_number)
            chunk_ledger['derived_artifacts'][model_hash(packet['payload'])] = entry
        chunk_ledger_path = chunk_dir / 'usage-ledger.json'
        save(chunk_ledger_path, chunk_ledger)
        chunks.append({'index': len(chunks), 'run_name': 'scale-' + chunk_dir.name,
                       'inputs_path': str(path), 'inputs_sha256': sha(path), 'selected': len(works[start:start + 100]),
                       'usage_ledger_path': str(chunk_ledger_path), 'usage_ledger_sha256': sha(chunk_ledger_path)})
    if sha(records_path) != source_sha or sha(official_conditions) != condition_sha:
        raise ValueError('Acquisition or official conditions changed during preparation')
    report = {'version': VERSION, 'stage': 'prepared_not_started', 'selected': len(works),
              'records_path': str(records_path), 'records_sha256': source_sha,
              'official_conditions_path': str(official_conditions), 'official_conditions_sha256': condition_sha,
              'ordered_inputs_path': str(ordered), 'ordered_inputs_sha256': sha(ordered),
              'source_database': str(next_dir / 'dataset.sqlite'), 'source_database_sha256': sha(next_dir / 'dataset.sqlite'),
              'usage_ledger_path': str(final_ledger), 'usage_ledger_sha256': sha(final_ledger),
              'historical_exclusion_proofs': old_proofs, 'historical_qids_excluded': len(excluded_qids),
              'with_explicit_genre_claims': sum(bool(row[3]) for row in selected),
              'original_languages': dict(Counter(w.original_language or 'unknown' for w in works)),
              'all_six_purpose_admissions_allowed': True, 'chunks': chunks,
              'model_calls': 0, 'web_requests': 0, 'old_classifications_modified': False}
    save(output / 'manifest.json', report)
    return report


def prepare_chunk(runtime_preparation, inputs, ledger_path, *, cli_version, run_name,
                  concurrency=2):
    """Fresh-interpreter entry: one frozen runtime, one context per <=100 works."""
    from recommend_book.classification.runner_v4 import build_run_config, resolve_executable, command_artifact_hashes
    from recommend_book.classification.validation import load_taxonomy
    from recommend_book.classification import native_attempt_v4
    from recommend_book import next_database_v4
    from recommend_book.jobs import canonical_json
    output = Path(runtime_preparation).resolve(strict=True)
    runtime = output / 'runtime'
    if not Path(native_attempt_v4.__file__).resolve().is_relative_to(runtime):
        raise ValueError('Fresh interpreter must import frozen v4 runtime')
    launcher = runtime / 'scripts/run_scale_guarded_runtime_v1.py'
    raw_launcher = (ROOT / 'scripts/run_scale_guarded_runtime_v1.py').read_bytes()
    if launcher.exists():
        if launcher.read_bytes() != raw_launcher:
            raise ValueError('Frozen scale launcher differs')
    else:
        launcher.write_bytes(raw_launcher)
    if concurrency not in (2, 4):
        raise ValueError('Scale concurrency is bounded to two or four')
    if not run_name or Path(run_name).name != run_name or '/' in run_name or '\\' in run_name:
        raise ValueError('Safe run name required')
    raw = Path(inputs).read_bytes()
    if not 1 <= len([line for line in raw.splitlines() if line.strip()]) <= 100:
        raise ValueError('One scale window is bounded to 1..100 works')
    config = build_run_config('gpt-6.1-sol', cli_version, load_taxonomy(), 5, execution_spec='provenance-v4')
    config_sha = hashlib.sha256(canonical_json(config).encode('utf-8')).hexdigest()
    source_ledger = Path(ledger_path).resolve(strict=True)
    ledger = json.loads(source_ledger.read_bytes())
    verifier = Path(native_attempt_v4.__file__).resolve()
    ledger.setdefault('execution_recipes', {})[config_sha] = {'verifier_path': str(verifier), 'verifier_sha256': sha(verifier)}
    implementation = Path(next_database_v4.__file__).resolve()
    ledger.setdefault('feature_recipes', {})['work-present-relevance-max-v1'] = {'path': str(implementation), 'sha256': sha(implementation)}
    frozen_ledger = output / ('scale-usage-ledger.' + run_name + '.v4.json')
    if frozen_ledger.exists():
        if json.loads(frozen_ledger.read_bytes()) != ledger:
            raise ValueError('Shared frozen scale ledger differs')
    else:
        save(frozen_ledger, ledger)
    helper = load_script('scale_guarded_preparation', runtime / 'scripts/run_guarded_classification_v4.py')
    namespace = runtime / 'data/autonomous-next-v3/classification' / run_name
    selected = output / ('selected-inputs-' + run_name)
    selected.mkdir()
    (selected / 'ordered.jsonl').write_bytes(raw)
    save(selected / 'manifest.json', {'ordered_inputs_sha256': hashlib.sha256(raw).hexdigest(), 'source_path': str(Path(inputs).resolve())})
    controls = {'batch_size': 5, 'concurrency': concurrency, 'timeout': 900,
                'max_run_seconds': 3600, 'max_observed_tokens': 2000000}
    prepared = helper.prepare(selected / 'ordered.jsonl', frozen_ledger, namespace,
        cli_version=cli_version, execution_spec='provenance-v4', **controls)
    freeze = load_script('scale_execution_freeze', runtime / 'scripts/freeze_classification_execution.py')
    freeze.build_run_config = lambda model, version, taxonomy, batch_size, **kwargs: build_run_config(
        model, version, taxonomy, batch_size, execution_spec='provenance-v4')
    context = output / ('execution-context-' + run_name)
    freeze.freeze_execution(selected / 'ordered.jsonl', context, model='gpt-6.1-sol', cli_version=cli_version,
                            execution_spec='provenance-v4', stop_on_rate_limit=True, **controls)
    command = resolve_executable()
    report = {'version': 'native-snapshot-preflight-v1', 'stage': 'prepared_not_started',
              'namespace': str(namespace), 'preparation_sha256': sha(namespace / 'preparation-manifest.json'),
              'config': config, 'config_sha256': config_sha, 'usage_ledger_path': str(frozen_ledger),
              'usage_ledger_sha256': sha(frozen_ledger), 'source_ledger_path': str(source_ledger),
              'source_ledger_sha256': sha(source_ledger), 'ordered_inputs_path': str(selected / 'ordered.jsonl'),
              'execution_context_manifest': str(context / 'manifest.json'), 'executable_command': command,
              'executable_sha256': command_artifact_hashes(command), 'model_invocations': 0,
              'guard_version': 'usage-guard-v4.0', 'old_recipes_rebound': False, 'controls': controls}
    save(output / ('preflight.' + run_name + '.json'), report)
    return {'stage': report['stage'], 'selected': prepared['selected'], 'run_name': run_name,
            'preparation_sha256': report['preparation_sha256'], 'config_sha256': config_sha}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('sources', 'resume-sources'):
        source = sub.add_parser(name)
        source.add_argument('--records', type=Path, required=True)
        source.add_argument('--output', type=Path, required=True)
        source.add_argument('--historical-database', type=Path, action='append', required=True)
        source.add_argument('--historical-mapping', type=Path, action='append', default=[])
        source.add_argument('--official-conditions', type=Path, required=True)
        source.add_argument('--limit', type=int, default=1000)
    for name in ('chunk', '_chunk'):
        chunk = sub.add_parser(name)
        chunk.add_argument('--runtime-preparation', type=Path, required=True)
        chunk.add_argument('--inputs', type=Path, required=True)
        chunk.add_argument('--usage-ledger', type=Path, required=True)
        chunk.add_argument('--cli-version', required=True)
        chunk.add_argument('--run-name', required=True)
        chunk.add_argument('--concurrency', type=int, choices=(2, 4), default=2)
    args = parser.parse_args()
    from recommend_book.scale_guard_context_v1 import enable_process_cache
    _cache = enable_process_cache()
    if args.command in ('sources', 'resume-sources'):
        result = prepare_sources(args.records, args.output, historical_databases=args.historical_database,
            historical_mappings=args.historical_mapping, official_conditions=args.official_conditions, limit=args.limit,
            resume=args.command == 'resume-sources')
    elif args.command == '_chunk':
        result = prepare_chunk(args.runtime_preparation, args.inputs, args.usage_ledger,
            cli_version=args.cli_version, run_name=args.run_name, concurrency=args.concurrency)
    else:
        builder = load_script('scale_runtime_builder', ROOT / 'scripts/prepare_native_snapshot_runtime_v1.py')
        builder.verify_source(args.runtime_preparation)
        env = dict(os.environ, PYTHONPATH=str(args.runtime_preparation.resolve() / 'runtime/src'), PYTHONDONTWRITEBYTECODE='1')
        command = [sys.executable, str(Path(__file__).resolve()), '_chunk',
            '--runtime-preparation', str(args.runtime_preparation.resolve()), '--inputs', str(args.inputs.resolve()),
            '--usage-ledger', str(args.usage_ledger.resolve()), '--cli-version', args.cli_version,
            '--run-name', args.run_name, '--concurrency', str(args.concurrency)]
        process = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, shell=False, timeout=900)
        (args.runtime_preparation / ('preflight.' + args.run_name + '.stdout.txt')).write_bytes(process.stdout)
        (args.runtime_preparation / ('preflight.' + args.run_name + '.stderr.txt')).write_bytes(process.stderr)
        if process.returncode:
            raise ValueError('Frozen scale chunk preparation failed; inspect retained stderr')
        print(process.stdout.decode('utf-8'))
        return
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
