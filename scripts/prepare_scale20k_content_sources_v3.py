"""Prepare a fresh, independently pinned source window after a failed run.

This is a recovery *source* builder.  It never retries a classification and it
does not decide whether an origin window is unattempted; the v3 coordinator
owns that reservation/exclusion check.  The builder reuses the established
content UsageGuard recipe on a new output directory and records the old source
and source-index pins as lineage.  Historical source directories and indexes
are read-only inputs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGIN_FIRST = 162
ORIGIN_LAST = 215
NEW_FIRST = ORIGIN_FIRST - 1
NEW_LAST = ORIGIN_LAST - 1
GENERATION = "scale20k-content-recovery-v2"
POLICY = "novel_content_with_display_permission_source_genre_optional"
RECOVERY_POLICY_VERSION = "scale20k-source-recovery-eligibility-v3"
EXACT_SHORTFALL_RECEIPT_VERSION = "scale20k-exact-shortfall-v1"
EXACT_SHORTFALL_TARGET = 20_000
ATTEMPT_RESERVATIONS_ROOT = (ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1"
                             / "attempt-reservations")


def sha(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_bytes())


def save_create_only(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
        stream.write(b"\n")
    return sha(path)


def canonical_run_name(window_index: int) -> str:
    """Return the v2 run name for a v20 global window (93 is the first)."""
    if type(window_index) is not int or not NEW_FIRST <= window_index <= NEW_LAST:
        raise ValueError("recovery window must map to new global 161..214")
    start = 1 + (window_index - 93) * 100
    return f"scale20k-content-{start:04d}-{start + 99:04d}-v2"


def origin_window_for(window_index: int) -> int:
    if type(window_index) is not int or not NEW_FIRST <= window_index <= NEW_LAST:
        raise ValueError("recovery window must map to new global 161..214")
    return window_index + 1


def _resolve_source(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _windows(index_document: dict[str, Any]) -> list[dict[str, Any]]:
    windows = index_document.get("windows")
    if not isinstance(windows, list):
        raise ValueError("source index windows are required")
    return windows


def _mapping_identity(path: Path) -> dict[str, str]:
    rows = read_json(path)
    if not isinstance(rows, list) or not rows:
        raise ValueError("source work-mapping must be a non-empty list")
    result = {}
    for row in rows:
        qid, input_hash = row.get("wikidata_qid"), row.get("input_sha256")
        if not isinstance(qid, str) or not isinstance(input_hash, str) or len(input_hash) != 64:
            raise ValueError("source mapping must pin each QID and input hash")
        if qid in result or input_hash in result.values():
            raise ValueError("source mapping QIDs and input hashes must be distinct")
        result[qid] = input_hash
    return result


def _validate_subset_holds(path: Path, omitted: set[str]) -> dict[str, Any]:
    """Accept a strict upstream subset only with explicit guard-held rows."""
    rows = read_json(path)
    if not isinstance(rows, list):
        raise ValueError("upstream subset hold proof must be a list")
    by_qid = {row.get("qid"): row for row in rows if isinstance(row, dict)}
    if set(by_qid) != omitted or any(row.get("status") != "guard_held" for row in by_qid.values()):
        raise ValueError("upstream subset must be exactly actual UsageGuard-held QIDs")
    return {"path": str(path.resolve()), "sha256": sha(path), "qids": sorted(omitted)}


def _catalogue_eligible_count(catalogue: Path) -> int:
    """Read the current display-eligible native count from a RO snapshot."""
    import sqlite3

    uri = catalogue.resolve(strict=True).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        return int(db.execute(
            "SELECT count(*) FROM native_classifications n JOIN works w USING(qid) "
            "WHERE w.display_allowed=1"
        ).fetchone()[0])


def _catalogue_native_overlap(catalogue: Path, qids: set[str]) -> set[str]:
    """Return selected QIDs already classified, using one bounded RO query."""
    import sqlite3

    if not qids:
        return set()
    ordered = sorted(qids)
    marks = ",".join("?" for _ in ordered)
    uri = catalogue.resolve(strict=True).as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = db.execute(
            "SELECT qid FROM native_classifications WHERE qid IN (" + marks + ")", ordered
        ).fetchall()
    return {row[0] for row in rows}


def _attempt_reservation_sets(root: Path | None = None) -> tuple[set[str], set[str]]:
    """Read the compact attempt ledger once for the final shortfall check."""
    qids: set[str] = set()
    hashes: set[str] = set()
    root = (ATTEMPT_RESERVATIONS_ROOT if root is None else root).resolve()
    if not root.is_dir():
        return qids, hashes
    for path in sorted(root.glob("*.json")):
        value = read_json(path)
        for key, target in (("qids", qids), ("input_hashes", hashes)):
            values = value.get(key, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise ValueError("attempt reservation has an invalid compact exclusion list")
            target.update(values)
    return qids, hashes


def _prior_control_run_name(window_index: int) -> str:
    if window_index == 161:
        return "scale20k-content-6701-6800-v1"
    if window_index > 161:
        return canonical_run_name(window_index - 1)
    raise ValueError("exact shortfall window has no approved prior control")


def _verify_prior_control(receipt: dict[str, Any], window_index: int) -> dict[str, Any]:
    prior = receipt.get("prior_control")
    if not isinstance(prior, dict) or not isinstance(prior.get("path"), str):
        raise ValueError("exact shortfall prior control pin is required")
    prior_path = Path(prior["path"]).resolve(strict=True)
    prior_sha = prior.get("sha256")
    if not isinstance(prior_sha, str) or len(prior_sha) != 64 or sha(prior_path) != prior_sha:
        raise ValueError("exact shortfall prior control SHA differs")
    from recommend_book.scale_content_control_v4 import verify_control_receipt
    verified = verify_control_receipt(prior_path, prior_sha)
    if verified.get("window_index") != window_index - 1 or verified.get("gate", {}).get("passed") is not True:
        raise ValueError("exact shortfall prior control is not the passed preceding window")
    if prior_path.stem != _prior_control_run_name(window_index):
        raise ValueError("exact shortfall prior control run name differs")
    return {"path": str(prior_path), "sha256": prior_sha,
            "window_index": verified["window_index"], "run_name": prior_path.stem}


def _shortfall_selection(receipt: dict[str, Any]) -> dict[str, str]:
    """Normalize the receipt's explicit QID/input-hash selection."""
    rows = receipt.get("selected")
    if not isinstance(rows, list):
        raise ValueError("exact shortfall receipt must list selected QID/input hashes")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("exact shortfall selected rows must be objects")
        qid = row.get("qid")
        digest = row.get("input_sha256", row.get("input_hash"))
        if (not isinstance(qid, str) or not qid or not isinstance(digest, str)
                or len(digest) != 64 or qid in result or digest in result.values()):
            raise ValueError("exact shortfall selected QID/input hashes must be unique")
        result[qid] = digest
    return result


