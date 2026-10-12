"""Offline, conservative OL bibliography enrichment into a new SQLite namespace.

No source discovery, classification, calibrated identity confidence, or human
verification is performed. The original database is opened immutable/read-only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import runpy
import sqlite3

from recommend_book.database import DatasetDB
from recommend_book.evaluation.splits import connected_groups
from recommend_book.ingestion.core import read_jsonl, write_jsonl
from recommend_book.ingestion.openlibrary import year
from recommend_book.models import Identifier, SourceRecord
from recommend_book.resolution import compare_records, work_identifiers


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def agent_ids(value):
    return {(i.source, i.value) for i in value.identifiers if i.scope == 'agent'}


def values(entity, prop):
    return [s['mainsnak']['datavalue']['value'] for s in entity.get('claims', {}).get(prop, [])
            if s.get('rank') != 'deprecated' and 'datavalue' in s.get('mainsnak', {})]


def suspicious(name):
    return not name.strip() or '\ufffd' in name or bool(re.fullmatch(r'[\d\s?\[\]().–—-]+', name)) or name.casefold() in {'various', 'pseud', 'pseud.', 'romancier', 'schriftsteller'} or any(x in name.casefold() for x in ('publisher:', 'editor:', 'kadokawa shoten'))


class Proofs:
    def __init__(self):
        self.cache = {}
        self.locators = []

    def bytes(self, path, digest):
        path = str(Path(path).resolve())
        if path not in self.cache:
            raw = Path(path).read_bytes()
            self.cache[path] = (hashlib.sha256(raw).hexdigest(), raw)
        actual, raw = self.cache[path]
        if actual != digest:
            raise ValueError(f'Raw SHA mismatch: {path}')
        return raw

    def ol(self, record):
        raw = self.bytes(record.raw_path, record.raw_sha256)
        fields = raw.rstrip(b'\r\n').split(b'\t', 4)
        expected = b'/type/author' if record.record_kind == 'agent' else b'/type/work'
        if len(fields) != 5 or fields[0] != expected or fields[1].decode() != record.source_id:
            raise ValueError('Open Library five-column key/type locator mismatch')
        item = json.loads(fields[4])
        if item.get('key') != record.source_id or item.get('type', {}).get('key') != expected.decode():
            raise ValueError('Open Library raw JSON key/type mismatch')
        if not record.titles or record.titles[0].value != (item.get('title') or item.get('name') or record.source_id):
            raise ValueError('Open Library preferred title/name raw mismatch')
        scope = record.record_kind
        expected_ids = {('openlibrary', record.source_id)}
        for source, vals in item.get('identifiers', {}).items():
            if source == 'wikidata':
                expected_ids.update((source, str(v)) for v in (vals if isinstance(vals, list) else [vals]))
        expected_ids.update((k, v) for k, v in item.get('remote_ids', {}).items() if k == 'wikidata')
        actual_ids = {(i.source, i.value) for i in record.identifiers if i.scope == scope}
        if actual_ids != expected_ids or any(i.scope != scope for i in record.identifiers):
            raise ValueError('Open Library scoped identifiers raw mismatch')
        # Work refs are accepted only on edition adapters. These candidates are
        # works/agents, so a normalized ref must never invent a work identity.
        if record.work_refs or item.get('works') or record.contents:
            raise ValueError('Unsupported Open Library work_refs/contents on work or agent candidates')
        authors = [a.get('author', a).get('key') for a in item.get('authors', [])]
        recorded = [next((i.value for i in c.identifiers if i.source == 'openlibrary' and i.scope == 'agent'), None)
                    for c in record.contributors if c.role == 'author']
        if authors != recorded or any(c.role != 'author' for c in record.contributors):
            raise ValueError('Open Library complete raw author references mismatch')
        if any(agent_ids(c) != {('openlibrary', ref)} or any(i.scope != 'agent' for i in c.identifiers)
               for c,ref in zip(record.contributors,authors)):
            raise ValueError('Open Library contributor scoped identifiers raw mismatch')
        expected_titles = [(item.get('title') or item.get('name') or record.source_id, None, 'preferred')]
        expected_titles.extend((v,None,'alternative') for v in item.get('alternative_names',[]) + item.get('other_titles',[]))
        if [(t.value,t.language,t.kind) for t in record.titles] != expected_titles:
            raise ValueError('Open Library normalized titles raw mismatch')
        languages = [x.get('key','').rsplit('/',1)[-1] for x in item.get('languages',[])]
        if record.language != (languages[0] if len(languages)==1 else None) or record.original_language is not None:
            raise ValueError('Open Library normalized language raw mismatch')
        if record.series is not None or record.region or record.publication_year is not None:
            raise ValueError('Unsupported normalized Open Library work metadata assertion')
        if record.publisher != ('; '.join(item.get('publishers', [])) or None):
            raise ValueError('Open Library normalized publisher raw mismatch')
        if record.first_publication_year != (year(item.get('first_publish_date')) if scope == 'work' else None):
            raise ValueError('Open Library first publication year raw mismatch')
        if record.subjects != item.get('subjects', []):
            raise ValueError('Open Library raw subjects mismatch')
        for evidence in record.evidence:
            if evidence.locator == 'description':
                text = item.get('description'); text = text.get('value') if isinstance(text, dict) else text
                if evidence.text != str(text) or evidence.llm_input_allowed or evidence.kind != 'description' or evidence.rights_policy_id != 'openlibrary-prose':
                    raise ValueError('Unreviewed OL prose must remain excluded from LLM input')
            elif evidence.locator == 'subjects':
                if evidence.text != '; '.join(record.subjects) or evidence.kind != 'subject' or evidence.rights_policy_id != 'openlibrary-metadata':
                    raise ValueError('Open Library subject evidence raw mismatch')
            else:
                raise ValueError('Unsupported Open Library evidence locator')
        self.locators.append({'source': record.source, 'source_id': record.source_id, 'raw_path': record.raw_path,
                              'raw_sha256': record.raw_sha256, 'locator': 'five-column[1,2,5];key;type;title/name;authors;identifiers/remote_ids;subjects;first_publish_date', 'verified': True})
        return item

    def wd(self, record):
        entity = json.loads(self.bytes(record.raw_path, record.raw_sha256)).get('entities', {}).get(record.source_id)
        if not entity or entity.get('id') != record.source_id:
            raise ValueError('Wikidata raw entity locator mismatch')
        ol_ids = {'/works/' + v for v in values(entity, 'P648') if isinstance(v, str) and v.endswith('W')}
        if work_identifiers(record) != {('wikidata',record.source_id)} | {('openlibrary',v) for v in ol_ids}:
            raise ValueError('Wikidata normalized work identities do not match raw entity/P648')
        self.locators.append({'source': record.source, 'source_id': record.source_id, 'raw_path': record.raw_path,
                              'raw_sha256': record.raw_sha256, 'locator': f'entities.{record.source_id};claims.P648;claims.P50', 'verified': True})
        return entity

    def wd_author(self, record, author, entity):
        qids = {v for s, v in agent_ids(author) if s == 'wikidata'}
        if len(qids) != 1:
            return 'missing or ambiguous Wikidata author authority'
        qid = next(iter(qids))
        if qid not in {v.get('id') for v in values(entity, 'P50') if isinstance(v, dict)}:
            return 'author authority not in raw P50'
        proof = record.extra.get('linked_label_provenance', {}).get(qid)
        if not proof:
            return 'missing saved author authority raw proof'
        raw_author = json.loads(self.bytes(proof['raw_path'], proof['raw_sha256'])).get('entities', {}).get(qid)
        if not raw_author or raw_author.get('id') != qid:
            return 'saved author authority entity locator mismatch'
        ol_ids = {'/authors/' + v for v in values(raw_author, 'P648') if isinstance(v, str) and v.endswith('A')}
        if {v for s, v in agent_ids(author) if s == 'openlibrary'} - ol_ids:
            return 'author Open Library authority ID not in saved raw P648'
        self.locators.append({'source': 'wikidata', 'source_id': qid, **{k: proof[k] for k in ('raw_path', 'raw_sha256')},
                              'locator': f'entities.{qid};claims.P648 (author);parent claims.P50', 'verified': True})
        return None


@contextmanager
def read_only(database):
    path = Path(database).resolve()
    wal = Path(str(path) + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Source database has nonempty WAL; require a stable snapshot')
    con = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    try:
        yield con
    finally:
        con.close()


def select_candidates(candidates, rows, proofs):
    works = [(v, r, canonical) for v, r, canonical in rows if r.source == 'wikidata' and r.record_kind == 'work']
    index = defaultdict(dict)
    authority_owners = defaultdict(set)
    for version, record, canonical in works:
        for ident in work_identifiers(record):
            index[ident][record.source_id] = (version, record, canonical)
        for author in record.contributors:
            if author.role == 'author':
                owners = {v for s, v in agent_ids(author) if s == 'wikidata'}
                for ident in agent_ids(author):
                    authority_owners[ident].update(owners)
    agents = {r.source_id: r for r in candidates if r.record_kind == 'agent'}
    raw = {r.source_id: proofs.ol(r) for r in candidates}
    accepted, held, outside, decisions, used_agents = [], [], [], [], {}
    for record in candidates:
        if record.record_kind != 'work':
            continue
        targets = {qid: target for ident in work_identifiers(record) for qid, target in index[ident].items()}
        decision = {'source_id': record.source_id, 'raw_path': record.raw_path, 'raw_sha256': record.raw_sha256,
                    'target_work_ids': sorted(targets), 'human_verified': False, 'independent_identity_confirmation': False}
        if not targets:
            outside.append(record); decision.update(status='outside_scope', reason='no explicit work ID in baseline Wikidata scope'); decisions.append(decision); continue
        reason, checks = None, []
        if len(targets) != 1:
            reason = 'ambiguous explicit work identity'
        version, target, canonical = next(iter(targets.values()))
        entity = proofs.wd(target)
        decision.update(target_version_id=version, target_canonical_id=canonical, target_raw_path=target.raw_path, target_raw_sha256=target.raw_sha256)
        remaining = [a for a in target.contributors if a.role == 'author']
        authors = [a for a in record.contributors if a.role == 'author']
        normalized, selected_agents = [], {}
        if not authors or len(authors) != len(remaining):
            reason = reason or 'missing or incomplete complete author set'
        for author in authors:
            keys = {v for s, v in agent_ids(author) if s == 'openlibrary'}
            authority = agents.get(next(iter(keys))) if len(keys) == 1 else None
            ids = agent_ids(author) | (agent_ids(authority) if authority else set())
            if len({v for s,v in ids if s == 'wikidata'}) > 1:
                reason = reason or 'ambiguous author authority namespace'
            if not authority:
                reason = reason or 'missing Open Library author authority record'
            if suspicious(author.name) or authority and any(suspicious(t.value) for t in authority.titles):
                reason = reason or 'suspect author preferred label'
            candidates_a = [a for a in remaining if ids & agent_ids(a)]
            if len(candidates_a) != 1:
                reason = reason or 'author authority ID correspondence is absent or ambiguous'; continue
            match = candidates_a[0]; remaining.remove(match)
            shared_sources = {s for s, _ in ids} & {s for s, _ in agent_ids(match)}
            if any(not {v for s, v in ids if s == namespace} & {v for s, v in agent_ids(match) if s == namespace} for namespace in shared_sources):
                reason = reason or 'conflicting author authority namespace'
            if any(len(authority_owners[ident]) > 1 for ident in ids):
                reason = reason or 'author authority bridges distinct Wikidata authors'
            reason = reason or proofs.wd_author(target, match, entity)
            check = {'raw_name': author.name, 'baseline_name': match.name, 'shared_agent_ids': sorted(ids & agent_ids(match)),
                     'baseline_author_qids': sorted(v for s, v in agent_ids(match) if s == 'wikidata'),
                     'authority_source_id': authority.source_id if authority else None,
                     'authority_raw_sha256': authority.raw_sha256 if authority else None,
                     'author_label_copied_from_baseline': True}
            checks.append(check)
            joined = sorted(ids | agent_ids(match))
            normalized.append(author.model_copy(update={'name': match.name, 'identifiers': [Identifier(source=s, value=v, scope='agent') for s, v in joined]}))
            if authority:
                selected_agents[authority.source_id] = authority.model_copy(update={'titles': [TitleCopy(authority.titles[0], match.name), *authority.titles[1:]],
                    'identifiers': [Identifier(source=s, value=v, scope='agent') for s, v in joined],
                    'extra': {**authority.extra, 'enrichment_audit': {'raw_preferred_label': authority.titles[0].value, 'derived_label': match.name,
                              'target_work_source_id': target.source_id, 'target_raw_sha256': target.raw_sha256, 'target_raw_path': target.raw_path,
                              'shared_agent_ids': check['shared_agent_ids'], 'independent_identity_confirmation': False, 'human_verified': False}}})
        if remaining:
            reason = reason or 'incomplete complete author set'
        enriched = record.model_copy(update={'contributors': normalized})
        consistency = compare_records(enriched, target)
        if consistency.status != 'same':
            reason = reason or consistency.reason
        if raw[record.source_id].get('series'):
            reason = reason or 'unrepresented series/position needs review; series position is not automatically a volume'
        if raw[record.source_id].get('volume') or raw[record.source_id].get('work_kind'):
            reason = reason or 'unrepresented raw volume/work_kind needs review'
        subtitle = raw[record.source_id].get('subtitle', '')
        if re.search(r'\b(?:vol(?:ume)?\.?|book|part)\s*\d+\b', subtitle, re.I):
            reason = reason or 'unrepresented subtitle volume/book unit needs review'
        if record.volume is not None or record.work_kind is not None:
            reason = reason or 'volume/work_kind lacks an explicit mapped Open Library raw assertion'
        decision.update(author_identity_checks=checks, consistency=consistency.__dict__)
        if reason:
            held.append(record); decision.update(status='held', reason=reason)
        else:
            decision.update(status='accepted', reason='single explicit work correspondence and complete nonconflicting author authorities')
            accepted.append(enriched.model_copy(update={'extra': {**enriched.extra, 'enrichment_audit': decision}}))
            for key, authority in selected_agents.items():
                if key in used_agents and agent_ids(used_agents[key]) != agent_ids(authority):
                    raise ValueError('Accepted author authority has inconsistent identity expansion')
                used_agents.setdefault(key, authority)
        decisions.append(decision)
    unused = [r for r in candidates if r.record_kind == 'agent' and r.source_id not in used_agents]
    return accepted, list(used_agents.values()), held, outside, unused, decisions


def TitleCopy(title, value):
    return title.model_copy(update={'value': value})


def group_tokens(connection, work_ids):
    # Reuse the same read-only identity API as the existing freeze job.
    helper = runpy.run_path(str(Path(__file__).with_name('freeze_poc_inputs.py')))
    return helper['group_identity_tokens'](connection, work_ids)


def partitions(groups):
    result = defaultdict(list)
    for work, group in groups.items():
        result[group].append(work)
    return sorted(sorted(members) for members in result.values())


def enrich(base_database, candidates_path, output, *, protected_paths=()):
    base_database, candidates_path, output = map(lambda p: Path(p).resolve(), (base_database, candidates_path, output))
    if output.exists():
        raise ValueError('Output namespace must be new')
    protected = {base_database, candidates_path, *map(lambda p: Path(p).resolve(), protected_paths)}
    protected.update(p for p in base_database.parent.rglob('*') if p.is_file())
    protected.update(p for p in candidates_path.parent.rglob('*') if p.is_file())
    if any(p == output or p.is_relative_to(output) for p in protected):
        raise ValueError('Output must not contain protected inputs')
    before = {str(p): sha(p) for p in sorted(protected)}
    candidates = list(read_jsonl(candidates_path))
    if any(r.source != 'openlibrary' or r.record_kind not in ('work', 'agent') for r in candidates):
        raise ValueError('Only Open Library work and agent candidates are supported')
    if len({r.source_id for r in candidates}) != len(candidates):
        raise ValueError('Duplicate candidate source identity')
    proofs = Proofs()
    with read_only(base_database) as source:
        source_rows = [(row['version_id'], SourceRecord.model_validate_json(row['record_json']), row['canonical_id'])
            for row in source.execute('SELECT r.version_id,r.record_json,w.canonical_id FROM source_heads h JOIN source_records r USING(version_id) LEFT JOIN works w ON w.source_version_id=r.version_id')]
        old_sources = {row['version_id']: tuple(row) for row in source.execute('SELECT * FROM source_records ORDER BY version_id')}
        old_nodes = dict(source.execute('SELECT work_id,canonical_id FROM works'))
        old_resolved = set(r[0] for r in source.execute('SELECT DISTINCT canonical_id FROM works WHERE resolved=1'))
        old_editions = {row['edition_id']: tuple(row) for row in source.execute('SELECT * FROM editions ORDER BY edition_id')}
        old_edition_links = [(row['edition_id'],row['work_id'],row['position']) for row in source.execute('SELECT * FROM edition_contents ORDER BY edition_id,work_id,position')]
        accepted, agents, held, outside, unused, decisions = select_candidates(candidates, source_rows, proofs)
        output.mkdir(parents=True)
        write_json(output / 'manifest.json', {'status': 'pending_validation', 'outputs_are_valid': False})
        with sqlite3.connect(output / 'dataset.sqlite') as target:
            source.backup(target)
        target.close()
    write_json(output / 'selection-decisions.json', decisions)
    write_jsonl([*accepted, *agents], output / 'accepted-records.jsonl')
    write_jsonl(held, output / 'held-records.jsonl')
    write_jsonl([*outside, *unused], output / 'outside-or-unused-records.jsonl')
    with DatasetDB(output / 'dataset.sqlite') as db:
        baseline_inputs = [w for w in db.work_inputs() if w.expression_id is None]
        baseline_tokens = group_tokens(db.connection, old_resolved)
        old_groups = connected_groups(baseline_inputs, group_identities=baseline_tokens)
        old_stats = db.stats()
        result = db.ingest([*agents, *accepted])
        inputs = db.work_inputs()
        new_nodes = dict(db.connection.execute('SELECT work_id,canonical_id FROM works'))
        crosswalk = [{'old_canonical_id': old, 'new_canonical_ids': sorted({new_nodes[node] for node, canonical in old_nodes.items() if canonical == old}),
                      'old_work_node_ids': sorted(node for node, canonical in old_nodes.items() if canonical == old)} for old in sorted(old_resolved)]
        if any(len(row['new_canonical_ids']) != 1 for row in crosswalk) or len({row['new_canonical_ids'][0] for row in crosswalk}) != len(old_resolved) or result['works'] != len(old_resolved):
            raise ValueError('Enrichment altered baseline canonical partition or added uncorroborated works')
        mapping = {row['old_canonical_id']: row['new_canonical_ids'][0] for row in crosswalk}
        all_roots = {old: new_nodes[node] for node,old in old_nodes.items()}
        new_primary = {w.work_id: w for w in inputs if w.expression_id is None}
        new_tokens = group_tokens(db.connection, new_primary)
        aligned = [new_primary[mapping[w.work_id]].model_copy(update={'work_id': w.work_id}) for w in baseline_inputs]
        aligned_tokens = {old: new_tokens[new] for old, new in mapping.items()}
        new_groups = connected_groups(aligned, group_identities=aligned_tokens)
        group_impact = {'connected_partition_unchanged': partitions(old_groups) == partitions(new_groups),
                       'groups_before': len(set(old_groups.values())), 'groups_after': len(set(new_groups.values())),
                       'token_changes': [{'old_canonical_id': old, 'new_canonical_id': mapping[old], 'removed': sorted(set(baseline_tokens[old])-set(aligned_tokens[old])),
                                          'added': sorted(set(aligned_tokens[old])-set(baseline_tokens[old]))} for old in mapping if baseline_tokens[old] != aligned_tokens[old]],
                       'before_partitions': partitions(old_groups), 'after_partitions': partitions(new_groups), 'old_frozen_group_ids_reused': False}
        comparison = []
        for old, new in zip(baseline_inputs, aligned):
            changed = {field: {'before': getattr(old, field), 'after': getattr(new, field)} for field in ('title','authors','original_language','first_publication_year','region','series') if getattr(old, field) != getattr(new, field)}
            if changed or old.source_record_ids != new.source_record_ids or old.evidence != new.evidence:
                comparison.append({'old_canonical_id': old.work_id, 'new_canonical_id': mapping[old.work_id], 'bibliographic_changes': changed,
                                   'source_refs_changed': old.source_record_ids != new.source_record_ids, 'evidence_changed': old.evidence != new.evidence})
        preserved = sum(tuple(db.connection.execute('SELECT * FROM source_records WHERE version_id=?',(version,)).fetchone()) == content for version, content in old_sources.items())
        audit = {'integrity_check': db.connection.execute('PRAGMA integrity_check').fetchone()[0],
                 'foreign_key_violations': len(db.connection.execute('PRAGMA foreign_key_check').fetchall()),
                 'baseline_immutable_versions_preserved': preserved, 'baseline_immutable_versions_total': len(old_sources),
                 'baseline_canonical_partition_preserved': True, 'accepted_works_have_one_target_canonical': True,
                 'baseline_editions_preserved': sum(tuple(db.connection.execute('SELECT * FROM editions WHERE edition_id=?',(edition,)).fetchone()) == value for edition,value in old_editions.items()),
                 'baseline_editions_total': len(old_editions),
                 'baseline_edition_work_links_preserved': sorted((edition,all_roots[work],position) for edition,work,position in old_edition_links) == sorted(tuple(row) for row in db.connection.execute('SELECT edition_id,work_id,position FROM edition_contents'))}
        for decision in decisions:
            if decision['status'] != 'accepted': continue
            versions = db.connection.execute('SELECT version_id FROM source_heads WHERE source=? AND source_id=?', ('openlibrary', decision['source_id'])).fetchone()
            canonical = db.connection.execute('SELECT canonical_id FROM works WHERE source_version_id=?',versions).fetchone()[0]
            if canonical != mapping[decision['target_canonical_id']]:
                raise ValueError('Accepted OL source did not merge with its verified target')
        if audit['integrity_check'] != 'ok' or audit['foreign_key_violations'] or preserved != len(old_sources):
            raise ValueError('Enriched DB integrity or immutable-version audit failed')
        if audit['baseline_editions_preserved'] != len(old_editions) or not audit['baseline_edition_work_links_preserved']:
            raise ValueError('Enrichment altered existing edition bibliography or work relationships')
        db.connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    write_json(output / 'canonical-crosswalk.json', crosswalk)
    write_json(output / 'group-identities.json', new_tokens)
    write_json(output / 'group-impact.json', group_impact)
    write_json(output / 'input-comparison.json', comparison)
    write_jsonl([w for w in inputs if w.expression_id is None], output / 'bibliographic-work-inputs.jsonl')
    write_jsonl([w for w in inputs if w.expression_id is not None], output / 'bibliographic-expression-inputs.jsonl')
    if any(sha(path) != digest for path,digest in before.items()):
        raise ValueError('Protected source/classification snapshot changed')
    write_json(output / 'raw-verification.json', {'verified_files': len(proofs.cache), 'locators': proofs.locators})
    write_json(output / 'protected-inputs-sha.json', before)
    report = {'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'verified_offline_bibliographic_enrichment', 'outputs_are_valid': True,
              'base_database': str(base_database), 'base_database_sha256': before[str(base_database)],
              'candidates_path': str(candidates_path), 'candidates_sha256': before[str(candidates_path)],
              'candidate_works': sum(r.record_kind=='work' for r in candidates), 'candidate_agents': sum(r.record_kind=='agent' for r in candidates),
              'accepted_works': len(accepted), 'accepted_agents': len(agents), 'held_works': len(held), 'outside_scope_works': len(outside), 'unused_agents': len(unused),
              'held_reasons': dict(Counter(d['reason'] for d in decisions if d['status']=='held')),
              'canonical_works_before': len(old_resolved), 'canonical_works_after': result['works'],
              'stats_before': old_stats, 'stats_after': result, 'protected_files_unchanged': len(before),
              'raw_sha_verified_files': len(proofs.cache), 'bibliographic_changed_works': sum(bool(c['bibliographic_changes']) for c in comparison),
              'processing_script_sha256': sha(Path(__file__)),
              'group_connected_partition_unchanged': group_impact['connected_partition_unchanged'], 'audit': audit,
              'classification_rebound': False, 'human_verified': False, 'independent_identity_confirmation': False, 'web_requests': 0, 'llm_started': False,
              'output_sha256': {p.name: sha(p) for p in output.iterdir() if p.is_file() and p.name != 'manifest.json'}}
    write_json(output / 'manifest.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-database', type=Path, default=Path('data/poc-combined-v1/dataset.sqlite'))
    parser.add_argument('--candidates', type=Path, default=Path('data/openlibrary-complete-v1/candidate-records.jsonl'))
    parser.add_argument('--output', type=Path, default=Path('data/poc-openlibrary-enriched-v1'))
    parser.add_argument('--protect', type=Path, action='append', default=[])
    args = parser.parse_args()
    paths = []
    for path in args.protect:
        paths.extend(p for p in path.rglob('*') if p.is_file()) if path.is_dir() else paths.append(path)
    report = enrich(args.base_database, args.candidates, args.output, protected_paths=paths)
    print(json.dumps({k:v for k,v in report.items() if k not in ('output_sha256','stats_before','stats_after')}, ensure_ascii=True))


if __name__ == '__main__':
    main()
