"""Pure GC/Alpha catalogue correspondence with unassessed LN classification.

Source ISBN and individual publisher/imprint remain unknown. Matching a full
own NDL bibliography does not identify a product, edition, original or series.
The caller authenticates reference artifacts and the sole own NDL resource.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from urllib.parse import urlparse

from .light_novel_bibliographic_match_v1 import SCOPE, _authors, _publisher
from .light_novel_explicit_series_match_v1 import _person_key
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_earthstar_match_v1 import _fallback_titles


_SPECS = {
    'gc': ('gc-product:', 'gcnovels.jp', r'/book/([0-9]+)', 'マイクロマガジン社'),
    'alphapolis': ('alphapolis-commercial-book:', 'www.alphapolis.co.jp', r'/book/detail/([0-9]+/[0-9]+)', 'アルファポリス'),
}
_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_ALPHA_LABELS = {'アルファポリス', 'レジーナブックス'}
_SERIAL = re.compile(r'(?:[ぁ-ゖァ-ヺA-Za-z]{1,4}-)?[0-9]+(?:[-.][0-9]+)*')


def reference_hold_reason(provider, reference):
    """Reference-only gates; unknown writer roles never become authors."""
    try:
        if provider not in _SPECS:
            return 'unsupported_catalogue_reference_provider'
        writers = [c for c in reference['credits'] if c['role'] == 'author' and _person_key(c['name'])]
        if not writers:
            return 'official_writer_role_unknown'
        marker, roles = ('role_literal', {'小説','著','著者'}) if provider == 'gc' else ('role_display', {'著','著者'})
        if any(c.get(marker) not in roles for c in writers):
            return 'official_writer_role_unknown'
        prefix, host, route, _ = _SPECS[provider]
        url = urlparse(reference['source_url'])
        identity = re.fullmatch(route, url.path)
        if (reference['source_unit'] != 'publisher_product_reference' or url.scheme != 'https'
                or url.netloc != host or not identity or url.query or url.fragment
                or reference['publisher_reference_id'] != prefix + identity[1]
                or not reference['title'] or not isinstance(reference['title'],str)
                or any(k not in reference or reference[k] is not None
                    for k in ('isbn','publisher','form','volume','official_lightnovel_tag'))
                or reference.get('story_series_asserted') is not False
                or reference.get('metadata_conflict') is not False or reference.get('bibliographic_variants') != []
                or len({_person_key(c['name']) for c in writers}) != len(writers)):
            return 'official_reference_scope_unproved'
        if provider == 'gc':
            if reference['label'] is not None or reference['label_literals'] != []:
                return 'official_reference_scope_unproved'
        elif (reference['label'] not in _ALPHA_LABELS
                or set(reference['label_literals']) != {reference['label']}):
            return 'official_reference_scope_unproved'
        membership = reference.get('source_scope_membership')
        if provider == 'alphapolis' and membership is not None:
            brand = 'regina' if reference['label']=='レジーナブックス' else 'alphapolis'
            entry = 'https://www.alphapolis.co.jp/book/' + brand + ('/tankou' if brand=='regina' else '/tankou/100')
            if (membership['source_unit'] != 'publisher_commercial_book_index_membership'
                    or membership['brand'] != brand or membership['entry_url'] != entry
                    or membership['label_context_literal'] != reference['label']
                    or membership['individual_lightnovel_classification'] != 'unknown'):
                return 'official_reference_scope_unproved'
        evidence = reference['source_evidence']
        if (not isinstance(evidence,list) or not evidence or any(not _SHA.fullmatch(r['raw_sha256'])
                or not r['locator'] or urlparse(r['source_url']).scheme != 'https'
                or urlparse(r['source_url']).netloc != host for r in evidence)):
            return 'official_reference_evidence_unproved'
        return None
    except (KeyError,TypeError,ValueError,AttributeError):
        return 'official_reference_scope_unproved'


def _direct(own, namespace, field, locator):
    return re.fullmatch(re.escape(own + '/{' + namespace + '}' + field) + r'\[[1-9][0-9]*\]',locator)


def _imprint(provider, reference, source):
    raw = source.get('series')
    if raw is None and provider == 'alphapolis':
        return {'rule':'own_imprint_unavailable_not_inferred_from_card_brand', 'source_value':None,
            'source_individual_brand_asserted':False}
    if not isinstance(raw,str) or not raw:
        return None
    segments = [s.strip() for s in unicodedata.normalize('NFKC',raw).split(';')]
    forms = [s.strip() for s in segments[0].split(' = ')]
    # Additional own imprint assertions cannot hide behind parsed numbering.
    if any(not _SERIAL.fullmatch(s) for s in segments[1:]) or any(not s for s in forms):
        return None
    if provider == 'gc':
        brands = {_key('GCNOVELS'):'GCNOVELS', _key('GCN文庫'):'GCN文庫'}
        matched = {brands.get(_key(s)) for s in forms}
        if None in matched or len(matched) != 1:
            return None
        selected = next(iter(matched))
    else:
        if any(_key(s) != _key(reference['label']) for s in forms):
            return None
        selected = reference['label']
    return {'rule':'unique_eligible_own_imprint_in_catalogue_profile', 'source_value':raw,
        'parsed_own_primary_imprint':segments[0], 'own_parallel_forms':forms,
        'matched_own_imprint':selected, 'source_individual_brand_asserted':False,
        'scope':'own_ndl_imprint_only_not_a_source_individual_brand_assertion'}


def _match(provider, reference, parts):
    if reference_hold_reason(provider,reference) is not None:
        return None
    source = parts['source']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(source['source_id'])
            or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/' + source['source_id']
            or source['provider_id'] not in {'iss-ndl-opac','iss-ndl-opac-national'}
            or source['record_kind'] != 'edition' or source.get('language') not in {'ja','jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?',code) for code in ndc)):
        return None
    publisher = _publisher(parts,_SPECS[provider][3])
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if any(not _direct(own,'http://purl.org/dc/terms/','publisher',a['locator']) for a in parts['publisher_agents']):
        return None
    for agent in publisher['distribution_agents']:
        if not re.fullmatch(re.escape(agent['locator']) + r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[1\]'
                + r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]',agent['description_locator']):
            return None
    if not parts['titles'] or any(not row['value'] or not any(
            _direct(own,ns,'title',row['locator']) for ns in ('http://purl.org/dc/terms/','http://purl.org/dc/elements/1.1/'))
            for row in parts['titles']):
        return None
    if parts['volume'] and not _direct(own,'http://ndl.go.jp/dcndl/terms/','volume',parts['volume']['locator']):
        return None
    imprint = _imprint(provider,reference,source)
    if imprint is None:
        return None
    title = _titles(parts,reference['title'],own) or _fallback_titles(parts,reference['title'],own)
    if title is None:
        return None
    authors = _authors(parts,[c['name'] for c in reference['credits'] if c['role']=='author'],own)
    if not authors:
        return None
    proof = {'rule_version':'publisher-catalogue-ndl-bibliographic-correspondence-v1','scope':SCOPE,
        'provider':provider,'publisher_reference_id':reference['publisher_reference_id'],
        'official_source_url':reference['source_url'],'official_title':reference['title'],
        'official_isbn':None,'official_publisher':None,'official_publisher_asserted':False,
        'official_label':reference['label'],'official_brand_context_literal':reference['label'],
        'official_individual_imprint_asserted':False,'official_lightnovel_tag':None,
        'light_novel_status':'unknown','light_novel_status_basis':'source_classification_context_unassessed',
        'source_id':source['source_id'],'source_url':source['source_url'],'source_raw_sha256':parts['source_raw_sha256'],
        'title_match':title,'authors':authors,'publisher_match':publisher,'imprint_match':imprint,
        'own_imprint':source.get('series'),'own_primary_resource_locator':own,
        'language':source['language'],'ndc_codes':deepcopy(ndc),
        'publisher_source_evidence':deepcopy(reference['source_evidence']),
        'same_edition_identity_asserted':False,'product_sku_identity_asserted':False,
        'original_work_identity_asserted':False,'story_series_identity_asserted':False,
        'global_author_alias_asserted':False,'global_author_identity_asserted':False}
    if provider == 'gc' and 'source_frame_scope' in reference:
        allowed = ('index_pages_complete','pages_verified','publisher_reported_total','references_verified',
            'unacquired_references','unacquired_http_status')
        proof['source_frame_scope'] = {k:deepcopy(reference['source_frame_scope'][k])
            for k in allowed if k in reference['source_frame_scope']}
    if provider == 'gc':
        proof['catalogue_brand_context'] = {'labels':['GCノベルズ','GCN文庫'],
            'scope':'verified_common_search_frame_site_scope_not_individual_card_assignment',
            'individual_brand_assignment':'unknown','light_novel_classification':'unknown'}
    if provider == 'alphapolis' and 'source_scope_membership' in reference:
        allowed = ('source_unit','brand','entry_url','index_scope','membership_basis','label_context_literal',
            'individual_lightnovel_classification','full_historical_catalogue_complete')
        proof['source_scope_membership'] = {k:deepcopy(reference['source_scope_membership'][k])
            for k in allowed if k in reference['source_scope_membership']}
    return proof


def match_catalogue_reference(provider, reference, primary_parts):
    """Return only a bibliographic correspondence, holding classification unknown."""
    try:
        return _match(provider,reference,primary_parts)
    except (KeyError,TypeError,ValueError,AttributeError):
        return None
