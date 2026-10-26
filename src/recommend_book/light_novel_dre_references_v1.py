"""Replay bounded official DRE yearly book cards, without story identity claims."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .light_novel_publisher_references_v1 import _read, _decode, _text, MAX_SOURCE_BYTES, MAX_METADATA_BYTES
from .normalization import normalize_isbn

ENTRY = 'https://drecom-media.jp/drenovels/product'
PRODUCT = re.compile(re.escape(ENTRY) + r'/([1-9][0-9]*)')


def _elements(text, tag, css):
    pattern = rf'<{tag}\b[^>]*class=["\'][^"\']*\b{re.escape(css)}\b[^"\']*["\'][^>]*>(.*?)</{tag}>'
    return re.findall(pattern, text, re.S)


def parse_year(document, year, source_url, raw_sha):
    if len(re.findall(rf'<option\b[^>]*value="{year}"[^>]*\bselected\b', document)) != 1:
        raise ValueError('DRE selected year not reproduced')
    rows = []
    for position, block in enumerate(_elements(document, 'li', 'bookColumn__item')):
        urls = re.findall(r'<a\b[^>]*href="(' + re.escape(ENTRY) + r'/[1-9][0-9]*)"', block)
        title = _elements(block, 'p', 'bookColumn__title')
        label = _elements(block, 'p', 'comicProductList_label')
        if len(urls) != 1 or len(title) != 1 or list(map(_text, label)) != ['DREノベルス']:
            raise ValueError('DRE book card scope or complete title ambiguous')
        credits = []
        for literal in _elements(block, 'span', 'comicProductList_author'):
            match = re.fullmatch(r'(著|イラスト)\s*/\s*(.+)', _text(literal))
            if match is None:
                raise ValueError('DRE creator role not explicit')
            credits.append({'role': 'author' if match[1] == '著' else 'illustrator', 'role_display':match[1], 'name':match[2]})
        labels = list(map(_text, _elements(block, 'span', 'productColumn__infoLabel')))
        values = list(map(_text, _elements(block, 'span', 'productColumn__infoValue')))
        if len(labels) != len(values) or len(set(labels)) != len(labels):
            raise ValueError('DRE card facts ambiguous')
        facts = dict(zip(labels, values))
        date = re.fullmatch(rf'{year}年([0-9]{{1,2}})月([0-9]{{1,2}})日', facts.get('発売日',''))
        if date is None or not 1 <= int(date[1]) <= 12 or not 1 <= int(date[2]) <= 31:
            raise ValueError('DRE release year differs from selected frame')
        isbn = normalize_isbn(facts['ISBN']) if 'ISBN' in facts else None
        if 'ISBN' in facts and (not isbn or not re.fullmatch(r'97[89][0-9]{10}',isbn)):
            raise ValueError('DRE explicit ISBN invalid')
        product_id = PRODUCT.fullmatch(urls[0])[1]
        rows.append({'publisher_reference_id':'dre-product:'+product_id, 'title':_text(title[0]),
            'source_url':urls[0], 'source_unit':'publisher_product_reference', 'publisher':'ドリコム',
            'label':'DREノベルス', 'label_literals':['DREノベルス'], 'credits':credits,
            'isbn':isbn, 'form':None, 'official_lightnovel_tag':None, 'story_series_asserted':False,
            'metadata_conflict':False, 'bibliographic_variants':[],
            'source_evidence':[{'source_url':source_url,'raw_sha256':raw_sha,'locator':f'bookColumn__item[{position}]'}]})
    if not rows or len({r['publisher_reference_id'] for r in rows}) != len(rows):
        raise ValueError('DRE yearly book cards empty or duplicated')
    return rows


def verify_dre_references(manifest_path, expected_sha256):
    path = Path(manifest_path).resolve()
    manifest = json.loads(_read(path, expected_sha256, MAX_METADATA_BYTES))
    if manifest.get('version') != 'dre-official-yearly-bibliography-v1':
        raise ValueError('DRE manifest version differs')
    pages = manifest['pages']
    if not 1 <= len(pages) <= 20 or len({p['year'] for p in pages}) != len(pages):
        raise ValueError('DRE finite year selection differs')
    rows = []
    for page in pages:
        year = page['year']
        if type(year) is not int or not 2022 <= year <= 2100 or page['url'] not in (ENTRY, ENTRY+f'?y={year}'):
            raise ValueError('DRE official year URL differs')
        if page.get('status') != 'success' or page.get('status_code') != 200:
            raise ValueError('DRE unsuccessful source page')
        artifact = page['artifact']
        raw_path = Path(artifact['path']).resolve()
        if not raw_path.is_relative_to(path.parent) or artifact['source_url'] != page['url']:
            raise ValueError('DRE source path or origin differs')
        raw = _read(raw_path, artifact['sha256'], MAX_SOURCE_BYTES)
        if len(raw) != artifact['size']:
            raise ValueError('DRE source size differs')
        rows.extend(parse_year(_decode(raw, 'text/html; charset=utf-8'),year,page['url'],artifact['sha256']))
    if len({r['publisher_reference_id'] for r in rows}) != len(rows):
        raise ValueError('DRE product repeated across selected years')
    return rows
