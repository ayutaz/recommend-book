"""Strict dual-primary DRE novel series proof and own-NDL-member matching.

The caller authenticates NDL primary parts. No title-prefix identity, prose,
manga, implicit writer role, or numeric-volume default is used here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import unicodedata

from .light_novel_publisher_references_v1 import _DOM, _Node, _decode, _relative
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .normalization import normalize_isbn

INDEX_ID = 'dre-novels-series-v2'
FRAME_VERSION = 'dre-explicit-linked-novel-series-dual-source-v2'
PRODUCT = re.compile(r'https://drecom-media\.jp/drenovels/product/[1-9][0-9]*')
SERIES = re.compile(r'https://drecom-media\.jp/drenovels/series/[A-Za-z0-9_-]+')
AUTHORITY = re.compile(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/[0-9]{6,12}')
SHA = re.compile(r'[0-9a-f]{64}')


def _one(nodes, name):
    if len(nodes) != 1:
        raise ValueError('DRE v2 own ' + name + ' absent or ambiguous')
    return nodes[0]


def _literal(node):
    if node.tag in {'script', 'style', 'input', 'img'}:
        return ''
    if node.tag == 'br':
        return '\n'
    return ''.join(_literal(p) if isinstance(p, _Node) else p for p in node.parts).strip()


def _roles(nodes):
    result = []
    for node in nodes:
        literal = _literal(node)
        match = re.fullmatch(r'(著|イラスト)\s*/\s*(.+)', literal)
        if not match:
            raise ValueError('DRE v2 explicit creator role absent')
        result.append({'role': 'author' if match[1] == '著' else 'illustrator',
                       'name': match[2], 'literal': literal})
    if not any(c['role'] == 'author' for c in result) or len({(c['role'], _key(c['name'])) for c in result}) != len(result):
        raise ValueError('DRE v2 direct author absent or duplicated')
    return result


def _facts(container, prefix):
    facts = {}
    for node in container.nodes(cls=prefix + '__info'):
        label = _literal(_one(node.nodes(cls=prefix + '__infoLabel'), 'fact label'))
        value = _literal(_one(node.nodes(cls=prefix + '__infoValue'), 'fact value'))
        if label in facts:
            raise ValueError('DRE v2 own fact duplicated')
        facts[label] = value
    isbn = normalize_isbn(facts.get('ISBN', ''))
    if not isbn:
        raise ValueError('DRE v2 own ISBN absent or invalid')
    return isbn, facts['ISBN']


def _snapshot(root, pin, artifact, cache, route):
    receipt_path = _relative(root, pin['receipt_path'])
    receipt = json.loads(artifact(receipt_path, pin['receipt_sha256'], cache))
    url = receipt.get('source_url')
    if (not isinstance(url, str) or not route.fullmatch(url) or receipt.get('http_status') != 200
            or receipt.get('final_url') != url or receipt.get('http_method') != 'GET'
            or not str(receipt.get('content_type', '')).lower().startswith('text/html')
            or any(receipt.get(k) for k in ('location', 'error', 'hold', 'size_cap_exceeded', 'host_hold_no_retry',
                'automatic_redirects', 'redirects_followed', 'http_redirects_followed', 'retry_count', 'http_retry_count'))):
        raise ValueError('DRE v2 source receipt URL/status/redirect differs')
    raw_path = _relative(root, receipt['raw_path'])
    raw = artifact(raw_path, receipt['sha256'], cache)
    if len(raw) > 5_000_000 or len(raw) != receipt.get('byte_length'):
        raise ValueError('DRE v2 source byte size differs')
    tree = _DOM(_decode(raw, receipt['content_type'])).root
    canonical = [n.attrs.get('href') for n in tree.nodes(tag='link')
                 if 'canonical' in n.attrs.get('rel', '').split()]
    if canonical != [url]:
        raise ValueError('DRE v2 own canonical differs')
    return tree, {'source_url': url, 'raw_path': str(raw_path), 'raw_sha256': receipt['sha256'],
                  'receipt_path': str(receipt_path), 'receipt_sha256': pin['receipt_sha256']}


def _members(tree, heading_class):
    headings = [n for n in tree.nodes(tag='h2') if n.attrs.get('id') == 'series']
    heading = _one(headings, 'novel member heading')
    if heading_class not in heading.attrs.get('class', '').split() or _literal(heading) != 'シリーズ既刊一覧':
        raise ValueError('DRE v2 own novel member scope differs')
    parents = {id(child): node for node in [tree, *tree.nodes()]
               for child in node.parts if isinstance(child, _Node)}
    section = heading
    while section.tag != 'section' and id(section) in parents:
        section = parents[id(section)]
    if section.tag != 'section' or any(n is not heading for n in section.nodes(tag='h2')):
        raise ValueError('DRE v2 novel section boundary differs')
    members = []
    for i, card in enumerate(section.nodes(tag='li', cls='bookColumn__item'), 1):
        urls = [n.attrs.get('href') for n in card.nodes(tag='a')]
        url = _one(urls, 'novel product link')
        if not isinstance(url, str) or not PRODUCT.fullmatch(url):
            raise ValueError('DRE v2 member leaves novel product route')
        title = _literal(_one(card.nodes(tag='span', cls='bookColumn__title'), 'complete novel card title'))
        roles = _roles(card.nodes(tag='span', cls='bookColumn__author'))
        isbn, literal = _facts(card, 'productColumn')
        if not title:
            raise ValueError('DRE v2 member title absent')
        members.append({'source_url': url, 'title': title, 'isbn': isbn, 'isbn_literal': literal,
            'authors': [c['name'] for c in roles if c['role'] == 'author'], 'credits': roles,
            'locator': '#series.' + heading_class + ' closest section li.bookColumn__item[' + str(i) + ']'})
    if not members or len({m['source_url'] for m in members}) != len(members) or len({m['isbn'] for m in members}) != len(members):
        raise ValueError('DRE v2 novel members absent or duplicated')
    return members


def _member_key(member):
    return (member['source_url'], member['isbn'], _key(member['title']),
            tuple(sorted(_key(a) for a in member['authors'])))


def verify_frame(frame, root, artifact, cache):
    """Replay a bounded frame of independently pinned product/series pairs."""
    if (frame.get('id') != INDEX_ID or frame.get('version') != FRAME_VERSION
            or frame.get('publisher') != 'ドリコム' or frame.get('label') != 'DREノベルス'
            or not isinstance(frame.get('pairs'), list) or not 1 <= len(frame['pairs']) <= 100):
        raise ValueError('DRE v2 frame identity or bounds differ')
    root = Path(root).resolve()
    rows = {}
    for pair in frame['pairs']:
        product, pe = _snapshot(root, pair['product'], artifact, cache, PRODUCT)
        series, se = _snapshot(root, pair['series'], artifact, cache, SERIES)
        own = _one(product.nodes(tag='section', cls='lineupColumn__item'), 'product boundary')
        if _literal(_one(own.nodes(cls='lineupColumn__label'), 'product label')) != 'DREノベルス':
            raise ValueError('DRE v2 product novel label differs')
        title = _literal(_one(own.nodes(tag='h1', cls='lineupColumn__title'), 'product title'))
        roles = _roles(own.nodes(tag='span', cls='lineupColumn__author'))
        isbn, literal = _facts(own, 'lineupColumn')
        links = [n.attrs.get('href') for n in own.nodes(tag='a') if _literal(n) == 'シリーズ紹介']
        if links != [se['source_url']]:
            raise ValueError('DRE v2 actual own series introduction link differs')
        main = _one(series.nodes(tag='section', cls='seriesDetail__main'), 'series main boundary')
        canonical_title = _literal(_one(main.nodes(tag='h1', cls='seriesDetail__mainTitle'), 'visible canonical title'))
        series_roles = _roles(main.nodes(tag='p', cls='seriesDetail__mainAuthor'))
        authors = [c['name'] for c in roles if c['role'] == 'author']
        canonical_authors = [c['name'] for c in series_roles if c['role'] == 'author']
        if not title or not canonical_title or sorted(map(_key, authors)) != sorted(map(_key, canonical_authors)):
            raise ValueError('DRE v2 direct series/product authors differ')
        product_members = _members(product, 'productDetail__subTitle--novels')
        series_members = _members(series, 'seriesDetail__subTitle--novels')
        if {_member_key(m) for m in product_members} != {_member_key(m) for m in series_members}:
            raise ValueError('DRE v2 two primary novel lists disagree')
        own_books = [m for m in series_members if m['source_url'] == pe['source_url']]
        own_book = _one(own_books, 'explicit own product membership')
        if (own_book['isbn'] != isbn or _key(own_book['title']) != _key(title)
                or sorted(map(_key, own_book['authors'])) != sorted(map(_key, authors))
                or any(sorted(map(_key, m['authors'])) != sorted(map(_key, authors)) for m in series_members)):
            raise ValueError('DRE v2 own product ISBN/title/author membership disagrees')
        reference = 'publisher-series-dre-v2:' + hashlib.sha256(se['source_url'].encode()).hexdigest()[:24]
        row = {'reference_id': reference, 'index_id': INDEX_ID, 'proof_version': FRAME_VERSION,
            'title': canonical_title, 'series_title': canonical_title, 'source_url': se['source_url'],
            'publisher': 'ドリコム', 'label': 'DREノベルス', 'authors': canonical_authors,
            'explicit_book_memberships': series_members,
            'membership_basis': 'own_product_direct_series_link_and_canonical_novel_series_members_v2',
            'source_evidence': [{'source_url': pe['source_url'], 'raw_sha256': pe['raw_sha256'],
                'locator': 'section.lineupColumn__item a with literal シリーズ紹介; own ISBN/title/author'},
                {'source_url': se['source_url'], 'raw_sha256': se['raw_sha256'],
                'locator': 'link[rel=canonical];section.seriesDetail__main;#series.seriesDetail__subTitle--novels'}],
            'primary_source_snapshots': {'product': pe, 'series': se}, 'raw_sha256': se['raw_sha256']}
        if reference in rows:
            raise ValueError('DRE v2 duplicate series pairs')
        rows[reference] = row
    return rows


def _complete_title(parts, target, own_parent):
    matched = _titles(parts, target, own_parent)
    if matched:
        return matched
    # Brackets explicitly delimit NDL's literal 上/下; no volume is invented.
    volume = parts['volume']
    if not volume or unicodedata.normalize('NFKC', volume['value']).strip() not in {'上', '下'}:
        return None
    selected = None
    rows = [r for r in parts['titles'] if '/{http://purl.org/dc/terms/}title[' in r['locator']
            and _parent(r['locator']) == own_parent]
    if not rows or _parent(volume['locator']) != own_parent:
        return None
    for row in rows:
        raw = row['value']; value = volume['value']
        main = raw[:-len('. ' + value)] if raw.endswith('. ' + value) else raw
        split = main.split(' : ', 1); main = split[0]; subtitle = split[1] if len(split) == 2 else ''
        variants = [main + left + value + right + subtitle for left, right in [('〈', '〉'), ('《', '》'), ('(', ')'), ('【', '】')]]
        if not main or not any(_key(v) == _key(target) for v in variants):
            return None
        selected = {'rule': 'own_complete_title_and_explicit_upper_lower_delimiter_v2',
            'source_value': raw, 'main': main, 'subtitle': subtitle or None, 'volume': value,
            'locators': [row['locator'], volume['locator']]}
    return selected


def _match_member(assertion, parts, *, title_grammar=None):
    """Match only authenticated own NDL fields; numeric ordering may be null."""
    from .light_novel_ndl_series_v1 import person_key
    from .light_novel_explicit_series_match_v1 import dated_own_author_bindings
    try:
        if assertion['index_id'] != INDEX_ID or assertion['proof_version'] != FRAME_VERSION or not SERIES.fullmatch(assertion['source_url']):
            return None
        source = parts['source']; identity = source['source_id']
        if (not re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', identity)
                or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + identity
                or source['provider_id'] not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
                or source['record_kind'] != 'edition' or source['language'] not in {'ja', 'jpn'}
                or not SHA.fullmatch(parts['source_raw_sha256'])
                or not any(n.startswith('913') for n in source['extra']['ndc_codes'])
                or _key((source.get('series') or '').split(';')[0]) not in {_key('DREノベルス'), _key('DRE NOVELS')}):
            return None
        publishers = parts['publisher_agents']
        own_publishers = [p for p in publishers if p['description_values'] == []]
        distributors = [p for p in publishers if p['description_values'] == ['頒布']]
        if (len(own_publishers) != 1 or _key(own_publishers[0]['name']) != _key('ドリコム')
                or len(own_publishers) + len(distributors) != len(publishers)
                or not publishers or any(not p['name'] or not p['locator'] for p in publishers)
                or source.get('publisher') != '; '.join(dict.fromkeys(p['name'] for p in publishers))
                or len({_parent(p['locator']) for p in publishers}) != 1
                or distributors and (any(not p['structured_agent'] for p in publishers)
                    or any(len(p['description_locators']) != 1 or not p['description_locators'][0] for p in distributors))):
            return None
        own_parent = _parent(own_publishers[0]['locator'])
        isbns = {normalize_isbn(i['value']) for i in source['identifiers'] if i['source'] == 'isbn'} - {None}
        members = [m for m in assertion['explicit_book_memberships'] if m['isbn'] in isbns]
        if len(members) != 1:
            return None
        member = members[0]
        title = title_grammar if title_grammar is not None else _complete_title(parts, member['title'], own_parent)
        if not title:
            return None
        volume = parts['volume']; literal = volume['value'] if volume else None
        if volume and _parent(volume['locator']) != own_parent:
            return None
        normalized = unicodedata.normalize('NFKC', literal or '').strip()
        numeric = re.fullmatch(r'(?:第)?([0-9]{1,4})(?:巻)?', normalized)
        if normalized and normalized not in {'上', '下'} and not numeric:
            return None
        if numeric and not 1 <= int(numeric[1]) <= 10000:
            return None
        bindings = []
        for name in member['authors']:
            literals = [l for l in parts['literal_authors'] if _parent(l['locator']) == own_parent and person_key(l['name']) == person_key(name)
                        and re.fullmatch(r'.+?\s*[\[（(]?\s*(著者|著|原作|作)\s*[\]）)]?', l['literal'])]
            ids = sorted({i['value'] for c in source['contributors'] if c['role'] == 'author' and person_key(c['name']) == person_key(name)
                          for i in c['identifiers'] if i['source'] == 'ndl-authority' and AUTHORITY.fullmatch(i['value'])})
            dated = dated_own_author_bindings(parts, name) if not (literals and ids) else []
            if literals and ids:
                bindings.append({'name': name, 'authority_ids': ids, 'own_writer_locators': [l['locator'] for l in literals]})
            elif dated:
                bindings.extend({'name': name, 'authority_ids': a['authority_ids'], 'dated_own_author_binding': a} for a in dated)
            else:
                return None
        if (not bindings or any(person_key(l['name']) not in {person_key(n) for n in member['authors']}
                                for l in parts['literal_authors'])):
            return None
        return {'rule_version': 'dre-dual-source-explicit-ndl-member-match-v2',
            'reference_id': assertion['reference_id'], 'title': assertion['title'],
            'matched_ndl_title': title['source_value'], 'title_origin': {'kind': 'official_canonical_series_title',
                'locator': 'section.seriesDetail__main h1.seriesDetail__mainTitle'},
            'title_match': title, 'authors': bindings, 'volume': literal,
            'volume_number': int(numeric[1]) if numeric else None,
            'ndc_codes': list(source['extra']['ndc_codes']), 'imprint': source['series'], 'publisher': source['publisher'],
            'isbn13': member['isbn'], 'official_member': deepcopy(member),
            'publisher_agent_policy': {'publisher': deepcopy(own_publishers[0]), 'explicit_distributors': deepcopy(distributors)},
            'edition_scoped_label_witness': {'official_label': 'DREノベルス',
                'ndl_literal_imprint': source['series'].split(';')[0].strip(),
                'explicit_member_isbn': member['isbn'], 'official_member_url': member['source_url'],
                'edition_scoped_only': True},
            'source_raw_sha256': parts['source_raw_sha256'], 'series_identity_basis': 'explicit_canonical_novel_series_and_exact_book_membership',
            'person_identity_asserted': False, 'original_work_identity_asserted': False}
    except (KeyError, TypeError, AttributeError, ValueError, IndexError):
        return None


def match_member(assertion, parts):
    """Keep successful v2 evidence dictionaries unchanged."""
    return _match_member(assertion, parts)


def _structured_value(node, locator):
    """Read only one direct RDF semantic value, never readings or other titles."""
    rdf = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
    ndl = 'http://ndl.go.jp/dcndl/terms/'
    if (node.attrib or len(node) != 1 or node[0].tag != '{' + rdf + '}Description'
            or node[0].attrib or (node.text or '').strip() or (node[0].tail or '').strip()):
        return None
    description = node[0]
    values = [child for child in description if child.tag == '{' + rdf + '}value']
    if (len(values) != 1 or any(child.tag not in {'{' + rdf + '}value', '{' + ndl + '}transcription'}
                              or child.attrib or len(child) or (child.tail or '').strip() for child in description)
            or (description.text or '').strip()):
        return None
    raw = values[0].text or ''
    if not raw or raw != raw.strip():
        return None
    return {'value': raw, 'raw_literal': raw, 'locator': locator,
            'value_locator': locator + '/{' + rdf + '}Description[1]/{' + rdf + '}value[1]'}


def _own_title_grammar(parts, own):
    """Two bounded own-field grammars; no generic parallel-title deletion."""
    dct = 'http://purl.org/dc/terms/'
    dc = 'http://purl.org/dc/elements/1.1/'
    ndl = 'http://ndl.go.jp/dcndl/terms/'
    rdf = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
    try:
        if own is None or own.tag != '{' + ndl + '}BibResource' or own.get('{' + rdf + '}about', '').split('#')[0] != parts['source']['source_url']:
            return None
        own_parent = _parent(parts['publisher_agents'][0]['locator'])
        primary, catalogued, alternatives, volumes = [], [], [], []
        for i, child in enumerate(own, 1):
            locator = own_parent + '/' + child.tag + '[' + str(i) + ']'
            if child.tag == '{' + dct + '}title':
                raw = child.text or ''
                if child.attrib or len(child) or not raw or raw != raw.strip():
                    return None
                primary.append({'value': raw, 'raw_literal': raw, 'locator': locator})
            elif child.tag in {'{' + dc + '}title', '{' + ndl + '}alternative'}:
                value = _structured_value(child, locator)
                if value is None:
                    return None
                (catalogued if child.tag == '{' + dc + '}title' else alternatives).append(value)
            elif child.tag == '{' + ndl + '}volume':
                from .ingestion.ndl import _value_text
                volumes.append({'value': _value_text(child), 'locator': locator})
        if len(catalogued) != 1 or not primary:
            return None
        dc_title = catalogued[0]
        expected_titles = [{'value': row['value'], 'locator': row['locator']} for row in [*primary, dc_title]]
        if sorted(parts['titles'], key=lambda r: r['locator']) != sorted(expected_titles, key=lambda r: r['locator']):
            return None
        if len(volumes) > 1 or (volumes[0] if volumes else None) != parts['volume']:
            return None
        pieces = dc_title['raw_literal'].split(' : ')
        if len(pieces) != 2 or not all(pieces) or any(' = ' in value for value in pieces):
            return None
        main, subtitle = pieces
        evidence = {'catalogued_title': dc_title, 'primary_titles': primary,
                    'Japanese_main': main, 'Japanese_subtitle': subtitle,
                    'locators': [p['locator'] for p in primary] + [dc_title['value_locator']]}
        if volumes:
            volume = volumes[0]
            if (alternatives or not re.fullmatch(r'[1-9][0-9]{0,3}', volume['value'])
                    or not all(p['raw_literal'] == dc_title['raw_literal'] + ' ' + volume['value'] for p in primary)):
                return None
            evidence.update(rule='own_structured_dc_title_and_exact_terminal_space_numeric_volume_v3',
                            volume=volume['value'], explicit_volume=volume)
            evidence['locators'].append(volume['locator'])
        else:
            if (len(alternatives) != 2 or alternatives[0]['raw_literal'] != alternatives[1]['raw_literal']
                    or ' = ' in alternatives[0]['raw_literal'] or ' : ' in alternatives[0]['raw_literal']):
                return None
            parallel = alternatives[0]['raw_literal']
            if not all(p['raw_literal'] == main + ' = ' + parallel + '. : ' + subtitle for p in primary):
                return None
            evidence.update(rule='own_structured_Japanese_title_and_two_identical_parallel_alternatives_v3',
                            volume=None, parallel_literal=parallel, parallel_alternatives=alternatives)
            evidence['locators'].extend(a['value_locator'] for a in alternatives)
        evidence['source_value'] = primary[0]['raw_literal']
        return evidence
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def _official_title_grammar_witness(assertion, member, grammar, artifact, cache):
    """Consume actual title literals from both pinned pages, including nonselected h1."""
    main, subtitle, volume = grammar['Japanese_main'], grammar['Japanese_subtitle'], grammar['volume']
    base_title = main + ' ～' + subtitle + '～'
    selected_title = main + (volume or '') + ' ～' + subtitle + '～'
    fields = []
    snapshots = assertion['primary_source_snapshots']
    for kind, route, heading_class in [('product', PRODUCT, 'productDetail__subTitle--novels'),
                                      ('series', SERIES, 'seriesDetail__subTitle--novels')]:
        snapshot = snapshots[kind]
        receipt_path = Path(snapshot['receipt_path'])
        tree, actual = _snapshot(receipt_path.parent, {'receipt_path': receipt_path.name,
            'receipt_sha256': snapshot['receipt_sha256']}, artifact, cache, route)
        if actual != snapshot:
            return None
        books = _members(tree, heading_class)
        if {_member_key(m) for m in books} != {_member_key(m) for m in assertion['explicit_book_memberships']}:
            return None
        selected = [m for m in books if m['isbn'] == member['isbn'] and m['source_url'] == member['source_url']]
        if len(selected) != 1 or _key(selected[0]['title']) != _key(selected_title):
            return None
        for book in books:
            # Every provided Japanese component agrees; any other printed number remains literal.
            title_key = _key(book['title'])
            pattern = re.escape(_key(main)) + r'(?:[1-9][0-9]{0,3})?' + re.escape(_key(' ～' + subtitle + '～'))
            if not re.fullmatch(pattern, title_key):
                return None
            fields.append({'source_url': actual['source_url'], 'raw_sha256': actual['raw_sha256'],
                'locator': book['locator'] + ' span.bookColumn__title', 'value': book['title'],
                'book_url': book['source_url'], 'isbn': book['isbn'], 'selected_member': book['isbn'] == member['isbn']})
        if kind == 'series':
            boundary = _one(tree.nodes(tag='section', cls='seriesDetail__main'), 'series main')
            title = _literal(_one(boundary.nodes(tag='h1', cls='seriesDetail__mainTitle'), 'canonical title'))
            locator = 'section.seriesDetail__main h1.seriesDetail__mainTitle'
            if _key(title) != _key(base_title) or _key(title) != _key(assertion['title']):
                return None
        else:
            boundary = _one(tree.nodes(tag='section', cls='lineupColumn__item'), 'product main')
            title = _literal(_one(boundary.nodes(tag='h1', cls='lineupColumn__title'), 'own product title'))
            locator = 'section.lineupColumn__item h1.lineupColumn__title'
            expected = selected_title if actual['source_url'] == member['source_url'] else base_title
            if _key(title) != _key(expected):
                return None
        fields.append({'source_url': actual['source_url'], 'raw_sha256': actual['raw_sha256'],
            'locator': locator, 'value': title, 'selected_member': kind == 'product' and actual['source_url'] == member['source_url']})
    return fields


def match_member_v3(assertion, parts, own, artifact, cache):
    """Fallback only: prove the observed own terminal-volume/parallel grammars."""
    legacy = match_member(assertion, parts)
    if legacy is not None:
        return legacy
    try:
        grammar = _own_title_grammar(parts, own)
        if grammar is None:
            return None
        # The common gates consume the unchanged actual NDL fields and explicit grammar.
        matched = _match_member(assertion, parts, title_grammar=grammar)
        if matched is None:
            return None
        fields = _official_title_grammar_witness(assertion, matched['official_member'], grammar, artifact, cache)
        if fields is None:
            return None
        return {**matched, 'rule_version': 'dre-dual-source-explicit-ndl-member-match-v3',
                'own_title_grammar': {**grammar, 'official_title_fields': fields,
                                     'source_raw_sha256': parts['source_raw_sha256']}}
    except (OSError, KeyError, TypeError, ValueError, AttributeError, IndexError):
        return None


def admission_conflict(assertion, records, anchor_id, memberships=()):
    """Read-only guard; canonical titles and anchors may not merge identities.

    ``records`` is the already SHA-authenticated entry-ID -> native record map.
    ``memberships`` is the caller's frozen edition/series binding inventory.
    """
    from .light_novel_admission_v3 import stored_title_closure, duplicate_title_key
    wanted = duplicate_title_key(assertion['title'])
    reference = assertion['reference_id']
    own_id = 'lns:ndl:' + anchor_id
    for identity, record in records.items():
        if not (identity.startswith(('lns:', 'lng:')) or record.get('kind') == 'publisher_group'):
            continue
        proof = record.get('publisher_series_proof', {})
        if proof.get('reference_id') == reference:
            continue
        names = stored_title_closure(record)
        if any(duplicate_title_key(name) == wanted for name in names):
            return 'potential_existing_series_title_or_alias_overlap_requires_review'
        if (identity == own_id or record.get('id') == anchor_id
                or record.get('anchor_edition_record', {}).get('id') == anchor_id):
            return 'existing_series_anchor_conflict'
    for binding in memberships:
        if binding['edition_id'] == 'lne:ndl:' + anchor_id and binding['series_id'] != own_id:
            return 'existing_edition_membership_anchor_conflict'
    return None