def _validate_exact_shortfall(
    receipt_path: Path,
    receipt_sha256: str,
    *,
    catalogue: Path,
    origin: dict[str, Any],
    origin_index_sha256: str,
    origin_source_sha256: str,
    window_index: int,
    record_qids: list[str],
) -> tuple[dict[str, Any], dict[str, str], set[str]]:
    """Authorize only a genuinely final, smaller-than-origin source window.

    The receipt is supplied and independently pinned by the coordinator.  This
    function only admits it after re-reading the mutable catalogue in RO mode,
    checking the exact selected identity, and checking the selected authorities
    have no native row.  Attempt exclusion is pinned as a zero-overlap fact in
    the receipt and is additionally checked when the receipt supplies its
    compact attempted QID/hash sets.
    """
    receipt_path = receipt_path.resolve(strict=True)
    if len(receipt_sha256) != 64 or sha(receipt_path) != receipt_sha256:
        raise ValueError("exact shortfall receipt SHA differs")
    receipt = read_json(receipt_path)
    if receipt.get("version") != EXACT_SHORTFALL_RECEIPT_VERSION:
        raise ValueError("exact shortfall receipt version differs")
    if receipt.get("target_eligible_native") != EXACT_SHORTFALL_TARGET:
        raise ValueError("exact shortfall target differs")
    receipt_window = receipt.get("run_index", receipt.get("window_index"))
    if receipt_window != window_index:
        raise ValueError("exact shortfall run index differs")
    if receipt.get("run_name") != canonical_run_name(window_index):
        raise ValueError("exact shortfall run name differs")
    if receipt.get("origin_index_sha256", receipt.get("old_origin_index_sha256")) != origin_index_sha256:
        raise ValueError("exact shortfall origin index SHA differs")
    if receipt.get("origin_source_sha256", receipt.get("source_sha256")) != origin_source_sha256:
        raise ValueError("exact shortfall origin source SHA differs")
    prior_control = _verify_prior_control(receipt, window_index)

    eligible_count = _catalogue_eligible_count(catalogue)
    remaining = EXACT_SHORTFALL_TARGET - eligible_count
    if receipt.get("eligible_native_count") != eligible_count:
        raise ValueError("exact shortfall catalogue eligible count differs")
    if receipt.get("remaining") != remaining:
        raise ValueError("exact shortfall remaining count differs")
    if not 0 < remaining < origin["selected"]:
        raise ValueError("exact shortfall must be positive and smaller than origin")
    selected = _shortfall_selection(receipt)
    if len(selected) != remaining or set(selected) != set(record_qids):
        raise ValueError("exact shortfall selected records do not match remaining")
    if len(record_qids) != remaining:
        raise ValueError("exact shortfall records length differs from remaining")
    if any(origin["input_identity"].get(qid) != digest for qid, digest in selected.items()):
        raise ValueError("exact shortfall record input differs from origin")

    native_overlap = _catalogue_native_overlap(catalogue, set(selected))
    if native_overlap:
        raise ValueError("exact shortfall selected authority already has native classification")
    if receipt.get("existing_native_overlap_count") != 0:
        raise ValueError("exact shortfall native overlap is not zero")
    attempted_qids, attempted_hashes = _attempt_reservation_sets()
    if (set(selected) & attempted_qids) or (set(selected.values()) & attempted_hashes):
        raise ValueError("exact shortfall selected input overlaps an existing attempt")
    if receipt.get("existing_attempt_overlap_count") != 0:
        raise ValueError("exact shortfall attempt overlap is not zero")
    omitted = set(origin["qids"]) - set(selected)
    receipt["_verified_prior_control"] = prior_control
    return receipt, selected, omitted


