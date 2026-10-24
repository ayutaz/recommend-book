"""Replay the fixed official Monster Bunko UI product-reference frame.

Only pinned local JSON and the two observed UI-route assets are read. Exported
rosters, publisher prose and images are not metadata authorities. A label-filter
membership does not establish a story series, original work, edition identity,
or an explicit light-novel classification.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import quote

from .light_novel_publisher_references_v1 import MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _read, _relative


_LABEL = 'モンスター文庫'
_SEARCH = 'https://www.futabasha.co.jp/search?label_name=%E3%83%A2%E3%83%B3%E3%82%B9%E3%82%BF%E3%83%BC%E6%96%87%E5%BA%AB'
_FIRST = ('https://book-api.futabasha.co.jp/books?v=2&request_mode=1&media=0&age_verification=3&request_from=&request_to=&keyword=&publication_form=&label_name=%E3%83%A2%E3%83%B3%E3%82%B9%E3%82%BF%E3%83%BC%E6%96%87%E5%BA%AB&series_name=&tag_name=&grouping=&author_name=&jdcn_code=&per_page=25&page=1&display_order=0&image_size=330')
_APP = 'https://www.futabasha.co.jp/js/app.js?id=69e58d6027437571400034f11aa419a5'
_SEARCH_JS = 'https://www.futabasha.co.jp/js/SearchResult.207854cf2f689f4e.js'
_WITNESSES = (
    'window.API_URL="https://book-api.futabasha.co.jp",window.API_URL_PROD="https://book-api.futabasha.co.jp"',
    'showMore:function(){this.page=this.page+1,this.fetchTargetItems()}',
    'n.data.books.results.total_pages>e.page?e.showMoreFlag=!0:e.showMoreFlag=!1',
    'resultContentCount=n.data.books.results.total_counts',
    'axios.get(API_URL+"/books",{params:{v:2,request_mode:e.request_mode,media:e.media,age_verification:e.age_verification,request_from:e.request_from,request_to:e.request_to,keyword:e.toSpaceJoinedString(e.keyword),publication_form:e.toSpaceJoinedString(e.publication_form),label_name:e.label_name,series_name:e.series_name,tag_name:e.tag_name,grouping:e.grouping,author_name:e.author_name,jdcn_code:e.jdcn_code?"".concat(e.jdcn_code,"0000000"):"",per_page:e.per_page,page:e.page,display_order:e.display_order,image_size:"330"}})',
    'defaultBookType:function(){if("rbook"==this.filter)return 1;if("ebook"==this.filter)return 2;if("abook"==this.filter)return 3;var e=this.bookInfoFromParent.web_rbooks_id,t=this.bookInfoFromParent.web_ebooks_id,o=this.bookInfoFromParent.web_abooks_id;return e?1:t?2:o?3:1}',
    '"/book/"+P.magazinePath+o.bookInfoFromParent.jdcn_code+"?type="+P.defaultBookType',
    'web_author_type_overwrite?o.bookInfoFromParent.book_authors[e-1].web_author_type_overwrite:o.bookInfoFromParent.book_authors[e-1].author_type_name',
)
_ROLES = {'著': 'author', '著者': 'author', 'イラスト': 'illustrator',
          '原作': 'original_author', '原案': 'original_concept', '監修': 'supervisor', '編': 'editor'}


def _json(path, expected):
    return json.loads(_read(path, expected, MAX_METADATA_BYTES))


def _page_url(number):
    return _FIRST.replace('&page=1&', f'&page={number}&')


def _source(root, receipt, url, *, javascript=False):
    statuses = [receipt[key] for key in ('status', 'http_status') if key in receipt]
    if (receipt.get('source_url') != url or not statuses or any(value != 200 for value in statuses)
            or receipt.get('final_url', url) != url or receipt.get('location') or receipt.get('error')
            or receipt.get('size_cap_exceeded') or receipt.get('http_redirects_followed', 0) != 0
            or receipt.get('http_retry_count', 0) != 0):
        raise ValueError('Futaba receipt URL or successful access scope differs')
    content_type = (receipt.get('content_type') or '').lower().split(';', 1)[0].strip()
    allowed = {'application/javascript', 'application/x-javascript', 'text/javascript'} if javascript else {'application/json'}
    if content_type not in allowed:
        raise ValueError('Futaba source content type differs')
    raw = _read(_relative(root, receipt.get('raw_path')), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('Futaba source size differs from receipt')
    return raw.decode('utf-8') if javascript else json.loads(raw)


def _route(root, proof, receipts):
    if (proof.get('frame_id') != 'futaba-monster-bunko-ui-observed-finite-route-v1'
            or proof.get('first_request_url_observed_in_page_resource_inventory') != _FIRST
            or proof.get('official_js_assets_read') != 2):
        raise ValueError('Futaba observed UI route differs')
    navigation = proof['navigation']
    if (len(navigation) != 2 or navigation[0].get('source_url') != 'https://www.futabasha.co.jp/'
            or navigation[0].get('observed_link_text') != 'レーベル一覧'
            or navigation[0].get('link_url') != 'https://www.futabasha.co.jp/labels/'
            or navigation[1].get('source_url') != 'https://www.futabasha.co.jp/labels'
            or navigation[1].get('observed_heading') != _LABEL
            or navigation[1].get('observed_link_url') != _SEARCH):
        raise ValueError('Futaba observed label navigation differs')
    ui = proof['rendered_search']
    if (ui.get('source_url') != _SEARCH or ui.get('document_title') != f'「レーベル : {_LABEL}」一覧 | 双葉社'
            or ui.get('initial_card_count') != 25 or ui.get('next_control_text') != 'もっと見る'):
        raise ValueError('Futaba rendered label index differs')
    expected = {'fixed_first_page': 1, 'fixed_final_page': 16, 'reported_total': 378, 'per_page': 25,
                'only_parameter_changed': 'page', 'next_rule': 'page + 1 only while total_pages > page'}
    if any(proof['finite_pagination'].get(k) != v for k, v in expected.items()):
        raise ValueError('Futaba finite UI pagination differs')
    witnesses = proof['script_witnesses']
    if len(witnesses) != len(_WITNESSES) or {w['literal'] for w in witnesses} != set(_WITNESSES):
        raise ValueError('Futaba route/role/URL witness set differs')
    scripts = {url: _source(root, receipts[url], url, javascript=True) for url in (_APP, _SEARCH_JS)}
    for witness in witnesses:
        url = _APP if witness['literal'] == _WITNESSES[0] else _SEARCH_JS
        locator = witness['locator']
        start, end = locator['start'], locator['end']
        if (witness['source_url'] != url or witness['raw_sha256'] != receipts[url]['sha256']
                or locator.get('kind') != 'utf8_decoded_character_range'
                or type(start) is not int or type(end) is not int or not 0 <= start <= end <= len(scripts[url])
                or scripts[url][start:end] != witness['literal']):
            raise ValueError('Futaba pinned route witness does not replay')
    return ui


def _isbn(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('Futaba ISBN literal type differs')
    digits = re.sub(r'[-\s]', '', value)
    if (re.fullmatch(r'97[89][0-9]{10}', digits)
            and sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(digits)) % 10 == 0):
        return digits
    return None


def _row(book, receipt, ordinal, route_proof_path, *, label_literal=_LABEL, label_id=14, search_url=_SEARCH):
    identifier, title = book['jdcn_code'], book['book_name']
    if (not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', identifier)
            or not isinstance(title, str) or not title.strip()
            or type(book['label_id']) is not int or book['label_id'] != label_id or book['label_name'] not in (label_literal, None)):
        raise ValueError('Futaba opaque ID, complete title or explicit label scope differs')
    form = book.get('publication_form_value')
    if form is not None and not isinstance(form, str) or form == '雑誌':
        raise ValueError('Futaba form literal outside product frame')
    pointer = f'/books/book_informations/{ordinal}'
    if any(book.get(key) is not None and not isinstance(book[key], str) for key in ('release_dt', 'start_dt')):
        raise ValueError('Futaba release date literal malformed')
    credits, credit_literals = [], []
    if not isinstance(book['book_authors'], list):
        raise ValueError('Futaba explicit credit list missing')
    for index, author in enumerate(book['book_authors']):
        name = author['author_name']
        literal = author.get('web_author_type_overwrite') or author.get('author_type_name')
        if not isinstance(name, str) or not name.strip() or literal is not None and not isinstance(literal, str):
            raise ValueError('Futaba explicit credit literal malformed')
        credits.append({'role': _ROLES.get(literal, 'unknown'), 'name': name})
        credit_literals.append({'role': literal, 'name': name,
                                'role_field': 'web_author_type_overwrite' if author.get('web_author_type_overwrite') else 'author_type_name',
                                'locator': pointer + f'/book_authors/{index}'})
    available = [(n, book.get(key)) for n, key in ((1, 'web_rbooks_id'), (2, 'web_ebooks_id'), (3, 'web_abooks_id')) if book.get(key)]
    default_type = available[0][0] if available else 1
    source_url = 'https://www.futabasha.co.jp/book/' + identifier + '?type=' + str(default_type)
    label = book['label_name']
    return {
        'publisher_reference_id': 'futaba-product:' + identifier,
        'source_unit': 'publisher_product_reference', 'source_url': source_url, 'title': title,
        'publisher': None, 'isbn': _isbn(book.get('isbn_code')), 'isbn_source_literal': book.get('isbn_code'),
        'form': form, 'credits': credits, 'credit_literals': credit_literals,
        'label': label, 'label_literals': [label] if label is not None else [],
        # The saved bibliographic witnesses say label/format, not "ライトノベル".
        'official_lightnovel_tag': None,
        'story_series_asserted': False, 'metadata_conflict': False, 'bibliographic_variants': [],
        'source_scope_membership': {
            'source_unit': 'publisher_label_filtered_product_listing',
            'membership_basis': 'returned_by_publisher_explicit_label_filter',
            'filter_parameter': 'label_name', 'filter_literal': label_literal,
            'rendered_index_url': search_url, 'request_url': receipt['source_url'],
            'raw_sha256': receipt['sha256'], 'source_label_id_literal': book['label_id'],
            'source_label_id_locator': pointer + '/label_id',
            'card_label_literal': label, 'card_label_locator': pointer + '/label_name',
            'official_ui_route_proof': route_proof_path,
            'does_not_assert_story_series_or_original_identity': True,
        },
        'release_date_source_literal': book.get('release_dt'),
        'start_date_source_literal': book.get('start_dt'),
        'source_evidence': [{'source_url': receipt['source_url'], 'raw_sha256': receipt['sha256'],
            'locator': pointer + ': book_name/isbn_code/book_authors(author_name,author_type_name,web_author_type_overwrite)/label_name/label_id/publication_form_value/release_dt/start_dt/jdcn_code/web_rbooks_id/web_ebooks_id/web_abooks_id'}],
    }


def verify_futaba_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return 378 bibliographic references from the fixed 16-page raw frame.

    This validates exact own label queries and UI-derived pagination/URLs. It
    does not consume the exported roster or assert novel/edition/work identity.
    """
    path = Path(manifest_path).resolve()
    try:
        manifest = _json(path, sha256)
        expected = {'version': 1, 'frame_id': 'futaba-monster-bunko-products-v1', 'unit': 'publisher_product_reference',
                    'label': _LABEL, 'status': 'complete_observed_finite_index', 'index_pages_complete': True,
                    'publisher_reported_total': 378, 'fixed_first_page': 1, 'fixed_final_page': 16, 'per_page': 25,
                    'search_url': _SEARCH, 'first_api_url': _FIRST, 'collection_holds': []}
        if any(manifest.get(k) != v for k, v in expected.items()) or manifest.get('index_pages_complete') is not True:
            raise ValueError('Futaba finite one-label frame differs')
        ref = manifest['source_receipts']
        log = _read(_relative(path.parent, ref['path']), ref['sha256'], MAX_METADATA_BYTES)
        receipt_list = [json.loads(line) for line in log.splitlines() if line.strip()]
        receipts = {r['source_url']: r for r in receipt_list}
        if len(receipts) != len(receipt_list):
            raise ValueError('Futaba duplicate receipt URL')
        ref = manifest['observed_route_proof']
        route_proof_path = ref['path']
        proof = _json(_relative(path.parent, ref['path']), ref['sha256'])
        ui = _route(path.parent, proof, receipts)
        pages = manifest['pagination_pages']
        if not isinstance(pages, list) or len(pages) != 16:
            raise ValueError('Futaba fixed page count differs')
        rows, seen = [], set()
        for number, page in enumerate(pages, 1):
            url = _page_url(number)
            receipt = receipts[url]
            if (page.get('page') != number or page.get('source_url') != url
                    or page.get('raw_sha256') != receipt['sha256'] or page.get('raw_path') != receipt['raw_path']
                    or page.get('byte_length') != receipt['byte_length']
                    or page.get('has_more_by_official_rule') is not (number < 16)
                    or page.get('next_url') != (_page_url(number + 1) if number < 16 else None)
                    or page.get('results_locator') != '/books/results' or page.get('items_locator') != '/books/book_informations'):
                raise ValueError('Futaba own query, next or source checkpoint differs')
            source = _source(path.parent, receipt, url)['books']
            expected_results = {'total_counts': 378, 'total_pages': 16, 'per_page': 25, 'current_page': number}
            if source['results'] != expected_results or page['results'] != source['results']:
                raise ValueError('Futaba source total or page boundary differs')
            books = source['book_informations']
            if not isinstance(books, list) or len(books) != (25 if number < 16 else 3) or page['count'] != len(books):
                raise ValueError('Futaba complete page or terminal count differs')
            for ordinal, book in enumerate(books):
                row = _row(book, receipt, ordinal, route_proof_path)
                if row['publisher_reference_id'] in seen:
                    raise ValueError('Futaba duplicate opaque product ID')
                seen.add(row['publisher_reference_id'])
                rows.append(row)
        if (len(rows) != 378 or len(seen) != 378 or ui['first_title_literal'] != rows[0]['title']
                or ui['first_title_url'] != rows[0]['source_url']):
            raise ValueError('Futaba unique total or observed first UI card differs')
        return rows
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Futaba source evidence schema cannot be reproduced') from exc


