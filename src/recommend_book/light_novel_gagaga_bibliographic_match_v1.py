"""Pure Gagaga lineup-to-own-NDL bibliographic correspondence.

The caller authenticates the fixed publisher frame with the existing Gagaga
source verifier and authenticates each own NDL projection. No source reads are
performed here. This proof does not identify an edition, product or story.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import re
import unicodedata

from .light_novel_bibliographic_match_v1 import SCOPE, _authors, _publisher
from .light_novel_catalogue_reference_match_v1 import _direct, _SERIAL
from .light_novel_explicit_series_match_v1 import _person_key
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_earthstar_match_v1 import _fallback_titles
from .normalization import normalize_isbn


ASSESSED_MANIFEST_SHA256 = 'c25ddc13389cd9a27417f9712d59a5145a0929f02a267a10ff9b678394dd7617'
_SHA = re.compile(r'[0-9a-f]{64}')
_NDL = re.compile(r'R100000002-[A-Za-z0-9._-]+')
_ITEM = re.compile(r'https://gagagabunko\.jp/lineup/(?:[0-9]{6}|hard|mook)\.html#[A-Za-z0-9_.-]+')
_RELEASE = re.compile(r'https://gagagabunko\.jp/release/index\.html#([A-Za-z0-9_.-]+)')
_PAGES = {'https://gagagabunko.jp/lineup/index.html',
    'https://gagagabunko.jp/lineup/indexOld.html',
    'https://gagagabunko.jp/lineup/index_cover.html',
    'https://gagagabunko.jp/release/index.html'}
_LABELS = {'ガガガ文庫','ガガガブックス','ガガガブックスf'}
_SOURCE_LABELS = {'ガガガブックス','ガガガブックスf'}


def reference_hold_reason(reference):
    """Validate the existing verifier's literal-role and item identity contract."""
    try:
        writers = [c for c in reference['credits'] if c['role']=='著' and _person_key(c['name'])]
        if not writers:
            return 'official_writer_role_unknown'
        if (any(c['role'] in {'author','文','作'} or not c['name'] for c in reference['credits'])
                or len({_person_key(c['name']) for c in writers}) != len(writers)):
            return 'official_writer_role_unknown'
        if (reference['source_unit'] != 'publisher_lineup_item_reference'
                or not isinstance(reference['title'],str) or not reference['title'].strip()
                or reference['publisher'] != '小学館' or reference['form'] is not None
                or reference['official_lightnovel_tag'] is not None
                or reference.get('volume') is not None
                or reference.get('story_series_asserted') is not False
                or reference.get('metadata_conflict') is not False or reference.get('bibliographic_variants') != []):
            return 'official_reference_scope_unproved'
        isbn = reference['isbn']
        if isbn is not None and (not isinstance(isbn,str) or not re.fullmatch(r'[0-9]{13}',isbn)
                or normalize_isbn(isbn) != isbn):
            return 'official_reference_scope_unproved'
        label = reference['label']
        if ((label is None and reference['label_literals'] != [])
                or (label is not None and (label not in _SOURCE_LABELS or reference['label_literals'] != [label]))):
            return 'official_reference_scope_unproved'
        evidence = reference['source_evidence']
        if (not isinstance(evidence,list) or not evidence or any(
                r['source_url'] not in _PAGES or not _SHA.fullmatch(r['raw_sha256'])
                or not isinstance(r['locator'],str) or not r['locator'] for r in evidence)):
            return 'official_reference_evidence_unproved'
        url = reference['source_url']
        release = _RELEASE.fullmatch(url)
        if _ITEM.fullmatch(url):
            expected = 'publisher-lineup:' + sha256(url.encode()).hexdigest()[:24]
            if (isbn is not None or label is not None or any(r['source_url'].endswith('/release/index.html') for r in evidence)
                    or not any(re.fullmatch(r'li\[[1-9][0-9]*\] a/p and explicit credit roles',r['locator']) for r in evidence)):
                return 'official_reference_scope_unproved'
            valid = reference['publisher_reference_id'] == expected
        elif release:
            valid = any(r['source_url']=='https://gagagabunko.jp/release/index.html'
                and r['locator']==f'section#{release[1]} div#title h3/span.textsize14/ISBN'
                and reference['publisher_reference_id']=='publisher-lineup-snapshot:'
                    + sha256((url+'\n'+r['raw_sha256']).encode()).hexdigest()[:24] for r in evidence)
        else:
            valid = False
        return None if valid else 'official_reference_scope_unproved'
    except (KeyError,TypeError,ValueError,AttributeError):
        return 'official_reference_scope_unproved'


