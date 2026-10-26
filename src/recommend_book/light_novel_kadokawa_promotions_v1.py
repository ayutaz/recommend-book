"""Replay novel-specific official promotion book links, never publisher prose."""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urljoin,urlsplit

from .light_novel_publisher_references_v1 import _DOM,_read,_decode,MAX_SOURCE_BYTES,MAX_METADATA_BYTES

PROMOTION = re.compile(r'https://promo\.kadokawa\.co\.jp/(?:fantasiabunko|dengekibunko)/(?:special|sp)/[A-Za-z0-9_-]+/')
PRODUCT_HOSTS = {'promo.kadokawa.co.jp','www.kadokawa.co.jp','kadokawa.co.jp','fantasiabunko.jp','dengekibunko.jp'}


def _redirect_bridge(entry, root):
    old=entry['wikidata_source_url'];final=entry['official_series_url']
    match=re.fullmatch(r'https://(dengekibunko|fantasiabunko)\.jp/((?:special|sp)/[A-Za-z0-9_-]+/)',old)
    if not match or final!='https://promo.kadokawa.co.jp/'+match[1]+'/'+match[2]:
        raise ValueError('direct novel relocation URL scope differs')
    pin=entry['redirect_receipt'];path=Path(pin['path']).resolve()
    if not path.is_relative_to(root):raise ValueError('redirect receipt escapes source bundle')
    receipt=json.loads(_read(path,pin['sha256'],MAX_METADATA_BYTES))
    artifact=receipt['artifact'];response=receipt['response'];raw_path=Path(artifact['path']).resolve()
    if (receipt.get('requested_url')!=old or receipt.get('final_url')!=old
            or receipt.get('http_status') not in (301,302) or receipt.get('status')!='failed'
            or receipt.get('attempts')!=1 or receipt.get('automatic_redirects') is not False
            or artifact.get('source_url')!=old or response.get('url')!=old
            or response.get('status')!=receipt['http_status']
            or response.get('headers',{}).get('location')!=final
            or not raw_path.is_relative_to(root)):
        raise ValueError('direct novel relocation receipt differs')
    raw=_read(raw_path,artifact['sha256'],MAX_SOURCE_BYTES)
    if artifact.get('size')!=len(raw):raise ValueError('redirect source size differs')
    return {'wikidata_source_url':old,'official_series_url':final,
            'receipt_path':str(path),'receipt_sha256':pin['sha256'],
            'raw_path':str(raw_path),'raw_sha256':artifact['sha256'],
            'http_status':receipt['http_status'],'location':final,
            'attempts':1,'automatic_redirects':False}


def book_links(document,source_url,title):
    from .light_novel_catalogue_v1 import normalize_search
    if not PROMOTION.fullmatch(source_url):
        raise ValueError('novel promotion URL outside explicit official scope')
    tree=_DOM(document).root
    headings=tree.nodes(tag='title')
    if len(headings)!=1 or normalize_search(title) not in {normalize_search(t) for t in headings[0].text().split('|')}:
        raise ValueError('official novel promotion title differs')
    containers=[n for n in tree.nodes(cls='series') if any(h.attrs.get('id')=='books' and h.text()=='シリーズ一覧' for h in n.nodes(tag='h2'))]
    selector='.series[h2#books=シリーズ一覧]'
    if not containers:
        containers=tree.nodes(cls='p-lp-books__wrap')+tree.nodes(cls='p-lp-books-thumbnail')
        selector='.p-lp-books__wrap|.p-lp-books-thumbnail'
    if not containers:
        containers=[n for n in tree.nodes() if n.attrs.get('id')=='book']
        selector='#book'
    if not 1<=len(containers)<=2:
        raise ValueError('explicit official book-list container absent or ambiguous')
    links={}
    for index,container in enumerate(containers):
        for ordinal,anchor in enumerate(container.nodes(tag='a')):
            url=urljoin(source_url,anchor.attrs.get('href',''));parts=urlsplit(url)
            match=re.fullmatch(r'/product/(?:[^/?#]+/)*([0-9]{12})(?:/|\.html)?',parts.path)
            if parts.scheme!='https' or parts.hostname not in PRODUCT_HOSTS or not match:
                continue
            identity='kadokawa:'+match[1]
            links.setdefault(identity,[]).append({'href':anchor.attrs.get('href',''),
                'resolved_url':url,'locator':f'{selector}[{index}]/a[{ordinal}]@href'})
    if not links:
        raise ValueError('official novel book list contains no product identifiers')
    return links


def verify_promotions(manifest_path,expected_sha256):
    path=Path(manifest_path).resolve()
    manifest=json.loads(_read(path,expected_sha256,MAX_METADATA_BYTES))
    version=manifest.get('version')
    if version not in {'light-novel-kadokawa-series-promotions-v1','light-novel-kadokawa-series-promotions-v2'}:
        raise ValueError('novel promotion manifest version differs')
    entries=manifest['series']
    if not 1<=len(entries)<=100:
        raise ValueError('bounded explicit novel promotion selection required')
    result={}
    for entry in entries:
        identity=entry['series_entry_id'];url=entry['official_series_url'];receipt=entry['receipt']
        if (not re.fullmatch(r'lns:wd:Q[1-9][0-9]*',identity) or identity in result
                or not isinstance(entry['title'],str) or not entry['title']
                or not PROMOTION.fullmatch(url) or receipt.get('status')!='success'
                or receipt.get('http_status')!=200):
            raise ValueError('novel promotion identity or status differs')
        artifact=receipt['artifact'];raw_path=Path(artifact['path']).resolve()
        response=receipt['response']
        if (artifact['source_url']!=url or not raw_path.is_relative_to(path.parent)
                or response.get('url')!=url or response.get('status')!=200 or response.get('location')
                or not str(response.get('content_type','')).lower().startswith('text/html')):
            raise ValueError('novel promotion origin, source root or content type differs')
        raw=_read(raw_path,artifact['sha256'],MAX_SOURCE_BYTES)
        if artifact['size']!=len(raw):raise ValueError('novel promotion source size differs')
        links=book_links(_decode(raw,response['content_type']),url,entry['title'])
        result[identity]={'series_entry_id':identity,'title':entry['title'],'source_url':url,
            'raw_sha256':artifact['sha256'],'books':links,'prose_ai_input_allowed':False}
        if version=='light-novel-kadokawa-series-promotions-v2':
            result[identity].update(wikidata_source_url=entry['wikidata_source_url'],
                                   redirect_evidence=_redirect_bridge(entry,path.parent))
    return result
