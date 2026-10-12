"""Cache-first, serialized independent evidence acquisition; no LLM execution."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse

from recommend_book.autonomous_enrichment import VERSION, SECTIONS, append_proposal, select_pilot, text_sha, verified_entity, wikipedia_evidence, year_bin, assess_ndl_identity
from recommend_book.ingestion.core import HTTPFetcher, RawArtifact, RawStore
from recommend_book.ingestion.ndl import NDLAdapter
from recommend_book.normalization import normalize_isbn
from recommend_book.models import SourceRecord, WorkInput, Evidence
from recommend_book.rights import RightsRegistry


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        for row in rows:
            stream.write((row.model_dump_json() if hasattr(row, 'model_dump_json') else json.dumps(row, ensure_ascii=False)) + '\n')
    temp.replace(path)


def load_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def verify_baseline(path):
    baseline = json.loads(Path(path).read_text(encoding='utf-8'))
    for artifact in baseline['artifacts']:
        if sha(artifact['path']) != artifact['sha256']:
            raise ValueError('Frozen baseline hash mismatch: ' + artifact['path'])
    return baseline


def cache_index(manifests):
    index = {}
    for manifest in manifests:
        if not Path(manifest).exists():
            continue
        for row in load_lines(manifest):
            host = urlparse(row['source_url']).hostname or ''
            if not host.endswith('.wikipedia.org') or row.get('metadata', {}).get('status_code', 200) != 200:
                continue
            raw = Path(row['path']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != row['sha256']:
                raise ValueError('cache raw hash mismatch')
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            pages = payload.get('query', {}).get('pages', [])
            pages = pages.values() if isinstance(pages, dict) else pages
            artifact = RawArtifact(row['sha256'], row['path'], row['size'], row['retrieved_at'], row['source_url'])
            for page in pages:
                qid = page.get('pageprops', {}).get('wikibase_item')
                if qid and page.get('revisions'):
                    index[(qid, host.split('.')[0])] = (page, artifact)
    return index


def denied_hashes():
    denied = set()
    counts = Counter()
    for path in [Path('data/openlibrary-complete-v1/candidate-records.jsonl'), Path('data/poc-openlibrary-enriched-v1/accepted-records.jsonl')]:
        if not path.exists():
            continue
        for row in load_lines(path):
            for ev in row.get('evidence', []):
                if ev.get('kind') in {'description', 'synopsis', 'excerpt', 'note'} and not ev.get('llm_input_allowed'):
                    denied.add(text_sha(ev['text']))
                    counts['openlibrary_denied_evidence_observations'] += 1
    path = Path('data/wikipedia-rescreen-v2/records.jsonl')
    if path.exists():
        for row in load_lines(path):
            for screen in row.get('extra', {}).get('screening', {}).values():
                if screen.get('rejected_paragraphs') and screen.get('text'):
                    denied.add(text_sha(screen['text']))
                    counts['previous_partially_screened_sections'] += 1
    counts['unique_denied_text_hashes'] = len(denied)
    return denied, dict(counts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['pilot', 'full'], default='pilot')
    parser.add_argument('--root', type=Path, default=Path('data/autonomous-poc-v1/enrichment'))
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--ndl-alternative', action='store_true', help='Exact ISBN/author-authority bounded secondary route, after Wikipedia phase completes')
    parser.add_argument('--reassess-ndl-from', type=Path, help='Read a finished NDL checkpoint offline; write assessment only in a new root')
    parser.add_argument('--scope-audit', action='store_true', help='Read all 2020 canonical/source scopes; save explicit edition-class quarantines')
    args = parser.parse_args()
    root = args.root
    root.mkdir(parents=True, exist_ok=True)
    baseline_path = Path('data/autonomous-poc-v1/baseline-manifest.json')
    verify_baseline(baseline_path)
    inputs_path = Path('data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/all-inputs.jsonl')
    world_path = Path('data/recommendation-poc-v3/world-works.json')
    inputs = {r['work_id']: WorkInput.model_validate(r) for r in load_lines(inputs_path)}
    world_payload = json.loads(world_path.read_text(encoding='utf-8'))
    world = world_payload if isinstance(world_payload, list) else world_payload.get('works', [])
    rows = [dict(r) for r in world if not r.get('features')]
    targets = {}
    db = sqlite3.connect('file:' + str(Path('data/poc-openlibrary-enriched-v1/dataset.sqlite').resolve()).replace('\\', '/') + '?mode=ro', uri=True)
    for canonical, version, serialized in db.execute("SELECT w.canonical_id,r.version_id,r.record_json FROM works w JOIN source_records r ON w.source_version_id=r.version_id JOIN source_heads h ON h.version_id=r.version_id WHERE r.source='wikidata' ORDER BY r.source_id"):
        if canonical in inputs:
            if canonical in targets:
                raise ValueError('multiple Wikidata work targets: ' + canonical)
            targets[canonical] = (version, SourceRecord.model_validate_json(serialized))
    db.close()
    if args.scope_audit:
        return scope_audit(root, world, targets, baseline_path)
    for row in rows:
        row['work_kind'] = targets[row['work_id']][1].work_kind
    if args.reassess_ndl_from:
        return reassess_ndl_snapshot(args.reassess_ndl_from, root, targets, baseline_path)
    if args.ndl_alternative:
        return collect_ndl_alternative(root, rows, targets, baseline_path)
    pilot = select_pilot(rows, 100)
    if len(rows) != 1026 or len(pilot) != 100:
        raise ValueError('frozen frame count mismatch')
    for name, frame in [('candidate-frame.jsonl', rows), ('pilot-frame.jsonl', pilot)]:
        target = root / name
        if target.exists() and load_lines(target) != frame:
            raise ValueError('fixed frame mismatch')
        if not target.exists():
            jsonl(target, frame)
    if args.phase == 'full':
        pilot_manifest = json.loads((root / 'pilot' / 'manifest.json').read_text(encoding='utf-8'))
        if not pilot_manifest['accepted_works'] or pilot_manifest['status'] != 'complete_with_holds':
            raise ValueError('pilot has not established usable evidence')
    selected = pilot if args.phase == 'pilot' else rows
    denied, deny_counts = denied_hashes()
    jsonl(root / 'preserved-denied-text-hashes.jsonl', [{'text_sha256': h} for h in sorted(denied)])
    registry = RightsRegistry.default()
    context = {'version': VERSION, 'baseline_sha256': sha(baseline_path), 'inputs_sha256': sha(inputs_path), 'world_sha256': sha(world_path),
               'policy_sha256': text_sha(json.dumps(registry.policies['wikipedia-screened-prose'].model_dump(), sort_keys=True)),
               'sections_sha256': text_sha(json.dumps(SECTIONS, sort_keys=True)), 'denied_sha256': text_sha(json.dumps(sorted(denied))),
               'implementation_sha256': sha(Path(__file__).parents[1] / 'src/recommend_book/autonomous_enrichment.py')}
    checkpoint_path = root / 'checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else {'context': context, 'pages': {}, 'network_requests': 0}
    if checkpoint['context'] != context:
        raise ValueError('checkpoint context mismatch; use new namespace')
    cache = cache_index([Path('data/wikipedia-frame-v2/raw/manifest.jsonl'), Path('data/autonomous-poc-v1/enrichment/raw/manifest.jsonl'), Path('data/autonomous-poc-v1/enrichment/strict-v2/raw/manifest.jsonl'), root / 'raw/manifest.jsonl'])
    fetcher = HTTPFetcher(RawStore(root / 'raw'), min_interval=1.1, max_attempts=3,
                          user_agent='recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)')
    groups = defaultdict(list)
    selections = {}
    for row in selected:
        canonical = row['work_id']
        version, record = targets[canonical]
        if version not in inputs[canonical].source_record_ids:
            raise ValueError('target source version absent from baseline input')
        entity = verified_entity(record)
        links = entity.get('sitelinks', {})
        languages = list(dict.fromkeys([record.original_language, 'en', 'ja', *SECTIONS]))
        choices = [(lang, links[lang + 'wiki']['title']) for lang in languages if lang in SECTIONS and lang + 'wiki' in links][:2]
        selections[canonical] = {'work_id': canonical, 'wikidata_work_id': record.source_id, 'stratum': [row.get('original_language'), year_bin(row.get('first_publication_year')), record.work_kind],
                                  'choices': choices, 'no_article_reason': None if choices else 'no_supported_work_sitelink'}
        for language, title in choices:
            key = record.source_id + ':' + language
            if not args.offline and checkpoint['pages'].get(key, {}).get('offline_only'):
                del checkpoint['pages'][key]
            if key not in checkpoint['pages']:
                groups[language].append((canonical, title))

    def process(canonical, language, page, artifact, cache_hit):
        version, record = targets[canonical]
        existing = {text_sha(e.text) for e in inputs[canonical].evidence}
        key = record.source_id + ':' + language
        try:
            evidence, ledger, outcomes = wikipedia_evidence(record, version, canonical, page, artifact, language, denied, existing, args.phase)
            checkpoint['pages'][key] = {'work_id': canonical, 'evidence': [e.model_dump() for e in evidence], 'ledger': ledger, 'outcomes': outcomes, 'cache_hit': cache_hit,
                                        'raw_path': artifact.path, 'raw_sha256': artifact.sha256}
        except (ValueError, KeyError, TypeError) as exc:
            checkpoint['pages'][key] = {'work_id': canonical, 'evidence': [], 'ledger': [], 'reason': str(exc), 'cache_hit': cache_hit, 'raw_path': artifact.path, 'raw_sha256': artifact.sha256}
        atomic(checkpoint_path, checkpoint)

    for language, candidates in groups.items():
        pending = []
        for canonical, title in candidates:
            record = targets[canonical][1]
            cached = cache.get((record.source_id, language))
            if cached:
                process(canonical, language, *cached, True)
            else:
                pending.append((canonical, title))
        if args.offline:
            for canonical, _ in pending:
                record = targets[canonical][1]
                checkpoint['pages'][record.source_id + ':' + language] = {'work_id': canonical, 'evidence': [], 'ledger': [], 'reason': 'offline_cache_missing', 'offline_only': True}
            continue
        for start in range(0, len(pending), 50):
            batch = pending[start:start + 50]
            response = fetcher.get(f'https://{language}.wikipedia.org/w/api.php', source='wikipedia', params={
                'action': 'query', 'titles': '|'.join(title for _, title in batch), 'prop': 'pageprops|revisions',
                'ppprop': 'wikibase_item|disambiguation', 'rvprop': 'ids|timestamp|content', 'rvslots': 'main', 'format': 'json', 'formatversion': 2, 'maxlag': 5})
            checkpoint['network_requests'] += response.attempts
            if response.status != 'success':
                for canonical, _ in batch:
                    record = targets[canonical][1]
                    checkpoint['pages'][record.source_id + ':' + language] = {'work_id': canonical, 'evidence': [], 'ledger': [], 'reason': 'fetch_failed:' + str(response.error), 'attempts': response.attempts}
            else:
                payload = json.loads(response.content)
                pages = payload.get('query', {}).get('pages', [])
                pages = list(pages.values()) if isinstance(pages, dict) else pages
                by_qid = {p.get('pageprops', {}).get('wikibase_item'): p for p in pages}
                for canonical, _ in batch:
                    record = targets[canonical][1]
                    page = by_qid.get(record.source_id)
                    if page:
                        process(canonical, language, page, response.artifact, False)
                    else:
                        checkpoint['pages'][record.source_id + ':' + language] = {'work_id': canonical, 'evidence': [], 'ledger': [], 'reason': 'api_error_or_raw_identity_mismatch', 'api_error': payload.get('error'), 'raw_path': response.artifact.path, 'raw_sha256': response.artifact.sha256}
            atomic(checkpoint_path, checkpoint)
            print(json.dumps({'language': language, 'processed_pages': len(checkpoint['pages']), 'requests': checkpoint['network_requests']}), flush=True)

    records, proposals, ledger, outcomes = [], [], [], []
    for row in selected:
        canonical = row['work_id']
        version, record = targets[canonical]
        evs, rows_ledger = [], []
        signatures = {text_sha(e.text) for e in inputs[canonical].evidence}
        for language, _ in selections[canonical]['choices']:
            outcome = checkpoint['pages'].get(record.source_id + ':' + language, {})
            for ev, led in zip(outcome.get('evidence', []), outcome.get('ledger', []), strict=True):
                ev = Evidence.model_validate(ev)
                if text_sha(ev.text) not in signatures:
                    signatures.add(text_sha(ev.text))
                    evs.append(ev)
                    rows_ledger.append(led)
        if evs:
            new_record, proposal = append_proposal(record, inputs[canonical], evs)
            records.append(new_record)
            proposals.append(proposal)
            ledger.extend(rows_ledger)
        page_outcomes = [checkpoint['pages'].get(record.source_id + ':' + lang, {}) for lang, _ in selections[canonical]['choices']]
        outcomes.append({**selections[canonical], 'status': 'accepted' if evs else 'held', 'new_evidence_count': len(evs),
                         'page_outcomes': page_outcomes, 'baseline_source_record_ids': inputs[canonical].source_record_ids,
                         'baseline_input_sha256': text_sha(inputs[canonical].model_dump_json())})
    phase_root = root / args.phase
    jsonl(phase_root / 'accepted-source-records.jsonl', records)
    jsonl(phase_root / 'accepted-inputs.jsonl', proposals)
    jsonl(phase_root / 'accepted-evidence-ledger.jsonl', ledger)
    jsonl(phase_root / 'outcomes.jsonl', outcomes)
    verify_baseline(baseline_path)
    files = {path.name: {'path': str(path), 'sha256': sha(path), 'bytes': path.stat().st_size} for path in phase_root.glob('*.jsonl')}
    manifest = {'status': 'complete_with_holds', 'phase': args.phase, 'candidate_works': len(rows), 'selected_works': len(selected),
        'accepted_works': len(records), 'held_works': len(selected) - len(records), 'accepted_evidence': len(ledger),
        'synopsis_evidence': sum(r['locator'].split(';section:')[1].split(';')[0] != 'lead' for r in ledger),
        'network_requests_total': checkpoint['network_requests'], 'cached_pages': sum(p.get('cache_hit', False) for p in checkpoint['pages'].values()),
        'raw_saved_bytes': sum(p.stat().st_size for p in (root / 'raw').rglob('*.bin')), 'preserved_denials': deny_counts,
        'canonical_ids_unchanged': True, 'baseline_artifacts_unchanged': True, 'metadata_changes': 0,
        'proposal_source_refs': 'baseline refs retained exactly; final immutable version refs and DB input hashes are established by database lane',
        'human_reviewed': False, 'human_gold_generated': False, 'llm_calls': 0, 'wdqs_requests': 0,
        'stratified_sampling': 'one per language/year-bin/material stratum followed by stable SHA256 fill; rare strata oversampled, not population representative',
        'screening': 'whole section withheld if any paragraph is rejected; automated exclusions do not establish legal clearance of unmarked third-party text',
        'context': context, 'files': files}
    atomic(phase_root / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ['status', 'phase', 'accepted_works', 'held_works', 'accepted_evidence', 'network_requests_total']}), flush=True)
    return 0


def collect_ndl_alternative(root, rows, targets, baseline_path):
    """Only query explicit ISBNs from saved work entities, never similar titles.

    Save metadata candidates independently. Work-level evidence ingestion needs
    the database lane's NDL-specific raw validator, so this route does not silently
    place edition or unresolved abstract text into accepted Wikipedia inputs.
    """
    final = root / 'full/manifest.json'
    if not final.exists() or json.loads(final.read_text(encoding='utf-8'))['status'] != 'complete_with_holds':
        raise ValueError('Finish Wikimedia serial acquisition before NDL alternative')
    accepted = {r['work_id'] for r in load_lines(root / 'full/accepted-inputs.jsonl')}
    output = root / 'ndl-alternative'
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output / 'checkpoint.json'
    context = {'baseline_sha256': sha(baseline_path), 'provider': 'iss-ndl-opac', 'max_records_per_isbn': 5, 'identity_version': VERSION}
    checkpoint = json.loads(checkpoint_path.read_text(encoding='utf-8')) if checkpoint_path.exists() else {'context': context, 'queries': {}}
    if checkpoint['context'] != context:
        raise ValueError('NDL checkpoint context mismatch')
    fetcher = HTTPFetcher(RawStore(root / 'raw'), min_interval=1.1, max_attempts=3,
                          user_agent='recommend-book-research/0.1 (https://github.com/ayutaz/recommend-book)')
    adapter = NDLAdapter(fetcher, approved_providers={'iss-ndl-opac'})
    frame = []
    for row in rows:
        canonical = row['work_id']
        if canonical in accepted:
            continue
        work = targets[canonical][1]
        entity = verified_entity(work)
        isbn_values = {n for prop in ['P212', 'P957'] for claim in entity.get('claims', {}).get(prop, [])
                       if claim.get('rank') != 'deprecated' and isinstance(value := claim.get('mainsnak', {}).get('datavalue', {}).get('value'), str) and (n := normalize_isbn(value))}
        for isbn in sorted(isbn_values):
            frame.append({'work_id': canonical, 'wikidata_work_id': work.source_id, 'isbn': isbn, 'target_raw_path': work.raw_path,
                          'target_raw_sha256': work.raw_sha256, 'locator': 'entities.' + work.source_id + '.claims.P212/P957'})
            key = canonical + ':' + isbn
            if key in checkpoint['queries']:
                continue
            result = adapter.search_sru(f'isbn="{isbn}" AND mediatype="books"', provider='iss-ndl-opac', max_records=5)
            observed = []
            for edition in result.records:
                assessment = assess_ndl_identity(work, edition)
                permitted_subject = [e.model_dump() for e in edition.evidence if e.kind == 'subject' and e.llm_input_allowed]
                observed.append({'record': edition.model_dump(), 'assessment': assessment, 'permitted_subject': permitted_subject,
                                 'status': 'eligible_edition_subject_requires_raw_validator' if assessment['accepted'] and permitted_subject else 'held',
                                 'abstract_or_description_retained_as_llm_input': False})
            checkpoint['queries'][key] = {'work_id': canonical, 'isbn': isbn, 'query': f'isbn="{isbn}" AND mediatype="books"',
                'collection_status': result.status, 'total_available': result.total_available, 'returned_records': len(result.records),
                'limit': 5, 'provider': 'iss-ndl-opac', 'errors': result.errors, 'metadata': result.metadata, 'observations': observed}
            atomic(checkpoint_path, checkpoint)
            print(json.dumps({'alternative': 'ndl', 'queries': len(checkpoint['queries']), 'returned': len(result.records)}), flush=True)
    jsonl(output / 'query-frame.jsonl', frame)
    jsonl(output / 'observations.jsonl', checkpoint['queries'].values())
    observations = [o for q in checkpoint['queries'].values() for o in q['observations']]
    reasons = Counter(reason for o in observations for reason in o['assessment']['reasons'])
    manifest = {'status': 'complete_with_holds', 'provider': 'iss-ndl-opac', 'queries': len(checkpoint['queries']), 'queried_works': len({f['work_id'] for f in frame}),
                'edition_records': len(observations), 'strong_identity_candidates': sum(o['assessment']['accepted'] for o in observations),
                'eligible_subject_candidates': sum(o['status'] == 'eligible_edition_subject_requires_raw_validator' for o in observations),
                'unaccepted_prose_llm_inputs': 0, 'identity_hold_reasons': dict(reasons), 'max_records_per_query': 5,
                'truncated_queries': sum(q['collection_status'] == 'truncated' for q in checkpoint['queries'].values()),
                'failed_queries': sum(q['collection_status'] == 'failed' for q in checkpoint['queries'].values()),
                'absent_isbn_works_not_title_searched': len(rows) - len(accepted) - len({f['work_id'] for f in frame}),
                'rights_policy_id': 'ndl-iss-ndl-opac', 'rights': RightsRegistry.default().policies['ndl-iss-ndl-opac'].model_dump(),
                'canonical_changes': 0, 'llm_calls': 0, 'human_reviewed': False, 'human_gold_generated': False}
    verify_baseline(baseline_path)
    atomic(output / 'manifest.json', manifest)
    print(json.dumps({k: manifest[k] for k in ['queries', 'edition_records', 'strong_identity_candidates', 'eligible_subject_candidates']}), flush=True)
    return 0


def reassess_ndl_snapshot(checkpoint_path, root, targets, baseline_path):
    source = json.loads(checkpoint_path.read_text(encoding='utf-8'))
    output = root / 'ndl-identity-reassessment'
    if output.exists():
        raise ValueError('assessment output already exists; use new namespace')
    observations, eligible, reasons = [], [], Counter()
    for query in source['queries'].values():
        for previous in query['observations']:
            edition = SourceRecord.model_validate(previous['record'])
            work = targets[query['work_id']][1]
            assessment = assess_ndl_identity(work, edition)
            subject = [e.model_dump() for e in edition.evidence if e.kind == 'subject' and e.llm_input_allowed]
            row = {'work_id': query['work_id'], 'target_old_version_id': targets[query['work_id']][0], 'record': edition.model_dump(),
                   'assessment': assessment, 'permitted_subject': subject, 'previous_assessment': previous['assessment'],
                   'status': 'eligible_edition_subject' if assessment['accepted'] and subject else 'held',
                   'raw_locator': edition.extra.get('raw_locator'), 'raw_path': edition.raw_path, 'raw_sha256': edition.raw_sha256,
                   'rights_policy_id': edition.rights_policy_id, 'rights': RightsRegistry.default().policies[edition.rights_policy_id].model_dump(),
                   'baseline_input_changes': 0, 'abstract_or_description_llm_input': False}
            observations.append(row)
            reasons.update(assessment['reasons'])
            if row['status'] == 'eligible_edition_subject':
                eligible.append(row)
    jsonl(output / 'observations.jsonl', observations)
    jsonl(output / 'eligible-edition-subjects.jsonl', eligible)
    verify_baseline(baseline_path)
    manifest = {'status': 'complete_with_holds', 'source_checkpoint': str(checkpoint_path), 'source_checkpoint_sha256': sha(checkpoint_path),
                'source_queries': len(source['queries']), 'observed_editions': len(observations), 'eligible_editions': len(eligible),
                'eligible_works': len({r['work_id'] for r in eligible}), 'identity_hold_reasons': dict(reasons),
                'network_requests': 0, 'llm_calls': 0, 'canonical_changes': 0, 'frozen_inputs_changed': False,
                'uri_equivalence_primary_url': 'https://www.ndl.go.jp/dlib/standards/lod/uri',
                'input_ready': False, 'scope': 'identity-backed edition subject metadata; independent DB raw validation required before work-level activation',
                'human_reviewed': False, 'human_gold_generated': False, 'implementation_sha256': sha(Path(__file__).parents[1] / 'src/recommend_book/autonomous_enrichment.py')}
    atomic(output / 'manifest.json', manifest)
    print(json.dumps(manifest), flush=True)
    return 0


def scope_audit(root, world, targets, baseline_path):
    destination = root / 'work-scope-quarantine.json'
    if destination.exists():
        raise ValueError('scope audit exists; use new namespace')
    wp_path = Path('data/autonomous-poc-v1/enrichment/final-v1/full/accepted-source-records.jsonl')
    wp_ids = {r['source_id'] for r in load_lines(wp_path)}
    ndl_path = Path('data/autonomous-poc-v1/enrichment/ndl-scope-v3/ndl-identity-reassessment/observations.jsonl')
    ndl_by_work = defaultdict(list)
    for row in load_lines(ndl_path):
        ndl_by_work[row['work_id']].append(row)
    db_path = Path('data/poc-openlibrary-enriched-v1/dataset.sqlite')
    db = sqlite3.connect('file:' + str(db_path.resolve()).replace('\\', '/') + '?mode=ro', uri=True)
    proofs, quarantine, non_wd = {}, [], []
    for row in world:
        canonical = row['work_id']
        versions = [{'source': s, 'source_id': i, 'version_id': v} for s, i, v in db.execute(
            'SELECT r.source,r.source_id,r.version_id FROM works w JOIN source_records r ON r.version_id=w.source_version_id JOIN source_heads h ON h.version_id=r.version_id WHERE w.canonical_id=? ORDER BY r.source,r.source_id', (canonical,))]
        if canonical not in targets:
            non_wd.append({'work_id': canonical, 'active_source_versions': versions, 'status': 'no_active_wikidata_P31_raw; same-class test not applicable'})
            continue
        version, record = targets[canonical]
        entity = verified_entity(record)
        claims = entity.get('claims', {})
        matching = [i for i, claim in enumerate(claims.get('P31', [])) if claim.get('rank') != 'deprecated' and claim.get('mainsnak', {}).get('snaktype') == 'value' and claim.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') == 'Q3331189']
        if not matching:
            continue
        proof = record.extra.get('linked_label_provenance', {}).get('Q3331189')
        if proof:
            payload = Path(proof['raw_path']).read_bytes()
            if hashlib.sha256(payload).hexdigest() != proof['raw_sha256']:
                raise ValueError('class proof raw hash differs')
            class_entity = json.loads(payload)['entities']['Q3331189']
            if class_entity.get('id') != 'Q3331189':
                raise ValueError('class proof identity differs')
            proofs[proof['raw_sha256']] = {**proof, 'entity_id': 'Q3331189', 'labels': class_entity['labels'], 'locator': 'entities.Q3331189.labels'}
        quarantine.append({'work_id': canonical, 'wikidata_id': record.source_id, 'source': 'wikidata', 'source_id': record.source_id,
            'target_source_version_id': version, 'all_active_source_versions': versions, 'status': 'quarantine_from_final_world_eligibility',
            'reason': 'explicit_P31_version_edition_or_translation_conflicts_with_work_node_scope',
            'legacy_normalized_values': {'record_kind': record.record_kind, 'work_kind': record.work_kind, 'titles': [t.model_dump() for t in record.titles], 'identifiers': [i.model_dump() for i in record.identifiers]},
            'raw_path': record.raw_path, 'raw_sha256': record.raw_sha256,
            'raw_claims': {p: claims.get(p, []) for p in ['P31', 'P7937', 'P629', 'P212', 'P957']},
            'locators': {p: 'entities.' + record.source_id + '.claims.' + p for p in ['P31', 'P7937', 'P629', 'P212', 'P957']},
            'class_match_indexes': matching, 'ndl_edition_evidence': ndl_by_work.get(canonical, []), 'in_wikipedia_accepted_79': record.source_id in wp_ids,
            'originals_modified': False, 'human_reviewed': False})
    db.close()
    if any(r['in_wikipedia_accepted_79'] for r in quarantine):
        raise ValueError('quarantine unexpectedly intersects accepted enrichment')
    verify_baseline(baseline_path)
    report = {'version': 'work-scope-quarantine-v1', 'status': 'mechanical_audit', 'baseline_world_works': len(world),
        'scanned_wikidata_canonical_works': len(targets), 'non_wikidata_canonical_works': non_wd,
        'scope_class': 'Q3331189', 'scope_class_label': 'version, edition or translation', 'class_raw_proofs': list(proofs.values()),
        'quarantine_count': len(quarantine), 'quarantines': quarantine, 'wikipedia_accepted_overlap': 0,
        'network_requests': 0, 'llm_calls': 0, 'baseline_modified': False, 'input_database_path': str(db_path), 'input_database_sha256': sha(db_path),
        'correction_history': [{'incorrect': 'Q3331189 is short story collection; identity18 minus nonexclusive reasons gives15',
            'correct': 'Saved primary class labels show version, edition or translation. Identity-only accepted18 of24; held6. Authority4 and volume5 overlap3. NDL collection evidence is multiple contents and Short stories subject, not this QID.'}],
        'final_eligibility_rule': 'Exclude quarantined canonical IDs in new final delivery; preserve legacy immutable values; WP79 inputs unchanged.'}
    atomic(destination, report)
    print(json.dumps({'quarantines': len(quarantine), 'scanned_wikidata': len(targets), 'non_wikidata': len(non_wd), 'wp79_overlap': 0,
                      'ids': [(r['work_id'], r['wikidata_id']) for r in quarantine]}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
