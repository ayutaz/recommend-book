"""Replay selected, pinned Overlap own cards and product bibliographies only.

The finite index manifests bind scope; only selected card pages are opened.
Neither a brand nor an unmarked creator becomes genre, publisher or writer
evidence. Product identifiers remain opaque even when their digits resemble ISBNs.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qs, urljoin, urlsplit

from .light_novel_overlap_references_v1 import _literal, _SCOPE
from .light_novel_publisher_references_v1 import (
    MAX_METADATA_BYTES, MAX_SOURCE_BYTES, _DOM, _Node, _decode, _read, _relative,
)
from .normalization import normalize_isbn


_BRANDS = {
    'BNK': ('オーバーラップ文庫', 'bnk__badge', 'overlap-product:', 34, 1019),
    'NVL': ('オーバーラップノベルス', 'nvl__badge', 'overlap-nvl-product:', 15, 427),
    'NVF': ('オーバーラップノベルスf', 'nvf__badge', 'overlap-nvf-product:', 7, 187),
    'NVR': ('ロサージュノベルス', 'nvr__badge', 'overlap-nvr-product:', 1, 25),
}
_MAX_DETAILS = sum(row[4] for row in _BRANDS.values())


def _one(nodes, field):
    if len(nodes) != 1:
        raise ValueError('Overlap own ' + field + ' is absent or ambiguous')
    return nodes[0]


def _key(value):
    return ''.join(unicodedata.normalize('NFKC', value).split())


def _direct(node, *, tag=None, cls=None):
    return [part for part in node.parts if isinstance(part, _Node)
            and (tag is None or part.tag == tag)
            and (cls is None or cls in part.attrs.get('class', '').split())]


def _json(root, pin):
    path = _relative(root, pin['manifest_path'])
    return json.loads(_read(path, pin['manifest_sha256'], MAX_METADATA_BYTES))


def _receipt(root, pin):
    path = _relative(root, pin['receipt_path'])
    return json.loads(_read(path, pin['receipt_sha256'], MAX_METADATA_BYTES))


def _source(root, receipt, url):
    statuses = [receipt[k] for k in ('http_status', 'status') if k in receipt]
    if (receipt.get('source_url') != url or receipt.get('final_url') != url
            or not statuses or any(value != 200 for value in statuses)
            or any(receipt.get(k) for k in ('location', 'error', 'hold', 'size_cap_exceeded',
                'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count'))):
        raise ValueError('Overlap source receipt status, redirect or scope differs')
    content_type = receipt.get('content_type') or ''
    if not content_type.lower().startswith('text/html'):
        raise ValueError('Overlap bibliographic source is not HTML')
    raw = _read(_relative(root, receipt.get('raw_path')), receipt.get('sha256'), MAX_SOURCE_BYTES)
    if receipt.get('byte_length') != len(raw):
        raise ValueError('Overlap declared source size differs')
    return _DOM(_decode(raw, content_type)).root


def _start(category):
    return 'https://over-lap.co.jp/Form/Product/ProductList.aspx?cat=' + category


def _product(url, category):
    if not isinstance(url, str):
        raise ValueError('Overlap product URL absent')
    parsed = urlsplit(url)
    identity = re.fullmatch(r'/[^/]+/product/0/([A-Za-z0-9_-]+)/', parsed.path)
    if (parsed.scheme != 'https' or parsed.netloc != 'over-lap.co.jp' or parsed.fragment
            or not identity or parse_qs(parsed.query, keep_blank_values=True) != {'cat': [category], 'swrd': ['']}):
        raise ValueError('Overlap product URL leaves own brand scope')
    return identity[1]


def _frames(root, pins):
    if not isinstance(pins, list) or not 1 <= len(pins) <= 2:
        raise ValueError('Overlap finite source frame count differs')
    result, seen = [], set()
    for pin in pins:
        manifest = _json(root, pin)
        schema = manifest.get('schema_version')
        if schema == 'overlap-three-additional-product-frames-v1':
            if (manifest.get('status') != 'terminal_three_fixed_brand_frames'
                    or manifest.get('index_pages_complete') is not True
                    or manifest.get('collected_pages') != 23 or manifest.get('card_occurrences') != 639
                    or manifest.get('unique_opaque_product_ids') != 639):
                raise ValueError('Overlap additional frame is not the finite terminal scope')
            frames = manifest['frames']
            if not isinstance(frames, list) or [f.get('category') for f in frames] != ['NVL', 'NVF', 'NVR']:
                raise ValueError('Overlap additional brand scope differs')
        elif schema == 'overlap-bunko-product-frame-v1':
            if (manifest.get('status') != 'terminal_fixed34_pages' or manifest.get('index_pages_complete') is not True
                    or manifest.get('processed_pages') != 34 or manifest.get('declared_finite_pages') != 34
                    or manifest.get('observed_card_occurrences') != 1019
                    or manifest.get('unique_product_references') != 1019):
                raise ValueError('Overlap Bunko frame is not the finite terminal scope')
            frames = [{**manifest, 'category': 'BNK', 'fixed_final_page': 34,
                       'card_occurrences': 1019, 'terminal': True}]
        else:
            raise ValueError('Overlap source frame schema differs')
        local = {}
        for frame in frames:
            category = frame['category']
            label, _, _, pages, total = _BRANDS[category]
            if (category in seen or frame.get('label') != label or frame.get('source_url') != _start(category)
                    or frame.get('terminal') is not True or frame.get('fixed_final_page') != pages
                    or frame.get('card_occurrences') != total or frame.get('unique_product_references') != total
                    or not isinstance(frame.get('pages'), list) or len(frame['pages']) != pages
                    or [p.get('page') for p in frame['pages']] != list(range(1, pages + 1))):
                raise ValueError('Overlap declared finite brand/page scope differs')
            seen.add(category)
            local[category] = frame
        result.append(local)
    return result


def _page_url(url, category, number):
    if number == 1:
        if url != _start(category):
            raise ValueError('Overlap first own card URL differs')
        return
    parsed = urlsplit(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    pno = query.pop('pno', [])
    if (parsed.scheme != 'https' or parsed.netloc != 'over-lap.co.jp' or parsed.fragment
            or not re.fullmatch(r'/[^/]+/category/0/' + category + '/', parsed.path)
            or query != _SCOPE or pno != [str(number)]):
        raise ValueError('Overlap own card page route differs')


def _card(root, frame, number, ordinal, cache):
    category = frame['category']
    label, badge, prefix, last, total = _BRANDS[category]
    if type(number) is not int or not 1 <= number <= last or type(ordinal) is not int:
        raise ValueError('Overlap own card page or ordinal outside bound')
    page = frame['pages'][number - 1]
    receipt = page['source_snapshot']
    source_url = receipt['source_url']
    _page_url(source_url, category, number)
    key = (category, number)
    if key not in cache:
        tree = _source(root, receipt, source_url)
        page_titles = [unicodedata.normalize('NFKC', n.text()) for n in tree.nodes(tag='title')]
        if page_titles != [unicodedata.normalize('NFKC', label + '｜OVERLAP')]:
            raise ValueError('Overlap card page brand title differs')
        container = _one(tree.nodes(cls='product-cards'), 'card list')
        cards = _direct(container, cls='product-card')
        expected = 30 if number < last else total - 30 * (last - 1)
        if len(cards) != expected or page.get('card_count') != expected:
            raise ValueError('Overlap selected own card page count differs')
        rows, urls, identities = [], set(), set()
        for index, node in enumerate(cards, 1):
            anchor = _one(node.nodes(tag='a'), 'product card anchor')
            url = urljoin(source_url, anchor.attrs.get('href', ''))
            identity = _product(url, category)
            title = _literal(_one(node.nodes(cls='product-card__title'), 'complete card title')).strip()
            own_label = _one(node.nodes(cls=badge), 'card brand').text()
            badges = [item for brand in _BRANDS.values() for item in node.nodes(cls=brand[1])]
            if not title or own_label != label or len(badges) != 1 or url in urls or identity in identities:
                raise ValueError('Overlap own card title/label or duplicate product differs')
            urls.add(url); identities.add(identity)
            rows.append({'publisher_product_reference_id': prefix + identity, 'source_url': url,
                'title': title, 'label': label, 'evidence': {'source_url': source_url,
                    'raw_sha256': receipt['sha256'], 'retrieved_at': receipt.get('retrieved_at'),
                    'selector': f'.product-cards > .product-card[{index}] a / .product-card__title / .{badge}'}})
        cache[key] = rows
    if not 1 <= ordinal <= len(cache[key]):
        raise ValueError('Overlap own card ordinal outside selected page')
    return cache[key][ordinal - 1]


def _detail(root, pin, card):
    url = pin['source_url']
    if url != card['source_url']:
        raise ValueError('Overlap detail URL was not observed on the selected own card')
    receipt = _receipt(root, pin)
    tree = _source(root, receipt, url)
    canonical_nodes = [n for n in tree.nodes(tag='link') if 'canonical' in n.attrs.get('rel', '').split()]
    if any(not n.attrs.get('href', '').strip() for n in canonical_nodes):
        raise ValueError('Overlap present canonical URL is empty')
    canonicals = [urljoin(url, n.attrs['href']) for n in canonical_nodes]
    if canonicals and canonicals != [url]:
        raise ValueError('Overlap canonical product URL contradicts own source')
    main = _one(tree.nodes(tag='main', cls='product-main'), 'product main boundary')
    contents = _one(_direct(main, cls='product-contents'), 'product contents')
    section = _one(_direct(contents, tag='section', cls='goods-explanation'), 'own product explanation')
    body = _one(_direct(section, cls='goods-explanation__body'), 'own bibliographic body')
    title = _literal(_one(_direct(body, tag='h1', cls='goods-explanation__title'), 'complete detail title')).strip()
    creator = _literal(_one(_direct(body, tag='p', cls='goods-explanation__author'), 'creator field')).strip()
    if not title or len(title) > 4096 or not creator or len(creator) > 4096 or _key(title) != _key(card['title']):
        raise ValueError('Overlap complete detail/card title or creator differs')
    parents = {id(child): node for node in [tree, *tree.nodes()]
               for child in node.parts if isinstance(child, _Node)}

    def in_related_card(node):
        while id(node) in parents and node is not body:
            if 'product-card' in node.attrs.get('class', '').split():
                return True
            node = parents[id(node)]
        return False

    # In the observed template brand belongs to the own index card. The detail
    # page's navigation and related-product badges are not this product's label.
    if any(not in_related_card(node) for brand in _BRANDS.values() for node in body.nodes(cls=brand[1])):
        raise ValueError('Overlap unexpected own detail brand field requires review')
    marked = re.fullmatch(r'(?s)(.*?)\s*(イラスト[／/])\s*(.+)', creator)
    if (not marked or not marked[1].strip() or not marked[3].strip()
            or len(re.findall(r'イラスト[／/]', creator)) != 1
            or re.search(r'(?:著者|著|原作|原案|漫画|文)[／/:：]', marked[1])):
        raise ValueError('Overlap creator field leaves unmarked/explicit illustrator schema')
    credits = [
        {'role': 'unknown', 'name': marked[1].strip(), 'role_literal': None,
         'role_basis': 'unmarked_creator_name_retained_unknown_not_inferred_from_position_or_css'},
        {'role': 'illustrator', 'name': marked[3].strip(), 'role_literal': 'イラスト',
         'role_marker_literal': marked[2], 'role_basis': 'explicit_role_marker_in_own_creator_field'},
    ]
    def own_spec(node):
        spec, aside = False, False
        while id(node) in parents and node is not main:
            classes = node.attrs.get('class', '').split()
            if 'product-card' in classes:
                return False
            spec |= 'sidebar-buy__specs' in classes
            aside |= node.tag == 'aside' and 'product-sidebar' in classes
            node = parents[id(node)]
        return node is main and spec and aside

    fields = []
    for number, node in enumerate(tree.nodes(cls='sidebar-buy__spec-row'), 1):
        if not own_spec(node):
            continue
        label = _literal(_one(node.nodes(cls='sidebar-buy__spec-label'), 'specification label')).strip()
        if _key(label) not in {'ISBN', '判型'}:
            continue
        value = _literal(_one(node.nodes(cls='sidebar-buy__spec-value'), 'specification value')).strip()
        fields.append({'field_key': _key(label), 'label_literal': label, 'value_literal': value,
            'locator': f'(.sidebar-buy__spec-row)[{number}] .sidebar-buy__spec-label / .sidebar-buy__spec-value'})
    isbn_values = sorted({f['value_literal'] for f in fields if f['field_key'] == 'ISBN'})
    forms = sorted({f['value_literal'] for f in fields if f['field_key'] == '判型'})
    if len(isbn_values) > 1 or len(forms) > 1:
        raise ValueError('Overlap own repeated bibliographic fields contradict')
    literal = isbn_values[0] if isbn_values else None
    isbn = normalize_isbn(literal) if literal and re.fullmatch(r'[0-9Xx][0-9Xx -]*', literal) else None
    if literal is not None and not isbn:
        raise ValueError('Overlap explicit ISBN field is invalid')
    evidence = [{'source_url': url, 'raw_sha256': receipt['sha256'], 'locator': selector} for selector in
                ['.goods-explanation__body h1.goods-explanation__title',
                 '.goods-explanation__body p.goods-explanation__author', *[f['locator'] for f in fields]]]
    holds = ['official_writer_role_unknown_unmarked_name_not_promoted']
    if isbn is None:
        holds.append('explicit_isbn_unknown')
    if not forms:
        holds.append('explicit_form_unknown')
    identity = card['publisher_product_reference_id']
    return {'publisher_product_reference_id': identity, 'publisher_reference_id': identity,
        'source_unit': 'publisher_product_detail_bibliographic_reference_not_confirmed_edition',
        'source_url': url, 'source_receipt': receipt, 'title': title, 'owncard_title_literal': card['title'],
        'title_exact_equal_to_owncard': title == card['title'], 'title_nfkc_whitespace_equal_to_owncard': True,
        'isbn': isbn, 'isbn_source_literal': literal, 'isbn_source_basis': 'explicit_ISBN_label_value_not_URL',
        'credits': credits, 'creator_field_literal': creator, 'label': card['label'],
        'label_source_scope': 'own_card_label_only_detail_own_label_not_observed', 'detail_label': None,
        'publisher': None, 'publisher_status': 'no_individual_publisher_field_observed_not_inferred_from_site_owner',
        'form': forms[0] if forms else None,
        'form_source_label': next((f['label_literal'] for f in fields if f['field_key'] == '判型'), None),
        'bibliographic_field_literals': fields, 'source_evidence': evidence,
        'owncard_source_evidence': card['evidence'], 'canonical_urls': canonicals,
        'canonical_status': 'not_present_no_redirect_observed' if not canonicals else 'observed_exact_source_url',
        'hold_reasons': holds, 'edition_identity_asserted': False, 'original_work_identity_asserted': False,
        'story_series_identity_asserted': False, 'existing_ndl_candidate_comparison': {'performed': False,
            'readiness': 'explicit_ISBN_and_full_title_candidate_lookup_possible_but_official_writer_role_gate_unqualified',
            'confirmed_metadata_match_count': None, 'source_role_not_filled_from_ndl': True}}


def verify_overlap_product_details(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Return selected card-to-detail facts within pinned finite four-brand bounds.

    This authenticates neither the whole saved index nor NDL matches. Missing
    ISBN/form and absent canonical remain unknown; conflicting fields are rejected.
    """
    path = Path(manifest_path).resolve()
    try:
        manifest = json.loads(_read(path, sha256, MAX_METADATA_BYTES))
        if manifest.get('schema_version') != 'overlap-product-details-proof-v1':
            raise ValueError('Overlap detail manifest schema differs')
        frames = _frames(path.parent, manifest['source_frames'])
        bound = sum(_BRANDS[category][4] for group in frames for category in group)
        details = manifest['details']
        if not isinstance(details, list) or not 1 <= len(details) <= min(bound, _MAX_DETAILS):
            raise ValueError('Overlap detail frame exceeds declared finite source bound')
        rows, urls, ids, cache = [], set(), set(), {}
        for pin in details:
            index, category = pin['frame_index'], pin['category']
            if type(index) is not int or not 0 <= index < len(frames) or category not in frames[index]:
                raise ValueError('Overlap selected source frame/category differs')
            _product(pin['source_url'], category)
            card = _card(path.parent, frames[index][category], pin['page'], pin['card_ordinal'], cache)
            identity, url = card['publisher_product_reference_id'], pin['source_url']
            if url in urls or identity in ids:
                raise ValueError('Overlap duplicate selected product identity')
            urls.add(url); ids.add(identity)
            rows.append(_detail(path.parent, pin, card))
        return rows
    except (KeyError, TypeError, IndexError, AttributeError, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
        raise ValueError('Overlap product detail evidence cannot be reproduced') from exc