def validate_origin(
    *,
    origin_source: Path,
    origin_source_sha256: str,
    origin_index: Path,
    origin_index_sha256: str,
    origin_window_index: int,
    origin_run_name: str,
    new_window_index: int,
) -> dict[str, Any]:
    """Validate immutable origin pins before creating any output directory."""
    if origin_window_index != origin_window_for(new_window_index):
        raise ValueError("origin and recovery window mapping differs")
    if not (ORIGIN_FIRST <= origin_window_index <= ORIGIN_LAST):
        raise ValueError("origin window is outside the approved unattempted range")
    origin_manifest = origin_source / "manifest.json"
    if len(origin_source_sha256) != 64 or sha(origin_manifest) != origin_source_sha256:
        raise ValueError("origin source manifest SHA differs")
    if len(origin_index_sha256) != 64 or sha(origin_index) != origin_index_sha256:
        raise ValueError("origin source-index SHA differs")
    document = read_json(origin_index)
    entries = [row for row in _windows(document) if row.get("window_index") == origin_window_index]
    if len(entries) != 1:
        raise ValueError("origin source-index must contain exactly one window")
    entry = entries[0]
    if entry.get("run_name") != origin_run_name:
        raise ValueError("origin run name differs from source-index")
    indexed_source = _resolve_source(ROOT, entry.get("source_path", ""))
    if indexed_source != origin_source.resolve():
        raise ValueError("origin source path differs from source-index")
    if entry.get("source_sha256") != origin_source_sha256:
        raise ValueError("origin source SHA differs from source-index")
    manifest = read_json(origin_source / "manifest.json")
    manifest_run_name = manifest.get("run_name")
    if manifest_run_name is None:
        chunks = manifest.get("chunks")
        if isinstance(chunks, list) and len(chunks) == 1:
            manifest_run_name = chunks[0].get("run_name")
    if manifest.get("window_index") != origin_window_index or manifest_run_name != origin_run_name:
        raise ValueError("origin manifest window/run binding differs")
    if manifest.get("stage") not in ("prepared_not_started", "prepared"):
        raise ValueError("origin source is not a prepared, unstarted source")
    selected = manifest.get("selected")
    if type(selected) is not int or not 1 <= selected <= 100:
        raise ValueError("origin selected count is invalid")
    mapping_path = origin_source / "work-mapping.json"
    origin_identity = _mapping_identity(mapping_path)
    if len(origin_identity) != selected:
        raise ValueError("origin mapping count differs from selected count")
    artifact_pins = {"work_mapping": {"path": str(mapping_path.resolve()), "sha256": sha(mapping_path)}}
    for path_key, sha_key in (
        ("records_path", "records_sha256"),
        ("ordered_inputs_path", "ordered_inputs_sha256"),
        ("usage_ledger_path", "usage_ledger_sha256"),
        ("source_database", "source_database_sha256"),
    ):
        raw_path, expected = manifest.get(path_key), manifest.get(sha_key)
        if raw_path is None:
            continue
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"origin manifest missing {sha_key}")
        artifact = Path(raw_path).resolve(strict=True)
        observed = sha(artifact)
        if observed != expected:
            raise ValueError(f"origin {path_key} SHA differs")
        artifact_pins[path_key] = {"path": str(artifact), "sha256": observed}
    policy_path = origin_source / "native-eligibility-policy.json"
    indexed_policy_sha = entry.get("eligibility_policy_sha256")
    if indexed_policy_sha is not None:
        if not policy_path.is_file() or sha(policy_path) != indexed_policy_sha:
            raise ValueError("origin eligibility policy SHA differs from source-index")
    return {
        "source_path": str(origin_source.resolve()),
        "source_sha256": origin_source_sha256,
        "index_path": str(origin_index.resolve()),
        "index_sha256": origin_index_sha256,
        "window_index": origin_window_index,
        "run_name": origin_run_name,
        "selected": selected,
        "qids": sorted(origin_identity),
        "input_hashes": sorted(origin_identity.values()),
        "input_identity": origin_identity,
        "manifest_version": manifest.get("version"),
        "artifact_pins": artifact_pins,
        "eligibility_policy": ({"path": str(policy_path.resolve()), "sha256": sha(policy_path)}
                                if policy_path.is_file() else None),
    }


