"""Pure, work-specific Wikipedia novel-credit concordance.

The caller authenticates the Wikidata metadata/entity and Wikipedia response
bytes before calling this module. No person sameAs, P50 repair, original-work
identity, prose classification, or source-file authentication is performed here.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from urllib.parse import quote

import mwparserfromhell

from .light_novel_bibliographic_match_v1 import _publisher
from .light_novel_earthstar_match_v1 import _fallback_titles
from .light_novel_reference_match_v1 import _key, _parent, _titles


_CREDIT_VERSION = 'wikipedia-work-specific-novel-credit-v1'
_MATCH_VERSION = 'ndl-edition-wikipedia-novel-credit-membership-v1'
_DC = 'http://purl.org/dc/elements/1.1/'
_DCT = 'http://purl.org/dc/terms/'
_DCNDL = 'http://ndl.go.jp/dcndl/terms/'
_AUTHORITY = re.compile(r'https?://id\.ndl\.go\.jp/auth/(?:entity|ndlna)/([0-9]{6,12})')
_IMPORT_NOTICE = re.compile(
    r'cc[ _-]?notice|source[ _-]?attribution|attribution|copyright|copyvio|copypaste|'
    r'copy[ _-]?paste|1911|eb1911|dnb|public[ _-]?domain|pd[ _-]?notice|'
    r'imported[ _-]?text|incorporates[ _-]?text|text[ _-]?incorporated', re.I)
_CONTENT = 'query.pages[0].revisions[0].slots.main.content'
_CREDIT_FIELDS = ('rule_version', 'title', 'series_entry_id', 'author_names', 'publisher', 'imprint',
    'imprint_wikidata', 'revision_id', 'page_id', 'source_url', 'lead_credit_literal', 'field_locators',
    'credit_scope', 'author_identity_asserted', 'P50_backfilled', 'original_work_identity_asserted')
_LOCATOR_FIELDS = ('title', 'author_names', 'publisher', 'imprint', 'lead_credit', 'series_qid')


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _plain(value):
    """Read explicit display text; do not silently discard generated fields."""
    code = mwparserfromhell.parse(str(value))
    for node in code.nodes:
        if isinstance(node, mwparserfromhell.nodes.Text):
            continue
        if isinstance(node, mwparserfromhell.nodes.Wikilink):
            visible = node.text if node.text is not None else node.title
            _require(all(isinstance(n, mwparserfromhell.nodes.Text) for n in visible.nodes),
                     'Generated or nested bibliographic display text')
            continue
        raise ValueError('Unsupported bibliographic field markup')
    result = code.strip_code().strip()
    _require(bool(result), 'Empty bibliographic field')
    return result


def _template_fields(template):
    result = {}
    for param in template.params:
        key = str(param.name).strip()
        _require(key not in result, 'Duplicate infobox field')
        result[key] = param.value
    return result


def _verify(metadata, entity, article_payload, expected_revision_id):
    qid = metadata['id']
    _require(isinstance(qid, str) and re.fullmatch(r'Q[1-9][0-9]*', qid)
             and entity['id'] == qid and metadata['kind'] == 'series', 'Exact series identity required')
    _require(not metadata['authors'] and not any(row.get('rank') != 'deprecated'
             for row in entity.get('claims', {}).get('P50', [])), 'Existing author claims require the P349 path')
    pages = article_payload['query']['pages']
    _require(isinstance(pages, list) and len(pages) == 1, 'Exact single article page required')
    page = pages[0]
    _require(page['title'] == entity['sitelinks']['jawiki']['title']
             and page['pageprops']['wikibase_item'] == qid, 'Sitelink/pageprops conflict')
    _require(type(page['ns']) is int and page['ns'] == 0 and 'missing' not in page
             and 'disambiguation' not in page['pageprops']
             and type(page['pageid']) is int and page['pageid'] > 0, 'Ambiguous or missing main-space article')
    revisions = page['revisions']
    _require(type(expected_revision_id) is int and expected_revision_id > 0
             and isinstance(revisions, list) and len(revisions) == 1
             and type(revisions[0]['revid']) is int and revisions[0]['revid'] == expected_revision_id,
             'Exact single pinned revision required')
    slot = revisions[0]['slots']['main']
    _require(slot['contentmodel'] == 'wikitext' and slot.get('contentformat', 'text/x-wiki') == 'text/x-wiki',
             'Unsupported article content model')
    text = slot['content']
    _require(isinstance(text, str) and text.strip(), 'Missing article text')
    parsed = mwparserfromhell.parse(text)
    templates = parsed.filter_templates(recursive=True)
    _require(not any(_IMPORT_NOTICE.search(str(t.name)) for t in templates), 'Import/attribution notice present')

    def template_name(value):
        return ' '.join(str(value.name).strip().replace('_', ' ').casefold().split())

    selected = []
    for suffix in ('Header', 'Novel', 'Footer'):
        name = 'infobox animanga/' + suffix.casefold()
        rows = [t for t in templates if template_name(t) == name]
        _require(len(rows) == 1, 'Unique Header/Novel/Footer structure required')
        positions = [i for i, n in enumerate(parsed.nodes) if n is rows[0]]
        _require(len(positions) == 1, 'Nested novel/header infobox is not evidence')
        selected.append((rows[0], positions[0]))
    (header, header_position), (novel, novel_position), (_, footer_position) = selected
    _require(header_position < novel_position < footer_position, 'Novel is outside its Header/Footer scope')
    header_fields, novel_fields = _template_fields(header), _template_fields(novel)
    title = _plain(header_fields['タイトル'])
    _require(_key(title) == _key(metadata['title']) == _key(page['title']), 'Complete work title conflict')
    _require(not {'作者', '作者表記', '原作', '原作者', '作画', '脚本'} & novel_fields.keys(),
             'Adaptation/original credit is not a Novel author field')
    if 'タイトル' in novel_fields:
        _require(_plain(novel_fields['タイトル']) == title, 'Novel title differs from Header')
    author = _plain(novel_fields['著者'])
    # This initial contract deliberately supports a single explicit writer.
    # Delimiter-based name splitting would invent an author set.
    _require(not re.search(r'[、,・/;\n]|(?:<br)|(?:原作|作画|著者)', author), 'Ambiguous Novel author field')
    publisher, imprint = (_plain(novel_fields[key]) for key in ('出版社', 'レーベル'))
    publishers = [row for row in metadata['publishers'] if _key(row['label']) == _key(imprint)]
    _require(len(publishers) == 1 and re.fullmatch(r'Q[1-9][0-9]*', publishers[0]['id']),
             'Novel imprint must agree with exactly one existing Wikidata publisher')

    tail = ''.join(str(n) for n in parsed.nodes[footer_position + 1:]).strip()
    first = re.split(r'\n\s*\n', tail, maxsplit=1)[0].split('。', 1)[0]
    lead_code = mwparserfromhell.parse(first)
    _require(not lead_code.filter_templates(recursive=True) and not lead_code.filter_headings(),
             'Generated or absent lead credit')
    lead = lead_code.strip_code().strip()
    pattern = (r'^『' + re.escape(title) + r'』(?:（[^（）\n]*）)?は、' + re.escape(author)
               + r'による(?:日本の)?(?:ライトノベル|小説)')
    found = re.match(pattern, lead)
    _require(found is not None, 'Lead complete title/Novel author credit does not agree')
    source_url = f'https://ja.wikipedia.org/w/index.php?oldid={expected_revision_id}'
    locators = {'title': _CONTENT + '/Infobox animanga/Header[1]/タイトル',
        'author_names': _CONTENT + '/Infobox animanga/Novel[1]/著者',
        'publisher': _CONTENT + '/Infobox animanga/Novel[1]/出版社',
        'imprint': _CONTENT + '/Infobox animanga/Novel[1]/レーベル',
        'lead_credit': _CONTENT + '/lead/paragraph[1]/sentence[1]',
        'series_qid': 'query.pages[0].pageprops.wikibase_item'}
    return {'rule_version': _CREDIT_VERSION, 'title': title, 'series_entry_id': 'lns:wd:' + qid,
        'author_names': [author], 'publisher': publisher, 'imprint': imprint,
        'imprint_wikidata': {'qid': publishers[0]['id'], 'label': publishers[0]['label']},
        'revision_id': expected_revision_id, 'page_id': page['pageid'], 'source_url': source_url,
        'lead_credit_literal': found.group(), 'field_locators': locators,
        'credit_scope': 'work_specific_novel_bibliographic_author_credit',
        'author_identity_asserted': False, 'P50_backfilled': False, 'original_work_identity_asserted': False,
        'attribution': {'credit': 'Wikipedia contributors: ' + page['title'],
            'source_url': source_url, 'history_url': 'https://ja.wikipedia.org/w/index.php?title='
                + quote(page['title'], safe='') + '&action=history',
            'license': 'CC-BY-SA-4.0', 'license_url': 'https://creativecommons.org/licenses/by-sa/4.0/',
            'changes': 'Minimal explicit bibliographic fields extracted; no prose classification reused'}}


def verify_novel_credit(metadata, entity, article_payload, expected_revision_id):
    """Verify authenticated response content; malformed/ambiguous evidence raises ValueError."""
    try:
        return _verify(metadata, entity, article_payload, expected_revision_id)
    except (KeyError, TypeError, AttributeError, IndexError) as error:
        raise ValueError('Incomplete Wikipedia novel-credit contract') from error


def _direct(locator, own, namespace, field):
    return isinstance(locator, str) and re.fullmatch(re.escape(own + '/{' + namespace + '}' + field)
                                                  + r'\[[1-9][0-9]*\]', locator) is not None


def _author_concordance(parts, names, own):
    writers = [(i, row) for i, row in enumerate(parts['source']['contributors']) if row['role'] == 'author']
    literals = parts['literal_authors']
    if ({row['name'] for _, row in writers} != set(names)
            or {row['name'] for row in literals} != set(names)):
        return None
    for row in literals:
        marked = re.fullmatch(r'(.+?)\s*(著者|著|作)', row['literal'])
        if (not marked or marked[1].strip() != row['name'] or not any(
                _direct(row['locator'], own, ns, 'creator') for ns in (_DC, _DCT))):
            return None
    output = []
    for name in names:
        matches, ids = [], set()
        for index, row in writers:
            if row['name'] != name:
                continue
            for identifier in row['identifiers']:
                if identifier['source'] != 'ndl-authority':
                    continue
                found = _AUTHORITY.fullmatch(identifier['value'])
                if not found:
                    return None
                ids.add(found[1])
                matches.append({'authority_uri': identifier['value'], 'projection_pointer': f'/source/contributors/{index}'})
        if len(ids) != 1:
            return None
        output.append({'name_literal': name, 'authority_uri': matches[0]['authority_uri'],
            'ndl_authority_id': next(iter(ids)), 'contributors': matches,
            'literal_writer': deepcopy(next(row for row in literals if row['name'] == name)),
            'own_primary_resource_locator': own, 'author_identity_asserted': False})
    return output


def _match(credit, parts, normalize_search):
    if (credit['rule_version'] != _CREDIT_VERSION or credit['author_identity_asserted'] is not False
            or credit['P50_backfilled'] is not False or credit['original_work_identity_asserted'] is not False
            or not re.fullmatch(r'lns:wd:Q[1-9][0-9]*', credit['series_entry_id'])
            or not isinstance(credit['author_names'], list) or len(credit['author_names']) != 1
            or not isinstance(credit['author_names'][0], str) or not credit['author_names'][0]
            or type(credit['revision_id']) is not int or credit['revision_id'] <= 0
            or credit['source_url'] != f"https://ja.wikipedia.org/w/index.php?oldid={credit['revision_id']}"):
        return None
    source = parts['source']
    identity = source['source_id']
    ndc = source['extra']['ndc_codes']
    if (not re.fullmatch(r'R100000002-[A-Za-z0-9._-]+', identity)
            or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + identity
            or source['provider_id'] not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
            or source['record_kind'] != 'edition' or source['language'] not in {'jpn', 'ja'}
            or not re.fullmatch(r'[0-9a-f]{64}', parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', code) for code in ndc)):
        return None
    publisher = _publisher(parts, credit['publisher'])
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if any(not _direct(row['locator'], own, _DCT, 'publisher') for row in parts['publisher_agents']):
        return None
    if any(not re.fullmatch(re.escape(row['locator'])
            + r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[[1-9][0-9]*\]'
            + r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]',
            row['description_locator']) for row in publisher['distribution_agents']):
        return None
    volume = parts['volume']
    if (not volume or not _direct(volume['locator'], own, _DCNDL, 'volume')
            or not re.fullmatch(r'[0-9]+', unicodedata.normalize('NFKC', volume['value']))
            or not 1 <= int(unicodedata.normalize('NFKC', volume['value'])) <= 10000):
        return None
    if not parts['titles'] or any(not row['value'] or not any(_direct(row['locator'], own, ns, 'title')
            for ns in (_DC, _DCT)) for row in parts['titles']):
        return None
    target = credit['title'] + volume['value']
    title = _titles(parts, target, own) or _fallback_titles(parts, target, own)
    if title is None:
        return None
    # A dc main-title field cannot bypass a different complete dcterms title.
    allowed_dc = {normalize_search(title['main']), normalize_search(title['main'] + (title['subtitle'] or '')),
                  normalize_search(title['main'] + (' : ' + title['subtitle'] if title['subtitle'] else ''))}
    if any(normalize_search(row['value']) not in allowed_dc for row in parts['titles']
           if _direct(row['locator'], own, _DC, 'title')):
        return None
    if (not source['series'] or ';' in source['series']
            or normalize_search(source['series']) != normalize_search(credit['imprint'])):
        return None
    authors = _author_concordance(parts, credit['author_names'], own)
    if not authors:
        return None
    # Keep provenance paths and arbitrary caller annotations outside public proof.
    public_credit = {key: deepcopy(credit[key]) for key in _CREDIT_FIELDS}
    public_credit['imprint_wikidata'] = {key: credit['imprint_wikidata'][key] for key in ('qid', 'label')}
    public_credit['field_locators'] = {key: credit['field_locators'][key] for key in _LOCATOR_FIELDS}
    public_credit['attribution'] = {key: credit['attribution'][key] for key in
        ('credit', 'source_url', 'history_url', 'license', 'license_url', 'changes')}
    return {'rule_version': _MATCH_VERSION, 'scope': 'edition_membership_only',
        'edition_id': 'lne:ndl:' + identity, 'series_entry_id': credit['series_entry_id'],
        'matched_title': title['source_value'], 'title_match': title, 'volume': volume['value'],
        'volume_number': int(unicodedata.normalize('NFKC', volume['value'])), 'ndc_codes': deepcopy(ndc),
        'publisher': publisher['publisher_agent']['name'], 'publisher_match': publisher,
        'imprint': deepcopy(public_credit['imprint_wikidata']), 'own_imprint': source['series'],
        'author_credit_concordance': authors,
        'matched_authority_ids': sorted({row['ndl_authority_id'] for row in authors}),
        'novel_credit': public_credit, 'author_identity_asserted': False, 'P50_backfilled': False,
        'original_work_identity_asserted': False, 'product_sku_identity_asserted': False,
        'sources': [credit['source_url'], source['source_url']],
        'field_locators': {'wikipedia': deepcopy(public_credit['field_locators']), 'title': title['locators'],
            'volume': volume['locator'], 'own_primary_resource': own,
            'imprint': {'own_primary_resource_locator': own, 'projection_pointer': '/source/series'},
            'language': {'own_primary_resource_locator': own, 'projection_pointer': '/source/language'},
            'ndc': {'own_primary_resource_locator': own, 'projection_pointer': '/source/extra/ndc_codes'}}}


def match_novel_credit(credit, parts, normalize_search):
    """Return a bounded own-edition membership proof, or None for a hold."""
    try:
        return _match(credit, parts, normalize_search)
    except (KeyError, TypeError, AttributeError, ValueError, IndexError):
        return None
