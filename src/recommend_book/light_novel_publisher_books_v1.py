"""Replay KADOKAWA book search metadata for private NDL edition matching.

The official imprint is a reference assertion, not a new work or series.
No publisher descriptions or images are returned. Display metadata must be
independently reproduced from an eligible NDL-created record by the caller.
"""
from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import re


KADOKAWA_LABELS = {
    '330': '電撃文庫', '55': 'MF文庫J', '344': 'ファンタジア文庫',
    '154': '角川スニーカー文庫',
    '53': 'MFブックス', '104': 'カドカワBOOKS', '197': 'ファミ通文庫',
    '114': '角川ビーンズ文庫', '178': 'ビーズログ文庫',
}
_ENDPOINT = 'https://www.kadokawa.co.jp/product/search/'
_QUERY_KEYS = {'pageno', 'pageno_book', 'pageno_media', 'size', 'label', 'releaseDate'}


def _checked_bytes(path: Path, sha256: str) -> bytes:
    raw = path.read_bytes()
    if len(raw) > 10_000_000:
        raise ValueError('publisher artifact exceeds bounded size')
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise ValueError('publisher artifact SHA mismatch')
    return raw


def _credits(value: str) -> list[dict]:
    result = []
    for block in re.findall(r'<li\b[^>]*>(.*?)</li>', value or '', re.S):
        text = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', block)).split())
        parts = text.split(' ', 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            result.append({'role': parts[0], 'name': parts[1]})
    return result


def verify_kadokawa_books(manifest_path: str | Path, sha256: str) -> list[dict]:
    """Reproduce metadata from a hash-pinned, possibly partial search frame.

    Manifest v1 has ``schema_version``, ``rights_scope``, and ``pages``.
    Each page is an original POST receipt with its exact query, relative raw
    path and SHA. Total/closure claims remain separate from these assertions.
    ISBN values, including null and invalid values, are preserved for callers
    to hold; an ISBN alone is never a work or series identity assertion.
    """
    path = Path(manifest_path).resolve()
    manifest = json.loads(_checked_bytes(path, sha256))
    if (manifest.get('schema_version') != 'kadokawa-publisher-books-v1'
            or manifest.get('rights_scope') != 'reference_only_bibliography'):
        raise ValueError('publisher book manifest schema/rights differs')
    pages = manifest.get('pages')
    if not isinstance(pages, list) or len(pages) > 5_000:
        raise ValueError('publisher book pages must be a bounded list')
    root = path.parent
    rows = {}
    seen_pages = set()
    for receipt in pages:
        if (receipt.get('http_status') != 200 or receipt.get('http_method') != 'POST'
                or receipt.get('source_url') != _ENDPOINT
                or receipt.get('final_url') != _ENDPOINT):
            raise ValueError('publisher book endpoint/status differs')
        query = receipt.get('request_data', {})
        label = str(query.get('label', ''))
        page = str(query.get('pageno_book', ''))
        if (set(query) != _QUERY_KEYS or label not in KADOKAWA_LABELS
                or query.get('releaseDate') != '1' or query.get('size') != '20'
                or not re.fullmatch(r'[1-9][0-9]*', page)
                or int(page) > 5_000
                or any(query.get(key) != page for key in ('pageno', 'pageno_media'))):
            raise ValueError('publisher book query does not reproduce declared scope')
        page_key = (label, page)
        if page_key in seen_pages:
            raise ValueError('duplicate page in publisher book manifest')
        seen_pages.add(page_key)
        raw_path = (root / receipt['raw_path']).resolve()
        if not raw_path.is_relative_to(root):
            raise ValueError('publisher raw path escapes manifest root')
        payload = json.loads(_checked_bytes(raw_path, receipt['sha256']))
        items = payload.get('result', {}).get('book')
        total = payload.get('count_raw', {}).get('book')
        if (not isinstance(items, list) or len(items) > 20
                or type(total) is not int or total < 0 or int(page) > max(1, (total + 19) // 20)):
            raise ValueError('publisher book payload count/shape differs')
        for position, item in enumerate(items):
            code = str(item.get('itemCode', ''))
            if not re.fullmatch(r'[0-9]{12}', code):
                raise ValueError('publisher book item code differs')
            if str(item.get('label_id')) != label:
                raise ValueError('publisher book item label differs from official query')
            title = item.get('title')
            if not isinstance(title, str) or not title.strip():
                raise ValueError('publisher book title missing')
            forms = payload.get('product_form')
            form = forms.get(str(item.get('product_form_id'))) if isinstance(forms, dict) else None
            form_name = form.get('name') if isinstance(form, dict) else None
            row = {
                'publisher_book_id': 'kadokawa:' + code,
                'reference_id': 'publisher-edition:kadokawa:' + code,
                'kind': 'edition', 'official_item_code': code,
                'source_url': 'https://www.kadokawa.co.jp/product/' + code + '/',
                'title': title, 'isbn': item.get('isbn'),
                'credits': _credits(item.get('disp_author_name')),
                'publisher': 'KADOKAWA', 'label': KADOKAWA_LABELS[label],
                # Preserve the legacy value for old proofs while distinguishing
                # site ownership from an absent individual publisher literal.
                'catalogue_owner': 'KADOKAWA', 'publisher_field_basis': 'catalogue_owner_context',
                'individual_publisher': None,
                'label_scope': KADOKAWA_LABELS[label], 'official_label_id': int(label),
                'official_label_site_id': item.get('label_site_id'),
                'release_date': item.get('release_date'),
                'product_form_id': item.get('product_form_id'),
                'official_product_form_name': form_name if isinstance(form_name, str) else None,
                'official_series_id': None, 'explicit_series_membership': None,
                'identity_to_catalogue': 'unassessed',
                'evidence': {'source_url': _ENDPOINT, 'http_method': 'POST',
                    'request_data': dict(query), 'source_sha256': receipt['sha256'],
                    'retrieved_at': receipt.get('retrieved_at'),
                    'raw_path': str(raw_path), 'json_pointer': f'/result/book/{position}'},
                'rights_scope': 'reference_only_bibliography',
            }
            previous = rows.get(code)
            if previous is not None:
                if {k: v for k, v in previous.items() if k != 'evidence'} != {
                        k: v for k, v in row.items() if k != 'evidence'}:
                    raise ValueError('conflicting duplicate publisher book metadata')
            else:
                rows[code] = row
    return list(rows.values())
