"""Offline, Plot-only source contracts for admitted LN works, never series."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import quote

import mwparserfromhell
from pydantic import Field

from .models import Contract, Evidence
from .jobs import canonical_json
from .autonomous_enrichment import SECTIONS, FULFILLED
from .ingestion.wikipedia import section_text
from .wikipedia_prose import screen_section, SCREEN_VERSION
from .light_novel_work_admission_v3 import verify_metadata
from .light_novel_work_article_selection_v2 import select_authenticated_page
from .light_novel_classification_v1 import IMPORT_NOTICE, TERMS_URL, WIKIDATA_TERMS, LICENSE_URL

SOURCE_VERSION = 'light-novel-work-source-admission-v1'
BATCH_SOURCE_VERSION = 'light-novel-work-source-admission-v2'


class WorkInput(Contract):
    work_id: str = Field(pattern=r'^lnw:wd:Q[1-9][0-9]*$')
    entity_kind: Literal['light_novel_work'] = 'light_novel_work'
    title: str
    authors: list[str] = Field(default_factory=list)
    original_language: Literal['ja'] = 'ja'
    evidence: list[Evidence]
    source_record_ids: list[str]
    source_manifest_sha256: str


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metadata_pins(metadata):
    verify_metadata(metadata)
    provenance = metadata['provenance']
    candidates = [provenance, *provenance['linked_provenance'].values(),
        *metadata['work_admission_proof']['type_authorities'].values(),
        {'raw_path': provenance['conditions_path'], 'raw_sha256': provenance['conditions_sha256']}]
    candidates.extend(json.loads(Path(provenance['conditions_path']).read_bytes())['snapshots'])
    pins = {}
    for pin in candidates:
        path = str(Path(pin['raw_path']).resolve())
        if sha(path) != pin['raw_sha256'] or path in pins and pins[path] != pin['raw_sha256']:
            raise ValueError('Work structured source pin differs')
        pins[path] = pin['raw_sha256']
    return pins


def build_article_evidence(metadata, page, artifact, language):
    entity = verify_metadata(metadata)
    if language not in ('en', 'ja'):
        raise ValueError('Work source language outside finite initial recipe')
    if (page.get('title') != entity.get('sitelinks', {}).get(language + 'wiki', {}).get('title')
            or page.get('pageprops', {}).get('wikibase_item') != metadata['id']
            or page.get('ns') != 0 or 'disambiguation' in page.get('pageprops', {})):
        raise ValueError('Work article exact novel identity differs')
    revisions = page.get('revisions', [])
    if len(revisions) != 1 or type(revisions[0].get('revid')) is not int:
        raise ValueError('Work article single revision required')
    revision = revisions[0];slot = revision.get('slots', {}).get('main', {})
    if slot.get('contentmodel', 'wikitext') != 'wikitext':
        raise ValueError('Work article content model differs')
    text = slot.get('content', slot.get('*', ''))
    if not text:
        raise ValueError('Work article text absent')
    path, pin = artifact['path'], artifact['sha256']
    if sha(path) != pin:
        raise ValueError('Work article raw SHA differs')
    selected, selection = select_authenticated_page(metadata, artifact, language)
    if page != selected:
        raise ValueError('Work page differs from authenticated raw selection')
    if any(IMPORT_NOTICE.search(str(t.name)) for t in mwparserfromhell.parse(text).filter_templates(recursive=True)):
        return [], [], {'article': 'additional_attribution_or_import_notice'}
    revision_id = revision['revid'];identity = 'lnw:wd:' + metadata['id']
    url = f'https://{language}.wikipedia.org/w/index.php?oldid={revision_id}'
    history = f'https://{language}.wikipedia.org/w/index.php?title={quote(page["title"], safe="")}&action=history'
    evidence, receipts, outcomes = [], [], {}
    for section in SECTIONS[language]:
        if section == 'lead':continue
        try:original = section_text(text, section)
        except ValueError:continue
        screened = screen_section(original, article_wikitext=text, factual_titles=[metadata['title'], page['title']])
        outcomes[section] = asdict(screened)
        if screened.article_blocked or len(screened.text) < 40:continue
        locator = f'revision:{revision_id};section:{section};screen:{SCREEN_VERSION};work-paragraphs:v1'
        attribution = {'credit': 'Wikipedia contributors: ' + page['title'], 'permalink': url,
            'history_url': history, 'raw_path': path, 'raw_sha256': pin, 'page_id': page['pageid'],
            'revision_id': revision_id, 'scope': 'work', 'work_id': identity,
            'fulfilled_obligations': FULFILLED,
            'changes': 'selected complete screened Plot paragraphs; rejected paragraphs omitted; wiki markup removed; work classification added'}
        item = Evidence(evidence_id=f'work-wp:{language}:{revision_id}:{section}', kind='synopsis',
            text=screened.text, source_url=url, rights_policy_id='light-novel-work-wikipedia-screened-v1',
            llm_input_allowed=True, language=language, locator=locator, license='CC-BY-SA-4.0',
            license_url=LICENSE_URL, attribution=attribution, inherited_restrictions=FULFILLED)
        if selection is not None:
            item.attribution['page_selection'] = selection
        evidence.append(item)
        receipts.append({'evidence_id': item.evidence_id, 'entity_kind': 'light_novel_work', 'scope': 'work',
            'raw_path': path, 'raw_sha256': pin, 'language': language, 'section': section,
            'page_id': page['pageid'], 'revision_id': revision_id, 'screening': asdict(screened),
            'evidence_sha256': digest(item.model_dump(mode='json'))})
        if selection is not None:
            receipts[-1]['page_selection'] = selection
    return evidence, receipts, outcomes


def validate_source_admission(inputs, manifest_path, expected_sha256):
    if sha(manifest_path) != expected_sha256:
        raise ValueError('Work source manifest SHA differs')
    manifest = json.loads(Path(manifest_path).read_bytes())
    if manifest.get('version') not in (SOURCE_VERSION, BATCH_SOURCE_VERSION) or manifest.get('scope') != 'work':
        raise ValueError('Work source version or scope differs')
    pins = manifest['artifact_sha256']
    for path, pin in pins.items():
        if sha(path) != pin:raise ValueError('Work source artifact SHA differs')
    if set(manifest['conditions']) != {TERMS_URL, WIKIDATA_TERMS, LICENSE_URL}:
        raise ValueError('Work source/output terms snapshots required')
    for condition in manifest['conditions'].values():
        if pins.get(condition['path']) != condition['sha256']:
            raise ValueError('Work official terms are not pinned')
    if pins.get(str(Path(manifest['metadata_path']).resolve())) != sha(manifest['metadata_path']):
        raise ValueError('Work metadata snapshot is not pinned')
    records = {r['id']:r for line in Path(manifest['metadata_path']).read_bytes().splitlines()
        if line.strip() for r in [json.loads(line)]}
    entries = {entry['work_id']: entry for entry in manifest['works']}
    if len(entries) != len(manifest['works']) or len({i.work_id for i in inputs}) != len(inputs):
        raise ValueError('Work source repeats identity')
    for work in inputs:
        entry = entries.get(work.work_id);metadata = records.get(work.work_id.removeprefix('lnw:wd:'))
        if (entry is None or metadata is None or work.source_manifest_sha256 != expected_sha256
                or entry['metadata_row_sha256'] != digest(metadata)):
            raise ValueError('Work source metadata identity differs')
        if any(pins.get(path) != pin for path,pin in metadata_pins(metadata).items()):
            raise ValueError('Work transitive structured pins absent')
        actual, receipts = [], []
        for article in entry['articles']:
            if pins.get(article['raw_path']) != article['raw_sha256']:
                raise ValueError('Work article pin absent')
            selected, selection = select_authenticated_page(metadata,
                {'path': article['raw_path'], 'sha256': article['raw_sha256']}, article['language'])
            if (article.get('page_selection') != selection or
                    selection is not None and manifest['version'] != BATCH_SOURCE_VERSION or
                    selection is None and 'page_selection' in article):
                raise ValueError('Work source page selection/version differs')
            proposed, proofs, outcomes = build_article_evidence(metadata, selected,
                {'path': article['raw_path'], 'sha256': article['raw_sha256']}, article['language'])
            if outcomes != article['outcomes']:raise ValueError('Work paragraph screening differs')
            actual.extend(proposed);receipts.extend(proofs)
        if (not actual or [e.model_dump(mode='json') for e in actual] != [e.model_dump(mode='json') for e in work.evidence]
                or receipts != entry['evidence_receipts'] or work.title != metadata['title']
                or work.authors != [a['label'] for a in metadata['authors']]
                or work.source_record_ids != ['wikidata:' + metadata['id']]):
            raise ValueError('Work exact Plot-only evidence/identity differs')
    return manifest


def reserve_inputs(inputs, ledger, run_name, *, prior_classification_ids):
    """Fresh lnw work IDs and immutable hashes, independent of series/world ledgers."""
    directory = Path(ledger);directory.mkdir(parents=True, exist_ok=True)
    hashes = [digest(work.model_dump(mode='json')) for work in inputs]
    ids = [work.work_id for work in inputs]
    if not 1 <= len(ids) <= 100 or len(set(ids)) != len(ids) or len(set(hashes)) != len(hashes):
        raise ValueError('Work reservation finite distinct inputs required')
    classified = set()
    for identity in prior_classification_ids:
        match = re.fullmatch(r'(?:ln[sw]:wd:|wd:)?(Q[1-9][0-9]*)', identity)
        if match is None:
            raise ValueError('Prior classification identity is not an explicit Wikidata work')
        classified.add('lnw:wd:' + match[1])
    if classified & set(ids):
        raise ValueError('Existing world/LN work classification requires verified reuse; never resubmit')
    prior = [json.loads(p.read_bytes()) for p in directory.glob('*.json')]
    if any((directory / (h + '.json')).exists() for h in hashes) or {r['work_id'] for r in prior} & set(ids):
        raise ValueError('Work already reserved; no automatic repeat or new-manifest bypass')
    for work, pin in zip(inputs, hashes):
        with (directory / (pin + '.json')).open('x', encoding='utf8') as handle:
            handle.write(canonical_json({'work_id': work.work_id, 'input_hash': pin, 'run_name': run_name,
                'reserved_at': datetime.now(timezone.utc).isoformat(), 'automatic_retry_allowed': False}) + '\n')
    return hashes
