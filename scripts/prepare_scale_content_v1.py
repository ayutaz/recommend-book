"""Prepare a bounded, provenance-pinned content input set for scale-v1.

The producer is deliberately offline with respect to the network: it reuses the
already saved and screened Wikipedia records, and joins them to the current
catalogue by the explicit Wikidata work identifier.  It never copies the
catalogue or the prose corpus and never starts an LLM process.  The output is
an ignored operational handoff for the scale native runner; each chunk has at
most 100 works so a later runner can pin only the inputs it actually uses.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Iterable
from urllib.parse import urlparse

from recommend_book.models import Evidence, SourceRecord, WorkInput

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGUE = ROOT / "data/autonomous-next-v3/scale-v1/catalogue.sqlite"
DEFAULT_SOURCE = ROOT / "data/wikipedia-rescreen-v2/records.jsonl"
DEFAULT_RIGHTS = ROOT / "data/autonomous-next-v3/classification/world-pilot-v2/usage-ledger.source.json"
POLICY = "wikipedia-screened-prose"
VERSION = "scale-content-preparation-v1"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha_stream(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n")


def write_jsonl(path: Path, rows: Iterable[object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b"".join(canonical(row) + b"\n" for row in rows)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _source_qid(record: SourceRecord) -> str:
    values = [i.value for i in record.identifiers if i.source == "wikidata" and i.scope == "work"]
    if len(values) != 1 or not values[0].startswith("Q"):
        raise ValueError(f"content record must have exactly one Wikidata work identifier: {record.source_id}")
    return values[0]


def _record_sha(record: SourceRecord) -> str:
    return hashlib.sha256(canonical(record.model_dump(mode="json"))).hexdigest()


def _read_records(path: Path) -> list[SourceRecord]:
    result = []
    for line_no, line in enumerate(path.read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        try:
            result.append(SourceRecord.model_validate(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"invalid content SourceRecord at {path}:{line_no}: {exc}") from exc
    return result


def _authority_rows(connection: sqlite3.Connection) -> dict[str, dict]:
    rows = {}
    for row in connection.execute("SELECT qid,version_id,record_sha256,raw_path,raw_sha256,record_json FROM source_versions"):
        qid, version_id, record_sha, raw_path, raw_sha, record_json = row
        rows[qid] = {"qid": qid, "version_id": version_id, "record_sha256": record_sha,
                     "raw_path": raw_path, "raw_sha256": raw_sha,
                     "record_json": json.loads(record_json)}
    return rows


def _catalogue_rows(connection: sqlite3.Connection) -> dict[str, dict]:
    return {row[0]: json.loads(row[1]) for row in connection.execute(
        "SELECT qid,row_json FROM works WHERE recommendable=1 AND display_allowed=1")}


def _native_qids(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT qid FROM native_classifications")}


def _content_evidence(record: SourceRecord) -> list[Evidence]:
    return [e for e in record.evidence if e.rights_policy_id == POLICY and e.kind == "synopsis"
            and e.llm_input_allowed and len(e.text.strip()) >= 100]


def _check_raw(record: SourceRecord) -> dict:
    path = Path(record.raw_path).resolve(strict=True)
    digest = sha(path)
    if digest != record.raw_sha256:
        raise ValueError(f"content raw SHA differs: {record.source_id}")
    identity = next((e.attribution.get("identity_proof", {}) for e in record.evidence
                     if e.attribution.get("identity_proof")), {})
    qid = _source_qid(record)
    if identity and identity.get("wikidata_work_id") != qid:
        raise ValueError(f"content identity proof differs: {record.source_id}")
    parsed = urlparse(record.source_url)
    if (parsed.scheme != "https" or not parsed.netloc.endswith(".wikipedia.org")
            or not parsed.path.endswith("/w/index.php") or not parsed.query.startswith("oldid=")):
        raise ValueError(f"content record lacks immutable revision URL: {record.source_id}")
    if any(e.source_url != record.source_url or "revision:" not in (e.locator or "") for e in _content_evidence(record)):
        raise ValueError(f"content evidence lacks revision locator: {record.source_id}")
    return {"path": str(path), "sha256": digest, "source_url": record.source_url,
            "source_id": record.source_id, "qid": qid, "record_sha256": _record_sha(record)}


def _check_wikipedia_identity(record: SourceRecord, qid: str, raw_pin: dict) -> dict:
    """Bind the saved article response to the work QID and revision."""
    payload = json.loads(Path(raw_pin["path"]).read_bytes())
    pages = payload.get("query", {}).get("pages", [])
    matches = [page for page in pages if page.get("pageprops", {}).get("wikibase_item") == qid]
    if len(matches) != 1:
        raise ValueError(f"Wikipedia pageprops QID differs: {record.source_id}")
    page = matches[0]
    page_id = record.extra.get("page_id")
    if page_id is not None and str(page.get("pageid")) != str(page_id):
        raise ValueError(f"Wikipedia pageid differs: {record.source_id}")
    revision_id = record.extra.get("revision_id")
    revisions = page.get("revisions", [])
    if revision_id is not None and not any(str(item.get("revid")) == str(revision_id) for item in revisions):
        raise ValueError(f"Wikipedia revision differs: {record.source_id}")
    article_title = record.extra.get("article_title")
    if article_title is not None and page.get("title") != article_title:
        raise ValueError(f"Wikipedia article title differs: {record.source_id}")
    return {
        "wikipedia_page_id": page.get("pageid"),
        "wikipedia_page_title": page.get("title"),
        "wikipedia_pageprops_wikibase_item": page.get("pageprops", {}).get("wikibase_item"),
        "wikipedia_revision_id": revision_id,
        "wikipedia_raw_path": raw_pin["path"],
        "wikipedia_raw_sha256": raw_pin["sha256"],
    }


def _hydrate_rights_and_identity(record: SourceRecord, qid: str, authority: dict, raw_pin: dict) -> SourceRecord:
    """Promote the saved producer's rights receipt into each Evidence object.

    The older rescreen producer kept these values in SourceRecord.extra.  The
    native prompt/guard receives Evidence, so leaving them only in extra would
    silently lose attribution and license pins at the LLM boundary.
    """
    source_attribution = dict(record.extra.get("attribution") or {})
    source_attribution.setdefault("fulfilled_obligations", record.extra.get("fulfilled_obligations", []))
    source_attribution.setdefault("license_scope", record.extra.get("license_scope"))
    existing_identity = dict(source_attribution.get("identity_proof") or {})
    raw_identity = _check_wikipedia_identity(record, qid, raw_pin)
    if existing_identity.get("wikidata_work_id") not in (None, qid):
        raise ValueError(f"existing identity proof differs: {record.source_id}")
    source_attribution["identity_proof"] = {
        **existing_identity,
        "wikidata_work_id": qid,
        "wikidata_authority_version_id": authority["version_id"],
        "wikidata_authority_record_sha256": authority["record_sha256"],
        "wikidata_authority_raw_path": authority["raw_path"],
        "wikidata_authority_raw_sha256": authority["raw_sha256"],
        "content_source_record_id": record.source_id,
        "content_source_record_sha256": _record_sha(record),
        **raw_identity,
    }
    inherited = record.extra.get("inherited_text_restrictions", [])
    evidence = []
    for item in record.evidence:
        if item.rights_policy_id != POLICY:
            evidence.append(item)
            continue
        evidence.append(item.model_copy(update={
            "license": item.license or record.extra.get("text_license"),
            "license_url": item.license_url or record.extra.get("license_url"),
            "attribution": {**source_attribution, **(item.attribution or {})},
            "inherited_restrictions": item.inherited_restrictions or inherited,
        }))
    extra = dict(record.extra)
    extra["scale_content_authority"] = {"qid": qid, **{k: authority[k] for k in ("version_id", "record_sha256", "raw_path", "raw_sha256")}}
    return record.model_copy(update={"evidence": evidence, "extra": extra})


def _work_input(qid: str, catalogue: dict, record: SourceRecord, authority: dict) -> WorkInput:
    # The scale catalogue's canonical public identity is wd:<QID>.  Keeping
    # this identity here lets the native handoff join by QID without inventing
    # a title-based merge or rebinding an older work ID.
    authors = [a.get("name") or a.get("qid") for a in catalogue.get("authors", [])]
    evidence = [e.model_copy(deep=True) for e in _content_evidence(record)]
    content_id = f"wp:{qid}:{record.source_id}"
    return WorkInput(work_id="wd:" + qid, title=catalogue["title"], authors=authors,
                     original_language=catalogue.get("original_language"),
                     first_publication_year=catalogue.get("year"),
                     region=[r.get("label") or r.get("qid") for r in catalogue.get("regions", [])],
                     evidence=evidence, source_record_ids=[authority["version_id"], content_id])


def _rights_receipt(rights_path: Path, config_path: Path) -> dict:
    ledger = json.loads(rights_path.read_bytes())
    saved_conditions = ledger.get("official_conditions", {})
    conditions = (saved_conditions.get(POLICY, []) if isinstance(saved_conditions, dict)
                  else saved_conditions)
    if not conditions:
        raise ValueError("saved official Wikipedia conditions are missing")
    pinned = []
    for item in conditions:
        raw = Path(item["raw_path"]).resolve(strict=True)
        if sha(raw) != item["raw_sha256"]:
            raise ValueError(f"official condition changed: {raw}")
        pinned.append({"source_url": item.get("source_url"), "final_url": item.get("final_url"),
                       "raw_path": str(raw), "raw_sha256": item["raw_sha256"],
                       "verified_at": item.get("verified_at"),
                       "verification_scope": item.get("verification_scope")})
    source_config = json.loads(config_path.read_bytes())
    policy = next(p for p in source_config["rights_policies"] if p["policy_id"] == POLICY)
    return {"version": "scale-content-rights-receipt-v1", "policy_id": POLICY,
            "policy_sha256": hashlib.sha256(canonical(policy)).hexdigest(),
            "config_path": str(config_path.resolve()), "config_sha256": sha(config_path),
            "official_conditions_source": str(rights_path.resolve()),
            "official_conditions_source_sha256": sha(rights_path),
            "official_conditions": pinned,
            "usage_decisions": {
                "acquire": "allowed_from_saved_snapshot",
                "local_storage": "allowed_with_attribution_share_alike_and_changes",
                "external_llm": "conditional_allowed_for_screened_prose_with_source_pins",
                "display": "conditional_hold_raw_prose; generated_feature_requires_output_review",
                "redistribution": "conditional_hold_until_attribution_share_alike_output_review",
                "commercial": "conditional_unresolved_for_raw_text_and_generated_output"
            },
            "scope": "screened article prose only; linked Wikidata CC0 does not transfer to Wikipedia text",
            "not_claimed": ["human legal clearance", "worldwide public domain", "unconditional commercial redistribution"]}


def prepare(*, catalogue_path: Path = DEFAULT_CATALOGUE, source_path: Path = DEFAULT_SOURCE,
            output: Path, limit: int = 200, chunk_size: int = 100,
            rights_path: Path = DEFAULT_RIGHTS, config_path: Path = ROOT / "config/sources.json") -> dict:
    if limit < 1 or limit > 1000 or chunk_size < 1 or chunk_size > 100:
        raise ValueError("limit must be 1..1000 and chunk_size must be 1..100")
    catalogue_path = catalogue_path.resolve(strict=True); source_path = source_path.resolve(strict=True)
    output = output.resolve()
    if output.exists():
        raise ValueError("output namespace must be new")
    content_records = _read_records(source_path)
    with sqlite3.connect(catalogue_path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        catalogue = _catalogue_rows(db); native = _native_qids(db); authorities = _authority_rows(db)
    candidates = []
    rejected = Counter()
    seen = set()
    for record in content_records:
        try:
            qid = _source_qid(record)
            if qid in seen: rejected["duplicate_qid"] += 1; continue
            seen.add(qid)
            if record.source != "wikipedia" or record.provider_id != "wikipedia": rejected["wrong_provider"] += 1; continue
            if record.rights_policy_id != POLICY: rejected["wrong_policy"] += 1; continue
            if record.work_kind not in ("novel", "light_novel"): rejected["non_novel"] += 1; continue
            if qid not in catalogue: rejected["not_current_eligible_catalogue"] += 1; continue
            if qid in native: rejected["already_native"] += 1; continue
            if qid not in authorities: rejected["missing_authority_record"] += 1; continue
            authority = authorities[qid]
            raw_pin = _check_raw(record)
            record = _hydrate_rights_and_identity(record, qid, authority, raw_pin)
            evidence = _content_evidence(record)
            if not evidence: rejected["no_allowed_synopsis"] += 1; continue
            authority_raw = Path(authority["raw_path"]).resolve(strict=True)
            if sha(authority_raw) != authority["raw_sha256"]: raise ValueError(f"authority raw SHA differs: {qid}")
            work = _work_input(qid, catalogue[qid], record, authority)
            candidates.append({"qid": qid, "record": record, "work": work, "content_raw": raw_pin,
                               "authority": {**{k: authority[k] for k in ("qid", "version_id", "record_sha256", "raw_path", "raw_sha256")},
                                             "raw_path": str(authority_raw)}})
        except ValueError as exc:
            # A malformed or changed saved record is isolated from the
            # checkpoint instead of being repaired or retried in place.
            rejected["invalid_provenance"] += 1
            continue
        except Exception as exc:
            rejected["invalid_record"] += 1
            continue
    selected = candidates[:limit]
    if len(selected) < limit:
        raise ValueError(f"only {len(selected)} admitted content works available, requested {limit}")
    selected_qids = [x["qid"] for x in selected]
    if len(set(selected_qids)) != limit: raise ValueError("selected content QIDs are not unique")
    receipt = _rights_receipt(rights_path.resolve(strict=True), config_path.resolve(strict=True))
    catalogue_stat = catalogue_path.stat()
    content_rows = [x["record"].model_dump(mode="json") for x in selected]
    work_rows = [x["work"].model_dump(mode="json") for x in selected]
    pin_rows = [{"qid": x["qid"], "work_id": x["work"].work_id, "authority": x["authority"],
                 "content": x["content_raw"], "content_record_sha256": _record_sha(x["record"]),
                 "content_record_source_id": x["record"].source_id} for x in selected]
    output.mkdir(parents=True)
    content_sha = write_jsonl(output / "content-source-records.jsonl", content_rows)
    inputs_sha = write_jsonl(output / "content-inputs.jsonl", work_rows)
    pins_sha = write_jsonl(output / "source-pins.jsonl", pin_rows)
    write_json(output / "rights-receipt.json", receipt)
    chunks = []
    for start in range(0, len(selected), chunk_size):
        chunk = selected[start:start + chunk_size]; name = f"chunk-{start + 1:04d}-{start + len(chunk):04d}"; d = output / "chunks" / name; d.mkdir(parents=True)
        csha = write_jsonl(d / "content-source-records.jsonl", [x["record"].model_dump(mode="json") for x in chunk])
        isha = write_jsonl(d / "content-inputs.jsonl", [x["work"].model_dump(mode="json") for x in chunk])
        psha = write_jsonl(d / "source-pins.jsonl", [{"qid": x["qid"], "work_id": x["work"].work_id, "authority": x["authority"], "content": x["content_raw"], "content_record_sha256": _record_sha(x["record"]), "content_record_source_id": x["record"].source_id} for x in chunk])
        chunk_manifest = {"version": "scale-content-chunk-v1", "name": name, "selected": len(chunk), "qids": [x["qid"] for x in chunk], "content_source_records_sha256": csha, "content_inputs_sha256": isha, "source_pins_sha256": psha, "rights_receipt_sha256": sha(output / "rights-receipt.json")}
        write_json(d / "manifest.json", chunk_manifest); chunks.append(chunk_manifest)
    synopsis_lengths = [len(e.text) for x in selected for e in _content_evidence(x["record"])]
    manifest = {"version": VERSION, "prepared_at": datetime.now(timezone.utc).isoformat(), "execution_state": "prepared_not_started", "llm_started": False, "network_requests": 0, "source_route": "saved_wikipedia_rescreen_v2", "source_path": str(source_path), "source_sha256": sha_stream(source_path), "catalogue_path": str(catalogue_path), "catalogue_observation": {"size": catalogue_stat.st_size, "mtime_ns": catalogue_stat.st_mtime_ns, "read_mode": "immutable_read_transaction; full DB hash intentionally omitted"}, "config_path": str(config_path.resolve()), "rights_receipt_path": str(output / "rights-receipt.json"), "rights_receipt_sha256": sha(output / "rights-receipt.json"), "candidate_record_count": len(content_records), "qualified_available_count": len(candidates), "selected_count": len(selected), "chunk_size": chunk_size, "chunk_count": len(chunks), "qids": selected_qids, "qids_sha256": hashlib.sha256(canonical(selected_qids)).hexdigest(), "languages": dict(sorted(Counter(next((e.language or "unknown" for e in x["record"].evidence if e.kind == "synopsis"), "unknown") for x in selected).items())), "content_evidence_count": len(synopsis_lengths), "synopsis_characters": {"min": min(synopsis_lengths), "max": max(synopsis_lengths), "median": statistics.median(synopsis_lengths), "total": sum(synopsis_lengths)}, "rejected_counts": dict(sorted(rejected.items())), "content_source_records_sha256": content_sha, "content_inputs_sha256": inputs_sha, "source_pins_sha256": pins_sha, "chunks": chunks, "identity_binding": "explicit Wikidata work QID in both catalogue and content SourceRecord; public work identity wd:<QID>; no title-only merge", "raw_prose_policy": "never promoted to CC0; raw display and commercial redistribution remain conditional/held", "no_old_results_rebound": True}
    write_json(output / "manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalogue", type=Path, default=DEFAULT_CATALOGUE)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--rights", type=Path, default=DEFAULT_RIGHTS)
    parser.add_argument("--config", type=Path, default=ROOT / "config/sources.json")
    args = parser.parse_args(argv)
    result = prepare(catalogue_path=args.catalogue, source_path=args.source, output=args.output, limit=args.limit, chunk_size=args.chunk_size, rights_path=args.rights, config_path=args.config)
    print(json.dumps({k: result[k] for k in ("selected_count", "qualified_available_count", "chunk_count", "languages", "content_inputs_sha256")}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
