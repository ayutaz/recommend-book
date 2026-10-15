import json
import sqlite3

import pytest

from recommend_book.diagnostic_preparation import (
    ADOPTION, EXPANSION, IDENTITY, OBSERVATIONS, SUPPLEMENT,
    ArtifactLedger, canonical_hash, diagnostic_contract, file_hash, prepare,
    select_cases, verify_preparation,
)


def finding(n, code="evidence_subject_scope_mismatch"):
    return {"work_id": f"work_{n:03}", "job_key": f"job_{n}",
            "input_hash": f"input_{n}", "status": "held", "reason_codes": [code]}


def test_fixed_caps_priority_and_order_do_not_depend_on_input_order():
    rows = [finding(n, "unresolved_version_scope_conflict") for n in range(25)]
    rows += [finding(100, "evidence_subject_scope_mismatch")]
    pairs = [{"case_id": f"pair_{n:03}", "side_versions": [str(n), str(n + 1)],
              "status": "held"} for n in range(45)]
    selected, identity = select_cases(rows[::-1], pairs[::-1])
    assert len(selected) == 20 and len(identity) == 40
    assert selected[0][1]["work_id"] == "work_100"
    assert selected[-1][1]["work_id"] == "work_018"
    assert identity[0][1]["case_id"] == "pair_000"


def test_uncertainty_only_and_resolved_inputs_are_not_re_reviewed():
    uncertain = finding(1, "semantic_entailment_uncertain")
    uncertain["status"] = "uncertain"
    resolved = finding(2)
    resolved["status"] = "accepted"
    assert select_cases([uncertain, resolved], [{"status": "verified_existing_binding"}]) == ([], [])


def test_duplicate_work_and_unordered_identity_pair_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        select_cases([finding(1), finding(1)], [])
    pair = {"case_id": "a", "side_versions": ["a", "b"], "status": "held"}
    with pytest.raises(ValueError, match="Duplicate"):
        select_cases([], [pair, pair | {"case_id": "b", "side_versions": ["b", "a"]}])


def test_contract_stays_notrun_and_never_claims_gold():
    contract = diagnostic_contract()
    assert contract["status"] == "notrun"
    assert contract["provenance"] == "llm_or_silver"
    assert contract["human_gold"] is False
    assert contract["execution_allowed"] is False
    assert contract["unknown_is_negative"] is False
    assert contract["new_model_calls"] == 0


