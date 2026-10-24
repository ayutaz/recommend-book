"""Strict own-bibliography projection of the national bibliography RDF forest."""
import copy
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, parse_qs

from .ingestion.ndl import RDF, DCNDL, DC, DCTERMS


def verify_bulk_receipt(path, sha256, artifact, cache):
    receipt = json.loads(artifact(path, sha256, cache))
    url = urlsplit(receipt.get('source_url', ''))
    query = parse_qs(url.query)
    if (receipt.get('version') != 'ndl-official-bulk-ui-probe-v2'
            or receipt.get('source_provider') != 'iss-ndl-opac-national'
            or url.scheme != 'https' or url.netloc != 'ndlsearch.ndl.go.jp'
            or url.path != '/bib' or query.get('cs') != ['marc']
            or receipt.get('official_help') != 'https://ndlsearch.ndl.go.jp/bib/help'):
        raise ValueError('official national bibliography receipt scope absent')
    raw = artifact(receipt['raw_path'], receipt['raw_sha256'], cache)
    rows = parse_bulk_documents(raw)
    counts = ('ui_total', 'documents', 'admin_records', 'bibliographic_records', 'unique_ids')
    if (any(type(receipt.get(k)) is not int or receipt[k] != len(rows) for k in counts)
            or receipt.get('bytes') != len(raw) or receipt.get('repository_ids') != ['R100000002']
            or receipt.get('all_count_match') is not True or receipt.get('maximum_selected') != 5000):
        raise ValueError('bulk receipt counts do not reproduce downloaded own records')
    return receipt, rows


def bulk_metadata_records(receipt_path, receipt_sha256, receipt, documents):
    """Create replayable candidates; a query or imprint never certifies genre."""
    from .ingestion.core import RawArtifact
    from .ingestion.ndl import NDLAdapter
    artifact = RawArtifact(receipt['raw_sha256'], receipt['raw_path'], receipt['bytes'],
                           receipt['retrieved_at'], receipt['source_url'])
    adapter = NDLAdapter(None, approved_providers={'iss-ndl-opac-national'})
    records = []
    for row in documents:
        source = adapter._normalize(row['node'], artifact, 'iss-ndl-opac-national',
            locator=f"bulk-docs[{row['index']}]/rdf:RDF").model_dump(mode='json')
        records.append({'schema_version': 'light-novel-edition-bulk-v1', 'source': 'ndl',
            'provider': 'iss-ndl-opac-national', 'id': source['source_id'],
            'title': source['titles'][0]['value'] if source['titles'] else '', 'volume': source['volume'],
            'authors': [c['name'] for c in source['contributors'] if c['role'] == 'author'],
            'publisher': source['publisher'], 'date': source['publication_year'],
            'isbn': [i['value'] for i in source['identifiers'] if i['source'] == 'isbn'],
            'languages': [source['language']] if source['language'] else [], 'label': source['series'],
            'ndc': source['extra'].get('ndc_codes', []), 'source_url': source['source_url'],
            'source_record': source, 'label_evidence': [], 'label_candidate': False,
            'admission': {'light_novel_confirmed': False},
            'provenance': {'url': source['source_url'], 'raw_path': receipt['raw_path'],
                'raw_sha256': receipt['raw_sha256'], 'conditions_path': receipt['conditions_path'],
                'conditions_sha256': receipt['conditions_sha256'], 'document_index': row['index'],
                'receipt_path': str(Path(receipt_path).resolve()), 'receipt_sha256': receipt_sha256}})
    return records


def parse_bulk_documents(raw):
    """Select one unambiguous NDL-admin-linked main resource per RDF document.

    The downloaded forest may also contain electronic/other-library records and
    holdings. Those nodes are deliberately absent from the returned RDF roots.
    """
    chunks = list(re.finditer(rb'<rdf:RDF\b.*?</rdf:RDF>', raw, re.S))
    if not chunks or len(chunks) > 5000:
        raise ValueError('bulk RDF document count outside 1..5000')
    remainder = re.sub(rb'<rdf:RDF\b.*?</rdf:RDF>', b'', raw, flags=re.S)
    if not re.fullmatch(rb'\s*(?:<\?xml\s+version=[^?]+\?>)?\s*', remainder):
        raise ValueError('unparsed bulk RDF content')
    result, identities = [], set()
    about, resource = f'{{{RDF}}}about', f'{{{RDF}}}resource'
    for index, chunk in enumerate(chunks):
        try:
            root = ET.fromstring(chunk[0])
        except ET.ParseError as exc:
            raise ValueError('bulk RDF document malformed') from exc
        if root.tag != f'{{{RDF}}}RDF':
            raise ValueError('bulk RDF namespace differs')
        admins = root.findall(f'{{{DCNDL}}}BibAdminResource')
        if len(admins) != 1:
            raise ValueError('bulk own administration ambiguous')
        admin = admins[0]
        url = admin.get(about, '')
        if not re.fullmatch(r'https://ndlsearch\.ndl\.go\.jp/books/R100000002-[A-Za-z0-9._-]+', url):
            raise ValueError('bulk own administration identity absent')
        categories = [n.text for n in admin.findall(f'{{{DCNDL}}}bibRecordCategory')]
        if categories != ['R100000002']:
            raise ValueError('bulk own repository category absent')
        targets = {n.get(resource) for n in admin.findall(f'{{{DCNDL}}}record')}
        if targets != {url + '#material'}:
            raise ValueError('bulk own administration link absent')
        mains = [n for n in root.findall(f'{{{DCNDL}}}BibResource')
                 if n.get(about) in targets and any(c.tag in {f'{{{DC}}}title', f'{{{DCTERMS}}}title'} for c in n)]
        if len(mains) != 1:
            raise ValueError('bulk own main bibliography ambiguous')
        if any(n is not mains[0] and n.tag in {f'{{{DCNDL}}}BibResource', f'{{{DCNDL}}}BibAdminResource', f'{{{DCNDL}}}Item'}
               for n in mains[0].iter()):
            raise ValueError('nested related bibliography cannot become own metadata')
        identity = url.rsplit('/', 1)[-1]
        if identity in identities:
            raise ValueError('bulk duplicate own bibliography identity')
        identities.add(identity)
        isolated = ET.Element(f'{{{RDF}}}RDF')
        isolated.append(copy.deepcopy(mains[0]))
        result.append({'source_id': identity, 'index': index, 'node': isolated})
    return result
