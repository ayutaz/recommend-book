"""Pure correspondence of authenticated publisher cards and own NDL records.

This scope can establish a light-novel bibliographic record without identifying
a product, printing, ISBN edition, original work or story series.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from urllib.parse import urlparse

from .light_novel_explicit_series_match_v1 import (
    _AUTHORITY, _WRITER_LITERAL, _person_key, dated_own_author_bindings,
)
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_earthstar_match_v1 import _fallback_titles


SCOPE = 'publisher_reference_to_ndl_bibliographic_record'
_SPECS = {
    'kodansha': ('kodansha-product:', 'www.kodansha.co.jp', r'/comic/products/([0-9]{10})/?',
                 '講談社', {'講談社ラノベ文庫'}),
    'hifumi': ('hifumi-product:', 'hifumi.co.jp', r'/lineup/([A-Za-z0-9._-]+)/',
               '一二三書房', {'サーガフォレスト', 'ブレイブ文庫', 'メルティアブックス'}),
}
_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')


def reference_hold_reason(provider, reference):
    """Report only a reference gate; it does not authenticate source artifacts."""
    if provider in {'gc', 'alphapolis'}:
        from .light_novel_catalogue_reference_match_v1 import reference_hold_reason as catalogue_hold
        return catalogue_hold(provider, reference)
    try:
        writers = [c['name'] for c in reference['credits'] if c['role'] == 'author' and _person_key(c['name'])]
        if not writers:
            return 'official_writer_role_unknown'
        if reference.get('official_lightnovel_tag') is not True:
            return 'official_lightnovel_tag_unknown'
        if provider not in _SPECS:
            return 'unsupported_correspondence_provider'
        prefix, host, route, _, labels = _SPECS[provider]
        url = urlparse(reference['source_url'])
        identity = re.fullmatch(route, url.path)
        if (reference['source_unit'] != 'publisher_product_reference' or url.scheme != 'https'
                or url.netloc != host or not identity or url.query or url.fragment
                or reference['publisher_reference_id'] != prefix + identity[1]
                or not reference['title'] or reference['label'] not in labels
                or reference['label'] not in reference['label_literals']
                or reference.get('publisher') is not None
                or reference.get('story_series_asserted') is not False
                or reference.get('metadata_conflict') is not False
                or reference.get('bibliographic_variants') != []
                or len({_person_key(name) for name in writers}) != len(writers)):
            return 'official_reference_scope_unproved'
        evidence = reference['source_evidence']
        if (not isinstance(evidence, list) or not evidence or any(
                not _SHA.fullmatch(row['raw_sha256']) or not row['locator']
                or urlparse(row['source_url']).scheme != 'https'
                or urlparse(row['source_url']).netloc != host for row in evidence)):
            return 'official_reference_evidence_unproved'
        return None
    except (KeyError, TypeError, AttributeError, ValueError):
        return 'official_reference_scope_unproved'


def candidate_title_keys(source):
    """Overinclusive saved-title index; every result still needs own raw replay."""
    try:
        result = set()
        volume = source.get('volume') or ''
        for row in source['titles']:
            raw = row['value']
            if not volume:
                result.add(_key(raw))
            complete = raw[:-len('. ' + volume)] if volume and raw.endswith('. ' + volume) else raw
            fields = complete.split(' : ', 1)
            main, subtitle = fields[0], fields[1] if len(fields) == 2 else ''
            result.update(_key(value) for value in (main + volume + subtitle, main + subtitle + volume))
            if subtitle:
                result.update(_key(value) for value in (main + volume + '~' + subtitle + '~',
                    main + '~' + subtitle + '~' + volume))
        return result - {''}
    except (KeyError, TypeError, AttributeError):
        return set()


def _publisher(parts, expected):
    agents = parts['publisher_agents']
    primary = [a for a in agents if a['description_values'] == []]
    distributors = [a for a in agents if a['description_values'] in (['発売'], ['頒布'])]
    if (len(primary) != 1 or _key(primary[0]['name']) != _key(expected)
            or len(primary) + len(distributors) != len(agents)
            or any(not a['name'] or not a['locator'] for a in agents)
            or len({_parent(a['locator']) for a in agents}) != 1
            or parts['source']['publisher'] != '; '.join(dict.fromkeys(a['name'] for a in agents))):
        return None
    if distributors and (any(not a['structured_agent'] for a in agents)
            or any(len(a['description_locators']) != 1
                or not a['description_locators'][0].startswith(a['locator'] + '/') for a in distributors)):
        return None
    return {'rule': 'sole_own_publisher_with_explicit_sales_or_distribution_only',
            'publisher_agent': {'name': primary[0]['name'], 'locator': primary[0]['locator']},
            'distribution_agents': [{'name': a['name'], 'role': a['description_values'][0],
                'locator': a['locator'], 'description_locator': a['description_locators'][0]} for a in distributors]}


def _authors(parts, names, own):
    output = []
    for name in names:
        contributors = [(index, c) for index, c in enumerate(parts['source']['contributors'])
                        if c['role'] == 'author' and _person_key(c['name']) == _person_key(name)]
        known = [{'name': c['name'], 'authority_ids': sorted({i['value'] for i in c['identifiers']
            if i['source'] == 'ndl-authority' and _AUTHORITY.fullmatch(i['value'])}),
            'projection_pointer': f'/source/contributors/{index}'} for index, c in contributors]
        known = [c for c in known if c['authority_ids']]
        if known:
            output.append({'official_writer': name, 'authority_status': 'known',
                'authority_ids': sorted({uri for c in known for uri in c['authority_ids']}),
                'binding_rule': 'same_name_own_author_with_known_authority', 'contributors': known,
                'own_primary_resource_locator': own})
            continue
        bound = dated_own_author_bindings(parts, name)
        if bound:
            output.append({'official_writer': name, 'authority_status': 'known',
                'authority_ids': sorted({uri for c in bound for uri in c['authority_ids']}),
                'binding_rule': 'dated_own_heading_and_explicit_writer_literal',
                'contributors': bound, 'own_primary_resource_locator': own})
            continue
        literal = None
        for row in parts.get('literal_authors', []):
            marked = _WRITER_LITERAL.fullmatch(row['literal'])
            direct = any(re.fullmatch(re.escape(own + '/{' + namespace + '}creator') + r'\[[1-9][0-9]*\]', row['locator'])
                         for namespace in ('http://purl.org/dc/elements/1.1/', 'http://purl.org/dc/terms/'))
            if (direct and marked and _person_key(marked['name']) == _person_key(name)
                    and _person_key(row['name']) == _person_key(name)):
                literal = row
                break
        if not contributors or literal is None:
            return None
        output.append({'official_writer': name, 'authority_status': 'unknown', 'authority_ids': [],
            'binding_rule': 'same_own_explicit_writer_literal_and_contributor',
            'literal': literal['literal'], 'locator': literal['locator'],
            'projection_pointers': [f'/source/contributors/{index}' for index, _ in contributors],
            'own_primary_resource_locator': own})
    return output


def _match(provider, reference, parts):
    if reference_hold_reason(provider, reference) is not None:
        return None
    source = parts['source']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(source['source_id'])
            or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + source['source_id']
            or source['provider_id'] not in {'iss-ndl-opac', 'iss-ndl-opac-national'}
            or source['record_kind'] != 'edition' or source.get('language') not in {'ja', 'jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?', code) for code in ndc)):
        return None
    publisher = _publisher(parts, _SPECS[provider][3])
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if not parts['titles'] or any(not row['value'] or _parent(row['locator']) != own for row in parts['titles']):
        return None
    imprint = source.get('series')
    forms = unicodedata.normalize('NFKC', imprint or '').split(';', 1)[0].strip().split(' = ')
    if not imprint or _key(reference['label']) not in {_key(value.strip()) for value in forms}:
        return None
    title = _titles(parts, reference['title'], own) or _fallback_titles(parts, reference['title'], own)
    if title is None:
        return None
    authors = _authors(parts, [c['name'] for c in reference['credits'] if c['role'] == 'author'], own)
    if not authors:
        return None
    return {'rule_version': 'publisher-ndl-bibliographic-correspondence-v1', 'scope': SCOPE,
        'provider': provider, 'publisher_reference_id': reference['publisher_reference_id'],
        'official_source_url': reference['source_url'], 'official_title': reference['title'],
        'official_label': reference['label'], 'official_lightnovel_tag': True,
        'official_publisher_asserted': False, 'source_id': source['source_id'],
        'source_url': source['source_url'], 'source_raw_sha256': parts['source_raw_sha256'],
        'title_match': title, 'authors': authors, 'publisher_match': publisher,
        'own_imprint': imprint, 'own_primary_resource_locator': own,
        'language': source['language'], 'ndc_codes': deepcopy(ndc),
        'publisher_source_evidence': deepcopy(reference['source_evidence']),
        'same_edition_identity_asserted': False, 'product_sku_identity_asserted': False,
        'original_work_identity_asserted': False, 'story_series_identity_asserted': False,
        'global_author_alias_asserted': False, 'global_author_identity_asserted': False}


def match_bibliographic_reference(provider, reference, primary_parts):
    """Return a bounded bibliographic proof, or hold incomplete/conflicting facts."""
    if provider in {'gc', 'alphapolis'}:
        from .light_novel_catalogue_reference_match_v1 import match_catalogue_reference
        return match_catalogue_reference(provider, reference, primary_parts)
    try:
        return _match(provider, reference, primary_parts)
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