def _receipts(root, ref):
    raw = _read(_relative(root, ref['path']), ref['sha256'], MAX_METADATA_BYTES)
    items = [json.loads(line) for line in raw.splitlines() if line.strip()]
    result = {item['source_url']: item for item in items}
    if len(result) != len(items):
        raise ValueError('Futaba duplicate receipt URL')
    return result


def verify_futaba_mnovels_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Replay the two fixed M Novels label-filtered frames, without a roster.

    The reused Monster evidence establishes the observed API/URL/role rules;
    each M label has its own observed navigation, query, totals and raw pages.
    No individual genre, publisher, volume or story-series claim is inferred.
    """
    return _verify_mnovels_references(manifest_path, sha256, ungrouped_only=False)


def verify_futaba_mnovels_ungrouped_references(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return the 26 explicitly ungrouped product references in the fixed M frame.

    The same authenticated pass validates all 616 product rows. Only an explicit
    null or blank textual grouping field qualifies; a missing field is not
    evidence of unknown grouping. This does not infer singleton groups, ISBNs,
    media identity, story-series identity or individual light-novel status.
    Consumer selection of currently unconnected references is separate.
    """
    return _verify_mnovels_references(manifest_path, sha256, ungrouped_only=True)


def _verify_mnovels_references(manifest_path, sha256, *, ungrouped_only):
    path = Path(manifest_path).resolve()
    ungrouped = []
    try:
        proof = _json(path, sha256)
        if proof.get('schema_version') != 'futaba-mnovels-references-proof-v1':
            raise ValueError('Futaba M reference proof schema differs')
        ref = proof['reused_monster_manifest']
        old_path = _relative(path.parent, ref['path'])
        old = _json(old_path, ref['sha256'])
        ref = old['observed_route_proof']
        _route(old_path.parent, _json(_relative(old_path.parent, ref['path']), ref['sha256']),
               _receipts(old_path.parent, old['source_receipts']))
        ref = proof['terminal']
        terminal_path = _relative(path.parent, ref['path'])
        root = terminal_path.parent
        terminal = _json(terminal_path, ref['sha256'])
        if (terminal.get('schema_version') != 'futaba-mnovels-two-frame-terminal-v1'
                or terminal.get('status') != 'terminal_two_fixed_label_frames'
                or terminal.get('source_reference_count') != 616 or terminal.get('holds') != []
                or len(terminal['frames']) != 2):
            raise ValueError('Futaba M fixed terminal frame differs')
        receipts = _receipts(root, terminal['receipt_log'])
        ref = proof['observed_probe_contract']
        probe_path = _relative(path.parent, ref['path'])
        probe = _json(probe_path, ref['sha256'])
        observation = probe['official_ui_observation']
        if (probe.get('schema_version') != 'futaba-mnovels-probe-contract-v1'
                or observation.get('labels_url') != 'https://www.futabasha.co.jp/labels'
                or observation.get('method') != 'rendered label heading and link; pageAssets exact books request URL observed for each'
                or len(probe['frames']) != 2):
            raise ValueError('Futaba M observed label route differs')
        rows, seen, requested = [], set(), set()
        configurations = [('mnovels', 'Ｍノベルス', 12, 335, 14), ('mnovels-f', 'Ｍノベルスｆ', 21, 281, 12)]
        for frame_ref, observed, (frame_id, label, label_id, total, final) in zip(terminal['frames'], probe['frames'], configurations, strict=True):
            if frame_ref['frame_id'] != frame_id:
                raise ValueError('Futaba M fixed frame identity differs')
            ref = frame_ref['manifest']
            frame_path = _relative(root, ref['path'])
            frame = _json(frame_path, ref['sha256'])
            search = 'https://www.futabasha.co.jp/search?label_name=' + quote(label)
            first = _FIRST.replace(quote(_LABEL), quote(label))
            expected = {'schema_version': 'futaba-mnovels-product-frame-v1', 'frame_id': f'futaba-{frame_id}-products-v1',
                        'status': 'complete_observed_finite_index', 'label': label, 'publisher_reported_total': total,
                        'fixed_first_page': 1, 'fixed_final_page': final, 'per_page': 25, 'search_url': search, 'first_api_url': first}
            if any(frame.get(k) != v for k, v in expected.items()) or frame.get('index_pages_complete') is not True:
                raise ValueError('Futaba M exact label query or finite boundary differs')
            bound = frame['probe_contract']
            if (_relative(frame_path.parent, bound['path']) != probe_path or bound['sha256'] != proof['observed_probe_contract']['sha256']):
                raise ValueError('Futaba M observed route binding differs')
            expected_observed = {'id': frame_id, 'label': label, 'encoded': quote(label), 'first_api_url': first,
                                 'search_url': search, 'label_id_literals': [label_id], 'label_name_literals': [label],
                                 'form_literals': ['単行本'], 'first_count': 25, 'explicit_isbn_field': 'isbn_code',
                                 'author_role_rule': 'web_author_type_overwrite else author_type_name'}
            if any(observed.get(k) != v for k, v in expected_observed.items()):
                raise ValueError('Futaba M observed label, form or role rule differs')
            pages = frame['pagination_pages']
            if not isinstance(pages, list) or len(pages) != final:
                raise ValueError('Futaba M complete page range differs')
            for number, page in enumerate(pages, 1):
                url = first.replace('&page=1&', f'&page={number}&')
                requested.add(url)
                receipt = receipts[url]
                if (page.get('page') != number or page.get('source_url') != url or page['receipt'] != receipt
                        or page.get('terminal') is not (number == final)
                        or page.get('results_locator') != '/books/results' or page.get('items_locator') != '/books/book_informations'):
                    raise ValueError('Futaba M own page/next boundary or receipt differs')
                source = _source(root, receipt, url)['books']
                result = {'total_counts': total, 'total_pages': final, 'per_page': 25, 'current_page': number}
                if source['results'] != result or page['results'] != result:
                    raise ValueError('Futaba M source pagination total differs')
                if number == 1 and (observed['first_receipt'] != receipt or observed['results'] != result):
                    raise ValueError('Futaba M first observed API inventory differs')
                books = source['book_informations']
                if not isinstance(books, list) or len(books) != min(25, total - (number - 1) * 25) or page['count'] != len(books):
                    raise ValueError('Futaba M complete page/terminal count differs')
                for ordinal, book in enumerate(books):
                    if book.get('publication_form_value') != '単行本':
                        raise ValueError('Futaba M explicit form differs')
                    row = _row(book, receipt, ordinal, proof['observed_probe_contract']['path'], label_literal=label, label_id=label_id, search_url=search)
                    if row['publisher_reference_id'] in seen:
                        raise ValueError('Futaba M duplicate opaque product ID')
                    seen.add(row['publisher_reference_id'])
                    rows.append(row)
                    if ungrouped_only:
                        evidence = {'source_url': url, 'raw_sha256': receipt['sha256'],
                                    'locator': f'/books/book_informations/{ordinal}/grouping'}
                        if ('grouping' not in book
                                or book['grouping'] is not None and not isinstance(book['grouping'], str)):
                            raise ValueError('Futaba M explicit grouping field absent or malformed: '
                                             f'{url} sha256={receipt["sha256"]} '
                                             f'ref={row["publisher_reference_id"]} locator={evidence["locator"]}')
                        grouping = book['grouping']
                        if grouping is None or not grouping.strip():
                            ungrouped.append({**row, 'grouping_source_literal': grouping,
                                'grouping_status': 'unknown_no_nonempty_publisher_grouping',
                                'grouping_field_evidence': evidence})
        if set(receipts) != requested or len(rows) != 616:
            raise ValueError('Futaba M complete receipt range or unique total differs')
        if ungrouped_only:
            if len(ungrouped) != 26:
                raise ValueError('Futaba M fixed explicit unknown-grouping count differs')
            return ungrouped
        return rows
    except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Futaba M source evidence schema cannot be reproduced') from exc
