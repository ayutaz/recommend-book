"""Freeze work-level classification inputs and an identical 100-work pilot."""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path

from recommend_book.database import DatasetDB
from recommend_book.poc import year_group


def pilot_inputs(inputs, size=100, seed=1729):
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise ValueError("Pilot size must be a positive integer")
    if len(inputs) < size or len({w.work_id for w in inputs}) != len(inputs):
        raise ValueError("Pilot needs enough distinct work-level inputs")
    quotas = {"ja": round(size * .35), "en": round(size * .35)}
    quotas["other"] = size - sum(quotas.values())
    pools = defaultdict(lambda: defaultdict(list))
    def order(work):
        return hashlib.sha256(f"{seed}:{work.work_id}".encode()).hexdigest()
    for work in inputs:
        if work.expression_id is not None:
            raise ValueError("Work-level pilot excludes expression-specific input rows")
        language = work.original_language or "unknown"
        group = language if language in {"ja", "en"} else "other"
        kinds = {e.kind for e in work.evidence if e.llm_input_allowed}
        depth = next((k for k in ("excerpt", "synopsis", "note") if k in kinds), "facts")
        pools[group][(language, year_group(work.first_publication_year), depth)].append(work)
    selected, remaining, deficits = [], [], {}
    for group, quota in quotas.items():
        cells = {key: deque(sorted(rows, key=order)) for key, rows in sorted(pools[group].items())}
        # Give every original language a turn before taking more year/depth
        # cells. Flattened lexical cells exhausted the 30-work quota before
        # reaching several languages that exist in the actual PoC frame.
        language_cells = defaultdict(deque)
        for (language, _, _), queue in cells.items():
            language_cells[language].append(queue)
        language_order = sorted(language_cells, key=lambda language:
            hashlib.sha256(f'{seed}:language:{language}'.encode()).hexdigest())
        picked = []
        while len(picked) < quota and any(language_cells.values()):
            for language in language_order:
                queues = language_cells[language]
                if queues and len(picked) < quota:
                    queue = queues.popleft()
                    picked.append(queue.popleft())
                    if queue:
                        queues.append(queue)
        deficits[group] = quota - len(picked)
        selected.extend(picked)
        remaining.extend(w for queue in cells.values() for w in queue)
    selected.extend(sorted(remaining, key=order)[:size - len(selected)])
    selected.sort(key=order)
    return selected, deficits


def write_inputs(path, inputs):
    content = "".join(w.model_dump_json() + "\n" for w in inputs).encode()
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def group_identity_tokens(connection, work_ids):
    """Read author/series authority links from the same DB snapshot as inputs.

    DB IDs capture resolved authorities. Scope-specific external IDs bridge
    separate DB entities carrying the same authority identifier. Names remain
    an additional grouping fallback in evaluation, never an identity assertion.
    """
    tokens={work:set() for work in work_ids}
    canonical={node:root or node for node,root in connection.execute('SELECT work_id,canonical_id FROM works')}
    external=defaultdict(set)
    external_by_version=defaultdict(set)
    for source,value,normalized,scope,entity,version in connection.execute(
            "SELECT source,value,normalized_value,scope,entity_id,source_version_id FROM external_ids WHERE scope IN ('agent','series')"):
        value=normalized or value
        if not source or not value:
            continue
        token=json.dumps([source.casefold(),value],ensure_ascii=False,separators=(',',':'))
        external[(scope,entity)].add(token)
        if scope=='series':
            external_by_version[version].add(token)
    for agent,work in connection.execute("SELECT agent_id,work_id FROM contributions WHERE role='author' AND work_id IS NOT NULL"):
        work=canonical.get(work,work)
        if work in tokens:
            tokens[work].add('author:db:'+agent)
            tokens[work].update('author:external:'+token for token in external[('agent',agent)])
    for node,series,version in connection.execute(
            "SELECT work_id,series_id,source_version_id FROM work_relations WHERE relation_type='series_member' AND series_id IS NOT NULL"):
        work=canonical.get(node,node)
        if work in tokens:
            tokens[work].add('series:db:'+series)
            # Current schema stores record-level series identifiers on the work
            # node. The membership source version binds them to this series.
            identifiers=external[('series',series)]|external[('series',node)]|external_by_version[version]
            tokens[work].update('series:external:'+token for token in identifiers)
    return {work:sorted(values) for work,values in sorted(tokens.items())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pilot-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Frozen snapshot output must be new; existing snapshots are immutable")
    with DatasetDB(args.database) as db:
        db.connection.execute('PRAGMA query_only=ON')
        db.connection.execute('BEGIN')
        inputs = [w for w in db.work_inputs() if w.expression_id is None]
        pilot, deficits = pilot_inputs(inputs, args.pilot_size, args.seed)
        identities=group_identity_tokens(db.connection,[work.work_id for work in inputs])
        snapshot = {"database": str(args.database.resolve()), "stats": db.stats(), "selected_work_ids": [w.work_id for w in inputs]}
        args.output.mkdir(parents=True)
        identity_content=json.dumps(identities,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        (args.output/'group-identities.json').write_bytes(identity_content)
        manifest = {"seed": args.seed, "work_count": len(inputs), "pilot_count": len(pilot), "work_inputs_sha256": write_inputs(args.output / "work-inputs.jsonl", inputs), "pilot_inputs_sha256": write_inputs(args.output / "pilot100.jsonl", pilot), "pilot_original_languages": dict(Counter(w.original_language or "unknown" for w in pilot)), "pilot_year_groups": dict(Counter(year_group(w.first_publication_year) for w in pilot)), "pilot_evidence_kinds": dict(Counter(e.kind for w in pilot for e in w.evidence)), "pilot_stratum_deficits": deficits, "sampling": "within-frame nested original-language then year/evidence-depth round-robin for operational pilot; no world representativeness claim", "snapshot": snapshot, "human_quality_verified": False}
        manifest['group_identities_file']='group-identities.json'
        manifest['group_identities_sha256']=hashlib.sha256(identity_content).hexdigest()
        manifest['grouping_note']='Author agent IDs, series membership IDs, scope-specific external IDs, and evaluation display-name fallback; no guarantee for missing authority links.'
        (args.output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in manifest.items() if key != "snapshot"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
