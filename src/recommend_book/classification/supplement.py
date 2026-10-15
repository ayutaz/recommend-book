"""Adopt a small completed guarded run into a separate, source-bound sidecar.

No main dataset connection, backup, inventory or hash occurs here. This records
machine/native and purpose evidence; it never certifies semantic label quality.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import uuid

from recommend_book import next_database
from recommend_book.jobs import canonical_json, job_key
from recommend_book.models import WorkInput
from recommend_book.usage_guard import UsageGuard, UsageDecision, GUARD_VERSION, METADATA_FIELDS, _record, _values, model_hash
from .runner import build_run_config, _strict_json
from .specs import get_spec
from .validation import ROOT, input_hash, load_taxonomy

VERSION = "guarded-classification-supplement-v1"


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return _strict_json(Path(path).read_text(encoding="utf-8"))


def _contained(root, relative):
    relative = Path(relative)
    if relative.is_absolute():
        raise ValueError("Expected relative artifact locator")
    target = (root / relative).resolve(strict=True)
    if not target.is_relative_to(root):
        raise ValueError("Artifact escapes original namespace")
    return target


def _pin(path, expected, pins):
    path = Path(path).resolve(strict=True)
    if file_sha(path) != expected or path in pins and pins[path] != expected:
        raise ValueError("Immutable artifact SHA differs")
    pins[path] = expected
    return path


def _observed(path, pins):
    path = Path(path).resolve(strict=True)
    return _pin(path, file_sha(path), pins)


def _decision(decision, guard, ledger_sha, origins, usage, payload=None):
    if (not isinstance(decision, UsageDecision) or decision.allowed is not True or decision.usage != usage
        or decision.guard_version != GUARD_VERSION or decision.ledger_path != str(guard.ledger_path)
        or decision.ledger_sha256 != ledger_sha or decision.input_hashes != origins
        or payload is not None and decision.payload_sha256 != model_hash(payload)):
        raise ValueError("Genuine complete-source usage decision required")
    return decision.model_dump(mode="json")


def _literal_local_facts(origins, guard, ledger, registry, pins):
    """Literal local conditions across all origins; never a guard permission grant.

    Real external/display checks separately reproduce the native batch, source
    text and obligations. Mixed-record evidence retains its own source policy.
    Only exact source rows are read; source database files are never hashed.
    """
    checks, metadata_n, evidence_n = [], 0, 0
    for origin in origins:
        if origin not in ledger["inputs"] or origin in ledger.get("revoked_input_hashes", []):
            raise ValueError("Complete batch local_storage source unknown or revoked")
        entry = ledger["inputs"][origin]
        work = guard._snapshot(entry)
        if model_hash(work) != origin or work.source_record_ids != entry["source_record_ids"] or set(work.source_record_ids) != set(entry["sources"]):
            raise ValueError("Complete batch local source binding differs")
        records = {}
        for version, binding in entry["sources"].items():
            if version != binding["source_version_id"]:
                raise ValueError("Local source version differs")
            record = _record(binding)
            _pin(record.raw_path, record.raw_sha256, pins)
            records[version] = record
        if set(entry["metadata"]) != set(METADATA_FIELDS):
            raise ValueError("Local metadata material roster differs")
        for field in METADATA_FIELDS:
            value, binding = getattr(work, field), entry["metadata"][field]
            if model_hash(value) != binding["value_sha256"]:
                raise ValueError("Local metadata value differs")
            if value is None or value == [] or value == "":
                continue
            values = value if isinstance(value, list) else [value]
            if set(binding["origins"]) != {str(i) for i in range(len(values))}:
                raise ValueError("Local metadata origin coverage differs")
            for position, item in enumerate(values):
                refs = binding["origins"][str(position)]
                if not refs:
                    raise ValueError("Local metadata source absent")
                for version in refs:
                    record = records[version]
                    policy = registry.policies.get(record.rights_policy_id)
                    if (policy is None or policy.source != record.source or policy.provider_id not in (record.provider_id, "*")
                        or "bibliographic" not in policy.fields or item not in _values(record, field)
                        or policy.usages.get("local_storage") != "allowed"):
                        raise ValueError("Every metadata source requires literal local_storage=allowed")
                    checks.append({"origin": origin, "field": field, "position": position, "source_version": version,
                                   "policy_sha256": model_hash(policy), "value_sha256": model_hash(item)})
                    metadata_n += 1
        if set(entry["evidence"]) != {e.evidence_id for e in work.evidence}:
            raise ValueError("Local evidence material roster differs")
        for evidence in work.evidence:
            binding = entry["evidence"][evidence.evidence_id]
            policy = registry.policies.get(evidence.rights_policy_id)
            refs = binding["source_versions"]
            if (binding["model_sha256"] != model_hash(evidence) or not refs or policy is None
                or evidence.kind not in policy.fields or policy.usages.get("local_storage") != "allowed"):
                raise ValueError("Every evidence requires its own literal local_storage=allowed")
            for version in refs:
                if evidence not in records[version].evidence:
                    raise ValueError("Local evidence source version differs")
                checks.append({"origin": origin, "evidence_sha256": model_hash(evidence), "source_version": version,
                               "policy_sha256": model_hash(policy)})
                evidence_n += 1
    return {"kind": "supplement-literal-local-storage-facts-v1", "originating_input_hashes": list(origins),
            "registry_sha256": model_hash([p.model_dump(mode="json") for p in registry.policies.values()]),
            "metadata_material_n": metadata_n, "evidence_material_n": evidence_n, "checks_sha256": model_hash(checks),
            "local_storage_inferred_from_display": False, "usage_guard_local_storage_decision_claimed": False}


def _preparation(path, expected_sha, pins):
    path = _pin(path, expected_sha, pins)
    preparation, namespace = _read(path), path.parent
    if preparation.get("version") != "guarded-classification-preparation-v3":
        raise ValueError("Existing guarded preparation-v3 required")
    source = Path(preparation["input_source_path"]).resolve(strict=True)
    ledger = Path(preparation["usage_ledger_path"]).resolve(strict=True)
    if source.suffix != ".jsonl" or ledger.suffix != ".json":
        raise ValueError("Small frozen JSONL input and JSON ledger required")
    _pin(source, preparation["input_sha256"], pins)
    _pin(ledger, preparation["usage_ledger_sha256"], pins)
    inputs = _pin(namespace / "inputs.jsonl", preparation["input_sha256"], pins)
    _pin(namespace / "usage-ledger.source.json", preparation["usage_ledger_sha256"], pins)
    works = [WorkInput.model_validate(_strict_json(line)) for line in inputs.read_text(encoding="utf-8").splitlines() if line.strip()]
    identities = [{"work_id": w.work_id, "expression_id": w.expression_id, "input_hash": input_hash(w)} for w in works]
    if not works or len({(w.work_id, w.expression_id) for w in works}) != len(works) or preparation["selected"] != len(works) or preparation["input_identities"] != identities:
        raise ValueError("Frozen selected input identities differ")
    config, taxonomy = preparation["config"], load_taxonomy()
    if model_hash(config) != preparation["config_sha256"] or build_run_config(config["model"], config["cli_version"], taxonomy, config["batch_size"], execution_spec=config["execution_spec"]) != config:
        raise ValueError("Current config differs from fixed preparation")
    spec = get_spec(config["execution_spec"])
    expected_context = {"config/autonomous-next-v3.json", "docs/source-rights-review-scope-v3.md"}
    if set(preparation["root_context_artifacts"]) != expected_context:
        raise ValueError("Frozen root context roster differs")
    artifacts = {**config["dependency_sha256"], "src/recommend_book/classification/runner.py": config["runner_hash"],
        "src/recommend_book/classification/specs.py": config["spec_hash"], "src/recommend_book/usage_guard.py": config["usage_guard_hash"],
        spec.prompt_path.relative_to(ROOT).as_posix(): config["prompt_hash"], spec.schema_path.relative_to(ROOT).as_posix(): config["schema_hash"],
        "src/recommend_book/classification/validation.py": config["validation_hash"], **config.get("native_codebook_sha256", {}), **preparation["root_context_artifacts"]}
    if preparation["artifact_sha256"] != artifacts:
        raise ValueError("Preparation execution/source artifact roster differs")
    expected_outputs = {"inputs.jsonl", "usage-ledger.source.json"}
    for relative, digest in artifacts.items():
        _pin(_contained(ROOT, relative), digest, pins)
        expected_outputs.add((Path("artifacts") / relative).as_posix())
    for work in works:
        preview = "prompt-previews/" + input_hash(work) + ".utf8"
        expected_outputs.update((preview, preview + ".receipt.json"))
    outputs = {Path(relative).as_posix(): digest for relative, digest in preparation["output_sha256"].items()}
    if set(outputs) != expected_outputs:
        raise ValueError("Prepared copied artifact/preview roster differs")
    for relative, digest in outputs.items():
        _pin(_contained(namespace, relative), digest, pins)
    guard = UsageGuard(ledger, expected_ledger_sha256=preparation["usage_ledger_sha256"])
    for work in works:
        for usage in ("external_llm", "display"):
            _decision(guard.check_input(work, usage), guard, preparation["usage_ledger_sha256"], [input_hash(work)], usage)
    execution = guard.assert_execution_allowed(preparation["config_sha256"], "external_llm")
    if not isinstance(execution, UsageDecision) or execution.allowed is not True or execution.payload_sha256 != preparation["config_sha256"] or execution.ledger_sha256 != preparation["usage_ledger_sha256"]:
        raise ValueError("Frozen execution recipe usage denied")
    return preparation, works, guard, spec, taxonomy


def _terminal(preparation, works, namespace, terminal, pins):
    run = (namespace / "run").resolve(strict=True)
    manifest_path = _pin(run / "manifest.json", terminal["run_manifest_sha256"], pins)
    manifest = _read(manifest_path)
    run_id = manifest["run_id"]
    if not isinstance(run_id, str) or not run_id or any(c not in "0123456789abcdef" for c in run_id):
        raise ValueError("Invalid original run ID")
    immutable = _pin(run / ("manifest." + run_id + ".json"), terminal["run_manifest_sha256"], pins)
    if immutable.read_bytes() != manifest_path.read_bytes():
        raise ValueError("Immutable terminal manifest differs")
    expected = {job_key(input_hash(w), preparation["config"]): w for w in works}
    if (type(terminal["actual_exit_code"]) is not int or terminal["actual_exit_code"] != 0 or terminal["run_id"] != run_id
        or terminal["selected"] != len(works) or terminal["completed"] != manifest["completed"]
        or manifest["config"] != preparation["config"] or manifest["input_identities"] != preparation["input_identities"]
        or manifest["run_dir"] != str(run) or manifest["execution_context"]["usage_ledger_sha256"] != preparation["usage_ledger_sha256"]
        or manifest["selected"] != len(works) or set(manifest["jobs"]) != set(expected)
        or manifest["stopped"] is not False or manifest["pending"] != 0 or manifest["human_quality_verified"] is not False):
        raise ValueError("Terminal run identity/count/config differs")
    completed = sum(state["status"] == "completed" for state in manifest["jobs"].values())
    if manifest["completed"] != completed or manifest["failed"] != len(works) - completed:
        raise ValueError("Terminal durable status counts differ")
    for name in ("timeout", "max_run_seconds", "max_observed_tokens", "stop_on_rate_limit"):
        if manifest["limits"][name] != preparation["controls"][name]:
            raise ValueError("Terminal run control differs from preparation")
    if manifest["initial_concurrency"] != preparation["controls"]["concurrency"]:
        raise ValueError("Terminal run concurrency differs from preparation")
    index = _observed(run / "results.jsonl", pins)
    snapshot = _observed(_contained(run, manifest["results_snapshot_path"]), pins)
    if snapshot != run / ("results." + run_id + ".jsonl") or snapshot.read_bytes() != index.read_bytes():
        raise ValueError("Immutable terminal result snapshot differs")
    return run, manifest, expected, index


def adopt_guarded_supplement(preparation_path, expected_preparation_sha256, terminal_path, expected_terminal_sha256, output_dir):
    """Replay a small frozen run and atomically publish SQLite plus typed packets.

    Main-work adoption and display-overlay decisions remain downstream. No model,
    metadata acquisition, dataset backup or main database scan is performed.
    """
    output = Path(output_dir).resolve()
    if output.exists():
        raise ValueError("Supplement output must be a fresh namespace")
    pins = {}
    _observed(Path(__file__), pins)
    preparation_path = Path(preparation_path).resolve(strict=True)
    terminal_path = Path(terminal_path).resolve(strict=True)
    if preparation_path.suffix != ".json" or terminal_path.suffix != ".json":
        raise ValueError("Pinned JSON preparation and terminal required")
    terminal_path = _pin(terminal_path, expected_terminal_sha256, pins)
    terminal = _read(terminal_path)
    if type(terminal.get("actual_exit_code")) is not int or terminal["actual_exit_code"] != 0:
        raise ValueError("Actual successful root process exit required")
    preparation, works, guard, spec, taxonomy = _preparation(preparation_path, expected_preparation_sha256, pins)
    for protected in (preparation_path.parent, terminal_path.parent, Path(preparation["input_source_path"]).parent, guard.ledger_path.parent):
        if output.is_relative_to(protected):
            raise ValueError("Fresh output must be outside protected source/run namespaces")
    run, manifest, expected, results_index = _terminal(preparation, works, preparation_path.parent, terminal, pins)
    ledger, registry, ledger_sha = guard._load()
    by_origin = {input_hash(w): w for w in works}
    feature_implementation = {"path": str(Path(next_database.__file__).resolve()), "sha256": file_sha(next_database.__file__)}
    if ledger.get("feature_recipes", {}).get("work-present-relevance-max-v1") != feature_implementation:
        raise ValueError("Exact current feature recipe must be preauthorized")
    _pin(feature_implementation["path"], feature_implementation["sha256"], pins)
    durable, packets, sources = {}, [], {}
    for key, work in expected.items():
        state = manifest["jobs"][key]
        path = _observed(run / "jobs" / (key + ".json"), pins)
        if _read(path) != state or state.get("status") not in ("completed", "held", "invalid_cached_result") or state.get("lease_until") is not None:
            raise ValueError("Durable terminal job state differs")
        for field, value in {"key": key, "work_id": work.work_id, "expression_id": work.expression_id, "input_hash": input_hash(work), "config": preparation["config"]}.items():
            if state.get(field) != value:
                raise ValueError("Durable job key/input/config differs")
        for version in work.source_record_ids:
            binding = ledger["inputs"][input_hash(work)]["sources"][version]
            sources[(key, version)] = {"binding": binding, "source_record": _record(binding).model_dump(mode="json")}
        if state["status"] != "completed":
            if state.get("result_path") or (run / "results" / (key + ".json")).exists():
                raise ValueError("Noncompleted job unexpectedly supplies a durable result")
            continue
        result_path = _observed(_contained(run, state["result_path"]), pins)
        if result_path != run / "results" / (key + ".json"):
            raise ValueError("Durable result path differs from exact job key")
        result = _read(result_path)
        spec.validate_result(result, work, taxonomy)
        if result["model"] != preparation["config"]["model"] or result["cli_version"] != preparation["config"]["cli_version"]:
            raise ValueError("Durable model/CLI differs from preparation")
        histories = [h for h in state.get("history", []) if h.get("source_manifest_path")]
        if not histories:
            raise ValueError("Typed native source manifest history absent")
        history = histories[-1]
        descriptor_path = _pin(_contained(run, history["source_manifest_path"]), history["source_manifest_sha256"], pins)
        descriptor = _read(descriptor_path)
        origins = descriptor["originating_input_hashes"]
        if not origins or len(set(origins)) != len(origins) or any(origin not in by_origin for origin in origins) or input_hash(work) not in origins:
            raise ValueError("Native complete batch contains foreign/missing selected input")
        if descriptor["run_id"] != manifest["run_id"] or Path(descriptor["run_dir"]).resolve() != run:
            raise ValueError("Native descriptor belongs to a different actual run")
        for artifact in descriptor["artifacts"].values():
            artifact_path = Path(artifact["path"]).resolve(strict=True)
            if not artifact_path.is_relative_to(run):
                raise ValueError("Native original artifact escapes run namespace")
            _pin(artifact_path, artifact["sha256"], pins)
        request = _read(descriptor["artifacts"]["request"]["path"])
        if request["job_keys"] != [job_key(origin, preparation["config"]) for origin in origins] or request["input_hashes"] != origins or request["run_id"] != manifest["run_id"]:
            raise ValueError("Native request batch/job/run differs")
        features = next_database.extract_classification_features(work, result, key, manifest["run_id"])
        recipe = {**feature_implementation, "extraction": "work-present-relevance-max-v1", "native_result_sha256": model_hash(result)}
        receipts = []
        for material, payload, extra in (("native", result, {}), ("features", features, {"derivation_recipe": recipe})):
            for usage in ("external_llm", "display"):
                decision = guard.check_derived(payload, origins, usage, source_manifest=descriptor, **extra)
                receipts.append({"material": material, "usage": usage, "decision": _decision(decision, guard, ledger_sha, origins, usage, payload)})
        local_facts = _literal_local_facts(origins, guard, ledger, registry, pins)
        receipts.append({"material": "literal_source_policy", "usage": "local_storage", "decision": local_facts})
        native_packet = {"kind": "classification", "payload": result, "value_sha256": model_hash(result), "originating_input_hashes": origins, "source_manifest": descriptor}
        feature_packet = {"kind": "classification_features", "payload": features, "value_sha256": model_hash(features), "originating_input_hashes": origins,
                          "source_manifest": descriptor, "features_recipe": recipe, "native_result": result}
        packets.append({"kind": VERSION, "work_id": work.work_id, "input_hash": input_hash(work), "input_json": work.model_dump(mode="json"), "job_key": key,
            **features, "native_result": result, "classification_originating_input_hashes": origins,
            "classification_derivation": native_packet, "feature_derivation": feature_packet,
            "source_refs": {version: sources[(key, version)] for version in work.source_record_ids},
            "feature_adoption": next_database._feature_adoption(result), "purpose_receipts": receipts,
            "quality_verified": False, "human_gold": False, "main_database_updated": False})
        durable[key] = result
    index_values = [_strict_json(line) for line in results_index.read_text(encoding="utf-8").splitlines() if line.strip()]
    if index_values != [durable[key] for key in expected if key in durable]:
        raise ValueError("Ordered terminal index and durable selected results differ")
    if any(file_sha(path) != digest for path, digest in pins.items()):
        raise ValueError("Protected original/source/recipe artifact changed during adoption")
    report = {"version": VERSION, "run_id": manifest["run_id"], "selected": len(works), "completed": len(durable),
        "feature_bearing_n": sum(bool(p["features"]) for p in packets), "conflict_held_n": sum(p["feature_adoption"]["status"] == "held_conflict" for p in packets),
        "quality_verified": False, "human_gold": False, "main_database_updated": False, "main_database_scanned": False, "new_llm_calls": 0,
        "preparation_sha256": expected_preparation_sha256, "terminal_sha256": expected_terminal_sha256,
        "source_files_sha256": {str(path): digest for path, digest in pins.items()}, "adopter_recipe_sha256": file_sha(__file__)}
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = output.parent / ("." + output.name + ".supplement-" + uuid.uuid4().hex)
    stage.mkdir()
    connection = None
    try:
        connection = sqlite3.connect(stage / "supplement.sqlite")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript('''
          CREATE TABLE supplement_runs(run_id TEXT PRIMARY KEY, preparation_json TEXT NOT NULL, terminal_json TEXT NOT NULL, manifest_json TEXT NOT NULL, provenance_json TEXT NOT NULL);
          CREATE TABLE supplement_inputs(job_key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES supplement_runs, position INTEGER NOT NULL, input_hash TEXT NOT NULL UNIQUE, work_id TEXT NOT NULL, expression_id TEXT, input_json TEXT NOT NULL, state_json TEXT NOT NULL);
          CREATE TABLE supplement_results(job_key TEXT PRIMARY KEY REFERENCES supplement_inputs, result_json TEXT NOT NULL, value_sha256 TEXT NOT NULL, source_manifest_json TEXT NOT NULL);
          CREATE TABLE supplement_features(job_key TEXT PRIMARY KEY REFERENCES supplement_results, features_json TEXT NOT NULL, provenance_json TEXT NOT NULL, packet_json TEXT NOT NULL);
          CREATE TABLE supplement_source_refs(job_key TEXT NOT NULL REFERENCES supplement_inputs, source_version_id TEXT NOT NULL, binding_json TEXT NOT NULL, source_record_json TEXT NOT NULL, PRIMARY KEY(job_key,source_version_id));
          CREATE TABLE supplement_receipts(job_key TEXT NOT NULL REFERENCES supplement_results, material TEXT NOT NULL, usage TEXT NOT NULL, decision_json TEXT NOT NULL, PRIMARY KEY(job_key,material,usage));
        ''')
        connection.execute("INSERT INTO supplement_runs VALUES(?,?,?,?,?)", (manifest["run_id"], canonical_json(preparation), canonical_json(terminal), canonical_json(manifest), canonical_json(report)))
        for position, (key, work) in enumerate(expected.items()):
            connection.execute("INSERT INTO supplement_inputs VALUES(?,?,?,?,?,?,?,?)", (key, manifest["run_id"], position, input_hash(work), work.work_id, work.expression_id, canonical_json(work.model_dump(mode="json")), canonical_json(manifest["jobs"][key])))
            for version in work.source_record_ids:
                source = sources[(key, version)]
                connection.execute("INSERT INTO supplement_source_refs VALUES(?,?,?,?)", (key, version, canonical_json(source["binding"]), canonical_json(source["source_record"])))
        for packet in packets:
            key, result = packet["job_key"], packet["native_result"]
            connection.execute("INSERT INTO supplement_results VALUES(?,?,?,?)", (key, canonical_json(result), model_hash(result), canonical_json(packet["classification_derivation"]["source_manifest"])))
            connection.execute("INSERT INTO supplement_features VALUES(?,?,?,?)", (key, canonical_json(packet["features"]), canonical_json(packet["feature_provenance"]), canonical_json(packet)))
            for receipt in packet["purpose_receipts"]:
                connection.execute("INSERT INTO supplement_receipts VALUES(?,?,?,?)", (key, receipt["material"], receipt["usage"], canonical_json(receipt["decision"])))
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",) or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Supplement SQLite integrity failed")
        connection.close(); connection = None
        (stage / "feature-packets.jsonl").write_text("".join(canonical_json(packet) + "\n" for packet in packets), encoding="utf-8")
        report["output_sha256"] = {name: file_sha(stage / name) for name in ("supplement.sqlite", "feature-packets.jsonl")}
        (stage / "manifest.json").write_text(canonical_json(report) + "\n", encoding="utf-8")
        if output.exists() or any(file_sha(path) != digest for path, digest in pins.items()):
            raise ValueError("Protected source/output changed before publication")
        stage.rename(output)
    finally:
        if connection is not None:
            connection.close()
        if stage.exists() and stage.parent == output.parent and stage.name.startswith("." + output.name + ".supplement-"):
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()
    return report