def _manifest_bindings(output: Path, window_index: int, run_name: str) -> dict[str, Any]:
    manifest_path = output / "manifest.json"
    manifest = read_json(manifest_path)
    chunks = manifest.get("chunks")
    manifest_run_name = manifest.get("run_name")
    if manifest_run_name is None and isinstance(chunks, list) and len(chunks) == 1:
        manifest_run_name = chunks[0].get("run_name")
    if manifest.get("window_index") != window_index or manifest_run_name != run_name:
        raise ValueError("new source manifest window/run binding differs")
    if manifest.get("stage") not in ("prepared_not_started", "prepared"):
        raise ValueError("new source must be prepared before classification")
    selected = manifest.get("selected")
    if type(selected) is not int or not 1 <= selected <= 100:
        raise ValueError("new source selected count is invalid")
    if not isinstance(chunks, list) or len(chunks) != 1 or chunks[0].get("run_name") != run_name:
        raise ValueError("new source must have one canonical chunk")
    paths: dict[str, Any] = {"manifest": {"path": str(manifest_path.resolve()), "sha256": sha(manifest_path)}}
    for key in ("ordered_inputs_path", "usage_ledger_path", "source_database"):
        raw = manifest.get(key)
        if not raw:
            raise ValueError(f"new source manifest missing {key}")
        path = Path(raw).resolve(strict=True)
        paths[key] = {"path": str(path), "sha256": sha(path)}
    mapping = output / "work-mapping.json"
    if not mapping.is_file():
        raise ValueError("new source work-mapping is required")
    paths["work_mapping"] = {"path": str(mapping.resolve()), "sha256": sha(mapping)}
    rows = read_json(mapping)
    identity = _mapping_identity(mapping)
    qids = list(identity)
    if len(qids) != selected or len(set(qids)) != selected or any(not isinstance(qid, str) for qid in qids):
        raise ValueError("new source mapping does not match selected unique QIDs")
    return {"manifest": manifest, "paths": paths, "qids": qids,
            "input_hashes": sorted(identity.values()), "identity": identity, "selected": selected}


