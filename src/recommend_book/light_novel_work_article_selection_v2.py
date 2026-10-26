"""Select one exact admitted work page inside an unchanged cached API body.

The complete response remains the authenticated artifact. Selection is not a
new response, an identity crosswalk, a Plot admission, or an adaptation policy.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .light_novel_work_admission_v3 import verify_metadata


RECIPE = 'exact-work-page-in-authenticated-api-envelope-v2'
MAX_PAGES = 100
MAX_BYTES = 20_000_000


def _pointer_key(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def select_authenticated_page(metadata, artifact, language):
    """Return the actual unique page and a batch receipt, or None for v1 sole pages."""
    entity = verify_metadata(metadata)
    if language not in ('ja', 'en'):
        raise ValueError('Work article selection language differs')
    title = entity.get('sitelinks', {}).get(language + 'wiki', {}).get('title')
    if not isinstance(title, str) or not title:
        raise ValueError('Work article actual sitelink absent')
    pin = artifact.get('sha256')
    if not isinstance(pin, str) or not re.fullmatch(r'[0-9a-f]{64}', pin):
        raise ValueError('Work article full raw SHA differs')
    path = Path(artifact['path']).resolve()
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError('Work article full raw SHA or size differs')
    payload = json.loads(raw)
    query = payload.get('query') if isinstance(payload, dict) else None
    pages = query.get('pages') if isinstance(query, dict) else None
    if isinstance(pages, list):
        indexed = [(str(i), page) for i, page in enumerate(pages)]
    elif isinstance(pages, dict):
        indexed = list(pages.items())
    else:
        raise ValueError('Work article native page container differs')
    if not 1 <= len(indexed) <= MAX_PAGES or any(not isinstance(page, dict) for _, page in indexed):
        raise ValueError('Work article finite native pages required')
    qid = metadata['id']
    owners = [(key, page) for key, page in indexed
              if page.get('title') == title or
              isinstance(page.get('pageprops'), dict) and page['pageprops'].get('wikibase_item') == qid]
    if len(owners) != 1:
        raise ValueError('Work article title or entity owner absent or ambiguous')
    key, page = owners[0]
    pageid = page.get('pageid')
    props = page.get('pageprops', {})
    if (page.get('title') != title or not isinstance(props, dict) or props.get('wikibase_item') != qid
            or type(page.get('ns')) is not int or page['ns'] != 0
            or type(pageid) is not int or pageid <= 0 or 'disambiguation' in props
            or any(flag in page for flag in ('missing', 'invalid', 'redirect'))
            or sum(p.get('pageid') == pageid for _, p in indexed) != 1
            or isinstance(pages, dict) and key != str(pageid)):
        raise ValueError('Work article exact page identity differs')
    revisions = page.get('revisions')
    if (not isinstance(revisions, list) or len(revisions) != 1 or not isinstance(revisions[0], dict)
            or type(revisions[0].get('revid')) is not int or revisions[0]['revid'] <= 0):
        raise ValueError('Work article exact single revision required')
    slots = revisions[0].get('slots')
    slot = slots.get('main') if isinstance(slots, dict) else None
    if not isinstance(slot, dict):
        raise ValueError('Work article revision main slot absent')
    content = slot.get('content', slot.get('*', ''))
    if slot.get('contentmodel', 'wikitext') != 'wikitext' or not isinstance(content, str) or not content:
        raise ValueError('Work article revision wikitext absent or different')
    if len(indexed) == 1:
        return page, None
    locator = '/query/pages/' + _pointer_key(key)
    return page, {'recipe': RECIPE, 'raw_path': str(path), 'raw_sha256': pin,
                  'raw_page_count': len(indexed), 'work_id': 'lnw:wd:' + qid,
                  'page_id': pageid, 'page_title': title, 'wikibase_item': qid,
                  'revision_id': revisions[0]['revid'], 'raw_page_locator': locator,
                  'raw_revision_locator': locator + '/revisions/0'}


def validate_authenticated_selection(metadata, page, artifact, language, selection):
    """Compare the complete persisted selector and supplied page with raw replay."""
    actual, expected = select_authenticated_page(metadata, artifact, language)
    if page != actual or selection != expected:
        raise ValueError('Work article persisted page selection differs from full raw replay')
    return actual
