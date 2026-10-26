"""Pure correspondence of authenticated Overlap details to own NDL records.

Unmarked source creators stay role-unknown. Their names may correspond to an
explicit own NDL writer without becoming a source author-role assertion. The
caller authenticates the card/detail sources and ALL same-ISBN own records.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata
from urllib.parse import parse_qs, urlparse, urlsplit

from .light_novel_bibliographic_match_v1 import SCOPE, _authors, _publisher
from .light_novel_catalogue_reference_match_v1 import _direct, _SERIAL
from .light_novel_explicit_series_match_v1 import _person_key
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_earthstar_match_v1 import _fallback_titles
from .light_novel_overlap_references_v1 import _SCOPE
from .normalization import normalize_isbn


_SHA=re.compile(r'[0-9a-f]{64}')
_NDL=re.compile(r'R100000002-[A-Za-z0-9._-]+')
_BRANDS={'nvl':'オーバーラップノベルス','nvf':'オーバーラップノベルスf','nvr':'ロサージュノベルス'}
_LAST_CARD_PAGE={'nvl':15,'nvf':7,'nvr':1}
_UNKNOWN_BASIS='unmarked_creator_name_retained_unknown_not_inferred_from_position_or_css'
_CARD_SCOPE='own_card_label_only_detail_own_label_not_observed'
_TITLE_LOCATOR='.goods-explanation__body h1.goods-explanation__title'
_CREATOR_LOCATOR='.goods-explanation__body p.goods-explanation__author'


def _brand(reference):
    identity=re.fullmatch(r'overlap-(nvl|nvf|nvr)-product:([A-Za-z0-9._-]+)',reference['publisher_product_reference_id'])
    url=urlparse(reference['source_url'])
    path=re.fullmatch(r'/[^/]+/product/0/([A-Za-z0-9._-]+)/',url.path)
    query=parse_qs(url.query,keep_blank_values=True)
    if (not identity or not path or identity[2]!=path[1] or url.scheme!='https'
            or url.netloc!='over-lap.co.jp' or url.fragment
            or query.get('cat')!=[identity[1].upper()] or set(query)-{'cat','swrd'}
            or ('swrd' in query and query['swrd']!=[''])):
        return None
    return identity[1]


def _card_page_url(url, brand):
    # Keep the exact first-page route; subsequent pages have the fixed filter
    # used by the independently authenticated finite source frame.
    if url=='https://over-lap.co.jp/Form/Product/ProductList.aspx?cat='+brand.upper():
        return True
    parsed=urlsplit(url)
    query=parse_qs(parsed.query,keep_blank_values=True)
    number=query.pop('pno',[])
    return (parsed.scheme=='https' and parsed.netloc=='over-lap.co.jp' and not parsed.fragment
        and re.fullmatch(r'/[^/]+/category/0/'+brand.upper()+'/',parsed.path) is not None
        and query==_SCOPE and len(number)==1 and re.fullmatch(r'[1-9][0-9]*',number[0]) is not None
        and 2<=int(number[0])<=_LAST_CARD_PAGE[brand])


def reference_hold_reason(reference):
    """Check explicit ISBN, own-card scope and unchanged source role semantics."""
    try:
        brand=_brand(reference)
        isbn=reference['isbn']
        if (brand is None or reference['source_unit']!='publisher_product_detail_bibliographic_reference_not_confirmed_edition'
                or reference.get('publisher_reference_id',reference['publisher_product_reference_id'])!=reference['publisher_product_reference_id']
                or not isinstance(isbn,str) or not re.fullmatch(r'[0-9]{13}',isbn) or normalize_isbn(isbn)!=isbn
                or reference['isbn_source_basis']!='explicit_ISBN_label_value_not_URL'
                or normalize_isbn(reference['isbn_source_literal'])!=isbn
                or not isinstance(reference['title'],str) or not reference['title'].strip()
                or _key(reference['title'])!=_key(reference['owncard_title_literal'])
                or reference['label']!=_BRANDS[brand] or reference['label_source_scope']!=_CARD_SCOPE
                or reference['detail_label'] is not None or reference['publisher'] is not None
                or reference.get('official_lightnovel_tag') is not None
                or reference.get('light_novel_status','unknown')!='unknown'
                or any(reference.get(k) is not False for k in ('edition_identity_asserted',
                    'original_work_identity_asserted','story_series_identity_asserted'))
                or reference.get('metadata_conflict',False) is not False or reference.get('bibliographic_variants',[])!=[]):
            return 'official_detail_scope_unproved'
        unknown=[c for c in reference['credits'] if c['role']=='unknown']
        if (not unknown or len({_person_key(c['name']) for c in unknown})!=len(unknown)
                or any(not _person_key(c['name']) or c['role_literal'] is not None
                    or c['role_basis']!=_UNKNOWN_BASIS for c in unknown)):
            return 'unmarked_creator_evidence_unproved'
        for credit in reference['credits']:
            if credit['role']=='unknown':
                continue
            if (credit['role']!='illustrator' or not credit['name'] or credit['role_literal']!='イラスト'
                    or credit['role_marker_literal']!='イラスト／'
                    or credit['role_basis']!='explicit_role_marker_in_own_creator_field'):
                return 'official_creator_role_scope_unproved'
        evidence=reference['source_evidence']
        if (not isinstance(evidence,list) or not evidence or len({r['raw_sha256'] for r in evidence})!=1
                or any(r['source_url']!=reference['source_url'] or not _SHA.fullmatch(r['raw_sha256'])
                    or not r['locator'] for r in evidence)
                or not {_TITLE_LOCATOR,_CREATOR_LOCATOR}<={r['locator'] for r in evidence}):
            return 'official_detail_evidence_unproved'
        isbn_fields=[r for r in reference['bibliographic_field_literals'] if r['field_key']=='ISBN']
        if (not isbn_fields or any(r['label_literal']!='ISBN' or normalize_isbn(r['value_literal'])!=isbn
                or r['locator'] not in {e['locator'] for e in evidence} for r in isbn_fields)):
            return 'explicit_isbn_field_unproved'
        card=reference['owncard_source_evidence']
        if (not _card_page_url(card['source_url'],brand)
                or not _SHA.fullmatch(card['raw_sha256']) or not card['selector']):
            return 'official_card_brand_evidence_unproved'
        return None
    except (KeyError,TypeError,ValueError,AttributeError):
        return 'official_detail_scope_unproved'


def _imprint(source, label):
    raw=source.get('series')
    if not isinstance(raw,str) or not raw:
        return None
    segments=[s.strip() for s in unicodedata.normalize('NFKC',raw).split(';')]
    forms=[s.strip() for s in segments[0].split(' = ')]
    if (any(_key(form)!=_key(label) for form in forms)
            or any(not _SERIAL.fullmatch(s) for s in segments[1:])):
        return None
    return {'rule':'unique_own_imprint_consistent_with_explicit_card_brand',
        'source_value':raw,'matched_own_imprint':label,'own_parallel_forms':forms,
        'official_brand_scope':_CARD_SCOPE,'detail_individual_imprint_asserted':False}


def _paired_imprint(reference, parts, own):
    # This observed pair is usable only inside the caller-authenticated,
    # same-ISBN own record after ALL other bibliography gates also succeed.
    # It is not a replacement or normalization of either source literal.
    source=parts['source']
    if (_brand(reference)!='nvl' or reference['label']!='オーバーラップノベルス'
            or source.get('series')!='OVERLAP NOVELS'):
        return None
    return {'rule':'observed_card_brand_own_imprint_pair_within_same_isbn_record',
        'scope':'same_isbn_authenticated_own_record_only','source_value':source['series'],
        'matched_own_imprint':source['series'],'official_card_brand':reference['label'],
        'official_brand_scope':_CARD_SCOPE,'detail_individual_imprint_asserted':False,
        'isbn13':reference['isbn'],'source_id':source['source_id'],'source_url':source['source_url'],
        'source_raw_sha256':parts['source_raw_sha256'],'own_primary_resource_locator':own,
        'own_imprint_projection_pointer':'/source/series','global_imprint_alias_asserted':False}


def _candidate(reference, parts):
    source=parts['source']
    ndc=sorted(set(source['extra']['ndc_codes']))
    if (not _NDL.fullmatch(source['source_id'])
            or source['source_url']!='https://ndlsearch.ndl.go.jp/books/'+source['source_id']
            or source['provider_id']!='iss-ndl-opac-national'
            or source['record_kind']!='edition' or source.get('language') not in {'ja','jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?',code) for code in ndc)
            or reference['isbn'] not in {normalize_isbn(r['value']) for r in source['identifiers'] if r['source']=='isbn'}):
        return None
    publisher=_publisher(parts,'オーバーラップ')
    if publisher is None:
        return None
    own=_parent(publisher['publisher_agent']['locator'])
    if any(not _direct(own,'http://purl.org/dc/terms/','publisher',a['locator']) for a in parts['publisher_agents']):
        return None
    for agent in publisher['distribution_agents']:
        if not re.fullmatch(re.escape(agent['locator'])+r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[1\]'
                +r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]',agent['description_locator']):
            return None
    if (not parts['titles'] or any(not r['value'] or not any(_direct(own,ns,'title',r['locator'])
            for ns in ('http://purl.org/dc/terms/','http://purl.org/dc/elements/1.1/')) for r in parts['titles'])
            or (parts['volume'] and not _direct(own,'http://ndl.go.jp/dcndl/terms/','volume',parts['volume']['locator']))):
        return None
    imprint=_imprint(source,reference['label']) or _paired_imprint(reference,parts,own)
    title=_titles(parts,reference['title'],own) or _fallback_titles(parts,reference['title'],own)
    if imprint is None or title is None:
        return None
    bindings=_authors(parts,[c['name'] for c in reference['credits'] if c['role']=='unknown'],own)
    if not bindings:
        return None
    for binding in bindings:
        binding['source_unmarked_creator']=binding.pop('official_writer')
        binding['source_writer_role_asserted']=False
    statements=parts.get('edition_statements',[])
    if not isinstance(statements,list) or any(not r['value'] or not _direct(
            own,'http://ndl.go.jp/dcndl/terms/','edition',r['locator']) for r in statements):
        return None
    signature=(_key(title['volume'] or ''),tuple(ndc),
        tuple(sorted(_key(r['value']) for r in statements)),
        tuple(sorted((_key(a['name']),a['role']) for a in publisher['distribution_agents'])))
    return {'source_id':source['source_id'],'source_url':source['source_url'],
        'source_raw_sha256':parts['source_raw_sha256'],'title_match':title,'publisher_match':publisher,
        'imprint_match':imprint,'own_imprint':source['series'],'language':source['language'],'ndc_codes':ndc,
        'own_primary_resource_locator':own,'edition_statements':deepcopy(statements),
        'unmarked_creator_own_writer_matches':bindings,'_signature':signature}


def _match(reference, parts, same_isbn_parts):
    if reference_hold_reason(reference) is not None:
        return None
    if not isinstance(same_isbn_parts,list) or not same_isbn_parts or parts not in same_isbn_parts:
        return None
    candidates=[_candidate(reference,p) for p in same_isbn_parts]
    if any(c is None for c in candidates):
        return None
    primary=candidates[same_isbn_parts.index(parts)]
    if (len({c['source_id'] for c in candidates})!=len(candidates)
            or any(c['_signature']!=primary['_signature'] for c in candidates)):
        return None
    for index in range(len(primary['unmarked_creator_own_writer_matches'])):
        known={tuple(c['unmarked_creator_own_writer_matches'][index]['authority_ids']) for c in candidates
            if c['unmarked_creator_own_writer_matches'][index]['authority_ids']}
        if len(known)>1:
            return None
    candidates.sort(key=lambda c:(c['source_id'],c['source_raw_sha256']))
    for candidate in candidates:
        del candidate['_signature']
    paired=any(c['imprint_match'].get('global_imprint_alias_asserted') is False for c in candidates)
    return {'rule_version':'overlap-detail-ndl-bibliographic-correspondence-v2' if paired
            else 'overlap-detail-ndl-bibliographic-correspondence-v1','scope':SCOPE,'provider':'overlap',
        'publisher_reference_id':reference['publisher_product_reference_id'],'source_unit':reference['source_unit'],
        'official_source_url':reference['source_url'],'official_title':reference['title'],
        'official_isbn':reference['isbn'],'official_isbn_source_literal':reference['isbn_source_literal'],
        'official_isbn_source_basis':reference['isbn_source_basis'],
        'official_credits':deepcopy(reference['credits']),'official_creator_field_literal':reference['creator_field_literal'],
        'source_author_role_status':'unknown','source_writer_role_asserted':False,'author_identity_asserted':False,
        'illustrator_confirmation':'unassessed','official_label':reference['label'],'official_label_scope':_CARD_SCOPE,
        'official_detail_label':None,'official_individual_imprint_asserted':False,
        'official_publisher':None,'official_publisher_asserted':False,'official_form':reference['form'],
        'official_lightnovel_tag':None,'light_novel_status':'unknown',
        'light_novel_status_basis':'source_classification_context_unassessed',
        **{k:deepcopy(primary[k]) for k in ('source_id','source_url','source_raw_sha256','title_match',
            'publisher_match','imprint_match','own_imprint','own_primary_resource_locator','language','ndc_codes',
            'edition_statements','unmarked_creator_own_writer_matches')},
        'publisher_source_evidence':deepcopy(reference['source_evidence']),
        'owncard_source_evidence':{k:reference['owncard_source_evidence'][k] for k in ('source_url','raw_sha256','selector')},
        'explicit_isbn_fields':deepcopy([r for r in reference['bibliographic_field_literals'] if r['field_key']=='ISBN']),
        'same_isbn_candidates':candidates,'candidate_raw_sha256s':sorted({c['source_raw_sha256'] for c in candidates}),
        'edition_identity_asserted':False,'same_edition_identity_asserted':False,'product_sku_identity_asserted':False,
        'original_work_identity_asserted':False,'story_series_identity_asserted':False,
        'global_author_alias_asserted':False,'global_author_identity_asserted':False}


def match_overlap_details(reference, primary_parts, *, same_isbn_parts):
    """Return bounded correspondence; require a caller-authenticated full ISBN inventory."""
    try:
        return _match(reference,primary_parts,same_isbn_parts)
    except (KeyError,TypeError,ValueError,AttributeError,IndexError):
        return None
