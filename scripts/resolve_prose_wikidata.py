"""Bounded Wikidata entity-API identity audit; never modifies existing prose inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import httpx

from recommend_book.ingestion.core import HTTPFetcher, RawStore, write_jsonl, utc_now
from recommend_book.ingestion.wikidata import claim_values, LANGUAGE_CODES
from recommend_book.models import Contributor, Identifier, SourceRecord, Title

API_URL = 'https://www.wikidata.org/w/api.php'
USER_AGENT = 'recommend-book-prose-identity-audit/0.1 (https://github.com/ayutaz/recommend-book; bounded Wikidata metadata verification)'


def request_params(*, ids=None, titles=None):
    values = ids if ids is not None else titles
    if not values or len(values) > 50 or (ids is not None and titles is not None):
        raise ValueError('one entity batch of 1..50 IDs or exact enWikisource titles required')
    if ids is not None and any(not re.fullmatch(r'Q[1-9][0-9]*', q) for q in ids):
        raise ValueError('invalid entity ID')
    params = {'action': 'wbgetentities', 'format': 'json', 'maxlag': 5,
              'props': 'labels|aliases|claims|sitelinks', 'languages': 'en'}
    params.update({'ids': '|'.join(ids)} if ids is not None else {'sites': 'enwikisource', 'titles': '|'.join(titles)})
    return params


def refs(entity, prop):
    return [v['id'] for v in claim_values(entity, prop) if isinstance(v, dict) and 'id' in v]


def label(entity):
    return entity.get('labels', {}).get('en', {}).get('value', entity.get('id', ''))


def names(entity):
    return [v['value'] for v in entity.get('labels', {}).values()] + [v['value'] for vs in entity.get('aliases', {}).values() for v in vs] + [v['text'] for v in claim_values(entity, 'P1476') if isinstance(v, dict) and v.get('text')]


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKC', value).casefold() if c.isalnum())


def years(entity):
    result = []
    for v in claim_values(entity, 'P577'):
        if isinstance(v, dict) and v.get('precision', 0) >= 9:
            match = re.match(r'\+(\d+)-', v.get('time', ''))
            if match: result.append(int(match[1]))
    return sorted(set(result))


def role(entity, entities):
    types = {label(entities.get(q, {})).casefold() for q in refs(entity, 'P31')}
    if types & {'revised version', 'revised edition'}: return 'revised_version'
    if types & {'edition', 'first edition', 'book edition'}: return 'edition'
    if 'version, edition or translation' in types: return 'edition_or_version'
    if not refs(entity, 'P629') and types & {'novel', 'literary work', 'written work', 'novella'}: return 'work'
    return 'unknown'


def resolve_candidate(candidate, entities):
    qid = candidate.get('edition_wikidata_candidate')
    entity = entities.get(qid, {})
    entity_role = role(entity, entities)
    reasons = []
    targets = refs(entity, 'P629')
    work_qid = qid if entity_role == 'work' else targets[0] if len(targets) == 1 else None
    work = entities.get(work_qid, {})
    if not entity or 'missing' in entity: reasons.append('candidate_entity_missing')
    if entity_role == 'unknown': reasons.append('entity_type_not_verified')
    if entity_role != 'work' and len(targets) != 1: reasons.append('P629_not_single_work')
    if not work or role(work, entities) != 'work': reasons.append('target_not_verified_work')
    if entity.get('sitelinks', {}).get('enwikisource', {}).get('title') != candidate.get('wikisource_title'):
        reasons.append('Wikisource_sitelink_missing_or_conflicting')
    if normalized(candidate['title']) not in {normalized(n) for n in names(work)}:
        reasons.append('work_title_missing_or_conflicting')
    authors = refs(work, 'P50')
    author_candidate = candidate.get('author_wikidata_candidate')
    author_matches = authors == [author_candidate] if author_candidate else normalized(candidate['author']) in {normalized(n) for q in authors for n in names(entities.get(q, {}))}
    if len(authors) != 1 or not author_matches:
        reasons.append('original_author_missing_or_conflicting')
    languages = refs(work, 'P407')
    english = languages == ['Q1860'] or languages == ['Q7979'] and label(entities.get('Q7979', {})) == 'British English'
    if not english: reasons.append('original_English_language_missing_or_conflicting')
    work_years, entity_years = years(work), years(entity)
    if candidate.get('first_publication_year') not in work_years: reasons.append('first_publication_year_missing_or_conflicting')
    if entity_role != 'work' and candidate.get('edition_year') not in entity_years: reasons.append('edition_year_missing_or_conflicting')
    if entity_role == 'work': reasons.append('Wikisource_links_work_not_specific_edition')
    # Edition authors, when present, must not contradict the original author.
    if refs(entity, 'P50') and set(refs(entity, 'P50')) != set(authors): reasons.append('edition_author_conflicting')
    return {**candidate, 'status': 'uncertain' if reasons else 'matched', 'human_verified': False,
            'entity_qid': qid, 'entity_role': entity_role, 'type_qids': refs(entity, 'P31'),
            'type_labels': {q: label(entities.get(q, {})) for q in refs(entity, 'P31')},
            'edition_qid': qid if entity_role in {'edition', 'edition_or_version'} else None,
            'revised_version_qid': qid if entity_role == 'revised_version' else None,
            'P629': targets, 'work_qid': work_qid, 'original_author_qids': authors,
            'author_comparison_basis': 'saved Wikisource Author page wikibase_item' if author_candidate else 'exact normalized Wikidata label/alias',
            'original_author_labels': {q: label(entities.get(q, {})) for q in authors},
            'original_language_qids': languages, 'work_publication_years': work_years,
            'entity_publication_years': entity_years, 'reasons': reasons,
            'integration_ready': not reasons, 'text_rights_changed': False}


def source_record(qid, entities, artifact):
    entity = entities[qid]
    entity_role = role(entity, entities)
    kind = 'work' if entity_role == 'work' else 'edition'
    lang_refs = refs(entity, 'P407')
    language = ({'Q7979': 'en-GB'} | LANGUAGE_CODES).get(lang_refs[0], lang_refs[0]) if len(lang_refs) == 1 else None
    published = years(entity)
    titles = [Title(value=label(entity), language='en')]
    title_assertions = []
    # Retain published-name assertions without claiming their language is the
    # work's original language. Identity/rank selection stays in names()/refs().
    for index, statement in enumerate(entity.get('claims', {}).get('P1476', [])):
        value = statement.get('mainsnak', {}).get('datavalue', {}).get('value')
        title_index = None
        if statement.get('rank') != 'deprecated' and isinstance(value, dict) and value.get('text'):
            title_index = len(titles)
            titles.append(Title(value=value['text'], language=value.get('language'), kind='alternative'))
        title_assertions.append({'locator': f'claims.P1476[{index}]', 'statement': statement,
                                 'title_index': title_index,
                                 'title_kind': 'alternative' if title_index is not None else None,
                                 'raw_sha256': artifact['sha256'], 'raw_path': artifact['path']})
    return SourceRecord(source='wikidata', source_id=qid, source_url=f'https://www.wikidata.org/wiki/{qid}',
        retrieved_at=artifact['retrieved_at'], provider_id='wikidata', record_kind=kind,
        titles=titles,
        contributors=[Contributor(name=label(entities.get(q, {'id': q})), identifiers=[Identifier(source='wikidata', value=q, scope='agent')]) for q in refs(entity, 'P50')],
        identifiers=[Identifier(source='wikidata', value=qid, scope=kind)],
        work_refs=[Identifier(source='wikidata', value=q, scope='work') for q in refs(entity, 'P629')],
        original_language=language if kind == 'work' else None, language=language if kind == 'edition' else None,
        first_publication_year=min(published) if published and kind == 'work' else None,
        publication_year=min(published) if published and kind == 'edition' else None,
        raw_sha256=artifact['sha256'], raw_path=artifact['path'], rights_policy_id='wikidata-cc0',
        extra={'entity_role': entity_role, 'claims': entity.get('claims', {}), 'sitelinks': entity.get('sitelinks', {}),
               'lastrevid': entity.get('lastrevid'), 'license': 'CC0-1.0', 'license_url': 'https://www.wikidata.org/wiki/Wikidata:Licensing',
               'attribution': 'Wikidata contributors', 'changes': 'bibliographic entity normalization; original response retained',
               'author_source': 'entity P50 only; no author copied from work', 'integration_requires_identity_map': True,
               'original_language_qids': lang_refs if kind == 'work' else [], 'language_qids': lang_refs if kind == 'edition' else [],
               'publication_years': published, 'human_verified': False,
               'title_assertions': title_assertions,
               'title_assertion_policy': 'published_title_candidates_v1; existing label retained; all nondeprecated P1476 values retained as alternative; no original/translation-language inference'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidates', type=Path, default=Path('data/prose-poc/wd-resolution-candidates.json'))
    parser.add_argument('--metadata', type=Path, default=Path('data/prose-poc/metadata.json'))
    parser.add_argument('--output', type=Path, default=Path('data/prose-poc/wikidata-resolution-v1'))
    parser.add_argument('--replay-from', type=Path, help='normalize already archived API responses without network requests')
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()): raise ValueError('output must be new or empty')
    args.output.mkdir(parents=True, exist_ok=True)
    candidates = json.loads(args.candidates.read_text('utf-8'))
    pages = list(json.loads(args.metadata.read_text('utf-8'))['pages'].values())
    titles = {f"ws:en:{p['pageid']}": p['title'] for p in pages}
    author_ids = {p['title'][7:]: p.get('pageprops', {}).get('wikibase_item') for p in pages if p['title'].startswith('Author:')}
    for c in candidates:
        c['wikisource_title'] = titles.get(c['candidate_id'])
        c['author_wikidata_candidate'] = author_ids.get(c['author'])
    store = RawStore(args.output / 'raw')
    entities, artifacts, requests, errors = {}, {}, [], []
    # Fixed endpoint and redirects disabled: no host/endpoint rotation or fallback.
    with httpx.Client(timeout=60, follow_redirects=False) as client:
        fetcher = HTTPFetcher(store, client=client, user_agent=USER_AGENT, min_interval=1.1, max_attempts=1)
        def fetch(params):
            if args.replay_from:
                prior = json.loads((args.replay_from / 'manifest.json').read_text('utf-8'))['requests']
                hits = [r for r in prior if r['params'] == params]
                if len(hits) != 1 or hits[0]['status'] != 'success':
                    errors.append('replay response missing or failed; no network fallback'); return False
                cached = hits[0]
                digest = cached['raw']['sha256']
                content = (args.replay_from / 'raw' / 'wikidata' / digest[:2] / (digest + '.bin')).read_bytes()
                if hashlib.sha256(content).hexdigest() != cached['raw']['sha256']: raise ValueError('replay raw SHA mismatch')
                raw = store.save(content, source='wikidata', url=API_URL, metadata={'replayed_from': cached['raw'], 'params': params})
                requests.append({**cached, 'raw': raw.__dict__, 'replayed': True})
                payload = json.loads(content)
                if 'error' in payload: errors.append(str(payload['error'])); return False
                for q, e in payload['entities'].items():
                    if re.fullmatch(r'Q[1-9][0-9]*', q) and 'missing' not in e:
                        entities[q] = e; artifacts[q] = raw.__dict__
                return True
            fetched = fetcher.get(API_URL, source='wikidata', params=params)
            requests.append({'params': params, 'status': fetched.status, 'http_status': fetched.status_code,
                             'error': fetched.error, 'raw': fetched.artifact.__dict__ if fetched.artifact else None})
            if fetched.status != 'success': errors.append(fetched.error or 'HTTP failure'); return False
            try:
                payload = json.loads(fetched.content)
                if 'error' in payload: raise ValueError(payload['error'])
                for q, e in payload['entities'].items():
                    if re.fullmatch(r'Q[1-9][0-9]*', q) and 'missing' not in e:
                        entities[q] = e; artifacts[q] = fetched.artifact.__dict__
                return True
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f'invalid API payload: {exc}'); return False
        initial = list(dict.fromkeys(c['edition_wikidata_candidate'] for c in candidates if c['edition_wikidata_candidate']))
        proceed = fetch(request_params(ids=initial))
        nulls = [c for c in candidates if not c['edition_wikidata_candidate'] and c['wikisource_title']]
        if proceed and nulls:
            proceed = fetch(request_params(titles=[c['wikisource_title'] for c in nulls]))
            for c in nulls:
                hits = [q for q, e in entities.items() if e.get('sitelinks', {}).get('enwikisource', {}).get('title') == c['wikisource_title']]
                if len(hits) == 1: c['edition_wikidata_candidate'] = hits[0]; c['qid_source'] = 'exact_Wikisource_sitelink_API'
        # Only identity dependencies, at most two closure batches; no searches/P279 crawl.
        for _ in range(2):
            pending = sorted({q for e in entities.values() for p in ('P629', 'P50', 'P31', 'P407') for q in refs(e, p)} - entities.keys())
            if not proceed or not pending: break
            if len(pending) > 50: errors.append('dependency batch exceeds 50; retained uncertain'); break
            proceed = fetch(request_params(ids=pending))
    results = [resolve_candidate(c, entities) for c in candidates]
    for r in results:
        r['raw_evidence'] = {q: artifacts[q] for q in [r['entity_qid'], r['work_qid'], *r['original_author_qids'], *r['type_qids']] if q in artifacts}
    relevant = {q for r in results for q in (r['entity_qid'], r['work_qid']) if q in entities and role(entities[q], entities) != 'unknown'}
    write_jsonl([source_record(q, entities, artifacts[q]) for q in sorted(relevant)], args.output / 'source-records.jsonl')
    for filename, content in [('identity-map.json', results), ('ambiguous.json', [r for r in results if r['status'] != 'matched']), ('entities.json', entities)]:
        (args.output / filename).write_text(json.dumps(content, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    manifest = {'created_at': utc_now(), 'api': API_URL, 'user_agent': USER_AGENT, 'min_interval': 1.1,
        'batch_limit': 50, 'maxlag': 5, 'max_attempts': 1, 'retry_after': 'HTTPFetcher persists cooldown; terminal responses stop this run',
        'candidate_sha256': hashlib.sha256(args.candidates.read_bytes()).hexdigest(), 'metadata_sha256': hashlib.sha256(args.metadata.read_bytes()).hexdigest(),
        'candidate_count': len(results), 'matched_count': sum(r['status'] == 'matched' for r in results), 'human_verified_count': 0,
        'replayed_from': str(args.replay_from) if args.replay_from else None,
        'requests': requests, 'errors': errors, 'text_refetch': False, 'existing_inputs_modified': False,
        'license_scope': 'Wikidata API metadata CC0 only; original Wikisource text CC-BY-SA conditions unchanged',
        'integration': 'independent snapshot; root approval required before changing prose SourceRecord work_refs',
        'output_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.glob('*') if p.is_file()}}
    (args.output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps({'candidate_count': len(results), 'matched': manifest['matched_count'], 'uncertain': len(results) - manifest['matched_count'], 'errors': errors}))


if __name__ == '__main__': main()
