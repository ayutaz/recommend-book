"""Evidence-only proposals from revision-pinned independent licensed sources.

Automatic screening is a bounded exclusion heuristic, not human or legal review.
This module never changes canonical identity, bibliographic metadata, or source rights.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, urlparse

from recommend_book.ingestion.wikipedia import section_text
from recommend_book.models import Evidence, SourceRecord, WorkInput
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.wikipedia_prose import SCREEN_VERSION, screen_section
from recommend_book.normalization import normalize_isbn
from recommend_book.ingestion.core import RawArtifact
from recommend_book.ingestion.ndl import NDLAdapter, SRU
import mwparserfromhell

VERSION = 'autonomous-enrichment-v1'
SECTIONS = {
    'en': ['lead', 'Plot', 'Plot summary', 'Synopsis', 'Summary'],
    'ja': ['lead', 'あらすじ', 'ストーリー', '物語'],
    'fr': ['lead', 'Résumé', 'Intrigue'], 'de': ['lead', 'Handlung', 'Inhalt'],
    'es': ['lead', 'Argumento', 'Sinopsis'], 'it': ['lead', 'Trama'],
    'pt': ['lead', 'Enredo', 'Sinopse'], 'ko': ['lead', '줄거리'],
    'ru': ['lead', 'Сюжет'], 'zh': ['lead', '情节', '情節', '故事简介', '故事簡介', '内容', '內容', '劇情', '情節概述', '概要'],
    'ar': ['lead', 'حبكة', 'ملخص', 'القصة'], 'bn': ['lead', 'কাহিনী', 'সারসংক্ষেপ'],
    'uz': ['lead'], 'fa': ['lead'], 'tr': ['lead'], 'nl': ['lead'], 'pl': ['lead'],
    'sv': ['lead'], 'uk': ['lead'], 'cs': ['lead'], 'id': ['lead'], 'he': ['lead'],
    'vi': ['lead'], 'hu': ['lead'], 'fi': ['lead'], 'ro': ['lead'], 'ta': ['lead'],
}
FULFILLED = ['attribution', 'mark_changes', 'share_alike', 'third_party_text_screened']


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def year_bin(year):
    return 'unknown' if year is None else 'pre1901' if year <= 1900 else '1901-1945' if year <= 1945 else '1946-1999' if year <= 1999 else '2000+'


def select_pilot(rows: list[dict], count=100) -> list[dict]:
    """One from each stratum then stable hash fill, never random re-sampling on resume."""
    rows = [r for r in rows if not r.get('features')]
    groups = {}
    order = lambda r: text_sha(VERSION + ':' + r['work_id'])
    for row in sorted(rows, key=order):
        key = (row.get('original_language') or 'unknown', year_bin(row.get('first_publication_year')), row.get('work_kind') or 'unknown')
        groups.setdefault(key, []).append(row)
    first = sorted((g[0] for g in groups.values()), key=order)[:count]
    selected = {r['work_id'] for r in first}
    first.extend(r for r in sorted(rows, key=order) if r['work_id'] not in selected and len(first) < count)
    return first


def verified_entity(work: SourceRecord) -> dict:
    payload = Path(work.raw_path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != work.raw_sha256:
        raise ValueError('target raw hash mismatch')
    entity = json.loads(payload).get('entities', {}).get(work.source_id)
    if work.source != 'wikidata' or work.record_kind != 'work' or not entity or entity.get('id') != work.source_id:
        raise ValueError('target raw identity missing')
    return entity


def wikipedia_evidence(work: SourceRecord, old_version: str, canonical: str, page: dict,
                       artifact, language: str, denied: set[str], existing: set[str], phase: str):
    entity = verified_entity(work)
    if page.get('pageprops', {}).get('wikibase_item') != work.source_id or page.get('ns') != 0 or 'disambiguation' in page.get('pageprops', {}):
        raise ValueError('Wikipedia raw identity mismatch or disambiguation')
    if language + 'wiki' not in entity.get('sitelinks', {}):
        raise ValueError('Wikipedia target sitelink identity missing')
    payload = Path(artifact.path).read_bytes()
    if hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise ValueError('Wikipedia raw hash mismatch')
    if urlparse(artifact.source_url).hostname != language + '.wikipedia.org':
        raise ValueError('Wikipedia raw host identity mismatch')
    pages = json.loads(payload).get('query', {}).get('pages', [])
    pages = list(pages.values()) if isinstance(pages, dict) else pages
    if page not in pages:
        raise ValueError('Wikipedia page does not equal saved raw page')
    revisions = page.get('revisions', [])
    if len(revisions) != 1 or not isinstance(revisions[0].get('revid'), int) or revisions[0]['revid'] <= 0:
        raise ValueError('immutable revision identity missing')
    revision = revisions[0]
    slot = revision.get('slots', {}).get('main', {})
    if slot.get('contentmodel', 'wikitext') != 'wikitext':
        raise ValueError('unsupported article content model')
    text = slot.get('content', slot.get('*', ''))
    # These imports may demand additional credits beyond the article history.
    # We do not silently assert those extra requirements have been discharged.
    import_notices = re.compile(r'cc[ _-]?notice|source[ _-]?attribution|attribution|copyright|copyvio|copypaste|copy[ _-]?paste|1911|eb1911|dnb|public[ _-]?domain|pd[ _-]?notice|imported[ _-]?text|incorporates[ _-]?text|text[ _-]?incorporated', re.I)
    imported = any(import_notices.search(str(t.name)) for t in mwparserfromhell.parse(text).filter_templates(recursive=True))
    revid = revision['revid']
    url = f'https://{language}.wikipedia.org/w/index.php?oldid={revid}'
    history = f'https://{language}.wikipedia.org/w/index.php?title={quote(page["title"], safe="")}&action=history'
    registry = RightsRegistry.default()
    policy = registry.policies['wikipedia-screened-prose']
    identity = {'wikidata_work_id': work.source_id, 'pageprops_wikibase_item': page['pageprops']['wikibase_item'],
                'page_id': page['pageid'], 'revision_id': revid, 'language': language,
                'target_raw_path': work.raw_path, 'target_raw_sha256': work.raw_sha256,
                'target_raw_locator': 'entities.' + work.source_id}
    evidence, ledger, outcomes = [], [], {}
    for section in SECTIONS.get(language, ['lead']):
        try:
            original = section_text(text, section)
        except ValueError:
            continue
        screen = screen_section(original, article_wikitext=text, factual_titles=[page['title'], *(t.value for t in work.titles)])
        outcome = {'screening': asdict(screen), 'status': 'held'}
        if imported:
            outcome['reason'] = 'additional_attribution_or_import_notice'
        elif screen.article_blocked:
            outcome['reason'] = 'article_rights_notice'
        elif screen.rejected_paragraphs:
            outcome['reason'] = 'section_contains_rejected_paragraphs'
        elif not screen.text or len(screen.text) < 20:
            outcome['reason'] = 'empty_or_insufficient_section'
        elif text_sha(screen.text) in denied:
            outcome['reason'] = 'previously_denied_text'
        elif text_sha(screen.text) in existing:
            outcome['reason'] = 'already_in_baseline'
        else:
            kind = 'note' if section == 'lead' else 'synopsis'
            permitted = {u: registry.permits(policy.policy_id, kind, u, set(FULFILLED)) for u in USAGES}
            if not all(permitted.values()):
                outcome['reason'] = 'usage_conditions_unfulfilled'
            else:
                locator = f'revision:{revid};section:{section};screen:{SCREEN_VERSION};whole-section-clean:{VERSION}'
                attribution = {'credit': f'Wikipedia contributors: {page["title"]}', 'permalink': url,
                    'history_url': history, 'changes': 'selected complete clean section; references removed; wiki markup converted to plain text',
                    'raw_path': artifact.path, 'raw_sha256': artifact.sha256, 'page_id': page['pageid'], 'revision_id': revid,
                    'identity_proof': identity, 'fulfilled_obligations': FULFILLED,
                    'screening_limitation': 'Automatic screening is not human review or legal certification; unmarked third-party problems may remain.'}
                ev = Evidence(evidence_id=f'wp:{language}:{revid}:{section}:{VERSION}', kind=kind, text=screen.text,
                    source_url=url, rights_policy_id=policy.policy_id, llm_input_allowed=True, language=language, locator=locator,
                    license=policy.license, license_url='https://creativecommons.org/licenses/by-sa/4.0/', attribution=attribution,
                    inherited_restrictions=FULFILLED)
                evidence.append(ev)
                ledger.append({'work_id': canonical, 'evidence_id': ev.evidence_id, 'source_url': url,
                    'raw_path': artifact.path, 'raw_sha256': artifact.sha256, 'locator': locator,
                    'target_source': work.source, 'target_source_id': work.source_id, 'target_old_version_id': old_version,
                    'rights_policy_id': policy.policy_id, 'usages': policy.usages, 'fulfilled_obligations': FULFILLED,
                    'identity_proof': identity, 'screen_version': SCREEN_VERSION, 'screening': asdict(screen),
                    'usage_permitted': permitted, 'license': ev.license, 'license_url': ev.license_url,
                    'evidence_text_sha256': text_sha(ev.text), 'candidate_phase': phase})
                outcome.update(status='accepted', evidence_id=ev.evidence_id)
        outcomes[section] = outcome
    return evidence, ledger, outcomes


def append_proposal(work: SourceRecord, original: WorkInput, evidence: list[Evidence]):
    ids = [e.evidence_id for e in evidence]
    if len(set(ids)) != len(ids) or set(ids) & {e.evidence_id for e in original.evidence}:
        raise ValueError('duplicate evidence identity')
    return (work.model_copy(update={'evidence': [*work.evidence, *evidence]}),
            original.model_copy(update={'evidence': [*original.evidence, *evidence]}))


def assess_ndl_identity(work: SourceRecord, edition: SourceRecord) -> dict:
    """Work-declared edition ISBN AND complete independently identified authors.

    This is an edition-to-work metadata candidate, never original-expression text.
    Names or similar titles cannot fulfill any missing authority condition.
    """
    entity = verified_entity(work)
    claims = entity.get('claims', {})
    def values(property_id):
        return [c.get('mainsnak', {}).get('datavalue', {}).get('value') for c in claims.get(property_id, []) if c.get('rank') != 'deprecated' and c.get('mainsnak', {}).get('snaktype') == 'value']
    isbns = {n for p in ['P212', 'P957'] for v in values(p) if isinstance(v, str) and (n := normalize_isbn(v))}
    observed = {n for i in edition.identifiers if i.source == 'isbn' and i.scope == 'edition' and (n := normalize_isbn(i.value))}
    reasons, proofs, expected_authorities = [], [], set()
    if any(isinstance(v, dict) and v.get('id') == 'Q3331189' for v in values('P31')) or values('P629'):
        reasons.append('target_wikidata_record_has_edition_scope')
    # Re-normalize the exact XML record before consulting any normalized identity.
    # This also rejects injected work_refs, authorities, ISBNs, and subject prose.
    try:
        edition_raw = Path(edition.raw_path).read_bytes()
        if hashlib.sha256(edition_raw).hexdigest() != edition.raw_sha256:
            raise ValueError('NDL raw hash mismatch')
        tree = ET.fromstring(edition_raw)
        artifact = RawArtifact(edition.raw_sha256, edition.raw_path, len(edition_raw), edition.retrieved_at, edition.source_url)
        matches = []
        for index, element in enumerate(tree.findall(f'.//{{{SRU}}}recordData'), 1):
            if len(element) == 0 and element.text and element.text.lstrip().startswith('<'):
                element = ET.fromstring(element.text)
            normalized = NDLAdapter(None, approved_providers={'iss-ndl-opac', 'iss-ndl-opac-national'})._normalize(element, artifact, edition.provider_id, locator=f'records[{index}]/recordData')
            if normalized.source_id == edition.source_id:
                matches.append(normalized)
        if len(matches) != 1 or matches[0].model_dump() != edition.model_dump():
            reasons.append('normalized_ndl_record_differs_from_raw')
    except (ET.ParseError, ValueError, OSError):
        reasons.append('normalized_ndl_record_differs_from_raw')
    if not isbns & observed:
        reasons.append('isbn_not_explicitly_in_work_raw')
    authors = {v['id'] for v in values('P50') if isinstance(v, dict) and 'id' in v}
    normalized_authors = {i.value for c in work.contributors if c.role == 'author' for i in c.identifiers if i.source == 'wikidata' and i.scope == 'agent'}
    complete = bool(authors) and authors == normalized_authors
    for qid in sorted(authors):
        provenance = work.extra.get('linked_label_provenance', {}).get(qid, {})
        if not provenance.get('raw_path') or not provenance.get('raw_sha256'):
            complete = False
            continue
        raw = Path(provenance['raw_path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != provenance['raw_sha256']:
            raise ValueError('author authority raw hash mismatch')
        author = json.loads(raw).get('entities', {}).get(qid, {})
        ids = {c.get('mainsnak', {}).get('datavalue', {}).get('value') for c in author.get('claims', {}).get('P349', []) if c.get('rank') != 'deprecated' and c.get('mainsnak', {}).get('snaktype') == 'value'}
        ids = {v for v in ids if isinstance(v, str) and re.fullmatch(r'\d+', v)}
        if author.get('id') != qid or not ids:
            complete = False
        expected_authorities.update(ids)
        proofs.append({'wikidata_author_id': qid, 'ndl_authority_ids': sorted(ids), 'raw_path': provenance['raw_path'], 'raw_sha256': provenance['raw_sha256'], 'locator': 'entities.' + qid + '.claims.P349'})
    if not complete:
        reasons.append('complete_author_authority_missing')
    # Official NDL URI specification pairs authority-info and name-entity URIs
    # using the identical authority ID; current SRU uses auth/entity/.
    actual_authorities = {i.value.rstrip('/').rsplit('/', 1)[-1] for c in edition.contributors if c.role == 'author' for i in c.identifiers if i.source == 'ndl-authority' and re.fullmatch(r'https?://id\.ndl\.go\.jp/auth/(?:ndlna|entity)/\d+/?', i.value)}
    if not actual_authorities or actual_authorities != expected_authorities:
        reasons.append('complete_author_authority_not_matched')
    if edition.volume and (not work.volume or work.volume != edition.volume) or work.volume and work.volume != edition.volume:
        reasons.append('volume_scope_not_matched')
    if edition.source != 'ndl' or edition.provider_id not in {'iss-ndl-opac', 'iss-ndl-opac-national'} or edition.record_kind != 'edition':
        reasons.append('unapproved_provider_or_record_scope')
    return {'accepted': not reasons, 'reasons': reasons, 'matched_isbns': sorted(isbns & observed), 'author_authority_proofs': proofs,
            'expected_authorities': sorted(expected_authorities), 'observed_authorities': sorted(actual_authorities),
            'work_id': work.source_id, 'edition_id': edition.source_id, 'scope': 'edition bibliographic/subject metadata only; not original-expression style or novel prose'}
