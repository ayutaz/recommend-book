import importlib.util
import json
import sqlite3
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "phase14_identity_diagnostic",
    Path(__file__).resolve().parents[1] / "scripts/phase14_identity_diagnostic.py",
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _create_phase8_db(path, qid):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE works (
                work_id TEXT, canonical_id TEXT, source_version_id TEXT,
                resolved INTEGER, title TEXT, work_kind TEXT,
                original_language TEXT, first_publication_year INTEGER
            );
            CREATE TABLE external_ids (
                source TEXT, value TEXT, normalized_value TEXT,
                scope TEXT, entity_id TEXT, source_version_id TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO works VALUES (?,?,?,?,?,?,?,?)",
            ("work-old", "work-old", "version-old", 1, "Synthetic Work", "novel", "en", 1900),
        )
        connection.execute(
            "INSERT INTO external_ids VALUES (?,?,?,?,?,?)",
            ("wikidata", qid, qid, "work", "work-old", "version-old"),
        )


def _create_subclass_db(path, qid):
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE source_records (source_id TEXT, version_id TEXT, record_json TEXT, raw_sha256 TEXT)"
        )
        connection.execute(
            "INSERT INTO source_records VALUES (?,?,?,?)",
            (qid, "version-subclass", "{}", "b" * 64),
        )


def test_duplicate_case_uses_exact_saved_qid_rows_and_keeps_merge_held(tmp_path, monkeypatch):
    base = tmp_path / "base"
    phase8 = base / "database/phase8-first10-v1/dataset.sqlite"
    subclass = base / "reference/phase3-declared-genre-subclass-admission-v1/source-bindings.sqlite"
    phase8.parent.mkdir(parents=True)
    subclass.parent.mkdir(parents=True)
    qid = "Q10936605"
    _create_phase8_db(phase8, qid)
    _create_subclass_db(subclass, qid)
    selection = subclass.parent / "selection.json"
    selection.write_text(
        json.dumps({
            "work_bindings": [{
                "source_qid": qid,
                "scope_decision": {
                    "scope": "original_work",
                    "holds": [],
                    "raw_sha256": "c" * 64,
                    "review_basis": "synthetic saved authority scope",
                },
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(_MODULE, "BASE", base)

    result = _MODULE.duplicate_case(qid)

    assert result["exact_qid_equal"] is True
    assert result["public_work_ids_are_distinct"] is True
    assert result["merge_authorized"] is False
    assert result["status"] == "held_scope_and_crosswalk_unverified"
    assert result["phase8_public_rows"][0]["work_id"] == "work-old"


def test_build_report_accepts_injected_saved_inputs_without_local_phase13_data(monkeypatch):
    duplicates = [
        {
            "qid": "Q10936605",
            "merge_authorized": False,
            "status": "held_scope_and_crosswalk_unverified",
        },
        {
            "qid": "Q105464593",
            "merge_authorized": False,
            "status": "held_scope_and_crosswalk_unverified",
        },
    ]
    held = [
        {"qid": "Q848612", "post_patch_replay": {"repair_effect": "eligible_after_title_key_fix"}},
        {"qid": "Q2384134", "post_patch_replay": {"repair_effect": "eligible_after_title_key_fix"}},
        {"qid": "Q213019", "post_patch_replay": {"repair_effect": "held"}},
    ]
    monkeypatch.setattr(_MODULE, "read_case_inputs", lambda: ({"eligible_cases": []}, {}))
    monkeypatch.setattr(_MODULE, "duplicate_case", lambda qid: next(item for item in duplicates if item["qid"] == qid))
    monkeypatch.setattr(_MODULE, "replay_held_cases", lambda _report, _records: held)

    report = _MODULE.build_report(Path("synthetic-output"))

    assert [case["qid"] for case in report["duplicate_cases"]] == ["Q10936605", "Q105464593"]
    replay = {case["qid"]: case for case in report["held_complete_input_cases"]}
    assert set(replay) == {"Q848612", "Q2384134", "Q213019"}
    assert replay["Q213019"]["post_patch_replay"]["repair_effect"] == "held"
    assert report["requirements"]["new_network_requests"] == 0
    assert report["requirements"]["new_model_calls"] == 0
