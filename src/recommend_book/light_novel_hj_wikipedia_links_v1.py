"""Opt-in HJ novel identity witnesses; no URL relocation or author repair.

Wikipedia supplies only an explicit novel-specific official link. Publisher
BookSeries membership and authenticated own NDL bibliography supply the edition
relationship. Existing P856 resolvers never call this contract implicitly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qs, quote, urlsplit

import mwparserfromhell

from .light_novel_admission_v3 import stored_title_closure
from .light_novel_ndl_series_v1 import _decode, _jsonld, match_bibliography
from .light_novel_wikipedia_credit_v1 import _author_concordance
from .light_novel_reference_match_v1 import _parent
from .normalization import normalize_isbn


VERSION = 'hj-wikipedia-novel-official-links-v1'
RULE = 'hj-wikipedia-novel-isbn-edition-membership-v1'
KIND = 'hj_wikipedia_novel_membership_v1'
_PARENT = re.compile(r'https://firecross\.jp/hjbunko/series/[1-9][0-9]*')
_BOOK = re.compile(r'https://firecross\.jp/hjbunko/product/[1-9][0-9]*')
_RIGHTS = 'Reference-only publisher page; no synopsis, image or AI-input reuse inferred'


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _bytes(pin, artifact, cache, limit=5_000_000):
    raw = artifact(pin['path'], pin['sha256'], cache)
    _require(len(raw) <= limit and hashlib.sha256(raw).hexdigest() == pin['sha256'], 'HJ source size/SHA differs')
    return raw


def _json(pin, artifact, cache, limit=5_000_000):
    return json.loads(_bytes(pin, artifact, cache, limit))


def _receipt_raw_path(receipt_path, literal):
    """Bind the original absolute or explicit coverage-store relative path."""
    _require(isinstance(literal, str) and bool(literal), 'HJ publisher receipt raw path missing')
    path = Path(literal)
    if path.is_absolute():
        return path.resolve()
    receipt = Path(receipt_path).resolve()
    _require(len(path.parts) == 2 and path.parts[0] == 'source-snapshots'
             and receipt.parent.name == 'source-snapshots'
             and path.name == receipt.with_suffix('.html').name,
             'HJ publisher receipt relative source layout differs')
    return (receipt.parent.parent / path).resolve()


def _plain(value):
    parsed = mwparserfromhell.parse(str(value))
    _require(all(isinstance(n, mwparserfromhell.nodes.Text)
                 or isinstance(n, mwparserfromhell.nodes.ExternalLink) and not n.brackets and n.title is None
                 for n in parsed.nodes), 'HJ generated official-link field')
    return str(value).strip()


def _novel_link(text, url, title, normalize):
    """A direct Cite web in an explicit 小説 section, never a mixed external list."""
    code = mwparserfromhell.parse(text)
    sections = []
    for section in code.get_sections(flat=True, include_headings=True):
        headings = section.filter_headings(recursive=False)
        if headings and str(headings[0].title).strip() == '小説':
            sections.append(section)
    _require(len(sections) == 1, 'HJ unique explicit novel section required')
    section = sections[0]
    # Tables and reference tags may contain Cite web, but hidden markup cannot
    # supply the witness. Nested child media sections cannot supply it either.
    nodes = section.filter_tags(recursive=True)
    _require(not any(str(t.tag).strip().casefold() in {'nowiki', 'pre', 'code', 'includeonly', 'noinclude'}
                     for t in nodes), 'HJ hidden/generated novel link')
    candidates = []
    for template in section.filter_templates(recursive=True):
        if str(template.name).strip().casefold().replace('_', ' ') != 'cite web':
            continue
        fields = {}
        for param in template.params:
            key = str(param.name).strip()
            _require(key not in fields, 'HJ duplicate citation field')
            fields[key] = param.value
        if 'url' not in fields or _plain(fields['url']) != url:
            continue
        _require(normalize(_plain(fields.get('title', ''))) == normalize(title)
                 and _plain(fields.get('website', '')) == 'HJ文庫公式Webサイト'
                 and _plain(fields.get('publisher', '')) == 'ホビージャパン',
                 'HJ novel citation complete title/provider differs')
        candidates.append(str(template))
    _require(len(candidates) == 1, 'HJ unique own novel official-link citation required')
    literal = candidates[0]
    _require(text.count(literal) == 1, 'HJ citation literal is ambiguous')
    offset = text.index(literal)
    return {'section': '小説', 'template': 'Cite web', 'character_offset': offset,
            'line': text.count('\n', 0, offset) + 1,
            'citation_literal_sha256': hashlib.sha256(literal.encode()).hexdigest()}


def verify_links(manifest_path, expected_sha256, artifact, cache):
    """Replay full pinned entities/pages/receipts into novel-only assertions."""
    from .light_novel_catalogue_v1 import LightNovelCatalogue, normalize_search
    from .light_novel_classification_v1 import (
        series_source_policy, _verify_pins, TERMS_URL, WIKIDATA_TERMS, LICENSE_URL,
    )
    try:
        document = _json({'path': manifest_path, 'sha256': expected_sha256}, artifact, cache, 1_000_000)
        _require(document.get('version') == VERSION and isinstance(document.get('series'), list)
                 and 1 <= len(document['series']) <= 5, 'HJ opt-in manifest version/bounds differs')
        _require(_json(document['source_policy'], artifact, cache) == series_source_policy(),
                 'HJ known Wikipedia source/license policy differs')
        _require(set(document['conditions']) == {TERMS_URL, WIKIDATA_TERMS, LICENSE_URL},
                 'HJ all official terms/license conditions required')
        terms_pins = {p['path']: p['sha256'] for p in document['conditions'].values()}
        _verify_pins(terms_pins)
        for pin in document['conditions'].values():
            _bytes(pin, artifact, cache)
        result = {}
        for witness in document['series']:
            identity = witness['series_entry_id']
            _require(isinstance(identity, str) and re.fullmatch(r'lns:wd:Q[1-9][0-9]*', identity)
                     and identity not in result, 'HJ typed series identity duplicate/invalid')
            qid = identity.rsplit(':', 1)[-1]
            metadata = _json(witness['metadata'], artifact, cache)
            _require(metadata.get('id') == qid and metadata.get('kind') == 'series'
                     and metadata.get('schema_version') == 'light-novel-metadata-v1', 'HJ metadata unit differs')
            projected = LightNovelCatalogue.__new__(LightNovelCatalogue)._project(metadata, cache)
            _require(projected['entry_id'] == identity and projected['entry_kind'] == 'series'
                     and projected['original_language'] == 'ja', 'HJ authenticated series/language differs')
            provenance = metadata['provenance']
            _require(witness['wikidata'] == {'path': provenance['raw_path'], 'sha256': provenance['raw_sha256']},
                     'HJ metadata/entity pin differs')
            entity = _json(witness['wikidata'], artifact, cache)['entities'][qid]
            _require(entity.get('id') == qid and entity.get('lastrevid') == provenance['entity_revision'],
                     'HJ exact entity revision differs')
            wp = witness['wikipedia']
            _require(wp['language'] == 'ja', 'HJ initial novel-section profile is Japanese only')
            ledger = _bytes(wp['receipt_ledger'], artifact, cache, 4_000_000).decode('utf-8').splitlines()
            _require(type(wp['receipt_line']) is int and 1 <= wp['receipt_line'] <= len(ledger), 'HJ receipt pointer differs')
            receipt = json.loads(ledger[wp['receipt_line'] - 1])
            _require(receipt['sha256'] == wp['raw']['sha256'] and receipt['path'] == wp['raw']['path']
                     and receipt.get('metadata', {}).get('status_code') == 200, 'HJ Wikipedia raw/receipt differs')
            u = urlsplit(receipt['source_url']); query = parse_qs(u.query)
            title = entity['sitelinks']['jawiki']['title']
            _require(u.scheme == 'https' and u.netloc == 'ja.wikipedia.org' and u.path == '/w/api.php'
                     and title in query.get('titles', [''])[0].split('|'), 'HJ exact sitelink request differs')
            raw = _bytes(wp['raw'], artifact, cache)
            _require(receipt.get('size') == len(raw), 'HJ Wikipedia receipt length differs')
            payload = json.loads(raw); pages = payload['query']['pages']
            _require(isinstance(pages, list) and 1 <= len(pages) <= 100, 'HJ bounded native page list required')
            matches = [(i, p) for i, p in enumerate(pages) if p.get('title') == title
                       or p.get('pageprops', {}).get('wikibase_item') == qid]
            _require(len(matches) == 1, 'HJ duplicate/conflicting native page ownership')
            index, page = matches[0]
            _require(wp['page_pointer'] == f'/query/pages/{index}' and page.get('title') == title
                     and page.get('pageprops', {}).get('wikibase_item') == qid
                     and page.get('ns') == 0 and type(page.get('pageid')) is int
                     and page['pageid'] == wp['page_id'] and page['pageid'] > 0
                     and 'missing' not in page and 'disambiguation' not in page.get('pageprops', {}),
                     'HJ sitelink/pageprops/page pointer differs')
            revisions = page['revisions']
            _require(len(revisions) == 1 and type(revisions[0].get('revid')) is int
                     and revisions[0]['revid'] == wp['revision_id'], 'HJ pinned revision differs')
            slot = revisions[0]['slots']['main']
            _require(slot.get('contentmodel') == 'wikitext' and slot.get('contentformat') == 'text/x-wiki',
                     'HJ content model differs')
            url = witness['official_series_url']
            _require(_PARENT.fullmatch(url) is not None, 'HJ novel route differs')
            link = _novel_link(slot['content'], url, metadata['title'], normalize_search)
            _require(link == wp['novel_link_locator'], 'HJ pinned novel citation locator differs')
            publisher = witness['publisher']
            pr = _json(publisher['receipt'], artifact, cache)
            _require(pr.get('source_url') == pr.get('final_url') == url and pr.get('http_status') == 200
                     and _receipt_raw_path(publisher['receipt']['path'], pr.get('raw_path'))
                         == Path(publisher['raw']['path']).resolve()
                     and pr.get('rights') == _RIGHTS and not any(pr.get(k) for k in
                     ('location', 'redirects_followed', 'http_redirects_followed', 'error'))
                     and pr.get('content_type', '').lower().startswith('text/html')
                     and publisher['raw']['sha256'] == pr.get('sha256'), 'HJ publisher receipt identity/rights differs')
            source = _bytes(publisher['raw'], artifact, cache)
            _require(len(source) == pr.get('byte_length'), 'HJ publisher length differs')
            values = _jsonld(_decode(source, pr['content_type']))
            series = [v for v in values if v.get('@type') == 'BookSeries']
            _require(len(series) == 1 and series[0].get('url') == url
                     and normalize_search(series[0].get('name', '')) == normalize_search(metadata['title']),
                     'HJ unique exact BookSeries differs')
            series = series[0]
            _require(series.get('publisher') == {'@type': 'Organization', 'name': 'ホビージャパン（HJ文庫）'},
                     'HJ publisher/imprint profile differs')
            authors = []
            for credit in series['author']:
                _require(credit.get('@type') == 'Person', 'HJ explicit person credit required')
                m = re.fullmatch(r'([^（）]+)（(著者|イラスト)）', credit.get('name', ''))
                _require(m is not None, 'HJ explicit own writer/illustrator role required')
                if m[2] == '著者': authors.append(m[1])
            _require(authors and len(authors) == len(set(authors)), 'HJ writer set absent/duplicate')
            books, unusable_books = [], []
            for i, book in enumerate(series['hasPart']):
                isbn = normalize_isbn(book.get('isbn', ''))
                _require(book.get('@type') == 'Book' and _BOOK.fullmatch(book.get('url', '')) is not None
                         and isinstance(book.get('name'), str) and bool(book['name']),
                         'HJ novel hasPart book differs')
                if isbn is None:
                    unusable_books.append({'url': book['url'], 'reason': 'explicit_member_ISBN_missing_or_invalid',
                                           'locator': f'BookSeries/hasPart/{i}'})
                    continue
                books.append({'url': book['url'], 'title': book['name'], 'isbn': isbn,
                              'locator': f'BookSeries/hasPart/{i}'})
            _require(books and len(books) <= 200 and len({b['url'] for b in books}) == len(books)
                     and len({b['isbn'] for b in books}) == len(books), 'HJ duplicate/oversized ISBN membership')
            permalink = f'https://ja.wikipedia.org/w/index.php?oldid={wp["revision_id"]}'
            attribution = {'credit': 'Wikipedia contributors: ' + title, 'source_url': permalink,
                'history_url': 'https://ja.wikipedia.org/w/index.php?title=' + quote(title, safe='') + '&action=history',
                'revision_id': wp['revision_id'], 'page_id': wp['page_id'], 'license': 'CC-BY-SA-4.0',
                'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
                'changes': 'Explicit novel official-link metadata extracted; no article prose reproduced',
                'obligations': ['attribution', 'mark_changes', 'share_alike'], 'quote_allowed': False}
            canonical_metadata = json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            result[identity] = {'series_entry_id': identity,
                'metadata_sha256': hashlib.sha256(canonical_metadata.encode()).hexdigest(),
                'title': metadata['title'], 'authors': authors, 'label': 'HJ文庫', 'publisher': 'ホビージャパン',
                'index_id': 'hj-bunko-series', 'reference_id': 'publisher-series:' + hashlib.sha256(url.encode()).hexdigest()[:24],
                'source_url': url, 'books': books, 'unusable_books': unusable_books,
                'wikipedia_raw_sha256': wp['raw']['sha256'],
                'publisher_raw_sha256': publisher['raw']['sha256'], 'novel_link_locator': link,
                'wikipedia_page_pointer': wp['page_pointer'], 'wikipedia_attribution': attribution}
        return result
    except (KeyError, TypeError, IndexError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError('Incomplete HJ Wikipedia novel-link proof') from exc


def membership_candidates(catalogue, rows, stored, assertions, cache):
    """Return candidates only; original metadata and author claims stay intact."""
    from .light_novel_catalogue_v1 import normalize_search
    result = {}
    for row in rows:
        if row['entry_kind'] != 'edition':
            continue
        record = json.loads(stored[row['entry_id']]['record_json'])
        parts = catalogue._edition_primary_bibliographic_parts(record, cache)
        if not parts or not parts['volume']:
            continue
        source = parts['source']
        # Reject decimals, named parts, missing volume and non-numeric suffixes.
        if not re.fullmatch(r'(?:第)?[1-9][0-9]{0,3}(?:巻)?', unicodedata.normalize('NFKC', parts['volume']['value']).strip()):
            continue
        matches = []
        for identity, assertion in assertions.items():
            parent = stored.get(identity)
            if not parent or parent['kind'] != 'series' or parent['record_sha256'] != assertion['metadata_sha256']:
                continue
            alternatives = [r for r in rows if r['entry_kind'] == 'series' and normalize_search(assertion['title']) in
                            {normalize_search(v) for v in stored_title_closure(json.loads(stored[r['entry_id']]['record_json']))}]
            if len(alternatives) != 1 or alternatives[0]['entry_id'] != identity:
                continue
            own_titles = [{**t, 'kind': 'authenticated_own_primary_title'} for t in parts['titles']]
            adapted = {**source, 'titles': own_titles, 'volume': parts['volume']['value'], '_cataloguing_main_titles': []}
            matched = match_bibliography(assertion, adapted, normalize_search)
            if matched is None:
                continue
            authors = _author_concordance(parts, assertion['authors'], _parent(parts['titles'][0]['locator']))
            if not authors:
                continue
            isbns = {normalize_isbn(i['value']) for i in source['identifiers'] if i['source'] == 'isbn'} - {None}
            books = [b for b in assertion['books'] if b['isbn'] in isbns]
            if len(books) != 1:
                continue
            complete_titles = [t['value'] for t in parts['titles']
                               if '/{http://purl.org/dc/terms/}title[' in t['locator']]
            volume_literal = parts['volume']['value']
            def complete_forms(title):
                # NDL's explicit '. <own volume>' separator is reproducible;
                # no punctuation deletion, prefix test, or SKU stripping.
                forms = [title]
                if title.endswith('. ' + volume_literal):
                    forms.append(title[:-len('. ' + volume_literal)] + ' ' + volume_literal)
                return {normalize_search(t) for t in forms}
            if not complete_titles or any(normalize_search(books[0]['title']) not in complete_forms(t)
                                          for t in complete_titles):
                continue
            matches.append({'rule_version': RULE, 'scope': 'edition_membership_only', 'edition_id': row['entry_id'],
                'series_entry_id': identity, 'volume_number': matched['volume_number'],
                'official_series_url': assertion['source_url'], 'official_book': books[0],
                'series_identity_basis': 'exact_wikidata_sitelink_same_pageprops_and_explicit_novel_official_link',
                'wikipedia_raw_sha256': assertion['wikipedia_raw_sha256'],
                'publisher_raw_sha256': assertion['publisher_raw_sha256'],
                'wikipedia_page_pointer': assertion['wikipedia_page_pointer'],
                'novel_link_locator': assertion['novel_link_locator'],
                'wikipedia_attribution': assertion['wikipedia_attribution'], 'ndl_bibliography_match': matched,
                'own_authority_bindings': authors, 'P50_backfilled': False, 'person_identity_asserted': False,
                'original_work_identity_asserted': False})
        if len(matches) == 1:
            result[row['entry_id']] = matches[0]
    return result


def _selected(catalogue, db, requested, assertions):
    from .light_novel_catalogue_v1 import normalize_search
    wanted = {normalize_search(a['title']) for a in assertions.values()}
    stored = {}
    placeholders = ','.join('?' for _ in requested) or 'NULL'
    for r in db.execute("SELECT * FROM entries WHERE kind='series' OR entry_id LIKE 'lns:%' OR entry_id IN ("
                        + placeholders + ')', requested):
        record = json.loads(r['record_json'])
        if r['entry_id'] in requested or wanted & {normalize_search(t) for t in stored_title_closure(record)}:
            _require(hashlib.sha256(r['record_json'].encode()).hexdigest() == r['record_sha256'], 'HJ stored metadata SHA differs')
            stored[r['entry_id']] = dict(r)
    _require(set(requested) <= set(stored) and all(stored[i]['kind'] == 'edition' for i in requested),
             'HJ requested own NDL edition absent/different kind')
    return stored


def resolve_memberships(catalogue, manifest_path, expected_sha256, *, edition_ids):
    """Explicit finite writer; protect old relations and replay under transaction."""
    _require(not isinstance(edition_ids, (str, bytes)) and edition_ids is not None, 'HJ finite edition collection required')
    requested = list(edition_ids)
    _require(len(requested) <= 1000 and len(set(requested)) == len(requested)
             and all(isinstance(i, str) and re.fullmatch(r'lne:ndl:R100000002-[A-Za-z0-9._-]+', i) for i in requested),
             'HJ finite typed edition scope differs')
    cache = {}; assertions = verify_links(manifest_path, expected_sha256, catalogue._artifact, cache)
    with catalogue._connect() as db:
        selected = _selected(catalogue, db, requested, assertions)
    rows = catalogue._membership_base_rows(selected, cache)
    plans = membership_candidates(catalogue, rows, selected, assertions, cache)
    result = {'requested_editions': len(requested), 'linked': 0, 'unchanged': 0,
              'protected_existing': 0, 'unresolved_editions': len(requested) - len(plans)}
    with catalogue._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = _selected(catalogue, db, requested, assertions)
        _require(current == selected, 'HJ selected/candidate metadata changed during resolution')
        fresh_cache = {}
        current_assertions = verify_links(manifest_path, expected_sha256, catalogue._artifact, fresh_cache)
        current_rows = catalogue._membership_base_rows(current, fresh_cache)
        _require(membership_candidates(catalogue, current_rows, current, current_assertions, fresh_cache) == plans,
                 'HJ source/proof changed during resolution')
        for identity in requested:
            previous = db.execute('SELECT * FROM edition_series_memberships WHERE edition_id=?', (identity,)).fetchone()
            membership = plans.get(identity)
            if not membership:
                result['protected_existing'] += int(previous is not None)
                continue
            sid = membership['series_entry_id']
            proof = {'kind': KIND, 'manifest_path': str(Path(manifest_path).resolve()), 'manifest_sha256': expected_sha256,
                     'edition_record_sha256': selected[identity]['record_sha256'],
                     'series_record_sha256': selected[sid]['record_sha256'], 'membership': membership}
            encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            if previous:
                result['unchanged' if previous['series_id'] == sid and previous['proof_json'] == encoded
                       and previous['proof_sha256'] == digest else 'protected_existing'] += 1
            else:
                db.execute('INSERT INTO edition_series_memberships VALUES(?,?,?,?)', (identity, sid, encoded, digest))
                result['linked'] += 1
    return result