def _seal_recovery_manifest(output: Path, *, window_index: int, run_name: str,
                            origin: dict[str, Any]) -> None:
    """Add only the new-generation seal to the newly-created manifest.

    The canonical preparer has already created this file in ``output``.  It is
    safe to update that fresh file; no historical manifest or receipt is ever
    rewritten.  The v1 state validator uses these fields to reject an origin
    source accidentally presented as a recovery source.
    """
    path = output / "manifest.json"
    manifest = read_json(path)
    chunks = manifest.get("chunks")
    manifest_run_name = manifest.get("run_name")
    if manifest_run_name is None and isinstance(chunks, list) and len(chunks) == 1:
        manifest_run_name = chunks[0].get("run_name")
    if manifest.get("window_index") != window_index or manifest_run_name != run_name:
        raise ValueError("canonical preparer returned a different recovery window")
    manifest.update({
        "recovery_generation": "scale20k-recovery-gate161-v1",
        "upstream_window_index": origin["window_index"],
        "upstream_run_name": origin["run_name"],
        "upstream_source_manifest_path": origin["source_path"] + "/manifest.json",
        "upstream_source_manifest_sha256": origin["source_sha256"],
        "upstream_source_index_path": origin["index_path"],
        "upstream_source_index_sha256": origin["index_sha256"],
        "recovery_lineage_version": "scale20k-recovery-lineage-v2",
        "model_calls": 0,
        "web_requests": 0,
        "old_sources_modified": False,
    })
    path.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def _refresh_new_legacy_policy_pin(output: Path) -> dict[str, str] | None:
    """Keep the newly-created canonical sidecar bound to the sealed manifest."""
    path = output / "native-eligibility-policy.json"
    if not path.is_file():
        return None
    value = read_json(path)
    manifest = output / "manifest.json"
    value["source_manifest_sha256"] = sha(manifest)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"path": str(path.resolve()), "sha256": sha(path)}


def _catalogue_snapshot(path: Path, qids: list[str]) -> dict[str, Any]:
    # The v2 builder already performs the authoritative RO snapshot.  This
    # hook is intentionally small so tests can replace it without a database.
    from run_scale20k_native_v2 import catalogue_snapshot
    return catalogue_snapshot(path, qids)


