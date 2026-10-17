"""Narrow saved-OPF ingestion of Standard Ebooks' own editorial description.

No network, book body, artwork, external subjects, or translation-style inference.
An edition contributes a work-level synopsis only after an exact Wikidata bridge.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree as ET

from recommend_book.models import Contributor, Evidence, Identifier, SourceRecord, Title

POLICY_ID = 'standardebooks-editorial-cc0'
ABOUT_URL = 'https://standardebooks.org/about'
MANUAL_URL = 'https://standardebooks.org/manual/1.9.1/9-metadata'
LOCATOR = '/opf:package/opf:metadata/dc:description;escaped-xhtml;complete-paragraphs-v1'
LICENSE_URL = 'https://creativecommons.org/publicdomain/zero/1.0/'
NS = {'opf': 'http://www.idpf.org/2007/opf', 'dc': 'http://purl.org/dc/elements/1.1/'}


def _reviewed_description_link(href):
    """Recognize local editorial referrals without ever following their URLs.

    Only canonical HTTPS author/ebook and single collection-slug paths qualify.
    Ports (even 443), credentials, queries, fragments and URL escapes stay held.
    """
    if not href or re.search(r'[\s\\?#%]', href):
        return False
    try:
        url = urlsplit(href)
    except ValueError:
        return False
    return (url.scheme == 'https' and url.hostname == 'standardebooks.org'
            and url.netloc == 'standardebooks.org'
            and url.username is None and url.password is None
            and re.fullmatch(r'/(?:ebooks/[a-z0-9-]+(?:/[a-z0-9-]+)?|collections/[a-z0-9-]+)', url.path) is not None)


def parse_opf(payload):
    """Extract only one title, author, ebook identity, work sameAs and description."""
    if b'<!DOCTYPE' in payload or b'<!ENTITY' in payload:
        raise ValueError('OPF external declarations unsupported')
    try:
        tree = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError('invalid OPF XML') from exc
    metadata = tree.findall('opf:metadata', NS)
    if tree.tag != '{' + NS['opf'] + '}package' or len(metadata) != 1:
        raise ValueError('one OPF package metadata required')
    metadata = metadata[0]

    def one(tag):
        nodes = metadata.findall(tag, NS)
        if len(nodes) != 1 or len(nodes[0]) or not (nodes[0].text or '').strip():
            raise ValueError('one plain OPF value required: ' + tag)
        return nodes[0], nodes[0].text.strip()

    ident, ebook = one('dc:identifier')
    if tree.get('unique-identifier') != ident.get('id') or not re.fullmatch(r'https://standardebooks.org/ebooks/[a-z0-9-]+/[a-z0-9-]+', ebook):
        raise ValueError('canonical Standard Ebooks identifier required')
    titles = metadata.findall('dc:title', NS)
    if len(titles) > 1:
        ids = [node.get('id') for node in titles]
        if not all(ids) or len(set(ids)) != len(ids) or any(len(node) or not (node.text or '').strip() for node in titles):
            raise ValueError('multiple OPF titles require unique IDs and plain values')
        title_types = [[n.text for n in metadata.findall('opf:meta', NS)
                        if n.get('property') == 'title-type' and n.get('refines') == '#' + ident]
                       for ident in ids]
        if any(len(types) != 1 or types[0] not in ('main', 'subtitle', 'expanded') for types in title_types) or sum(types == ['main'] for types in title_types) != 1:
            raise ValueError('multiple OPF titles require one explicit main and unambiguous supported types')
        title = titles[title_types.index(['main'])].text.strip()
    else:
        _, title = one('dc:title')
    author_node, author = one('dc:creator')
    author_roles = [n.text for n in metadata.findall('opf:meta', NS) if n.get('property') == 'role' and n.get('refines') == '#' + str(author_node.get('id'))]
    if author_roles.count('aut') != 1 or len(author_roles) != len(set(author_roles)) or not set(author_roles) <= {'aut', 'wpr', 'ann', 'dto', 'ill'} or one('dc:publisher')[1] != 'Standard Ebooks':
        raise ValueError('one explicit Standard Ebooks author/publisher required')
    if not one('dc:language')[1].startswith('en-') and one('dc:language')[1] != 'en':
        raise ValueError('only English editorial descriptions supported')
    rights = one('dc:rights')[1]
    if 'CC0' not in rights or LICENSE_URL not in rights:
        raise ValueError('OPF contributor CC0 dedication absent')
    meta = metadata.findall('opf:meta', NS)
    # Membership in a named prize/list/series is not a collected-work identity.
    # Preserve none of it as work subjects, genres, contents, or series labels.
    for collection in (n for n in meta if n.get('property') == 'belongs-to-collection'):
        types = [n.text for n in meta if n.get('property') == 'collection-type' and n.get('refines') == '#' + str(collection.get('id'))]
        if len(types) != 1 or types[0] not in ('set', 'series'):
            raise ValueError('untyped collection membership requires separate review')
    if any(n.get('property') == 'schema:hasPart' for n in meta) or metadata.findall('dc:type', NS):
        raise ValueError('collection or ambiguous type requires separate review')
    links = [n.get('href') for n in metadata.findall('opf:link', NS) if n.get('rel') == 'schema:sameAs' and n.get('refines') is None]
    if len(links) != 1 or not links[0].startswith('https://en.wikipedia.org/wiki/'):
        raise ValueError('one work-level English Wikipedia sameAs required')
    description = one('dc:description')[1]
    try:
        fragment = ET.fromstring('<description>' + description + '</description>')
    except ET.ParseError as exc:
        raise ValueError('invalid escaped description XHTML') from exc
    if not len(fragment) or any(n.tag != 'p' or (n.tail or '').strip() for n in fragment) or (fragment.text or '').strip():
        raise ValueError('description must contain complete escaped XHTML paragraphs')
    for node in fragment.iter():
        if node is fragment:
            continue
        if node.tag not in ('p', 'i', 'em', 'b', 'strong', 'u', 's', 'a') or set(node.attrib) - ({'href'} if node.tag == 'a' else set()):
            raise ValueError('third-party quotation or unsupported description markup')
        if node.tag == 'p' and node not in list(fragment):
            raise ValueError('nested paragraph not supported')
        if node.tag == 'a' and not _reviewed_description_link(node.get('href')):
            raise ValueError('external description link requires separate review')
    text = '\n\n'.join(' '.join(''.join(p.itertext()).split()) for p in fragment)
    if len(text) < 20 or re.search('[“”«»"]|copyright|quoted from|according to|excerpt from', text, re.I):
        raise ValueError('third-party quotation or unattributed material requires review')
    return dict(ebook=ebook, title=title, author=author, same_as=links[0], text=text)


def verify_authority_bridge(bridge, same_as):
    path = Path(bridge['target_raw_path'])
    if not path.is_absolute() or hashlib.sha256(path.read_bytes()).hexdigest() != bridge['target_raw_sha256']:
        raise ValueError('Wikidata authority raw hash/path differs')
    qid = bridge['wikidata_work_id']
    entity = json.loads(path.read_bytes())['entities'][qid]
    if not re.fullmatch(r'Q[1-9][0-9]*', qid) or entity.get('id') != qid:
        raise ValueError('Wikidata authority identity differs')
    assertions = [s for p in ('P629', 'P31') for s in entity.get('claims', {}).get(p, []) if s.get('rank') != 'deprecated' and
                  (p == 'P629' or s.get('mainsnak', {}).get('datavalue', {}).get('value', {}).get('id') in ('Q3331189', 'Q53731850'))]
    if assertions:
        raise ValueError('Wikidata edition assertion conflicts with work bridge')
    title = entity['sitelinks']['enwiki']['title']
    expected = {'wikidata_work_id': qid, 'target_raw_path': str(path), 'target_raw_sha256': bridge['target_raw_sha256'],
                'target_raw_locator': 'entities.' + qid, 'enwiki_title': title, 'work_same_as': same_as}
    if bridge != expected or same_as != 'https://en.wikipedia.org/wiki/' + quote(title.replace(' ', '_'), safe='():,'):
        raise ValueError('exact Wikidata enwiki/work sameAs bridge differs')


def _record(payload, *, raw_path, source_url, retrieved_at, bridge, editorial=False):
    data = parse_opf(payload)
    repository = data['ebook'].split('/ebooks/', 1)[1].replace('/', '_')
    if not re.fullmatch(r'https://raw\.githubusercontent\.com/standardebooks/' + re.escape(repository) + r'/(?:master|[0-9a-f]{40})/src/epub/content\.opf', source_url):
        raise ValueError('OPF acquisition source URL differs from ebook repository')
    verify_authority_bridge(bridge, data['same_as'])
    sha = hashlib.sha256(payload).hexdigest()
    evidence = Evidence(evidence_id=f'se:{repository}:description:{sha[:12]}', kind='synopsis', text=data['text'],
        source_url=source_url, rights_policy_id=POLICY_ID, llm_input_allowed=True, language='en', locator=LOCATOR,
        license='CC0-1.0', license_url=LICENSE_URL,
        attribution={'credit': 'Standard Ebooks contributors', 'ebook_url': data['ebook'], 'raw_path': raw_path,
                     'raw_sha256': sha, 'authority_bridge': bridge,
                     'changes': 'complete editorial description; escaped XHTML converted to plain text',
                     'scope': 'work synopsis only; no translation-style inference; no ebook body, images or external subjects'})
    return SourceRecord(source='standardebooks', provider_id='standardebooks',
        source_id=data['ebook'] + ('#editorial-description' if editorial else ''),
        source_url=source_url, retrieved_at=retrieved_at, record_kind='work' if editorial else 'edition', rights_policy_id=POLICY_ID,
        titles=[Title(value=data['title'], language='en')], contributors=[Contributor(name=data['author'])],
        identifiers=([Identifier(source='wikidata', value=bridge['wikidata_work_id'], scope='work')] if editorial else
                     [Identifier(source='standardebooks', value=data['ebook'], scope='edition')]),
        work_refs=[Identifier(source='wikidata', value=bridge['wikidata_work_id'], scope='work')],
        language=None if editorial else 'en', publisher=None if editorial else 'Standard Ebooks',
        evidence=[evidence] if editorial else [], raw_path=raw_path, raw_sha256=sha,
        extra={'authority_bridge': bridge, 'edition_source_id': data['ebook'],
               'scope': 'work-level editorial synopsis only' if editorial else 'edition identity only; synopsis is a separate work record'})


def ingest_editorial_description(opf_path, *, source_url, retrieved_at, wikidata_record):
    """Build an immutable edition identity; use editorial_work_record for synopsis."""
    wd = wikidata_record
    if wd.source != 'wikidata' or wd.provider_id != 'wikidata' or wd.record_kind != 'work' or wd.rights_policy_id != 'wikidata-cc0':
        raise ValueError('saved Wikidata work record required')
    path = Path(opf_path).resolve()
    data = parse_opf(path.read_bytes())
    if hashlib.sha256(Path(wd.raw_path).read_bytes()).hexdigest() != wd.raw_sha256:
        raise ValueError('Wikidata authority raw hash differs')
    entity = json.loads(Path(wd.raw_path).read_bytes())['entities'][wd.source_id]
    bridge = {'wikidata_work_id': wd.source_id, 'target_raw_path': wd.raw_path, 'target_raw_sha256': wd.raw_sha256,
              'target_raw_locator': 'entities.' + wd.source_id, 'enwiki_title': entity['sitelinks']['enwiki']['title'],
              'work_same_as': data['same_as']}
    return _record(path.read_bytes(), raw_path=str(path), source_url=source_url, retrieved_at=retrieved_at, bridge=bridge)


def editorial_work_record(edition_record):
    """Project only the reviewed work synopsis, preserving the separate edition.

    Ingest both records. The edition retains ebook language and identity but no
    evidence; the editorial record carries one work-scoped synopsis. The English
    prose is evidence language, never an inferred original/translation language.
    """
    if edition_record.record_kind != 'edition':
        raise ValueError('Standard Ebooks edition identity required')
    payload = Path(edition_record.raw_path).read_bytes()
    verify_record(edition_record, payload)
    return _record(payload, raw_path=edition_record.raw_path, source_url=edition_record.source_url,
                   retrieved_at=edition_record.retrieved_at, bridge=edition_record.extra['authority_bridge'], editorial=True)


def verify_record(record, payload):
    """Guard-side reproduction rejects injected fields, subjects, body, and bridges."""
    expected = _record(payload, raw_path=record.raw_path, source_url=record.source_url,
                       retrieved_at=record.retrieved_at, bridge=record.extra['authority_bridge'],
                       editorial=record.record_kind == 'work')
    if record != expected:
        raise ValueError('Standard Ebooks immutable record not reproduced by scoped OPF recipe')
    return expected
