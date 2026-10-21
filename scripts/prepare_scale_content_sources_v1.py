"""Reproduce supported content evidence from saved raw; never rename old IDs."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
from types import ModuleType, SimpleNamespace

from recommend_book.models import SourceRecord
from recommend_book.wikipedia_paragraphs import build_evidence

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_USAGES = ('acquire', 'local_storage', 'external_llm', 'display')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def adapt_saved_content(records_path, catalogue, output):
    records_path = Path(records_path).resolve(strict=True)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    candidates = [SourceRecord.model_validate_json(line) for line in records_path.read_bytes().splitlines() if line.strip()]
    with sqlite3.connect(Path(catalogue).resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.execute('BEGIN')
        admitted = {qid: (version, digest, payload) for qid, version, digest, payload in db.execute(
            'SELECT w.qid,w.version_id,v.record_sha256,v.record_json FROM works w '
            'JOIN source_versions v USING(version_id) WHERE w.display_allowed=1')}
        classified = {row[0] for row in db.execute('SELECT qid FROM native_classifications')}
    accepted, outcomes, counts, seen = [], [], Counter(), set()
    for candidate in candidates:
        qids = {item.value for item in candidate.identifiers if item.source == 'wikidata' and item.scope == 'work'}
        if len(qids) != 1:
            raise ValueError('Content source lacks one exact work QID')
        qid = next(iter(qids))
        if qid in seen:
            raise ValueError('Duplicate content authority')
        seen.add(qid)
        if qid in classified or qid not in admitted:
            counts['baseline_native_or_not_display_eligible'] += 1
            continue
        version, digest, payload = admitted[qid]
        if hashlib.sha256(payload.encode('utf-8')).hexdigest() != digest:
            raise ValueError('Saved catalogue source record SHA differs')
        work = SourceRecord.model_validate_json(payload)
        if work.source != 'wikidata' or work.source_id != qid:
            raise ValueError('Actual catalogue authority is not exact Wikidata work')
        if sha(candidate.raw_path) != candidate.raw_sha256 or sha(work.raw_path) != work.raw_sha256:
            raise ValueError('Saved raw source SHA differs')
        raw = json.loads(Path(candidate.raw_path).read_bytes())
        pages = raw['query']['pages']; pages = list(pages.values()) if isinstance(pages, dict) else pages
        matches = [page for page in pages if page.get('pageid') == candidate.extra['page_id']
                   and any(rev.get('revid') == candidate.extra['revision_id'] for rev in page.get('revisions', []))]
        if len(matches) != 1:
            raise ValueError('Exact saved article page/revision is ambiguous')
        language = candidate.extra['article_language']
        artifact = SimpleNamespace(path=candidate.raw_path, sha256=candidate.raw_sha256,
            source_url=f'https://{language}.wikipedia.org/w/api.php', retrieved_at=candidate.retrieved_at)
        try:
            evidence, ledger, sections = build_evidence(work, matches[0], artifact, candidate.extra['article_language'])
        except (ValueError, KeyError) as exc:
            counts['actual_supported_recipe_held'] += 1
            outcomes.append({'qid': qid, 'status': 'held', 'reason': str(exc)})
            continue
        narrative = [item for item in evidence if item.kind in ('synopsis', 'excerpt')]
        if not narrative:
            counts['actual_supported_narrative_absent'] += 1
            outcomes.append({'qid': qid, 'status': 'held', 'sections': sections})
            continue
        # New evidence is independently rebuilt from the raw article and current
        # WD authority by the existing recipe. Old v2 bytes/records stay intact.
        enriched = work.model_copy(update={'evidence': [*work.evidence, *evidence],
            'extra': {**work.extra, 'scale_content_supported_v1': {
                'parent_content_raw_path': candidate.raw_path,
                'parent_content_raw_sha256': candidate.raw_sha256,
                'authority_version_id': version, 'authority_record_sha256': digest,
                'recipe': 'screened-paragraphs-v1', 'evidence_proofs': ledger}}}, deep=True)
        accepted.append(enriched)
        counts['supported_records'] += 1
        counts['supported_narrative_evidence'] += len(narrative)
        counts['supported_narrative_characters'] += sum(len(item.text) for item in narrative)
        outcomes.append({'qid': qid, 'status': 'reproduced_proposal_not_usage_admitted',
            'old_candidate_narrative_characters': sum(len(item.text) for item in candidate.evidence if item.kind in ('synopsis', 'excerpt')),
            'supported_narrative_characters': sum(len(item.text) for item in narrative),
            'new_evidence_ids': [item.evidence_id for item in evidence], 'sections': sections})
    path = output / 'records.jsonl'
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        for record in accepted:
            stream.write(record.model_dump_json() + '\n')
    save(output / 'outcomes.json', outcomes)
    report = {'version': 'scale-supported-content-proposal-v1', 'source_records_path': str(records_path),
        'source_records_sha256': sha(records_path), 'records_path': str(path), 'records_sha256': sha(path),
        'counts': dict(counts), 'actual_usage_guard_admitted': False, 'model_calls': 0,
        'network_requests': 0, 'old_sources_modified': False,
        'recipe_path': str(Path(build_evidence.__code__.co_filename).resolve()),
        'recipe_sha256': sha(build_evidence.__code__.co_filename)}
    save(output / 'manifest.json', report)
    return report


def native_preparer(*, excluded_qids, run_name, window_index, resumed=False):
    """Reuse the actual sealed-source recipe without modifying historical code."""
    path = ROOT / 'scripts/prepare_scale_classification_v1.py'
    source = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    replacements = {
        "VERSION = 'scale-classification-source-v1'": "VERSION = 'scale-content-classification-source-v1'",
        "if set(conditions) != {'wikidata-cc0'}:":
            "if not {'wikidata-cc0', 'wikipedia-screened-prose'} <= set(conditions):",
        "for usage, items in receipts.items() for i, row in enumerate(items) if not row['allowed']":
            "for usage, items in receipts.items() for i, row in enumerate(items) if usage in REQUIRED_USAGES and not row['allowed']",
        "'run_name': 'scale-' + chunk_dir.name": "'run_name': CONTENT_RUN_NAME",
        "'all_six_purpose_admissions_allowed': True, 'chunks': chunks,":
            "'all_six_purpose_admissions_allowed': all(r['allowed'] for rows in receipts.values() for r in rows), "
            "'required_purpose_admissions_allowed': True, 'required_input_usages': list(REQUIRED_USAGES), "
            "'window_index': CONTENT_WINDOW_INDEX, 'chunks': chunks,",
        'new_scale_input_keeps_subjects_and_two_preferred_descriptions; full_SourceRecord_retained':
            'content_input_keeps_supported_narrative_notes_subjects_and_two_preferred_descriptions; full_SourceRecord_retained',
    }
    for before, after in replacements.items():
        if source.count(before) != 1:
            raise ValueError('Immutable content source preparation anchor drift: ' + before[:70])
        source = source.replace(before, after)
    if resumed:
        source = source.replace("initial = output / 'usage-ledger-inputs.json'",
                                "initial = output / 'usage-ledger-inputs-narrowed-v1.json'")
        source = source.replace("save(output / 'admission-failures.json', failures)",
                                "save(output / 'admission-failures-narrowed-v1.json', failures)")
    module = ModuleType('new_content_native_source_preparer')
    module.__file__ = str(path)
    module.REQUIRED_USAGES = REQUIRED_USAGES
    module.CONTENT_RUN_NAME = run_name
    module.CONTENT_WINDOW_INDEX = window_index
    exec(compile(source, str(path), 'exec'), module.__dict__)

    def choose(path, _old_exclusions, *, limit):
        selected, rejected = [], []
        for number, line in enumerate(Path(path).read_bytes().splitlines(), 1):
            if not line.strip():
                continue
            record = SourceRecord.model_validate_json(line)
            if record.source_id in excluded_qids:
                rejected.append({'source_id': record.source_id, 'reasons': ['already_native_classified']})
                continue
            raw = json.loads(Path(record.raw_path).read_bytes())
            if sha(record.raw_path) != record.raw_sha256:
                raise ValueError('Content authority raw changed')
            issues = module.scope_issues(record, raw['entities'][record.source_id])
            narrative = [e for e in record.evidence if e.llm_input_allowed and e.kind in ('synopsis', 'excerpt')
                         and e.rights_policy_id != 'wikidata-cc0']
            if issues or not narrative:
                rejected.append({'source_id': record.source_id, 'reasons': issues or ['supported_narrative_absent']})
                continue
            genres = [v['id'] for v in module.claim_values(raw['entities'][record.source_id], 'P136')
                      if isinstance(v, dict) and 'id' in v]
            selected.append((0, number, record, genres))
        if len(selected) != limit:
            raise ValueError('Exact new content window differs from eligible selected count')
        return selected, rejected

    original_select = module.scale_evidence_input

    def retain_content(work):
        metadata = original_select(work)
        ids = {e.evidence_id for e in metadata.evidence}
        return work.model_copy(update={'evidence': [e for e in work.evidence if e.llm_input_allowed
            and (e.evidence_id in ids or e.rights_policy_id != 'wikidata-cc0')]}, deep=True)

    module.choose_records = choose
    module.scale_evidence_input = retain_content
    # Exclusion is a current-native QID observation, not an old rights/feature
    # readmission. The main database is never copied or hashed here.
    module.historical_qids = lambda *_args, **_kwargs: (set(excluded_qids), [])
    return module


def narrowed_registry(original, output):
    """Require stronger existing prose proof for acquire/storage; add no grant."""
    original = Path(original).resolve(strict=True)
    output = Path(output).resolve()
    registry = json.loads(original.read_bytes())
    policies = [p for p in registry['rights_policies'] if p['policy_id'] == 'wikipedia-screened-prose']
    if len(policies) != 1:
        raise ValueError('One original screened-prose policy required')
    for usage in ('acquire', 'local_storage'):
        if policies[0]['usages'][usage] != 'allowed':
            raise ValueError('Original allowed content purpose changed')
        policies[0]['usages'][usage] = 'conditional'
    if output.exists():
        if json.loads(output.read_bytes()) != registry:
            raise ValueError('Narrowed content policy sidecar differs')
    else:
        save(output, registry)
    return output


def prepare_native_window(records_path, catalogue, output, official_conditions, *, window_index, run_name, resumed=False):
    from recommend_book.scale_guard_context_v1 import enable_process_cache
    from recommend_book.rights import RightsRegistry
    from recommend_book.usage_guard import model_hash
    records_path = Path(records_path).resolve(strict=True)
    rows = [SourceRecord.model_validate_json(line) for line in records_path.read_bytes().splitlines() if line.strip()]
    if not 1 <= len(rows) <= 100 or len({r.source_id for r in rows}) != len(rows):
        raise ValueError('One distinct actual content window of1..100 required')
    with sqlite3.connect(Path(catalogue).resolve().as_uri() + '?mode=ro', uri=True) as db:
        excluded = {row[0] for row in db.execute('SELECT qid FROM native_classifications')}
        eligible = {row[0] for row in db.execute('SELECT qid FROM works WHERE display_allowed=1 AND recommendable=1')}
    if any(r.source_id in excluded or r.source_id not in eligible for r in rows):
        raise ValueError('Native content window contains old native or currently ineligible authority')
    output = Path(output).resolve()
    registry = narrowed_registry(ROOT / 'config/sources.json',
        output.parents[1] / 'native-source-registry-v1.json')
    if resumed:
        original_receipts = output / 'admission-receipts.json'
        prior = json.loads(original_receipts.read_bytes())
        if any(not r['allowed'] for usage in ('external_llm', 'display') for r in prior[usage]):
            raise ValueError('Only known mixed-source acquire/storage branch can use this resume')
        ledger = json.loads((output / 'usage-ledger-inputs.json').read_bytes())
        ledger.update(registry_path=str(registry), registry_file_sha256=sha(registry),
            registry_sha256=model_hash([p.model_dump(mode='json') for p in RightsRegistry.from_file(registry).policies.values()]))
        initial = output / 'usage-ledger-inputs-narrowed-v1.json'
        save(initial, ledger)
        files = [records_path, output / 'ordered-inputs.jsonl', output / 'sources/source-bindings.sqlite',
                 output / 'usage-ledger-inputs.json', original_receipts, initial, registry,
                 Path(official_conditions).resolve()]
        save(output / 'admission-interrupted-checkpoint.v1.json', {
            'selected': len(rows), 'stopped_before_model_calls': True,
            'reason': 'actual mixed-source allowed-state branch held acquire/storage; require stronger conditional proof',
            'source_db_and_inputs_reused_without_reingest': True,
            'files': {str(p): sha(p) for p in files}})
    _cache = enable_process_cache()
    helper = native_preparer(excluded_qids=excluded, run_name=run_name, window_index=window_index, resumed=resumed)
    result = helper.prepare_sources(records_path, output, historical_databases=[catalogue],
        official_conditions=official_conditions, limit=len(rows), registry_path=registry, resume=resumed)
    return {'source_preparation': str(Path(output).resolve()), 'selected': result['selected'],
        'required_purpose_admissions_allowed': result['required_purpose_admissions_allowed'],
        'required_input_usages': result['required_input_usages'], 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--catalogue', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native', action='store_true')
    parser.add_argument('--resume-native', action='store_true')
    parser.add_argument('--official-conditions', type=Path)
    parser.add_argument('--window-index', type=int)
    parser.add_argument('--run-name')
    args = parser.parse_args()
    if args.native:
        if args.official_conditions is None or args.window_index is None or args.run_name is None:
            parser.error('Actual native window requires conditions, window index and unique run name')
        value = prepare_native_window(args.records, args.catalogue, args.output, args.official_conditions,
                                     window_index=args.window_index, run_name=args.run_name, resumed=args.resume_native)
    else:
        value = adapt_saved_content(args.records, args.catalogue, args.output)
    print(json.dumps(value, ensure_ascii=True))


if __name__ == '__main__':
    main()
