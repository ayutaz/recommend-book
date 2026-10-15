"""Versioned complete-paragraph selection; screening is not legal certification.

This recipe selects exactly the existing screen's accepted paragraphs. It never
edits a rejected paragraph or removes an article-level rights/import notice.
Historical whole-section proposals and evidence identities remain unchanged.
"""
from dataclasses import asdict
from types import SimpleNamespace
from urllib.parse import quote

import mwparserfromhell

from recommend_book.autonomous_enrichment import FULFILLED, verified_entity, wikipedia_evidence, text_sha
from recommend_book.ingestion.wikipedia import section_text
from recommend_book.models import Evidence
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.wikipedia_prose import SCREEN_VERSION, screen_section

VERSION = 'screened-paragraphs-v1'
CHANGES = 'selected complete screened paragraphs; rejected paragraphs omitted; references removed; wiki markup converted to plain text'
GENRE_VERSION = 'screened-infobox-genre-v1'
GENRE_CHANGES = 'selected entire article-level infobox genre parameter; citation references removed; wiki link markup converted to plain text; no taxonomy mapping'
GENRE_PREFIX = 'Article-level declared genre (not exhaustive story annotation): '


def selection_proof(original, screen):
    return {'recipe_version': VERSION, 'screen_version': SCREEN_VERSION,
            'source_section_sha256': text_sha(original), 'selected_text_sha256': text_sha(screen.text),
            'accepted_paragraphs': screen.accepted_paragraphs,
            'rejected_paragraphs': screen.rejected_paragraphs,
            'article_blocked': screen.article_blocked}


def locator(revision_id, section):
    return f'revision:{revision_id};section:{section};screen:{SCREEN_VERSION};screened-paragraphs:{VERSION}'


def build_evidence(record, page, artifact, language='ja'):
    """Return (evidence, provenance ledger rows, section outcomes), without I/O writes.

    Exact target/raw/page checks and source-wide notice rejection are inherited
    from the original recipe. Rights flags remain proposals until UsageGuard
    independently reproduces each fully bound input in a new namespace.
    """
    entity = verified_entity(record)
    if entity.get('sitelinks', {}).get(language + 'wiki', {}).get('title') != page.get('title'):
        raise ValueError('Wikipedia exact target sitelink title differs')
    _, _, outcomes = wikipedia_evidence(record, '', record.source_id, page, artifact,
                                         language, set(), set(), VERSION)
    registry = RightsRegistry.default()
    policy = registry.policies['wikipedia-screened-prose']
    revision = page['revisions'][0]
    revision_id = revision['revid']
    slot = revision['slots']['main']
    text = slot.get('content', slot.get('*', ''))
    url = f'https://{language}.wikipedia.org/w/index.php?oldid={revision_id}'
    history = f'https://{language}.wikipedia.org/w/index.php?title={quote(page["title"], safe="")}&action=history'
    identity = {'wikidata_work_id': record.source_id, 'pageprops_wikibase_item': page['pageprops']['wikibase_item'],
                'page_id': page['pageid'], 'revision_id': revision_id, 'language': language,
                'target_raw_path': record.raw_path, 'target_raw_sha256': record.raw_sha256,
                'target_raw_locator': 'entities.' + record.source_id}
    evidence, ledger = [], []
    for section, outcome in outcomes.items():
        screening = outcome['screening']
        if outcome.get('reason') in ('additional_attribution_or_import_notice', 'article_rights_notice') or screening['article_blocked']:
            continue
        if not screening['text'] or len(screening['text']) < 20:
            outcome.update(status='held', reason='empty_or_insufficient_screened_paragraphs')
            continue
        kind = 'note' if section == 'lead' else 'synopsis'
        permitted = {usage: registry.permits(policy.policy_id, kind, usage, set(FULFILLED)) for usage in USAGES}
        if not all(permitted.values()):
            outcome.update(status='held', reason='usage_conditions_unfulfilled')
            continue
        # Use the existing screen's exact text and rejection index/reason data.
        from recommend_book.wikipedia_prose import ScreenResult
        screen = ScreenResult(**screening)
        selection = selection_proof(section_text(text, section), screen)
        attribution = {'credit': 'Wikipedia contributors: ' + page['title'], 'permalink': url,
                       'history_url': history, 'changes': CHANGES, 'raw_path': artifact.path,
                       'raw_sha256': artifact.sha256, 'page_id': page['pageid'], 'revision_id': revision_id,
                       'identity_proof': identity, 'fulfilled_obligations': list(FULFILLED),
                       'paragraph_selection': selection,
                       'screening_limitation': 'Automatic screening is not human review or legal certification; unmarked third-party problems may remain.'}
        ev = Evidence(evidence_id=f'wp:{language}:{revision_id}:{section}:{VERSION}', kind=kind,
                      text=screen.text, source_url=url, rights_policy_id=policy.policy_id,
                      llm_input_allowed=True, language=language, locator=locator(revision_id, section),
                      license=policy.license, license_url='https://creativecommons.org/licenses/by-sa/4.0/',
                      attribution=attribution, inherited_restrictions=list(FULFILLED))
        evidence.append(ev)
        ledger.append({'recipe_version': VERSION, 'evidence_id': ev.evidence_id, 'locator': ev.locator,
                       'raw_path': artifact.path, 'raw_sha256': artifact.sha256,
                       'identity_proof': identity, 'paragraph_selection': selection,
                       'fulfilled_obligations': list(FULFILLED), 'usage_permitted': permitted,
                       'screening': asdict(screen), 'evidence_text_sha256': text_sha(ev.text)})
        outcome.update(status='accepted', evidence_id=ev.evidence_id, recipe_version=VERSION)
        outcome.pop('reason', None)
    return evidence, ledger, outcomes