def _imprint(reference, source):
    raw = source.get('series')
    if not isinstance(raw,str) or not raw:
        return None
    segments = [s.strip() for s in unicodedata.normalize('NFKC',raw).split(';')]
    forms = [s.strip() for s in segments[0].split(' = ')]
    brands = {_key(label):label for label in _LABELS}
    selected = {brands.get(_key(value)) for value in forms}
    if (None in selected or len(selected) != 1 or any(not _SERIAL.fullmatch(s) for s in segments[1:])):
        return None
    label = next(iter(selected))
    if reference['label'] is not None and reference['label'] != label:
        return None
    return {'rule':'unique_eligible_own_gagaga_imprint','source_value':raw,
        'parsed_own_primary_imprint':segments[0],'own_parallel_forms':forms,'matched_own_imprint':label,
        'source_individual_brand_asserted':reference['label'] is not None,
        'scope':'own_ndl_imprint_not_an_inferred_source_individual_label'}


def _match(reference, parts):
    if reference_hold_reason(reference) is not None:
        return None
    source = parts['source']
    ndc = source['extra']['ndc_codes']
    if (not _NDL.fullmatch(source['source_id'])
            or source['source_url'] != 'https://ndlsearch.ndl.go.jp/books/'+source['source_id']
            or source['provider_id'] not in {'iss-ndl-opac','iss-ndl-opac-national'}
            or source['record_kind'] != 'edition' or source.get('language') not in {'ja','jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?',code) for code in ndc)):
        return None
    publisher = _publisher(parts,'小学館')
    if publisher is None:
        return None
    own = _parent(publisher['publisher_agent']['locator'])
    if any(not _direct(own,'http://purl.org/dc/terms/','publisher',a['locator']) for a in parts['publisher_agents']):
        return None
    for agent in publisher['distribution_agents']:
        if not re.fullmatch(re.escape(agent['locator'])+r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[1\]'
                +r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]',agent['description_locator']):
            return None
    if not parts['titles'] or any(not row['value'] or not any(_direct(own,ns,'title',row['locator'])
            for ns in ('http://purl.org/dc/terms/','http://purl.org/dc/elements/1.1/')) for row in parts['titles']):
        return None
    if parts['volume'] and not _direct(own,'http://ndl.go.jp/dcndl/terms/','volume',parts['volume']['locator']):
        return None
    imprint = _imprint(reference,source)
    title = _titles(parts,reference['title'],own) or _fallback_titles(parts,reference['title'],own)
    if imprint is None or title is None:
        return None
    writers = [c for c in reference['credits'] if c['role']=='著']
    authors = _authors(parts,[c['name'] for c in writers],own)
    if not authors:
        return None
    return {'rule_version':'gagaga-lineup-ndl-bibliographic-correspondence-v1','scope':SCOPE,
        'provider':'gagaga','publisher_reference_id':reference['publisher_reference_id'],
        'official_source_url':reference['source_url'],'official_title':reference['title'],
        'official_writer_credits':deepcopy(writers),'official_isbn':reference['isbn'],
        'isbn_identity_asserted':False,'official_publisher':None,'official_publisher_asserted':False,
        'catalogue_owner_context':{'literal':reference['publisher'],'basis':'verifier_site_owner_context_not_individual_publisher_literal'},
        'official_label':reference['label'],'official_individual_imprint_asserted':reference['label'] is not None,
        'official_lightnovel_tag':None,'light_novel_status':'unknown',
        'light_novel_status_basis':'source_classification_context_unassessed',
        'source_id':source['source_id'],'source_url':source['source_url'],'source_raw_sha256':parts['source_raw_sha256'],
        'title_match':title,'authors':authors,'publisher_match':publisher,'imprint_match':imprint,
        'own_imprint':source.get('series'),'own_primary_resource_locator':own,
        'language':source['language'],'ndc_codes':deepcopy(ndc),
        'publisher_source_evidence':deepcopy(reference['source_evidence']),
        'same_edition_identity_asserted':False,'product_sku_identity_asserted':False,
        'original_work_identity_asserted':False,'story_series_identity_asserted':False,
        'global_author_alias_asserted':False,'global_author_identity_asserted':False}


def match_gagaga_reference(reference, primary_parts):
    """Return a bibliographic proof with unknown source classification retained."""
    try:
        return _match(reference,primary_parts)
    except (KeyError,TypeError,ValueError,AttributeError):
        return None
