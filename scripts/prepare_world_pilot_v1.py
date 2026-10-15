"""Freeze new saved novel inputs and exact licensed content, without model calls.

Reads the small saved candidate inventories and selected raw responses only.
The original main database is read-only; all source versions are newly ingested
into the explicitly new output namespace. Existing classifiers are never run.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace

from recommend_book.database import DatasetDB
from recommend_book.ingestion.wikidata import claim_values
from recommend_book.models import SourceRecord, WorkInput
from recommend_book.normalization import normalize_text
from recommend_book.rights import RightsRegistry, USAGES
from recommend_book.usage_guard import UsageGuard, build_input_entry, model_hash, source_binding
from recommend_book.wikipedia_paragraphs import build_evidence

VERSION = "phase7-world-pilot-v1"
FIRST_TEN = (
    "Q100997892", "Q1051999", "Q1034591", "Q111904001", "Q1075013",
    "Q105840426", "Q10262050", "Q112489757", "Q106103769", "Q11251448",
)
NEW_FIRST_TEN = (
    "Q100997892", "Q1034591", "Q111904001", "Q105840426", "Q10262050",
    "Q112489757", "Q106103769", "Q10936605", "Q1083331", "Q108006399",
)
BASE = Path("data/autonomous-next-v3")
MAIN = BASE / "database/final-delivery-main-v1/classified-database/dataset.sqlite"
OUTPUT = BASE / "preparation/phase7-world-pilot-v1"
WD = Path("data/wikidata-poc-v2/works-enriched.jsonl")
WP = Path("data/wikipedia-rescreen-v2/records.jsonl")
OFFICIAL = BASE / "rights/phase7-world-pilot-v1/official-conditions.json"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf8")


def records(path):
    with Path(path).open(encoding="utf8") as stream:
        for number, line in enumerate(stream, 1):
            yield number, SourceRecord.model_validate_json(line)


def scope_issues(record, entity):
    """Reject editions, series, shorts and conflicting literary-form assertions."""
    values = lambda prop: [v.get("id") for v in claim_values(entity, prop) if isinstance(v, dict)]
    all_values = lambda prop: [s.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                              for s in entity.get("claims", {}).get(prop, [])
                              if s.get("rank") != "deprecated" and isinstance(s.get("mainsnak", {}).get("datavalue", {}).get("value"), dict)]
    issues = []
    if record.work_kind not in ("novel", "light_novel") or not set(values("P31") + values("P7937")) & {"Q8261", "Q747381"}:
        issues.append("novel_or_light_novel_assertion_absent")
    if all_values("P629") or "Q3331189" in all_values("P31"):
        issues.append("edition_assertion")
    if set(all_values("P31") + all_values("P7937")) & {"Q49084", "Q277759", "Q1667921"}:
        issues.append("short_story_or_series_assertion")
    if record.series:
        issues.append("series_member_deferred_in_initial_scope")
    if not record.titles or re.fullmatch(r"Q\d+", record.titles[0].value):
        issues.append("human_title_absent")
    if not record.contributors or any(re.fullmatch(r"Q\d+", c.name) for c in record.contributors if c.role == "author"):
        issues.append("author_name_not_available")
    return issues


def prepare(output=OUTPUT):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(Path.cwd().resolve()):
        raise ValueError("Output must be a new directory within this workspace")
    registry_path = Path("config/sources.json").resolve()
    registry = RightsRegistry.from_file(registry_path)
    official = json.loads(OFFICIAL.read_bytes())
    # Selected raw pages are reused only after matching their complete bytes.
    wp_by_qid = {next(i.value for i in r.identifiers if i.source == "wikidata"): (n, r)
                 for n, r in records(WP)}
    database = sqlite3.connect(MAIN.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    database.execute("PRAGMA query_only=ON")
    main_ids = {r[0] for r in database.execute("SELECT source_id FROM source_records WHERE source='wikidata'")}
    candidates, excluded = [], []
    for number, record in records(WD):
        qid = record.source_id
        if qid in main_ids or qid not in wp_by_qid:
            continue
        wp_number, article = wp_by_qid[qid]
        synopsis = [e for e in article.evidence if e.kind == "synopsis"]
        if sum(len(e.text) for e in synopsis) < 700:
            continue
        if record.work_kind not in ("novel", "light_novel"):
            continue
        raw = Path(record.raw_path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.raw_sha256:
            raise ValueError("Wikidata selected raw changed")
        entity = json.loads(raw)["entities"][qid]
        issues = scope_issues(record, entity)
        matches = []
        for ident in record.identifiers:
            matches.extend(dict(source=ident.source, value=ident.value, entity_id=row[0])
                           for row in database.execute("SELECT DISTINCT entity_id FROM external_ids WHERE source=? AND value=? AND scope=?",
                                                      (ident.source, ident.value, ident.scope)))
        title_matches = list(database.execute("SELECT DISTINCT work_id FROM titles WHERE normalized_value=?", (normalize_text(record.titles[0].value),)))
        if matches or title_matches:
            issues.append("existing_external_id_or_exact_title_candidate")
        if issues:
            excluded.append({"source_id": qid, "reasons": issues, "external_matches": matches,
                             "exact_title_matches": [x[0] for x in title_matches]})
            continue
        candidates.append((number, record, wp_number, article))
    database.close()
    by_id = {r.source_id: (n, r, wn, w) for n, r, wn, w in candidates}
    missing = set(FIRST_TEN) - set(by_id)
    if missing:
        raise ValueError("First-ten identity/scope failed: " + repr(sorted(missing)))
    # Fixed first ten, then round-robin by original language and QID. <=100 total.
    selected = [by_id[qid] for qid in FIRST_TEN]
    remainder = sorted((r for r in candidates if r[1].source_id not in FIRST_TEN), key=lambda r: r[1].source_id)
    groups = {}
    for row in remainder:
        groups.setdefault(row[1].original_language or "unknown", []).append(row)
    while groups and len(selected) < 100:
        for language in sorted(list(groups)):
            if len(selected) == 100:
                break
            selected.append(groups[language].pop(0))
            if not groups[language]:
                del groups[language]
    output.mkdir(parents=True)
    prepared, inventory, raw_cache, evidence_audit = [], [], {}, []
    for wd_line, record, wp_line, article in selected:
        language = article.extra["article_language"]
        key = article.raw_path
        if key not in raw_cache:
            payload = Path(key).read_bytes()
            if hashlib.sha256(payload).hexdigest() != article.raw_sha256:
                raise ValueError("Wikipedia selected raw changed")
            raw_cache[key] = json.loads(payload)
        pages = raw_cache[key]["query"]["pages"]
        page = next(p for p in pages if p.get("pageprops", {}).get("wikibase_item") == record.source_id)
        artifact = SimpleNamespace(path=str(Path(key).resolve()), sha256=article.raw_sha256,
                                   source_url="https://" + language + ".wikipedia.org/w/api.php")
        evidence, proof, outcomes = build_evidence(record, page, artifact, language)
        evidence = [e for e in evidence if e.kind == "synopsis"]
        if sum(len(e.text) for e in evidence) < 700:
            raise ValueError("New complete screen no longer supplies substantive synopsis: " + record.source_id)
        # An explicitly new mixed-source proposal; WP evidence keeps its own licence.
        proposal = record.model_copy(deep=True)
        proposal.evidence = evidence
        proposal.raw_path = str(Path(proposal.raw_path).resolve())
        prepared.append(proposal)
        evidence_audit.append({"source_id": record.source_id, "outcomes": outcomes, "evidence_proofs": proof})
        inventory.append({"ordinal": len(inventory) + 1, "source_id": record.source_id,
            "title": proposal.titles[0].value, "authors": [c.name for c in proposal.contributors if c.role == "author"],
            "work_kind": proposal.work_kind, "original_language": proposal.original_language,
            "first_publication_year": proposal.first_publication_year, "region": proposal.region,
            "original_main_duplicate": False, "source_inventory": str(WD), "source_inventory_line": wd_line,
            "source_record_sha256": model_hash(record), "article_inventory": str(WP), "article_inventory_line": wp_line,
            "article_record_sha256": model_hash(article), "raw_path": proposal.raw_path, "raw_sha256": proposal.raw_sha256,
            "wikipedia_raw_path": artifact.path, "wikipedia_raw_sha256": artifact.sha256,
            "wikipedia_language": language, "wikipedia_revision_id": page["revisions"][0]["revid"],
            "evidence_ids": [e.evidence_id for e in evidence], "synopsis_chars": sum(len(e.text) for e in evidence),
            "scope": "individual novel work; Wikipedia authored synopsis; no original/translation prose-style claim",
            "expression_id": None, "edition_assertions": [], "matching_method": "exact Wikidata work ID + raw sitelink + pageprops; no title-based merge"})
    with DatasetDB(output / "source-bindings.sqlite") as target:
        target.ingest(prepared)
        works_by_qid = {}
        for work in target.work_inputs():
            ref = work.source_record_ids[0]
            qid = target.connection.execute("SELECT source_id FROM source_records WHERE version_id=?", (ref,)).fetchone()[0]
            works_by_qid[qid] = work
    works = [works_by_qid[r["source_id"]] for r in inventory]
    input_path = output / "ordered-inputs.jsonl"
    input_path.write_text("".join(w.model_dump_json() + "\n" for w in works), encoding="utf8")
    (output / "first-ten-inputs.jsonl").write_text("".join(w.model_dump_json() + "\n" for w in works[:10]), encoding="utf8")
    ledger = {"version": "usage-ledger-v3", "registry_path": str(registry_path), "registry_file_sha256": sha(registry_path),
              "registry_sha256": model_hash([p.model_dump(mode="json") for p in registry.policies.values()]),
              "official_conditions": official, "inputs": {}, "revoked_input_hashes": [],
              "derived_artifacts": {}, "execution_recipes": {}}
    for line, work in enumerate(works, 1):
        bindings = {ref: source_binding(output / "source-bindings.sqlite", ref) for ref in work.source_record_ids}
        ledger["inputs"][model_hash(work)] = build_input_entry(work, bindings, input_ref={"path": str(input_path), "sha256": sha(input_path), "line_number": line})
        inventory[line-1].update(work_id=work.work_id, source_record_ids=work.source_record_ids, input_sha256=model_hash(work))
    ledger_path = output / "usage-ledger.json"
    write(ledger_path, ledger)
    guard = UsageGuard(ledger_path, expected_ledger_sha256=sha(ledger_path))
    receipts = {usage: [guard.check_input(work, usage=usage).model_dump(mode="json") for work in works] for usage in USAGES}
    write(output / "admission-receipts.json", receipts)
    failures = [dict(usage=usage, work_id=works[i].work_id, reasons=r["reasons"]) for usage, items in receipts.items() for i, r in enumerate(items) if not r["allowed"]]
    write(output / "candidate-inventory.json", inventory)
    write(output / "excluded-candidates.json", excluded)
    write(output / "evidence-screening.json", evidence_audit)
    manifest = {"version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "ready" if not failures else "held", "first_batch_n": 10, "maximum_fixed_n": len(works),
        "acquisitions": 0, "model_calls": 0, "original_database_mutations": 0,
        "first_ten_source_ids": list(FIRST_TEN), "new_novel_n": len(works), "bibliographic_only_n": 0,
        "original_languages": dict(Counter(w.original_language for w in works)),
        "input_path": str(input_path), "input_sha256": sha(input_path), "ledger_path": str(ledger_path), "ledger_sha256": sha(ledger_path),
        "source_database_path": str(output / "source-bindings.sqlite"), "script_sha256": sha(__file__),
        "guard_sha256": sha("src/recommend_book/usage_guard.py"), "official_conditions_path": str(OFFICIAL.resolve()),
        "official_conditions_sha256": sha(OFFICIAL), "admission_failures": failures,
        "expansion_condition": "classify first ten once; inspect scope, rights, failures and adoption before adding any remaining fixed inputs",
        "independent_precision_recall": "not evaluated", "human_review": False}
    write(output / "manifest.json", manifest)
    if failures:
        raise ValueError("Complete admission failed; see admission-receipts.json")
    print(json.dumps({k: manifest[k] for k in ("state", "first_batch_n", "maximum_fixed_n", "original_languages", "ledger_sha256")}))


def finalize_existing(output=OUTPUT):
    """Preserve the first admission attempt and require stronger mixed-source replay.

    Unconditional acquisition/storage entries in the general registry cannot
    admit a Wikipedia evidence model enclosed in a Wikidata proposal record.
    Making those two purposes conditional adds the existing full raw and
    attribution checks; it grants no new purpose, field or source permission.
    """
    output = Path(output).resolve()
    final_path = output / "usage-ledger-admitted.json"
    if final_path.exists():
        raise ValueError("Final admission already exists; never replace it")
    original = output / "usage-ledger.json"
    original_manifest = output / "manifest.json"
    ledger = json.loads(original.read_bytes())
    registry_json = json.loads(Path(ledger["registry_path"]).read_bytes())
    changes = []
    for policy in registry_json["rights_policies"]:
        if policy["policy_id"] == "wikipedia-screened-prose":
            for usage in ("acquire", "local_storage"):
                if policy["usages"][usage] != "allowed":
                    raise ValueError("Unexpected baseline policy state")
                policy["usages"][usage] = "conditional"
                changes.append({"policy_id": policy["policy_id"], "usage": usage,
                                "before": "allowed", "after": "conditional",
                                "obligations": policy["obligations"]})
    registry_json["version"] += "+phase7-mixed-source-strict-v1"
    registry_path = output / "registry-mixed-source-strict-v1.json"
    write(registry_path, registry_json)
    registry = RightsRegistry.from_file(registry_path)
    ledger.update(registry_path=str(registry_path), registry_file_sha256=sha(registry_path),
                  registry_sha256=model_hash([p.model_dump(mode="json") for p in registry.policies.values()]))
    # No outstanding DatasetDB connection remains. Finalize a standalone file.
    target = output / "source-bindings.sqlite"
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Source database integrity check failed")
    write(final_path, ledger)
    works = [WorkInput.model_validate_json(line) for line in (output / "ordered-inputs.jsonl").read_text(encoding="utf8").splitlines()]
    guard = UsageGuard(final_path, expected_ledger_sha256=sha(final_path))
    receipts = {usage: [guard.check_input(work, usage=usage).model_dump(mode="json") for work in works] for usage in USAGES}
    receipt_path = output / "admission-receipts-admitted.json"
    write(receipt_path, receipts)
    failures = [dict(usage=usage, work_id=works[i].work_id, reasons=r["reasons"])
                for usage, items in receipts.items() for i, r in enumerate(items) if not r["allowed"]]
    manifest = json.loads(original_manifest.read_bytes())
    manifest.update(state="ready" if not failures else "held", ledger_path=str(final_path),
        ledger_sha256=sha(final_path), source_database_sha256=sha(target), admission_failures=failures,
        admission_receipts_path=str(receipt_path), admission_receipts_sha256=sha(receipt_path),
        original_attempt={"ledger_path": str(original), "ledger_sha256": sha(original),
                          "manifest_path": str(original_manifest), "manifest_sha256": sha(original_manifest),
                          "receipts_path": str(output / "admission-receipts.json"),
                          "receipts_sha256": sha(output / "admission-receipts.json")},
        registry_change={"path": str(registry_path), "sha256": sha(registry_path), "changes": changes,
                         "rationale": "Strictly stronger conditional replay for already licensed mixed-source acquisition/storage; no guard changes"},
        first_ten={"input_path": str(output / "first-ten-inputs.jsonl"), "input_sha256": sha(output / "first-ten-inputs.jsonl"),
                   "work_ids": [w.work_id for w in works[:10]], "input_model_hashes": [model_hash(w) for w in works[:10]],
                   "all_six_usages_allowed": all(r["allowed"] for items in receipts.values() for r in items[:10])},
        finalized_at=datetime.now(timezone.utc).isoformat(), finalizer_script_sha256=sha(__file__))
    write(output / "manifest-admitted.json", manifest)
    print(json.dumps({"state": manifest["state"], "first_ten_allowed": manifest["first_ten"]["all_six_usages_allowed"],
                      "fixed_pool_n": len(works), "failures_n": len(failures), "ledger_sha256": sha(final_path)}))
    if failures:
        raise ValueError("Stricter complete admission held")


def register_next_projections(output=OUTPUT, source=None):
    """Bind the actual next-database metadata/alias packets in a further ledger."""
    from recommend_book.next_database import get_next_inputs

    output = Path(output).resolve()
    source = Path(source or BASE / "database/phase7-world-pilot-source-v1").resolve()
    final = output / "usage-ledger-projections.json"
    if final.exists():
        raise ValueError("Projection ledger already exists")
    admitted = output / "usage-ledger-admitted.json"
    ledger = json.loads(admitted.read_bytes())
    groups = get_next_inputs(source / "dataset.sqlite")
    frozen = {w.work_id: w for w in [WorkInput.model_validate_json(line) for line in
              (output / "ordered-inputs.jsonl").read_text(encoding="utf8").splitlines()]}
    if any(w != frozen.get(w.work_id) for w in groups["classification"]) or len(groups["classification"]) != len(frozen):
        raise ValueError("Actual next classification projections differ from frozen inputs")
    for kind in ("bibliographic", "aliases"):
        path = source / (kind + "-inputs.jsonl")
        for line_number, line in enumerate(path.read_text(encoding="utf8").splitlines(), 1):
            work = WorkInput.model_validate_json(line)
            bindings = {ref: source_binding(source / "dataset.sqlite", ref) for ref in work.source_record_ids}
            ledger["inputs"][model_hash(work)] = build_input_entry(work, bindings,
                input_ref={"path": str(path), "sha256": sha(path), "line_number": line_number})
    packets_path = source / "derivations.jsonl"
    packets = [json.loads(line) for line in packets_path.read_text(encoding="utf8").splitlines()]
    for line_number, packet in enumerate(packets, 1):
        ledger["derived_artifacts"][model_hash(packet["payload"])] = {
            "kind": packet["kind"], "value_sha256": packet["value_sha256"],
            "originating_input_hashes": packet["originating_input_hashes"],
            "raw_path": str(packets_path), "raw_sha256": sha(packets_path),
            "format": "jsonl", "line_number": line_number, "locator": "/payload", "recipe": packet["recipe"]}
    write(final, ledger)
    guard = UsageGuard(final, expected_ledger_sha256=sha(final))
    receipts = []
    for usage in ("display", "commercial"):
        for packet in packets:
            decision = guard.check_derived(packet["payload"], packet["originating_input_hashes"], usage)
            receipts.append({"kind": packet["kind"], "work_id": packet["payload"]["work_id"],
                             "decision": decision.model_dump(mode="json")})
    receipt_path = output / "projection-admission-receipts.json"
    write(receipt_path, receipts)
    failures = [r for r in receipts if not r["decision"]["allowed"]]
    report = {"state": "ready" if not failures else "held", "ledger_path": str(final), "ledger_sha256": sha(final),
        "previous_admitted_ledger": {"path": str(admitted), "sha256": sha(admitted)},
        "actual_next_source_database": {"path": str(source / "dataset.sqlite"), "sha256": sha(source / "dataset.sqlite")},
        "classification_models_match_frozen": True, "counts": {k: len(v) for k, v in groups.items()},
        "derived_packets": len(packets), "receipts": {"path": str(receipt_path), "sha256": sha(receipt_path)},
        "failures_n": len(failures), "new_model_calls": 0, "new_acquisitions": 0}
    write(output / "projection-manifest.json", report)
    print(json.dumps(report))
    if failures:
        raise ValueError("Actual next projection derivation admission held")


def exclude_current_display(output=OUTPUT):
    """Freeze genuinely new displayed works without replacing the original pool."""
    output = Path(output).resolve()
    destination = output / "new-selection-manifest.json"
    if destination.exists():
        raise ValueError("New selection is already frozen")
    current = BASE / "database/phase3-personal-poc-with-declared-genres-v2/delivery.json"
    previous = json.loads(current.read_bytes())
    old = {row["work_id"] for row in previous["ranking"]}
    inventory = json.loads((output / "candidate-inventory.json").read_bytes())
    works = [WorkInput.model_validate_json(line) for line in (output / "ordered-inputs.jsonl").read_text(encoding="utf8").splitlines()]
    by_qid = {row["source_id"]: work for row, work in zip(inventory, works)}
    first = [by_qid[qid] for qid in NEW_FIRST_TEN]
    if any(work.work_id in old for work in first):
        raise ValueError("New first ten still overlaps current display")
    first_ids = {work.work_id for work in first}
    chosen = first + [work for work in works if work.work_id not in old | first_ids]
    excluded = [row for row in inventory if row["work_id"] in old]
    paths = {}
    for key, subset in (("ordered", chosen), ("first-ten", chosen[:10]), ("remaining", chosen[10:])):
        path = output / ("new-" + key + "-inputs.jsonl")
        path.write_text("".join(work.model_dump_json() + "\n" for work in subset), encoding="utf8")
        paths[key] = {"path": str(path), "sha256": sha(path), "n": len(subset)}
    report = {"version": "phase7-new-display-selection-v1", "fixed_pool_n": len(chosen),
        "first_batch_n": 10, "remaining_n": len(chosen) - 10, "original_pool_n": len(works),
        "previously_displayed_excluded_n": len(excluded), "excluded": excluded,
        "latest_display": {"path": str(current.resolve()), "sha256": sha(current), "work_count": len(old)},
        "first_ten_source_ids": list(NEW_FIRST_TEN), "first_ten_work_ids": [w.work_id for w in first],
        "first_ten_original_languages": dict(Counter(w.original_language for w in first)),
        "selected_work_ids": [w.work_id for w in chosen], "files": paths,
        "ledger": {"path": str(output / "usage-ledger-projections.json"), "sha256": sha(output / "usage-ledger-projections.json")},
        "source_database": str((BASE / "database/phase7-world-pilot-source-v1/dataset.sqlite").resolve()),
        "model_calls": 0, "original_inputs_unchanged": True,
        "note": "Main database and old native indexes alone missed declared-genre supplements; current 1989-work display is an additional exclusion baseline."}
    write(destination, report)
    print(json.dumps({key: report[key] for key in ("fixed_pool_n", "first_batch_n", "remaining_n", "previously_displayed_excluded_n", "files", "ledger")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--finalize-existing", action="store_true")
    parser.add_argument("--register-next-projections", action="store_true")
    parser.add_argument("--next-source", type=Path)
    parser.add_argument("--exclude-current-display", action="store_true")
    args = parser.parse_args()
    if args.exclude_current_display:
        exclude_current_display(args.output)
    elif args.register_next_projections:
        register_next_projections(args.output, args.next_source)
    else:
        (finalize_existing if args.finalize_existing else prepare)(args.output)
