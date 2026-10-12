"""Single-writer SQLite dataset with immutable inputs and rebuildable identity views.

Every input source has a stable work node. Identity decisions form a reversible
graph over those nodes; derived bibliographic tables never replace source data.
The connection belongs to its creating thread. Classification workers write files.
"""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .models import Evidence, SourceRecord, WorkInput
from .normalization import normalize_isbn, normalize_text
from .resolution import compare_records, work_identifiers


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _id(prefix: str, *parts) -> str:
    return prefix + "_" + sha256(_json(parts).encode()).hexdigest()[:24]


SCHEMA = """
CREATE TABLE source_records (
 version_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
 parent_version_id TEXT REFERENCES source_records(version_id), payload_sha256 TEXT NOT NULL,
 raw_sha256 TEXT NOT NULL, raw_path TEXT NOT NULL, record_json TEXT NOT NULL,
 source_url TEXT NOT NULL, provider_id TEXT NOT NULL, retrieved_at TEXT NOT NULL,
 rights_policy_id TEXT NOT NULL
);
CREATE TRIGGER immutable_source_update BEFORE UPDATE ON source_records BEGIN
 SELECT RAISE(ABORT, 'source records are immutable'); END;
CREATE TRIGGER immutable_source_delete BEFORE DELETE ON source_records BEGIN
 SELECT RAISE(ABORT, 'source records are immutable'); END;
CREATE TABLE source_heads (source TEXT NOT NULL, source_id TEXT NOT NULL,
 version_id TEXT NOT NULL REFERENCES source_records(version_id), PRIMARY KEY(source,source_id));
CREATE TABLE works (work_id TEXT PRIMARY KEY, canonical_id TEXT,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id),
 resolved INTEGER NOT NULL, title TEXT NOT NULL, original_language TEXT,
 first_publication_year INTEGER, work_kind TEXT, volume TEXT, region_json TEXT NOT NULL);
CREATE TABLE expressions (expression_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id),
 language TEXT, expression_kind TEXT NOT NULL, translator_key TEXT NOT NULL);
CREATE TABLE editions (edition_id TEXT PRIMARY KEY, source_version_id TEXT NOT NULL REFERENCES source_records(version_id),
 publisher TEXT, publication_year INTEGER, language TEXT, edition_statement TEXT);
CREATE TABLE edition_contents (edition_id TEXT NOT NULL REFERENCES editions(edition_id),
 work_id TEXT NOT NULL REFERENCES works(work_id), expression_id TEXT REFERENCES expressions(expression_id),
 position INTEGER NOT NULL, PRIMARY KEY(edition_id,work_id,position));
CREATE TABLE agents (agent_id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL);
CREATE TABLE contributions (agent_id TEXT NOT NULL REFERENCES agents(agent_id),
 work_id TEXT REFERENCES works(work_id), expression_id TEXT REFERENCES expressions(expression_id),
 edition_id TEXT REFERENCES editions(edition_id), role TEXT NOT NULL,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id));
CREATE TABLE series (series_id TEXT PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL);
CREATE TABLE work_relations (work_id TEXT NOT NULL REFERENCES works(work_id),
 target_work_id TEXT REFERENCES works(work_id), series_id TEXT REFERENCES series(series_id),
 relation_type TEXT NOT NULL, position TEXT,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id));
CREATE TABLE titles (work_id TEXT NOT NULL REFERENCES works(work_id),
 expression_id TEXT REFERENCES expressions(expression_id), edition_id TEXT REFERENCES editions(edition_id),
 value TEXT NOT NULL, normalized_value TEXT NOT NULL, language TEXT, kind TEXT NOT NULL,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id));
CREATE TABLE external_ids (source TEXT NOT NULL, value TEXT NOT NULL, normalized_value TEXT,
 scope TEXT NOT NULL, entity_id TEXT NOT NULL,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id));
CREATE INDEX external_lookup ON external_ids(source,value,scope);
CREATE TABLE field_assertions (assertion_id TEXT PRIMARY KEY,
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id), entity_id TEXT NOT NULL,
 field_name TEXT NOT NULL, value_json TEXT NOT NULL, normalized_value TEXT,
 rights_policy_id TEXT NOT NULL, source_url TEXT NOT NULL, provider_id TEXT NOT NULL,
 retrieved_at TEXT NOT NULL);
CREATE TABLE evidence (evidence_key TEXT PRIMARY KEY, evidence_id TEXT NOT NULL,
 work_id TEXT NOT NULL REFERENCES works(work_id), expression_id TEXT REFERENCES expressions(expression_id),
 source_version_id TEXT NOT NULL REFERENCES source_records(version_id), evidence_json TEXT NOT NULL);
CREATE TABLE resolution_decisions (decision_id TEXT PRIMARY KEY, left_work_id TEXT NOT NULL,
 right_work_id TEXT NOT NULL, left_version_id TEXT REFERENCES source_records(version_id),
 right_version_id TEXT REFERENCES source_records(version_id), status TEXT NOT NULL,
 reason TEXT NOT NULL, method TEXT NOT NULL, reviewer TEXT, confidence REAL,
 active INTEGER NOT NULL DEFAULT 1, undone INTEGER NOT NULL DEFAULT 0);
CREATE TABLE resolution_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 decision_id TEXT NOT NULL REFERENCES resolution_decisions(decision_id),
 event_type TEXT NOT NULL, timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, details_json TEXT NOT NULL);
CREATE TABLE ingestion_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL, source_id TEXT NOT NULL, version_id TEXT NOT NULL REFERENCES source_records(version_id),
 event_type TEXT NOT NULL, timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE review_items (review_id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(work_id),
 target_work_id TEXT REFERENCES works(work_id), decision_id TEXT REFERENCES resolution_decisions(decision_id),
 reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'unresolved');
CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT INTO schema_migrations(version) VALUES(1);
PRAGMA user_version=1;
"""

