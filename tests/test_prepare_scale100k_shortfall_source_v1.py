"""Offline fixtures for the v8 final-shortfall source slicer."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_scale100k_shortfall_source_v1",
    ROOT / "scripts/prepare_scale100k_shortfall_source_v1.py",
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    records = tmp_path / "records.jsonl"
    records.write_text("records\n", encoding="utf-8")
    ledger = tmp_path / "usage-ledger.json"
    ledger.write_text("{}\n", encoding="utf-8")
    database = tmp_path / "dataset.sqlite"
    with sqlite3.connect(database) as db:
        db.executescript("CREATE TABLE works(work_id TEXT, source_version_id TEXT);"
                         "CREATE TABLE source_records(source TEXT, source_id TEXT, version_id TEXT);")
    inputs = source / "ordered-inputs.jsonl"
    works = []
    mapping = []
    for i in range(3):
        work_id = f"work-{i}"
        qid = f"Q{i + 1}"
        record_id = f"src-{i}"
        work = {
            "work_id": work_id, "expression_id": None, "title": f"Book {i}",
            "authors": [f"Author {i}"], "original_language": "en",
            "first_publication_year": 1900 + i, "region": [], "series": None,
            "evidence": [], "source_record_ids": [record_id],
        }
        works.append(work)
        with sqlite3.connect(database) as db:
            db.execute("INSERT INTO works VALUES (?, ?)", (work_id, record_id))
            db.execute("INSERT INTO source_records VALUES (?, ?, ?)", ("wikidata", qid, record_id))
        work_hash = _model_hash(work)
        mapping.append({"work_id": work_id, "input_work_id": work_id,
                        "wikidata_qid": qid, "catalogue_work_id": f"wd:{qid}",
                        "input_sha256": work_hash, "source_record_ids": [record_id]})
    input_raw = "".join(json.dumps(work, ensure_ascii=False, separators=(",", ":")) + "\n" for work in works).encode()
    inputs.write_bytes(input_raw)
    mapping_path = source / "work-mapping.json"
    mapping_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    manifest = {
        "version": "scale-content-classification-source-v1", "window_index": 97,
        "selected": 3, "records_path": str(records), "records_sha256": _sha(records),
        "source_database": str(database), "source_database_sha256": _sha(database),
        "ordered_inputs_path": str(inputs), "ordered_inputs_sha256": _sha(inputs),
        "required_purpose_admissions_allowed": True, "all_six_purpose_admissions_allowed": True,
        "chunks": [{"index": 0, "inputs_path": str(inputs), "inputs_sha256": _sha(inputs),
                    "usage_ledger_path": str(ledger), "usage_ledger_sha256": _sha(ledger),
                    "selected": 3, "run_name": "scale100k-content-5501-5600-v7"}],
    }
    (source / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    exclusion = tmp_path / "exclusions.json"
    exclusion.write_text(json.dumps({"version": "scale100k-native-exclusions-v1", "qids": [], "input_hashes": []}), encoding="utf-8")
    reservations = tmp_path / "reservations"
    reservations.mkdir()
    return source, database, mapping, inputs, exclusion, reservations, tmp_path / "runtime"


def _model_hash(work):
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    return model_hash(WorkInput.model_validate(work))


def _kwargs(tmp_path, fixture):
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    return dict(source=source, logical_index=97, selected=2,
                output_root=tmp_path / "v8-sources", output_override=tmp_path / "v8-overrides.json",
                exclusion_index=exclusion, reservations=reservations, native_database=None,
                existing_override=tmp_path / "existing-v3.json", runtime_root=runtime)


def test_slices_only_n_inputs_and_reuses_sealed_artifacts(tmp_path):
    fixture = _fixture(tmp_path)
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    before = {path: path.read_bytes() for path in (source / "manifest.json", source / "work-mapping.json", inputs, database)}
    result = builder.build_shortfall(**_kwargs(tmp_path, fixture))
    assert result["selected"] == 2
    folder = tmp_path / "v8-sources" / "window-5501-5600-v8"
    manifest = json.loads((folder / "manifest.json").read_bytes())
    assert manifest["window_index"] == 97
    assert manifest["selected"] == 2
    assert manifest["chunks"][0]["run_name"] == "scale100k-content-5501-5600-v8"
    assert manifest["chunks"][0]["selected"] == 2
    assert manifest["chunks"][0]["inputs_path"] == str(folder / "ordered-inputs.jsonl")
    assert manifest["source_database"] == str(database)
    assert manifest["ordered_inputs_path"] == str(folder / "ordered-inputs.jsonl")
    assert manifest["ordered_inputs_sha256"] == _sha(folder / "ordered-inputs.jsonl")
    assert manifest["shortfall_lineage"]["selected_count"] == 2
    assert manifest["shortfall_lineage"]["source_database_ledger_raw_reused_without_copy"] is True
    assert len((folder / "ordered-inputs.jsonl").read_bytes().splitlines()) == 2
    assert {path: path.read_bytes() for path in before} == before


def test_shortfall_outputs_are_createonly(tmp_path):
    fixture = _fixture(tmp_path)
    kwargs = _kwargs(tmp_path, fixture)
    builder.build_shortfall(**kwargs)
    before = kwargs["output_override"].read_bytes()
    with pytest.raises(FileExistsError):
        builder.build_shortfall(**kwargs)
    assert kwargs["output_override"].read_bytes() == before


def test_same_source_assignment_is_allowed_but_foreign_assignment_is_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    same = tmp_path / "same.json"
    same.write_text(json.dumps({"97": str(source.resolve())}), encoding="utf-8")
    kwargs = _kwargs(tmp_path, fixture)
    kwargs["existing_override"] = same
    builder.build_shortfall(**kwargs)

    fixture = _fixture(tmp_path / "foreign")
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    foreign = (tmp_path / "foreign.json")
    foreign.write_text(json.dumps({"97": str(tmp_path / "another-source")}), encoding="utf-8")
    kwargs = _kwargs(tmp_path / "foreign", fixture)
    kwargs["existing_override"] = foreign
    with pytest.raises(ValueError, match="different source"):
        builder.build_shortfall(**kwargs)


def test_attempted_or_existing_native_qid_is_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    exclusion.write_text(json.dumps({"version": "scale100k-native-exclusions-v1", "qids": ["Q1"], "input_hashes": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps attempted/native"):
        builder.build_shortfall(**_kwargs(tmp_path, fixture))

    fixture = _fixture(tmp_path / "native")
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    native_db = tmp_path / "native.sqlite"
    with sqlite3.connect(native_db) as db:
        db.execute("CREATE TABLE native_classifications(qid TEXT)")
        db.execute("INSERT INTO native_classifications VALUES ('Q1')")
    kwargs = _kwargs(tmp_path / "native", fixture)
    kwargs["native_database"] = native_db
    with pytest.raises(ValueError, match="existing native authority"):
        builder.build_shortfall(**kwargs)


def test_started_v8_run_is_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    source, database, mapping, inputs, exclusion, reservations, runtime = fixture
    (runtime / "terminal-scale100k-content-5501-5600-v8").mkdir(parents=True)
    with pytest.raises(ValueError, match="already started"):
        builder.build_shortfall(**_kwargs(tmp_path, fixture))
