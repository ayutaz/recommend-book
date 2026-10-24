"""DX fallback for one authenticated own NDL bibliographic edition.

The official book identifier is parsed from a publisher URL, not an independently
printed ISBN field. Its checksum-normalized value is only a lookup corroboration
for the own NDL ISBN, complete title, writers, publisher and imprint. The caller
authenticates both sources; this module performs no I/O or global identity work.
"""
from __future__ import annotations

from copy import deepcopy
import re
import unicodedata

from .light_novel_bibliographic_match_v1 import _authors, _publisher
from .light_novel_catalogue_reference_match_v1 import _direct, _SERIAL
from .light_novel_earthstar_match_v1 import _fallback_titles
from .light_novel_explicit_series_match_v1 import _person_key
from .light_novel_reference_match_v1 import _key, _parent, _titles
from .light_novel_shueisha_books_v1 import _BOOK, _INDEX, _isbn
from .normalization import normalize_isbn


_SHA=re.compile(r'[0-9a-f]{64}')
_NDL=re.compile(r'R100000002-[A-Za-z0-9._-]+')
_LABELS={'ダッシュエックス文庫','スーパーダッシュ文庫','ダッシュエックス文庫DIGITAL',
    'Dノベルf／ダッシュエックスノベルエフ','Dノベル／ダッシュエックスノベル'}
_WRITERS={'著','著者','著者・イラスト','著・イラスト','小説'}


def _official(publisher):
    product=_BOOK.fullmatch(publisher['source_url'])
    identifier=publisher['publisher_book_id']
    isbn=_isbn(identifier)
    if (not product or product[1]!=identifier or publisher['isbn_as_publisher_url']!=identifier
            or not isbn or publisher['isbn']!=isbn or publisher['kind']!='book'
            or publisher['publisher']!='集英社' or publisher['label'] not in _LABELS
            or not isinstance(publisher['title'],str) or not publisher['title'].strip()
            or publisher['story_series_identity']!='unassessed_no_title_stripping'):
        return None
    witness=publisher['source_evidence']
    if (not _INDEX.fullmatch(witness['source_url']) or not _SHA.fullmatch(witness['source_sha256'])
            or not re.fullmatch(r'section\.result_box:nth-of-type\([1-9][0-9]*\) h4 / span\.boldText',witness['selector'])):
        return None
    writers=[credit for credit in publisher['credits'] if credit['role'] in _WRITERS]
    if (not writers or any(not isinstance(c['name'],str) or not _person_key(c['name']) for c in writers)
            or len({_person_key(c['name']) for c in writers})!=len(writers)):
        return None
    return isbn,writers


def _imprint(publisher, parts, own, isbn):
    source=parts['source']
    raw=source.get('series')
    if not isinstance(raw,str) or not raw:
        return None
    segments=[value.strip() for value in unicodedata.normalize('NFKC',raw).split(';')]
    forms=[value.strip() for value in segments[0].split(' = ')]
    if any(not _SERIAL.fullmatch(value) for value in segments[1:]):
        return None
    label=publisher['label']
    allowed={_key(value) for value in [label,*label.split('／')]}
    if forms and all(_key(value) in allowed for value in forms):
        return {'rule':'exact_official_label_or_component','official_label':label,'source_value':raw,
            'own_parallel_forms':forms,'own_primary_resource_locator':own,'projection_pointer':'/source/series'}
    if label!='スーパーダッシュ文庫' or any(value!='集英社スーパーダッシュ文庫' for value in forms):
        return None
    return {'rule':'same_record_superdash_literal_pair','scope':'this_authenticated_own_bibliographic_record_only',
        'official_label':label,'source_value':raw,'own_parallel_forms':forms,'own_primary_resource_locator':own,
        'projection_pointer':'/source/series','source_id':source['source_id'],
        'source_raw_sha256':parts['source_raw_sha256'],'own_isbn13':isbn,
        'isbn_lookup_basis':'publisher_url_identifier_to_own_primary_isbn','official_printed_ISBN_asserted':False,
        'global_imprint_alias_asserted':False}