def _infobox_genre(text):
    """Select a single top-level header in an explicit novel infobox context.

    A Novel block establishes only article context, never original-work identity
    or permission to transfer adaptation plot. Callers bind the original work
    separately. Complex/unmarked third-party text remains uncertain.
    """
    code = mwparserfromhell.parse(text)
    top = code.filter_templates(recursive=False)
    name = lambda template: str(template.name).strip().casefold()
    headers = [template for template in top if name(template) == 'infobox animanga/header']
    if len(headers) != 1 or sum(name(template) == 'infobox animanga/header' for template in code.filter_templates(recursive=True)) != 1:
        raise ValueError('one unambiguous top-level animanga header required')
    header = headers[0]
    positions = {key: [index for index, template in enumerate(top) if name(template) == 'infobox animanga/' + key] for key in ('header', 'novel', 'footer')}
    if len(positions['novel']) != 1 or len(positions['footer']) != 1 or not positions['header'][0] < positions['novel'][0] < positions['footer'][0]:
        raise ValueError('unambiguous novel context before infobox footer required')
    params = [parameter for parameter in header.params if str(parameter.name).strip() == 'ジャンル']
    if len(params) != 1:
        raise ValueError('one complete genre parameter required')
    original = str(params[0].value)
    selected = mwparserfromhell.parse(original)
    for tag in list(selected.filter_tags(recursive=True)):
        if str(tag.tag).strip().casefold() == 'ref':
            try:
                selected.remove(tag, recursive=True)
            except ValueError:
                pass
    # References can contain citation templates/URLs. Nothing outside complete
    # ref tags is silently stripped or truncated to obtain an acceptable value.
    if any(not isinstance(node, (mwparserfromhell.nodes.Text, mwparserfromhell.nodes.Wikilink)) for node in selected.nodes):
        raise ValueError('genre permits only plain text and wiki links after references')
    screened = screen_section(str(selected), article_wikitext=text)
    if screened.article_blocked or screened.rejected_paragraphs or not screened.text or screened.accepted_paragraphs != 1:
        raise ValueError('genre contains unsupported, quoted, or complex markup')
    return screened.text, {'recipe_version': GENRE_VERSION, 'template': 'Infobox animanga/Header',
                           'parameter': 'ジャンル', 'scope': 'article_level_work_assertion',
                           'source_parameter_sha256': text_sha(original), 'source_header_sha256': text_sha(str(header)),
                           'infobox_context_sha256': text_sha('\n'.join(str(template) for template in top if name(template).startswith('infobox animanga/'))),
                           'selected_value_sha256': text_sha(screened.text),
                           'novel_context': 'single top-level Infobox animanga/Novel before footer; original-work correspondence separately required'}


