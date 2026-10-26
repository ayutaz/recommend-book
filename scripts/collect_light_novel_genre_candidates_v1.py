"""Separate, non-admitted P136 light-novel candidate frame. No catalogue writes.

The type-only frame is retained. Existing entity bytes are reused, including
successful probes, rather than requesting the same entities again. A generic
literary work stays a candidate until a source proves its unit of identity.
"""
from __future__ import annotations
import argparse
from collections import Counter
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location('_ln_metadata_helpers', Path(__file__).with_name('collect_light_novel_metadata_v1.py'))
H = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(H)
ROOT = Path('data/light-novels-v1/acquisition/genre-candidates-v1')
PARENT = Path('data/light-novels-v1/acquisition')


def discovery_query(limit=100, offset=0):
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError('bounded 1..100 page and nonnegative offset required')
    return ('PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> '
            'SELECT DISTINCT ?work WHERE { ?work wdt:P136 wd:Q747381. } '
            f'ORDER BY ?work LIMIT {limit} OFFSET {offset}')


def prior_entities(parent):
    parent = Path(parent)
    result = {}
    for name in ('entities-index.json', 'original-language-resolution-index-v2.json', 'supplemental-entities-index-v1.json'):
        path = parent / name
        if path.exists():
            result.update(H.load(path))
    for name in ('light-novel-genre-entities-probe-v1.json', 'series-form-entity-receipt-v1.json'):
        path = parent / name
        if path.exists():
            receipt = H.load(path)
            raw = Path(receipt['raw_path']).read_bytes()
            if H.sha(raw) != receipt['raw_sha256']:
                raise ValueError('prior probe hash mismatch')
            for qid, entity in json.loads(raw).get('entities', {}).items():
                result[qid] = {'raw_path': receipt['raw_path'], 'raw_sha256': receipt['raw_sha256'], 'entity_revision': entity.get('lastrevid')}
    return result


def read_entity(qid, provenance):
    raw = Path(provenance['raw_path']).read_bytes()
    if H.sha(raw) != provenance['raw_sha256']:
        raise ValueError('entity SHA mismatch')
    return json.loads(raw)['entities'][qid]


def project_candidate(entity, linked, provenance, conditions_path):
    row = H.normalize_entity(entity, linked, provenance, conditions_path)
    description = entity.get('descriptions', {}).get('en', {}).get('value', '')
    is_genre = 'Q747381' in H.item_values(entity, 'P136')
    row.update(schema_version='light-novel-genre-candidate-v1', unit_kind='unknown',
               japanese_candidate='Q5287' in row['original_languages'] or H.description_origin(description),
               candidate_basis={'light_novel_genre_asserted': is_genre, 'genre_property': 'P136', 'english_description': description,
                                'unit_granularity_not_inferred_from_title_or_genre': True},
               admission={'catalogue_admission_allowed': False, 'status': 'candidate_only',
                          'reasons': ['requires_independent_series_or_individual_work_scope_proof']})
    row.pop('kind', None)
    return row