def _match(publisher, parts):
    official=_official(publisher)
    if official is None:
        return None
    isbn,writers=official
    source=parts['source']
    ndc=source['extra']['ndc_codes']
    if (not _NDL.fullmatch(source['source_id'])
            or source['source_url']!='https://ndlsearch.ndl.go.jp/books/'+source['source_id']
            or source['provider_id'] not in {'iss-ndl-opac','iss-ndl-opac-national'}
            or source['record_kind']!='edition' or source.get('language') not in {'ja','jpn'}
            or not _SHA.fullmatch(parts['source_raw_sha256'])
            or not ndc or any(not re.fullmatch(r'913(?:\.[0-9]+)?',value) for value in ndc)):
        return None
    pub=_publisher(parts,'集英社')
    if pub is None:
        return None
    own=_parent(pub['publisher_agent']['locator'])
    if any(not _direct(own,'http://purl.org/dc/terms/','publisher',agent['locator']) for agent in parts['publisher_agents']):
        return None
    for agent in pub['distribution_agents']:
        if not re.fullmatch(re.escape(agent['locator'])+r'/\{http://xmlns\.com/foaf/0\.1/\}Agent\[1\]'
                +r'/\{http://purl\.org/dc/terms/\}description\[[1-9][0-9]*\]',agent['description_locator']):
            return None
    identifiers=[{'value':row['value'],'projection_pointer':f'/source/identifiers/{index}',
        'own_primary_resource_locator':own} for index,row in enumerate(source['identifiers'])
        if row['source']=='isbn' and normalize_isbn(row['value'])==isbn]
    if (not identifiers or not parts['titles'] or any(not row['value'] or not any(
            _direct(own,ns,'title',row['locator']) for ns in ('http://purl.org/dc/terms/','http://purl.org/dc/elements/1.1/'))
            for row in parts['titles']) or (parts['volume'] and not _direct(
                own,'http://ndl.go.jp/dcndl/terms/','volume',parts['volume']['locator']))):
        return None
    imprint=_imprint(publisher,parts,own,isbn)
    title=_titles(parts,publisher['title'],own) or _fallback_titles(parts,publisher['title'],own)
    if imprint is None or title is None:
        return None
    authors=_authors(parts,[writer['name'] for writer in writers],own)
    if not authors:
        return None
    # The shared dated-heading helper has a legacy series-specific key. This
    # provider's explicit evidence is a book credit, never a series assertion.
    for author in authors:
        for contributor in author.get('contributors',[]):
            corroboration=contributor.get('edition_bound_corroboration')
            if corroboration and 'official_series_author' in corroboration:
                corroboration['official_book_writer']=corroboration.pop('official_series_author')
    statements=parts.get('edition_statements',[])
    if not isinstance(statements,list) or any(not row['value'] or not _direct(
            own,'http://ndl.go.jp/dcndl/terms/','edition',row['locator']) for row in statements):
        return None
    return {'rule_version':'shueisha-dx-ndl-contained-bibliographic-edition-v2','scope':'edition_only',
        'confirmed_scope':'contained_bibliographic_edition_only','provider':'shueisha-dx',
        'publisher_book_id':publisher['publisher_book_id'],'isbn_as_publisher_url':publisher['isbn_as_publisher_url'],
        'isbn_lookup_basis':'publisher_url_identifier_to_own_primary_isbn','official_printed_ISBN_asserted':False,
        'isbn13':isbn,'primary_isbn_evidence':identifiers,
        'official_source_url':publisher['source_url'],'official_title':publisher['title'],
        'official_label':publisher['label'],'official_credits':deepcopy(publisher['credits']),
        'official_book_writer_credits':deepcopy(writers),
        'catalogue_owner':'集英社','catalogue_owner_basis':'publisher_catalogue_site_owner_context',
        'official_publisher':None,'official_publisher_asserted':False,'individual_publisher_agreement':'unknown',
        'publisher_source_evidence':{k:publisher['source_evidence'][k] for k in ('source_url','source_sha256','selector')},
        'source_id':source['source_id'],'source_url':source['source_url'],'source_raw_sha256':parts['source_raw_sha256'],
        'own_primary_resource_locator':own,'title_match':title,'matched_ndl_title':title['source_value'],
        'authors':authors,'publisher_match':pub,'own_publisher':source['publisher'],
        'own_imprint':source['series'],'imprint_match':imprint,'language':source['language'],'ndc_codes':deepcopy(ndc),
        'edition_statements':deepcopy(statements),'author_identity_asserted':False,
        'same_edition_identity_asserted':False,'product_sku_identity_asserted':False,'printing_identity_asserted':False,
        'paper_ebook_same_edition_asserted':False,'original_work_identity_asserted':False,'story_series_identity_asserted':False,
        'global_author_alias_asserted':False,'global_author_identity_asserted':False,
        'sources':[source['source_url'],publisher['source_url']]}


def match_shueisha_bibliographic_edition(publisher, primary_parts):
    """Match a contained own bibliography; URL digits are not printed ISBN evidence."""
    try:
        return _match(publisher,primary_parts)
    except (KeyError,TypeError,ValueError,AttributeError):
        return None