def test_ledger_rejects_changed_source_and_expected_hash(tmp_path):
    source = tmp_path / "source.json"
    source.write_text('{"saved":true}', encoding="utf-8")
    ledger = ArtifactLedger(tmp_path)
    ledger.add(source)
    ledger.verify()
    source.write_text('{"saved":false}', encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        ledger.verify()
    with pytest.raises(ValueError, match="hash"):
        ledger.add(source, expected_sha256="0" * 64)


def test_frozen_reference_contains_exact_old_value_hash():
    a = {"status": "held", "reason_codes": ["scope"]}
    assert canonical_hash(a) == canonical_hash(json.loads(json.dumps(a)))
    assert canonical_hash(a) != canonical_hash(a | {"status": "accepted"})


@pytest.fixture
def small_saved_case(tmp_path):
    def write(path, value):
        path = tmp_path / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")
    raw = tmp_path / "raw.bin"
    raw.write_bytes(b"saved public metadata")
    evidence = {"evidence_id": "e1", "kind": "synopsis", "text": "DO NOT EXPORT SOURCE TEXT",
                "locator": "revision:123;section:plot", "expression_ref": None,
                "rights_policy_id": "saved", "llm_input_allowed": True}
    inp = {"work_id": "work_001", "expression_id": None, "evidence": [evidence]}
    label = {"label_id": "genre.mystery", "scope": "work", "expression_id": None,
             "decision": "present", "evidence": [{"evidence_id": "e1", "locator": evidence["locator"],
                                                     "quote": "DO NOT EXPORT QUOTE"}]}
    result = {"labels": [label], "missing_axes": [{"axis": "style", "decision": "unknown"}]}
    old = finding(1) | {"input_hash": canonical_hash(inp), "native_result_value_sha256": canonical_hash(result),
                        "label_ids": ["genre.mystery"]}
    observation = {"job_key": old["job_key"], "input_hash": old["input_hash"],
                   "native_result_value_sha256": old["native_result_value_sha256"], "old_findings": "reuse"}
    write(OBSERVATIONS, {"observations": [observation]})
    write(ADOPTION, {"cases": [old], "source_audits": [{"path": str(OBSERVATIONS), "sha256": file_hash(tmp_path / OBSERVATIONS)}]})
    write(EXPANSION, {"work_reviews": []})
    write(IDENTITY, {"same_candidates": [], "unresolved_holds": []})
    write(SUPPLEMENT, {"cases": []})
    write("config/taxonomy.json", {"labels": [{"label_id": "genre.mystery", "definition": "A puzzle drives the story"}]})
    write("config/quality-evaluation-v1.json", {"taxonomy": {"path": "config/taxonomy.json", "sha256": file_hash(tmp_path / "config/taxonomy.json")},
                                               "candidate_inventory": {"database_path": "db.sqlite"}})
    for path in ["src/recommend_book/diagnostic_preparation.py", "scripts/prepare_codex_diagnostics.py", "tests/test_diagnostic_preparation.py"]:
        write(path, "synthetic fixture code provenance")
    db = sqlite3.connect(tmp_path / "db.sqlite")
    db.executescript("""
        CREATE TABLE next_classification_inputs(job_key TEXT PRIMARY KEY,work_id TEXT,input_hash TEXT,input_json TEXT,result_json TEXT,run_id TEXT);
        CREATE TABLE next_classification_source_refs(job_key TEXT,source_version_id TEXT);
        CREATE TABLE source_records(version_id TEXT,source TEXT,source_id TEXT,raw_path TEXT,raw_sha256 TEXT,payload_sha256 TEXT,rights_policy_id TEXT);
        CREATE TABLE works(source_version_id TEXT,work_id TEXT,canonical_id TEXT,resolved INTEGER,work_kind TEXT);
    """)
    db.execute("insert into next_classification_inputs values(?,?,?,?,?,?)", (old["job_key"], old["work_id"], old["input_hash"], json.dumps(inp), json.dumps(result), "run_saved"))
    db.execute("insert into next_classification_source_refs values(?,?)", (old["job_key"], "src1"))
    db.execute("insert into source_records values(?,?,?,?,?,?,?)", ("src1", "wikidata", "Q1", str(raw), file_hash(raw), "saved_payload_hash", "wikidata-cc0"))
    db.execute("insert into works values(?,?,?,?,?)", ("src1", old["work_id"], old["work_id"], 1, "novel"))
    db.commit()
    db.close()
    return tmp_path


def test_actual_preparation_has_only_refs_no_text_and_is_immutable(small_saved_case):
    root = small_saved_case
    report = prepare(root=root, output="output")
    assert report["classification_work_n"] == 1 and report["identity_pair_n"] == 0
    pin = file_hash(root / "output/manifest.json")
    assert verify_preparation(root=root, output="output", manifest_sha256=pin)["verified"]
    packet_path = root / "output/classification-task-packets.json"
    packet_text = packet_path.read_text(encoding="utf-8")
    assert "DO NOT EXPORT" not in packet_text
    packet = json.loads(packet_text)[0]
    assert packet["new_step4_question"] and packet["external_llm_blockers"]
    assert packet["old_finding"]["json_pointer"] == "/cases/0"
    assert packet["labels"][0]["scope"] == "work"
    assert packet["missing_axes_ref"]["preserve_all_unknown"] is True
    with pytest.raises(ValueError, match="new workspace"):
        prepare(root=root, output="output")
    packet_path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen output changed"):
        verify_preparation(root=root, output="output", manifest_sha256=pin)


def test_verifier_rejects_selected_database_source_change(small_saved_case):
    root = small_saved_case
    prepare(root=root, output="output")
    pin = file_hash(root / "output/manifest.json")
    db = sqlite3.connect(root / "db.sqlite")
    db.execute("update source_records set source_id='Q2' where version_id='src1'")
    db.commit()
    db.close()
    with pytest.raises(ValueError, match="database row changed"):
        verify_preparation(root=root, output="output", manifest_sha256=pin)


def rewrite_manifest(root, *, update_ledger_pin=True):
    path = root / "output/manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for name in list(manifest["output_sha256"]):
        manifest["output_sha256"][name] = file_hash(root / "output" / name)
    if update_ledger_pin:
        manifest["source_ledger_sha256"] = file_hash(root / "output/source-ledger.json")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return file_hash(path)


@pytest.mark.parametrize("attack", ["empty_tasks", "contract", "source"])
def test_recomputed_manifest_cannot_replace_original_external_pin(small_saved_case, attack):
    root = small_saved_case
    prepare(root=root, output="output")
    original_pin = file_hash(root / "output/manifest.json")
    packet_path = root / "output/classification-task-packets.json"
    if attack == "empty_tasks":
        packet_path.write_text("[]", encoding="utf-8")
    elif attack == "contract":
        packets = json.loads(packet_path.read_text(encoding="utf-8"))
        packets[0]["execution_allowed"] = True
        packet_path.write_text(json.dumps(packets), encoding="utf-8")
    else:
        (root / "raw.bin").write_bytes(b"changed cached fixture source")
        ledger_path = root / "output/source-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        ledger["files_sha256"]["raw.bin"] = file_hash(root / "raw.bin")
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    rewrite_manifest(root)
    with pytest.raises(ValueError, match="manifest.*pin"):
        verify_preparation(root=root, output="output", manifest_sha256=original_pin)
    with pytest.raises(ValueError, match="externally pinned"):
        verify_preparation(root=root, output="output")


def test_source_ledger_digest_is_checked_independently(small_saved_case):
    root = small_saved_case
    prepare(root=root, output="output")
    path = root / "output/source-ledger.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    pin = rewrite_manifest(root, update_ledger_pin=False)
    with pytest.raises(ValueError, match="Source ledger digest"):
        verify_preparation(root=root, output="output", manifest_sha256=pin)


@pytest.mark.parametrize("attack", ["outside_source", "output_filename", "database_path", "sql_table", "sql_key"])
def test_verifier_rejects_unsafe_paths_and_sql_even_with_new_pin(small_saved_case, attack):
    root = small_saved_case
    prepare(root=root, output="output")
    ledger_path = root / "output/source-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if attack == "outside_source":
        ledger["files_sha256"]["../outside-DO-NOT-READ.bin"] = "0" * 64
    elif attack == "database_path":
        ledger["database_path"] = "../outside-DO-NOT-OPEN.sqlite"
    elif attack == "sql_table":
        ledger["database_rows"][0]["table"] = 'source_records WHERE 1=1 UNION SELECT * FROM source_records --'
    elif attack == "sql_key":
        ledger["database_rows"][0]["key_column"] = 'job_key OR 1=1 --'
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    pin = rewrite_manifest(root)
    if attack == "output_filename":
        path = root / "output/manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["output_sha256"]["../outside-DO-NOT-READ.bin"] = "0" * 64
        path.write_text(json.dumps(manifest), encoding="utf-8")
        pin = file_hash(path)
    message = "outside workspace|output file set|Unsupported database binding"
    with pytest.raises(ValueError, match=message):
        verify_preparation(root=root, output="output", manifest_sha256=pin)


def test_verifier_rejects_output_directory_escape(small_saved_case):
    with pytest.raises(ValueError, match="outside workspace"):
        verify_preparation(root=small_saved_case, output="../outside", manifest_sha256="0" * 64)