def prepare_native_window(
    records_path: Path,
    catalogue: Path,
    output: Path,
    official_conditions: Path,
    *,
    window_index: int,
    run_name: str,
    origin_source: Path,
    origin_source_sha256: str,
    origin_index: Path,
    origin_index_sha256: str,
    origin_window_index: int,
    origin_run_name: str,
    observer: Path,
    subset_holds: Path | None = None,
    exact_shortfall_receipt: Path | None = None,
    exact_shortfall_receipt_sha256: str | None = None,
) -> dict[str, Any]:
    """Run the established guard once and emit a v3 recovery policy."""
    if run_name != canonical_run_name(window_index):
        raise ValueError("run name is not the canonical recovery name")
    origin = validate_origin(origin_source=origin_source, origin_source_sha256=origin_source_sha256,
        origin_index=origin_index, origin_index_sha256=origin_index_sha256,
        origin_window_index=origin_window_index, origin_run_name=origin_run_name,
        new_window_index=window_index)
    output = output.resolve()
    if output.exists():
        raise FileExistsError("recovery output is CREATEONLY")
    observer = observer.resolve(strict=True)
    builder_path = Path(__file__).resolve()
    builder_sha = sha(builder_path)
    observer_sha = sha(observer)
    catalogue = catalogue.resolve(strict=True)
    # Capture the selected authorities before the canonical UsageGuard call.
    # This is an independent read-only observation; it does not copy or write
    # the catalogue and prevents a changed source record from being admitted.
    from recommend_book.models import SourceRecord
    records_path = records_path.resolve(strict=True)
    record_rows = [SourceRecord.model_validate_json(line) for line in records_path.read_bytes().splitlines() if line.strip()]
    record_qids = [record.source_id for record in record_rows]
    if not record_qids or len(record_qids) > 100 or len(set(record_qids)) != len(record_qids):
        raise ValueError("recovery records must contain distinct bounded QIDs")
    if not set(record_qids) <= set(origin["qids"]):
        raise ValueError("recovery records contain a QID outside the approved upstream source")
    omitted = set(origin["qids"]) - set(record_qids)
    exact_shortfall = None
    exact_shortfall_selection = None
    if (exact_shortfall_receipt is None) != (exact_shortfall_receipt_sha256 is None):
        raise ValueError("exact shortfall receipt path and SHA are a required pair")
    if exact_shortfall_receipt is not None:
        exact_shortfall, exact_shortfall_selection, exact_omitted = _validate_exact_shortfall(
            exact_shortfall_receipt, exact_shortfall_receipt_sha256,
            catalogue=catalogue, origin=origin,
            origin_index_sha256=origin_index_sha256,
            origin_source_sha256=origin_source_sha256,
            window_index=window_index, record_qids=record_qids)
        if exact_omitted != omitted:
            raise ValueError("exact shortfall omitted QIDs differ from origin")
    subset_pin = None
    if omitted:
        if exact_shortfall is not None:
            # A final shortfall is intentionally distinct from a UsageGuard
            # hold; retain every omitted origin work as unused_final_shortfall.
            subset_pin = None
        elif subset_holds is None:
            raise ValueError("strict upstream subset requires actual UsageGuard hold proof")
        else:
            subset_holds = subset_holds.resolve(strict=True)
            subset_pin = _validate_subset_holds(subset_holds, omitted)
    before = _catalogue_snapshot(catalogue, record_qids)
    # Importing the frozen v2 wrapper keeps the genuine SourceRecord, raw pin,
    # and UsageGuard implementation in one place; this module owns only the
    # new generation/lineage sidecar.
    from prepare_scale20k_content_sources_v2 import prepare_native_window as canonical_prepare
    result = canonical_prepare(records_path.resolve(strict=True), catalogue, output, official_conditions.resolve(strict=True),
        window_index=window_index, run_name=run_name)
    _seal_recovery_manifest(output, window_index=window_index, run_name=run_name, origin=origin)
    legacy_policy = _refresh_new_legacy_policy_pin(output)
    bindings = _manifest_bindings(output, window_index, run_name)
    after = _catalogue_snapshot(catalogue, bindings["qids"])
    if before != after:
        raise ValueError("catalogue changed while preparing recovery source")
    if set(bindings["qids"]) != set(record_qids):
        raise ValueError("canonical output QIDs differ from the records supplied to recovery")
    if any(bindings["identity"][qid] != origin["input_identity"][qid] for qid in bindings["qids"]):
        raise ValueError("canonical output input hashes differ from the approved upstream source")
    if sha(origin_source / "manifest.json") != origin_source_sha256 or sha(origin_index) != origin_index_sha256:
        raise ValueError("immutable origin changed during preparation")
    if sha(builder_path) != builder_sha or sha(observer) != observer_sha:
        raise ValueError("recovery code changed during preparation")
    policy = {
        "version": RECOVERY_POLICY_VERSION,
        "generation": GENERATION,
        "recovery_generation": "scale20k-recovery-gate161-v1",
        "policy": POLICY,
        "source_builder_path": str(builder_path),
        "source_builder_sha256": builder_sha,
        "observer_path": str(observer),
        "observer_sha256": observer_sha,
        "new_window_index": window_index,
        "new_run_name": run_name,
        "upstream_window_index": origin["window_index"],
        "upstream_subset_holds": subset_pin,
        "upstream_subset_status": (
            "unused_final_shortfall" if exact_shortfall is not None else
            ("guard_held" if subset_pin is not None else "full_origin")),
        "unused_final_shortfall": (
            [{"qid": qid, "input_sha256": origin["input_identity"][qid],
              "status": "unused_final_shortfall"} for qid in sorted(omitted)]
            if exact_shortfall is not None else []),
        "exact_shortfall_receipt": (
            {"path": str(exact_shortfall_receipt.resolve()),
             "sha256": exact_shortfall_receipt_sha256,
             "version": exact_shortfall["version"],
             "eligible_native_count": exact_shortfall["eligible_native_count"],
             "target_eligible_native": exact_shortfall["target_eligible_native"],
             "remaining": exact_shortfall["remaining"],
             "run_name": exact_shortfall["run_name"],
             "prior_control": exact_shortfall["_verified_prior_control"],
             "selected": [{"qid": qid, "input_sha256": digest}
                          for qid, digest in sorted(exact_shortfall_selection.items())],
             "existing_native_overlap_count": exact_shortfall["existing_native_overlap_count"],
             "existing_attempt_overlap_count": exact_shortfall["existing_attempt_overlap_count"]}
            if exact_shortfall is not None else None),
        "origin": origin,
        "source_manifest_path": bindings["paths"]["manifest"]["path"],
        "source_manifest_sha256": bindings["paths"]["manifest"]["sha256"],
        "selected": bindings["selected"],
        "qids": bindings["qids"],
        "bindings": bindings["paths"],
        "canonical_source_policy": legacy_policy,
        "catalogue_path": str(catalogue),
        "catalogue_before": before,
        "catalogue_after": after,
        "required_purpose_admissions_allowed": result.get("required_purpose_admissions_allowed") is True,
        "model_calls": 0,
        "network_requests": 0,
        "metadata_mutations": 0,
        "old_source_and_index_modified": False,
        "old_exclusion_responsibility": "driver3.reserve_window validates baseline/native/attempt/failed exclusions before model",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    policy_path = output / "native-recovery-policy-v3.json"
    policy_sha = save_create_only(policy_path, policy)
    return {"source_preparation": str(output), "selected": bindings["selected"],
            "source_manifest_sha256": bindings["paths"]["manifest"]["sha256"],
            "eligibility_policy_path": str(policy_path),
            "eligibility_policy_sha256": policy_sha,
            "recovery_policy_path": str(policy_path), "recovery_policy_sha256": policy_sha,
            "model_calls": 0, "network_requests": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", action="store_true", required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--official-conditions", type=Path, required=True)
    parser.add_argument("--window-index", type=int, required=True)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--origin-source", type=Path, required=True)
    parser.add_argument("--origin-source-sha256", required=True)
    parser.add_argument("--origin-index", type=Path, required=True)
    parser.add_argument("--origin-index-sha256", required=True)
    parser.add_argument("--origin-window-index", type=int, required=True)
    parser.add_argument("--origin-run-name", required=True)
    parser.add_argument("--observer", type=Path, required=True)
    parser.add_argument("--subset-holds", type=Path,
                        help="Optional CREATEONLY proof for upstream QIDs held by UsageGuard")
    parser.add_argument("--exact-shortfall-receipt", type=Path,
                        help="CREATEONLY coordinator receipt for a final smaller source window")
    parser.add_argument("--exact-shortfall-receipt-sha256",
                        help="SHA256 pin for --exact-shortfall-receipt")
    args = parser.parse_args()
    result = prepare_native_window(args.records, args.catalogue, args.output, args.official_conditions,
        window_index=args.window_index, run_name=args.run_name,
        origin_source=args.origin_source, origin_source_sha256=args.origin_source_sha256,
        origin_index=args.origin_index, origin_index_sha256=args.origin_index_sha256,
        origin_window_index=args.origin_window_index, origin_run_name=args.origin_run_name,
        observer=args.observer, subset_holds=args.subset_holds,
        exact_shortfall_receipt=args.exact_shortfall_receipt,
        exact_shortfall_receipt_sha256=args.exact_shortfall_receipt_sha256)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