def build_infobox_genre_evidence(record, page, artifact, language='ja'):
    """Return (proposed note evidence, ledger rows, outcomes), without writes.

    Genre is a licensed article assertion, not independent classification gold.
    It is deliberately not an expression excerpt or an exhaustive story label.
    """
    # Reuse the strict raw/QID/sitelink/source-wide notice checks; no paragraph
    # evidence or its permission is repurposed as genre evidence.
    _, _, section_outcomes = build_evidence(record, page, artifact, language)
    if any(outcome.get('reason') in ('additional_attribution_or_import_notice', 'article_rights_notice') for outcome in section_outcomes.values()):
        return [], [], {'genre': {'status': 'held', 'reason': 'article_rights_or_import_notice'}}
    revision = page['revisions'][0]
    revision_id = revision['revid']
    slot = revision['slots']['main']
    text = slot.get('content', slot.get('*', ''))
    try:
        value, proof = _infobox_genre(text)
    except ValueError as exc:
        return [], [], {'genre': {'status': 'held', 'reason': str(exc)}}
    registry = RightsRegistry.default()
    policy = registry.policies['wikipedia-screened-prose']
    permitted = {usage: registry.permits(policy.policy_id, 'note', usage, set(FULFILLED)) for usage in USAGES}
    if not all(permitted.values()):
        return [], [], {'genre': {'status': 'held', 'reason': 'usage_conditions_unfulfilled'}}
    url = f'https://{language}.wikipedia.org/w/index.php?oldid={revision_id}'
    history = f'https://{language}.wikipedia.org/w/index.php?title={quote(page["title"], safe="")}&action=history'
    identity = {'wikidata_work_id': record.source_id, 'pageprops_wikibase_item': page['pageprops']['wikibase_item'],
                'page_id': page['pageid'], 'revision_id': revision_id, 'language': language,
                'target_raw_path': record.raw_path, 'target_raw_sha256': record.raw_sha256,
                'target_raw_locator': 'entities.' + record.source_id}
    attribution = {'credit': 'Wikipedia contributors: ' + page['title'], 'permalink': url, 'history_url': history,
                   'changes': GENRE_CHANGES, 'raw_path': artifact.path, 'raw_sha256': artifact.sha256,
                   'page_id': page['pageid'], 'revision_id': revision_id, 'identity_proof': identity,
                   'fulfilled_obligations': list(FULFILLED), 'genre_selection': proof,
                   'screening_limitation': 'Automatic screening is not human review or legal certification; unmarked third-party problems may remain. Article genre is not exhaustive annotation or a mapping to target labels.'}
    ev = Evidence(evidence_id=f'wp:{language}:{revision_id}:infobox-genre:{GENRE_VERSION}', kind='note',
                  text=GENRE_PREFIX + value, source_url=url, rights_policy_id=policy.policy_id,
                  llm_input_allowed=True, language=language,
                  locator=f'revision:{revision_id};template:Infobox animanga/Header;parameter:ジャンル;recipe:{GENRE_VERSION}',
                  license=policy.license, license_url='https://creativecommons.org/licenses/by-sa/4.0/',
                  attribution=attribution, inherited_restrictions=list(FULFILLED))
    row = {'recipe_version': GENRE_VERSION, 'evidence_id': ev.evidence_id, 'locator': ev.locator,
           'raw_path': artifact.path, 'raw_sha256': artifact.sha256, 'identity_proof': identity,
           'genre_selection': proof, 'fulfilled_obligations': list(FULFILLED), 'usage_permitted': permitted,
           'evidence_text_sha256': text_sha(ev.text)}
    return [ev], [row], {'genre': {'status': 'accepted', 'evidence_id': ev.evidence_id, 'recipe_version': GENRE_VERSION}}


def verify_infobox_genre(evidence, record, page):
    """Independently regenerate the full typed evidence from bound raw bytes."""
    from recommend_book.jobs import canonical_json
    artifact = SimpleNamespace(path=evidence.attribution['raw_path'], sha256=evidence.attribution['raw_sha256'],
                               source_url=f'https://{evidence.language}.wikipedia.org/w/api.php')
    expected, _, _ = build_infobox_genre_evidence(record, page, artifact, evidence.language)
    if len(expected) != 1 or canonical_json(expected[0].model_dump(mode='json')) != canonical_json(evidence.model_dump(mode='json')):
        raise ValueError('exact infobox genre assertion/provenance not reproduced')
