"""Compose the existing world catalogue and a separately scoped light-novel store.

The world database and its immutable proofs are not migrated. Typed series,
works and editions remain distinct, including in reading-state exports.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re


_QID = re.compile(r'Q[1-9][0-9]*')
_LIGHT = re.compile(r'(?:ln[sw]:wd:Q[1-9][0-9]*|ln[se]:ndl:[A-Za-z0-9._-]+)')
_KINDS = {'series', 'work', 'edition'}
_STATES = {'unknown', 'unread', 'reading', 'read'}


def _light_id(key):
    if not isinstance(key, str):
        raise ValueError('invalid catalogue reference')
    if _LIGHT.fullmatch(key):
        return True
    if _QID.fullmatch(key):
        return False
    raise ValueError('invalid catalogue reference')


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _facets(*values):
    merged = {'languages': {}, 'genres': {}}
    for value in values:
        for field, key in [('languages', 'value'), ('genres', 'qid')]:
            for item in value.get(field, []):
                identity = item[key]
                if identity not in merged[field]:
                    merged[field][identity] = dict(item, count=0)
                merged[field][identity]['count'] += item.get('count', 0)
    return {field: sorted(items.values(), key=lambda x: (-x['count'], str(x.get('qid', x.get('value')))))
            for field, items in merged.items()}


class UnifiedCatalogue:
    def __init__(self, world, light_novels):
        self.world, self.light_novels = world, light_novels
        self.db_path = world.db_path

    def _world_qids(self):
        with self.world._connect() as db:
            return {row[0] for row in db.execute('SELECT qid FROM works WHERE display_allowed=1')}

    def search_world_catalogue(self, **kwargs):
        return self.world.search_catalogue(**kwargs)

    @staticmethod
    def _window(search, first, offset, length, filters):
        result = []
        while length > 0:
            page, within = divmod(offset, 100)
            block = first if page == 0 else search(page=page + 1, page_size=100, **filters)
            chunk = block['items'][within:within + length]
            result.extend(chunk)
            if not chunk:
                break
            length -= len(chunk)
            offset += len(chunk)
        return result

    def search_catalogue(self, *, family='all', entry_kind=None, page=1, page_size=30, **filters):
        if family not in ('all', 'world', 'light_novel') or entry_kind not in (None, '', *_KINDS):
            raise ValueError('invalid catalogue family or kind')
        if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
            raise ValueError('invalid pagination')
        partitions = []
        if family != 'world':
            args = dict(filters, entry_kind=entry_kind)
            if family == 'all':
                args['exclude_work_qids'] = self._world_qids()
            search = self.light_novels.search_catalogue
            first = search(page=1, page_size=100, **args)
            partitions.append((search, first, args))
        if family != 'light_novel' and entry_kind in (None, '', 'work'):
            search = self.world.search_catalogue
            first = search(page=1, page_size=100, **filters)
            partitions.append((search, first, filters))
        total = sum(first['total'] for _, first, _ in partitions)
        offset, remaining, items = (page-1)*page_size, page_size, []
        for search, first, args in partitions:
            if offset >= first['total']:
                offset -= first['total']
                continue
            take = min(remaining, first['total'] - offset)
            items.extend(self._window(search, first, offset, take, args))
            remaining -= take
            offset = 0
            if not remaining:
                break
        return {'items': items, 'total': total, 'page': page, 'page_size': page_size,
                'pages': math.ceil(total / page_size), 'filters': dict(filters, family=family, entry_kind=entry_kind),
                'count_unit': 'catalogue_entries', 'partition_order': ['light_novel', 'world']}

    def work_detail(self, entry_id):
        return (self.light_novels if _light_id(entry_id) else self.world).work_detail(entry_id)

    def similar_works(self, entry_id, **kwargs):
        return (self.light_novels if _light_id(entry_id) else self.world).similar_works(entry_id, **kwargs)

    def set_read_status(self, entry_id, status):
        return (self.light_novels if _light_id(entry_id) else self.world).set_read_status(entry_id, status)

    def catalogue_stats(self):
        world = self.world.catalogue_stats()
        light = self.light_novels.catalogue_stats()
        return {**world, 'light_novel_series': light['series'],
                'light_novel_featured_series': light['content_feature_series'],
                'light_novel_works': light['individual_works'], 'light_novel_editions': light['editions']}

    def catalogue_facets(self):
        return _facets(self.world.catalogue_facets(), self.light_novels.catalogue_facets())

    def preference_facets(self):
        # The existing taxonomy is already the shared, public choice set.
        return {**self.world.preference_facets(), **self.catalogue_facets()}

    def recommend_preferences(self, *, liked_qids=None, liked_entry_ids=None, family='all', limit=30, **kwargs):
        if family not in ('all', 'world', 'light_novel') or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('invalid recommendation family or limit')
        qids, entries = list(dict.fromkeys(liked_qids or [])), list(dict.fromkeys(liked_entry_ids or []))
        if any(_light_id(q) for q in qids) or any(not _light_id(q) for q in entries):
            raise ValueError('reference kind differs')
        liked_world = [self.world.work_detail(q) for q in qids]
        liked_light = [self.light_novels.work_detail(e) for e in entries]
        if any(r is None for r in [*liked_world, *liked_light]):
            raise ValueError('liked catalogue entry is absent')
        excluded_world = set(qids)
        excluded_light = set(entries)
        for row in liked_light:
            if row.get('qid'):
                excluded_world.add(row['qid'])
            excluded_world.update(member['qid'] for member in row.get('members', []) if member.get('qid'))
            excluded_light.update(member['entry_id'] for member in row.get('members', []) if member.get('entry_id'))
        world_args = dict(kwargs)
        # A series preference can supply a genre filter or requested feature;
        # this never changes the labels stored on a candidate volume.
        if liked_light and not qids:
            world_args['genre'] = world_args.get('genre') or next(
                (g['qid'] for row in liked_light for g in row.get('genres', [])), None)
            features = {f['label_id'] for row in liked_light for f in row.get('llm_features', [])
                        if f.get('decision', 'present') == 'present'}
            world_args['preferred_features'] = sorted(set(world_args.get('preferred_features') or []) | features)
        partitions = []
        if family != 'world':
            refs = [{**g, 'source_url': row.get('source_url', ''), 'scope': 'work'}
                    for row in liked_world for g in row.get('genres', [])]
            data = self.light_novels.recommend_preferences(liked_entry_ids=entries, reference_genres=refs, limit=100, **kwargs)
            partitions.append(('light_novel', data))
        if family != 'light_novel':
            data = self.world.recommend_preferences(liked_qids=qids, limit=100, **world_args)
            partitions.append(('world', data))
        ranked = {}
        for origin, data in partitions:
            for rank, row in enumerate(data.get('items', []), 1):
                key = row.get('entry_id') or row['qid']
                if key in excluded_light or row.get('qid') in excluded_world:
                    continue
                # Typed work entries and world work entries with the same QID
                # compete as one candidate. A series remains a distinct unit.
                identity = key if row.get('entry_kind') in ('series', 'edition') else row.get('qid') or key
                if identity not in ranked:
                    ranked[identity] = {'row': dict(row), 'score': 0.0, 'origins': []}
                ranked[identity]['score'] += 1 / (60 + rank)
                ranked[identity]['origins'].append(origin)
        selected = sorted(ranked.values(), key=lambda r: (-r['score'], r['row'].get('title', ''),
                                                         r['row'].get('entry_id', r['row'].get('qid', ''))))[:limit]
        items = [dict(r['row'], score=round(r['score'], 8), score_kind='uncalibrated_reciprocal_rank_fusion',
                      recommendation_catalogues=r['origins']) for r in selected]
        return {'mode': 'preference_similarity', 'items': items, 'personalized': False,
                'explanation': '指定した作品・シリーズと、根拠のある特徴から候補を順位付けしています。'
                               'シリーズと各巻を区別し、スコアは未校正です。',
                'classification_scope': 'mixed_explicit_units', 'human_quality_verified': False}

    @staticmethod
    def _history_payload(world_rows, light_rows):
        records = []
        for light, rows in [(False, world_rows), (True, light_rows)]:
            for row in rows:
                key = row['entry_id' if light else 'qid']
                if _light_id(key) != light or row['status'] not in _STATES:
                    raise ValueError('stored reading-state identity or status is invalid')
                datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00'))
                kind = 'work' if not light else {'lns': 'series', 'lnw': 'work', 'lne': 'edition'}[key[:3]]
                records.append({'entry_id': key, 'entry_kind': kind, 'status': row['status'],
                                'updated_at': row['updated_at']})
        records.sort(key=lambda r: r['entry_id'])
        return {'version': 'catalogue-history-export-v2', 'kind': 'read-status',
                'records': records, 'record_count': len(records)}

    def _attach_history(self, db):
        db.execute('ATTACH DATABASE ? AS light_history', (str(self.light_novels.db_path.resolve()),))

    @staticmethod
    def _read_history(db):
        return (db.execute('SELECT qid,status,updated_at FROM main.read_status ORDER BY qid').fetchall(),
                db.execute('SELECT entry_id,status,updated_at FROM light_history.read_status ORDER BY entry_id').fetchall())

    def export_reading_history(self):
        with self.world._connect() as db:
            self._attach_history(db)
            db.execute('BEGIN')
            payload = self._history_payload(*self._read_history(db))
        return {**payload, 'sha256': _digest(payload)}

    def delete_reading_history(self, *, export_sha256, confirmation):
        if confirmation != 'DELETE_READ_STATUS' or not isinstance(export_sha256, str):
            raise ValueError('current export and explicit confirmation are required')
        with self.world._connect() as db:
            self._attach_history(db)
            db.execute('BEGIN IMMEDIATE')
            payload = self._history_payload(*self._read_history(db))
            if _digest(payload) != export_sha256:
                raise ValueError('reading state changed since export')
            deleted = db.execute('DELETE FROM main.read_status').rowcount
            deleted += db.execute('DELETE FROM light_history.read_status').rowcount
        return {'version': payload['version'], 'deleted_count': deleted, 'export_sha256': export_sha256}