def collect(root=ROOT, parent=PARENT, *, max_pages=500):
    root, parent = Path(root), Path(parent)
    root.mkdir(parents=True, exist_ok=True)
    fetcher = H.make_fetcher(root)
    conditions = parent / 'source-conditions.json'
    if not conditions.exists():
        raise FileNotFoundError('prior approved Wikidata source conditions required')
    state_path = root / 'discovery.json'
    state = H.load(state_path) if state_path.exists() else {'version': 'light-novel-genre-frame-v1', 'ids': [], 'offset': 0, 'pages': [], 'complete': False}
    # Reuse the already acquired first page; no duplicate discovery request.
    probe_path = parent / 'light-novel-genre-discovery-probe-v1.json'
    if state['offset'] == 0 and probe_path.exists():
        probe = H.load(probe_path)
        receipt = probe['receipt']
        raw = Path(receipt['raw_path']).read_bytes()
        if H.sha(raw) != receipt['raw_sha256']:
            raise ValueError('discovery probe SHA mismatch')
        ids = [r['work']['value'].rsplit('/', 1)[-1] for r in json.loads(raw)['results']['bindings']]
        if ids != probe['ids']:
            raise ValueError('discovery probe projection differs')
        state.update(ids=ids, offset=100, complete=len(ids) < 100)
        state['pages'].append({'offset': 0, 'ids': ids, 'receipt': receipt, 'reused_probe': True})
        H.save(state_path, state)
    for _ in range(max_pages):
        if state['complete'] or state.get('held'):
            break
        result = fetcher.get(H.QLEVER_URL, source='qlever', params={'query': discovery_query(100, state['offset']), 'format': 'json'}, headers={'Accept': 'application/sparql-results+json'})
        if result is None:
            state['held'] = 'discovery request held; no automatic retry'
            H.save(state_path, state)
            break
        raw, receipt = result
        ids = [r['work']['value'].rsplit('/', 1)[-1] for r in json.loads(raw)['results']['bindings']]
        state['pages'].append({'offset': state['offset'], 'ids': ids, 'receipt': receipt})
        state['ids'] = list(dict.fromkeys(state['ids'] + ids))
        state.update(offset=state['offset'] + 100, complete=len(ids) < 100)
        H.save(state_path, state)
        print(json.dumps({'phase': 'genre_discovery', 'candidates': len(state['ids']), 'complete': state['complete']}), flush=True)
    known = prior_entities(parent)
    index_path = root / 'entities-index.json'
    index = H.load(index_path) if index_path.exists() else {}
    reused = []
    for qid in state['ids']:
        if qid not in index and qid in known:
            index[qid] = {**known[qid], 'reused_prior_raw': True}
            reused.append(qid)
    H.save(index_path, index)
    missing = [qid for qid in state['ids'] if qid not in index]
    for start in range(0, len(missing), 50):
        batch = missing[start:start + 50]
        result = fetcher.get(H.ENTITY_URL, source='wikidata', params={'action': 'wbgetentities', 'ids': '|'.join(batch), 'props': 'info|labels|aliases|claims|descriptions|sitelinks', 'format': 'json'})
        if result is None:
            continue
        raw, receipt = result
        for qid, entity in json.loads(raw).get('entities', {}).items():
            if 'missing' not in entity:
                index[qid] = {'raw_path': receipt['raw_path'], 'raw_sha256': receipt['raw_sha256'], 'entity_revision': entity.get('lastrevid'), 'reused_prior_raw': False}
        H.save(index_path, index)
        print(json.dumps({'phase': 'genre_entities', 'fetched_or_reused': len(index), 'discovered': len(state['ids'])}), flush=True)
    linked_index = H.load(parent / 'linked-index.json') if (parent / 'linked-index.json').exists() else {}
    linked = {q: {**x['entity'], '_provenance': {'raw_path': x['raw_path'], 'raw_sha256': x['raw_sha256']}} for q, x in linked_index.items()}
    candidates = [project_candidate(read_entity(qid, index[qid]), linked, index[qid], str(conditions)) for qid in state['ids'] if qid in index]
    H.jsonl(root / 'candidates.jsonl', candidates)
    H.jsonl(root / 'unfetched.jsonl', [{'id': qid, 'status': 'held'} for qid in state['ids'] if qid not in index])
    report = {'version': 'light-novel-genre-acquisition-v1', 'updated_at': H.now(), 'discovery_complete': state['complete'], 'discovered': len(state['ids']), 'fetched_or_reused': len(candidates), 'reused_prior_raw': sum(bool(p.get('reused_prior_raw')) for p in index.values()), 'japanese_candidates': sum(r['japanese_candidate'] for r in candidates), 'catalogue_admissions': 0, 'unit_kind': 'unknown until independent source resolves it', 'national_japanese_light_novel_coverage_proved': False}
    H.save(root / 'manifest.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT)
    parser.add_argument('--prior', type=Path, default=PARENT)
    parser.add_argument('--max-pages', type=int, default=500)
    args = parser.parse_args()
    print(json.dumps(collect(args.output, args.prior, max_pages=args.max_pages), indent=2))


if __name__ == '__main__':
    main()