MIGRATION_2 = """
ALTER TABLE ingestion_events ADD COLUMN previous_version_id TEXT REFERENCES source_records(version_id);
ALTER TABLE ingestion_events ADD COLUMN observed_at TEXT;
ALTER TABLE ingestion_events ADD COLUMN active INTEGER NOT NULL DEFAULT 1;
ALTER TABLE evidence ADD COLUMN scope_valid INTEGER NOT NULL DEFAULT 1;
INSERT INTO schema_migrations(version) VALUES(2);
PRAGMA user_version=2;
"""

MIGRATION_3 = """
CREATE INDEX evidence_work_scope_order ON evidence(work_id,scope_valid,evidence_key);
CREATE INDEX works_resolved_canonical ON works(resolved,canonical_id,work_id);
INSERT INTO schema_migrations(version) VALUES(3);
PRAGMA user_version=3;
"""


class DatasetDB:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self.connection.executescript(SCHEMA)
            version = 1
        if version == 1:
            self.connection.executescript(MIGRATION_2)
            version = 2
        if version == 2:
            self.connection.executescript(MIGRATION_3)
        elif version != 3:
            self.close()
            raise ValueError(f"Unsupported dataset schema version: {version}")

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def ingest(self, records: Iterable[SourceRecord]) -> dict:
        counts = dict(inserted=0, updated=0, unchanged=0)
        with self.connection:
            for record in records:
                if not isinstance(record, SourceRecord):
                    record = SourceRecord.model_validate(record)
                payload = record.model_dump(mode="json")
                serialized = _json(payload)
                # Retrieval observations do not create duplicate content versions.
                digest = sha256(_json({key: value for key, value in payload.items() if key != "retrieved_at"}).encode()).hexdigest()
                version_id = _id("src", record.source, record.source_id, digest)
                current = self.connection.execute("SELECT version_id FROM source_heads WHERE source=? AND source_id=?", (record.source, record.source_id)).fetchone()
                if current and current[0] == version_id:
                    self.connection.execute("INSERT INTO ingestion_events(source,source_id,version_id,event_type,observed_at,active) VALUES(?,?,?,'observe',?,0)", (record.source, record.source_id, version_id, record.retrieved_at))
                    counts["unchanged"] += 1
                    continue
                self.connection.execute("INSERT OR IGNORE INTO source_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (version_id, record.source, record.source_id, current[0] if current else None, digest, record.raw_sha256, record.raw_path, serialized, record.source_url, record.provider_id, record.retrieved_at, record.rights_policy_id))
                self.connection.execute("INSERT INTO source_heads VALUES(?,?,?) ON CONFLICT(source,source_id) DO UPDATE SET version_id=excluded.version_id", (record.source, record.source_id, version_id))
                self.connection.execute("INSERT INTO ingestion_events(source,source_id,version_id,event_type,previous_version_id,observed_at) VALUES(?,?,?,'ingest',?,?)", (record.source, record.source_id, version_id, current[0] if current else None, record.retrieved_at))
                counts["updated" if current else "inserted"] += 1
            if counts["inserted"] or counts["updated"]:
                self._rebuild()
        return {**self.stats(), **counts}

    def _source_records(self):
        rows = self.connection.execute("SELECT r.version_id,r.record_json FROM source_heads h JOIN source_records r ON r.version_id=h.version_id ORDER BY r.source,r.source_id")
        return [(row["version_id"], SourceRecord.model_validate_json(row["record_json"])) for row in rows]

    def _active_records(self) -> dict[str, tuple[str, SourceRecord]]:
        sources = self._source_records()
        authorities = [record for _, record in sources if record.record_kind == "agent"]
        result = {}
        for version, record in sources:
            if record.record_kind != "agent":
                contributors = []
                for contributor in record.contributors:
                    identifiers = {(item.source, item.value): item for item in contributor.identifiers if item.scope == "agent"}
                    matches = []
                    remaining = list(authorities)
                    changed = True
                    while changed:
                        changed = False
                        for authority in list(remaining):
                            authority_ids = {(item.source, item.value): item for item in authority.identifiers if item.scope == "agent"}
                            if identifiers.keys() & authority_ids.keys():
                                identifiers.update(authority_ids)
                                matches.append(authority)
                                remaining.remove(authority)
                                changed = True
                    label = next((authority.titles[0].value for authority in matches if authority.titles), contributor.name)
                    contributors.append(contributor.model_copy(update={"identifiers": list(identifiers.values()), "name": label}))
                enriched = record.model_copy(update={"contributors": contributors})
                result[_id("work", record.source, record.source_id)] = (version, enriched)
        return result

    def _rebuild(self):
        db = self.connection
        for table in ("review_items", "evidence", "field_assertions", "external_ids", "titles", "work_relations", "contributions", "edition_contents", "editions", "expressions", "agents", "series", "works"):
            db.execute(f"DELETE FROM {table}")
        from .models import Contributor
        for version, authority in self._source_records():
            if authority.record_kind != "agent":
                continue
            contributor = Contributor(name=authority.titles[0].value if authority.titles else authority.source_id, identifiers=authority.identifiers)
            agent_id = self._agent_id(contributor, authority, version)
            self._field_assertions(version, authority, agent_id)
        records = self._active_records()
        parents = {node: node for node in records}
        forbidden_pairs = [(row["left_work_id"], row["right_work_id"]) for row in db.execute("SELECT * FROM resolution_decisions WHERE undone=1 AND status='same'") if row["left_work_id"] in records and row["right_work_id"] in records and (row["method"] == "human" or row["left_version_id"] == records[row["left_work_id"]][0] and row["right_version_id"] == records[row["right_work_id"]][0])]

        def find(node):
            while parents[node] != node:
                parents[node] = parents[parents[node]]
                node = parents[node]
            return node

        def merge(left, right, manual=False):
            a, b = find(left), find(right)
            if a == b:
                return True
            # Prevent transitive ID bridges from hiding incompatible members.
            if not manual:
                members_a = [node for node in records if find(node) == a]
                members_b = [node for node in records if find(node) == b]
                if any(x in members_a and y in members_b or y in members_a and x in members_b for x, y in forbidden_pairs):
                    return False
                for x in members_a:
                    for y in members_b:
                        consistency = compare_records(records[x][1], records[y][1])
                        if consistency.status == "different" or consistency.status == "uncertain" and consistency.reason != "title or author similarity is insufficient work identity":
                            return False
            parents[max(a, b)] = min(a, b)
            return True

        candidates = set()
        buckets = defaultdict(list)
        for node, (_, record) in records.items():
            for source, value in work_identifiers(record):
                buckets[("identity", source, value)].append(node)
            for title in record.titles:
                buckets[("title", normalize_text(title.value))].append(node)
        for nodes in buckets.values():
            nodes = sorted(set(nodes))
            candidates.update((a, b) for index, a in enumerate(nodes) for b in nodes[index + 1:])
        db.execute("UPDATE resolution_decisions SET active=0 WHERE method='rule'")
        reviews = []
        for left, right in sorted(candidates):
            lv, lr = records[left]
            rv, rr = records[right]
            result = compare_records(lr, rr)
            decision_id = _id("resolution", lv, rv, result.status, result.reason)
            db.execute("INSERT OR IGNORE INTO resolution_decisions(decision_id,left_work_id,right_work_id,left_version_id,right_version_id,status,reason,method,confidence,active) VALUES(?,?,?,?,?,?,?,'rule',?,0)", (decision_id, left, right, lv, rv, result.status, result.reason, result.confidence))
            row = db.execute("SELECT undone FROM resolution_decisions WHERE decision_id=?", (decision_id,)).fetchone()
            if row[0]:
                reviews.append((left, right, decision_id, "previous identity decision was undone"))
                continue
            db.execute("UPDATE resolution_decisions SET active=1 WHERE decision_id=?", (decision_id,))
            if result.status == "same":
                if not merge(left, right):
                    db.execute("UPDATE resolution_decisions SET active=0 WHERE decision_id=?", (decision_id,))
                    reviews.append((left, right, decision_id, "transitive cluster contains conflicting identity"))
            elif result.status == "uncertain" or work_identifiers(lr) & work_identifiers(rr):
                reviews.append((left, right, decision_id, result.reason))
        for row in db.execute("SELECT * FROM resolution_decisions WHERE method='human' AND active=1 AND undone=0 ORDER BY decision_id"):
            if row["left_work_id"] in records and row["right_work_id"] in records:
                merge(row["left_work_id"], row["right_work_id"], manual=True)

        clusters = defaultdict(list)
        for node in records:
            clusters[find(node)].append(node)
        resolved = {key: any(records[node][1].record_kind == "work" for node in nodes) for key, nodes in clusters.items()}
        for node, (version, record) in records.items():
            canonical = find(node)
            db.execute("INSERT INTO works VALUES(?,?,?,?,?,?,?,?,?,?)", (node, canonical, version, int(resolved[canonical]), record.titles[0].value if record.titles else "", record.original_language, record.first_publication_year, record.work_kind, record.volume, _json(record.region)))
            if not resolved[canonical]:
                reviews.append((node, None, None, "edition has no corroborated work record"))
        expression_by_node = {}
        expression_identifiers = defaultdict(set)
        identities = defaultdict(set)
        for node, (version, record) in records.items():
            canonical = find(node)
            for source, value in work_identifiers(record):
                identities[(source, value)].add(canonical)
            language = record.language or record.original_language
            translators = [item for item in record.contributors if item.role == "translator"]
            translator_key = _json(sorted(self._agent_id(item, record, version) for item in translators))
            expression_kind = str(record.extra.get("expression_kind") or ("translation" if translators or language and record.original_language and language != record.original_language else "original"))
            expression_id = _id("expression", canonical, language, expression_kind, translator_key, record.extra.get("revision"))
            db.execute("INSERT OR IGNORE INTO expressions VALUES(?,?,?,?,?)", (expression_id, canonical, language, expression_kind, translator_key))
            expression_by_node[node] = expression_id
            expression_identifiers[expression_id].add(expression_id)
            for identifier in record.identifiers:
                if identifier.scope == "expression":
                    expression_identifiers[identifier.value].add(expression_id)
        for node, (version, record) in records.items():
            canonical = find(node)
            expression_id = expression_by_node[node]
            edition_id = _id("edition", record.source, record.source_id) if record.record_kind == "edition" else None
            if edition_id:
                db.execute("INSERT INTO editions VALUES(?,?,?,?,?,?)", (edition_id, version, record.publisher, record.publication_year, record.language, record.extra.get("edition_statement")))
                db.execute("INSERT INTO edition_contents VALUES(?,?,?,0)", (edition_id, canonical, expression_id))
                for position, content in enumerate(record.contents, 1):
                    targets = identities.get((content.source, content.value), set()) if content.scope == "work" else set()
                    if len(targets) == 1:
                        target = next(iter(targets))
                        db.execute("INSERT OR IGNORE INTO edition_contents VALUES(?,?,NULL,?)", (edition_id, target, position))
                        db.execute("INSERT INTO work_relations VALUES(?,?,NULL,'contains',?,?)", (canonical, target, str(position), version))
                    else:
                        reviews.append((node, None, None, f"unresolved or ambiguous content reference: {content.source}:{content.value}"))
            for contributor in record.contributors:
                agent_id = self._agent_id(contributor, record, version)
                db.execute("INSERT INTO contributions VALUES(?,?,?,?,?,?)", (agent_id, canonical if contributor.role == "author" else None, expression_id if contributor.role == "translator" else None, edition_id if contributor.role not in ("author", "translator") else None, contributor.role, version))
            for title in record.titles:
                db.execute("INSERT INTO titles VALUES(?,?,?,?,?,?,?,?)", (canonical, expression_id if record.record_kind == "edition" else None, edition_id, title.value, normalize_text(title.value), title.language, title.kind, version))
            for identifier in [*record.identifiers, *record.work_refs]:
                entity_id = {"work": canonical, "expression": expression_id, "edition": edition_id or node, "record": version, "agent": node, "series": node}[identifier.scope]
                normalized = normalize_isbn(identifier.value) if identifier.source.casefold() in ("isbn", "isbn10", "isbn13", "isbn_10", "isbn_13") else identifier.value
                db.execute("INSERT INTO external_ids VALUES(?,?,?,?,?,?)", (identifier.source, identifier.value, normalized, identifier.scope, entity_id, version))
                if normalized is None:
                    reviews.append((node, None, None, f"invalid ISBN: {identifier.value}"))
            if record.series:
                # Names alone do not establish author/series identity across sources.
                series_id = _id("series", record.source, normalize_text(record.series))
                db.execute("INSERT OR IGNORE INTO series VALUES(?,?,?)", (series_id, record.series, normalize_text(record.series)))
                db.execute("INSERT INTO work_relations VALUES(?,NULL,?,'series_member',?,?)", (canonical, series_id, record.volume, version))
            raw_record = SourceRecord.model_validate_json(db.execute("SELECT record_json FROM source_records WHERE version_id=?", (version,)).fetchone()[0])
            self._field_assertions(version, raw_record, canonical)
            for index, evidence in enumerate(record.evidence):
                scope_expression = expression_id if evidence.expression_ref or record.record_kind == "edition" and (record.language and record.language != record.original_language or any(item.role == "translator" for item in record.contributors)) else None
                scope_valid = True
                if evidence.expression_ref:
                    targets = expression_identifiers.get(evidence.expression_ref, set())
                    if len(targets) == 1:
                        candidate = next(iter(targets))
                        candidate_work = db.execute("SELECT work_id FROM expressions WHERE expression_id=?", (candidate,)).fetchone()[0]
                        if candidate_work == canonical:
                            scope_expression = candidate
                        else:
                            scope_valid = False
                    else:
                        scope_valid = False
                    if not scope_valid:
                        reviews.append((node, None, None, f"unresolved expression evidence reference: {evidence.expression_ref}"))
                db.execute("INSERT INTO evidence VALUES(?,?,?,?,?,?,?)", (_id("evidence", version, index), evidence.evidence_id, canonical, scope_expression, version, evidence.model_dump_json(), int(scope_valid)))
                # Evidence-specific permission and URL are kept at field level as well.
                db.execute("INSERT INTO field_assertions VALUES(?,?,?,?,?,?,?,?,?,?)", (_id("assertion", version, "evidence", index), version, scope_expression or canonical, f"evidence[{index}]", evidence.model_dump_json(), None, evidence.rights_policy_id, evidence.source_url, record.provider_id, record.retrieved_at))
        for node, target, decision, reason in reviews:
            db.execute("INSERT OR IGNORE INTO review_items(review_id,work_id,target_work_id,decision_id,reason) VALUES(?,?,?,?,?)", (_id("review", node, target, decision, reason), node, target, decision, reason))

    def _agent_id(self, contributor, record, version):
        identifiers = sorted((item.source, item.value) for item in contributor.identifiers if item.scope == "agent")
        # Unidentified names remain source-local; names alone never merge agents.
        agent_id = _id("agent", identifiers) if identifiers else _id("agent", record.source, record.source_id, normalize_text(contributor.name), contributor.role)
        existing = {row[0] for source, value in identifiers for row in self.connection.execute("SELECT entity_id FROM external_ids WHERE scope='agent' AND source=? AND value=?", (source, value))}
        if len(existing) == 1:
            agent_id = next(iter(existing))
        self.connection.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?)", (agent_id, contributor.name, normalize_text(contributor.name)))
        for identifier in contributor.identifiers:
            self.connection.execute("INSERT INTO external_ids VALUES(?,?,?,?,?,?)", (identifier.source, identifier.value, identifier.value, identifier.scope, agent_id, version))
        return agent_id

    def _field_assertions(self, version, record, entity_id):
        for name, value in record.model_dump(mode="json").items():
            if value is None or value == [] or value == {}:
                continue
            normalized = normalize_text(value) if isinstance(value, str) else None
            self.connection.execute("INSERT INTO field_assertions VALUES(?,?,?,?,?,?,?,?,?,?)", (_id("assertion", version, name), version, entity_id, name, _json(value), normalized, record.rights_policy_id, record.source_url, record.provider_id, record.retrieved_at))

    def stats(self) -> dict:
        db = self.connection
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("source_records", "expressions", "editions", "agents", "series", "field_assertions", "resolution_decisions")}
        counts.update(works=db.execute("SELECT count(DISTINCT canonical_id) FROM works WHERE resolved=1").fetchone()[0], unresolved_works=db.execute("SELECT count(DISTINCT canonical_id) FROM works WHERE resolved=0").fetchone()[0], work_nodes=db.execute("SELECT count(*) FROM works").fetchone()[0], active_source_records=db.execute("SELECT count(*) FROM source_heads").fetchone()[0], review_queue=db.execute("SELECT count(*) FROM review_items").fetchone()[0])
        # Same permission/scope rules as work_inputs, without reparsing every
        # source or constructing thousands of WorkInput/Pydantic objects.
        counts["classification_ready_works"] = db.execute("SELECT count(DISTINCT e.work_id) FROM evidence e JOIN works w ON w.work_id=e.work_id WHERE w.resolved=1 AND e.scope_valid=1 AND json_extract(e.evidence_json,'$.llm_input_allowed')=1").fetchone()[0]
        return counts

    def work_inputs(self) -> list[WorkInput]:
        records = self._active_records()
        members = defaultdict(list)
        for row in self.connection.execute("SELECT work_id,canonical_id FROM works WHERE resolved=1 ORDER BY canonical_id,work_id"):
            members[row["canonical_id"]].append(row["work_id"])
        # Batch the evidence read. Per-work SELECT formerly scanned the whole
        # evidence table once for every canonical work (minutes at the PoC size).
        all_evidence = defaultdict(lambda: defaultdict(list))
        for row in self.connection.execute("SELECT e.work_id,e.expression_id,e.evidence_json FROM evidence e JOIN works w ON w.work_id=e.work_id WHERE w.resolved=1 AND e.scope_valid=1 ORDER BY e.work_id,e.evidence_key"):
            evidence = Evidence.model_validate_json(row["evidence_json"])
            if evidence.llm_input_allowed:
                all_evidence[row["work_id"]][row["expression_id"]].append(evidence)
        output = []
        for canonical, nodes in members.items():
            sources = sorted((records[node] for node in nodes), key=lambda pair: (pair[1].record_kind != "work", pair[1].source, pair[1].source_id))
            representative = sources[0][1]
            authors = list(dict.fromkeys(item.name for _, record in sources for item in record.contributors if item.role == "author"))
            scoped_evidence = all_evidence.get(canonical, {})
            base = dict(work_id=canonical, title=representative.titles[0].value if representative.titles else "", authors=authors, original_language=representative.original_language, first_publication_year=representative.first_publication_year, region=list(dict.fromkeys(value for _, record in sources for value in record.region)), series=representative.series, source_record_ids=[version for version, _ in sources])
            output.append(WorkInput(**base, evidence=scoped_evidence.get(None, [])))
            for expression_id, evidence in sorted(((key, value) for key, value in scoped_evidence.items() if key is not None)):
                output.append(WorkInput(**base, expression_id=expression_id, evidence=evidence))
        return output

    def review_queue(self) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM review_items ORDER BY review_id")]

    def merge_work(self, source_id: str, target_id: str, *, reason: str, reviewer: str) -> str:
        if not reason.strip() or not reviewer.strip() or source_id == target_id:
            raise ValueError("Manual merge requires distinct work IDs, a reason and a reviewer")
        for node in (source_id, target_id):
            if not self.connection.execute("SELECT 1 FROM works WHERE work_id=?", (node,)).fetchone():
                raise KeyError(node)
        decision_id = _id("human", source_id, target_id, reason, reviewer, self.connection.execute("SELECT count(*) FROM resolution_decisions").fetchone()[0])
        with self.connection:
            self.connection.execute("INSERT INTO resolution_decisions(decision_id,left_work_id,right_work_id,status,reason,method,reviewer) VALUES(?,?,?,'same',?,'human',?)", (decision_id, source_id, target_id, reason, reviewer))
            self.connection.execute("INSERT INTO resolution_events(decision_id,event_type,details_json) VALUES(?,'merge',?)", (decision_id, _json(dict(reason=reason, reviewer=reviewer))))
            self._rebuild()
        return decision_id

    def undo(self, decision_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM resolution_decisions WHERE decision_id=?", (decision_id,)).fetchone()
        if row is None:
            raise KeyError(decision_id)
        with self.connection:
            self.connection.execute("UPDATE resolution_decisions SET active=0,undone=1 WHERE decision_id=?", (decision_id,))
            self.connection.execute("INSERT INTO resolution_events(decision_id,event_type,details_json) VALUES(?,'undo','{}')", (decision_id,))
            self._rebuild()
        return self.stats()

    def undo_ingest(self, version_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM source_records WHERE version_id=?", (version_id,)).fetchone()
        if row is None:
            raise KeyError(version_id)
        current = self.connection.execute("SELECT version_id FROM source_heads WHERE source=? AND source_id=?", (row["source"], row["source_id"])).fetchone()
        if current is None or current[0] != version_id:
            raise ValueError("Only the currently active source version can be undone")
        with self.connection:
            event = self.connection.execute("SELECT * FROM ingestion_events WHERE source=? AND source_id=? AND version_id=? AND event_type='ingest' AND active=1 ORDER BY event_id DESC LIMIT 1", (row["source"], row["source_id"], version_id)).fetchone()
            previous_version = event["previous_version_id"] if event else row["parent_version_id"]
            if event:
                self.connection.execute("UPDATE ingestion_events SET active=0 WHERE event_id=?", (event["event_id"],))
            if previous_version:
                self.connection.execute("UPDATE source_heads SET version_id=? WHERE source=? AND source_id=?", (previous_version, row["source"], row["source_id"]))
            else:
                self.connection.execute("DELETE FROM source_heads WHERE source=? AND source_id=?", (row["source"], row["source_id"]))
            self.connection.execute("INSERT INTO ingestion_events(source,source_id,version_id,event_type,active) VALUES(?,?,?,'undo',0)", (row["source"], row["source_id"], version_id))
            self._rebuild()
        return self.stats()

    def export(self, path: str | Path) -> dict:
        """Local audit snapshot including restricted records; no redistribution claim."""
        tables = [row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        snapshot = {"schema_version": 3, "export_purpose": "local_audit", "stats": self.stats(), "tables": {table: [dict(row) for row in self.connection.execute(f'SELECT * FROM "{table}"')] for table in tables}}
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(path), **snapshot["stats"]}
