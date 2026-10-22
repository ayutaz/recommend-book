"""Focused offline tests for the v9 timeout recovery wrapper builder."""

import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "prepare_scale100k_timeout_recovery_sources_v1",
    ROOT / "scripts/prepare_scale100k_timeout_recovery_sources_v1.py",
)
builder = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(builder)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model_hash(work):
    from recommend_book.models import WorkInput
    from recommend_book.usage_guard import model_hash
    return model_hash(WorkInput.model_validate(work))


def _fixture(tmp_path):
    old_root = tmp_path / "old-v3"
    old_root.mkdir(parents=True)
    old_map = {str(i): str(tmp_path / f"preserved-{i}") for i in range(42, 76)}
    old_map["76"] = str(tmp_path / "failed-76-v7")
    for logical in range(77, 97):
        source = old_root / f"window-{logical}"
        source.mkdir()
        records = source / "records.jsonl"
        records.write_text(f"record-{logical}\n", encoding="utf-8")
        ledger = source / "usage-ledger.json"
        ledger.write_text("{}\n", encoding="utf-8")
        database = source / "dataset.sqlite"
        work_id, qid, source_id = f"work-{logical}", f"Q{logical}", f"src-{logical}"
        with sqlite3.connect(database) as db:
            db.executescript("CREATE TABLE works(work_id TEXT, source_version_id TEXT);"
                             "CREATE TABLE source_records(source TEXT, source_id TEXT, version_id TEXT);")
            db.execute("INSERT INTO works VALUES (?, ?)", (work_id, source_id))
            db.execute("INSERT INTO source_records VALUES (?, ?, ?)", ("wikidata", qid, source_id))
        work = {"work_id": work_id, "expression_id": None, "title": f"Book {logical}",
                "authors": [f"Author {logical}"], "original_language": "en",
                "first_publication_year": 1900 + logical, "region": [], "series": None,
                "evidence": [], "source_record_ids": [source_id]}
        inputs = source / "ordered-inputs.jsonl"
        input_raw = (json.dumps(work, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        inputs.write_bytes(input_raw)
        mapping = [{"work_id": work_id, "input_work_id": work_id, "wikidata_qid": qid,
                    "catalogue_work_id": f"wd:{qid}", "input_sha256": _model_hash(work),
                    "source_record_ids": [source_id]}]
        mapping_path = source / "work-mapping.json"
        mapping_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "version": "scale-content-classification-source-v1", "window_index": logical,
            "selected": 1, "records_path": str(records), "records_sha256": _sha(records),
            "source_database": str(database), "source_database_sha256": _sha(database),
            "required_purpose_admissions_allowed": True,
            "chunks": [{"index": 0, "inputs_path": str(inputs), "inputs_sha256": _sha(inputs),
                        "usage_ledger_path": str(ledger), "usage_ledger_sha256": _sha(ledger),
                        "selected": 1, "run_name": f"scale100k-content-{(logical - 42) * 100 + 1:04d}-{(logical - 42) * 100 + 100:04d}-v7"}],
        }
        (source / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        old_map[str(logical)] = str(source.resolve())
    old_override = tmp_path / "window-source-overrides-v3.json"
    old_override.write_text(json.dumps(old_map, sort_keys=True) + "\n", encoding="utf-8")

    baseline = tmp_path / "baseline-native-5000.jsonl"
    baseline.write_text("".join(json.dumps({"qid": f"QB{i}"}) + "\n" for i in range(5000)), encoding="utf-8")
    reservations = tmp_path / "attempt-reservations"
    reservations.mkdir()
    (reservations / "failed76.json").write_text(
        json.dumps({"run_name": "scale100k-content-3401-3500-v7", "qids": ["QFAILED"], "input_hashes": ["failed-hash"]}),
        encoding="utf-8")

    diagnostic = tmp_path / "timeout-diagnostic.json"
    diagnostic.write_text(json.dumps({
        "version": "scale100k-review-timeout-diagnostic-v1",
        "run_name": "scale100k-content-3401-3500-v7",
        "failed_global_index": 76, "prior_global_index": 75, "selected_works": 100,
        "model_calls_added": 0, "original_artifacts_modified": False,
        "conservative_failure_rate": 0.06, "failed_window_accepted": False,
        "artifacts_sha256": {"terminal": "a" * 64},
    }, sort_keys=True), encoding="utf-8")
    control = tmp_path / "control75.json"
    control.write_text(json.dumps({"version": "scale-content-window-control-v4", "window_index": 75,
                                   "gate": {"passed": True, "selected": 100}}), encoding="utf-8")
    return old_override, diagnostic, control, baseline, reservations, tmp_path / "runtime"


def _kwargs(tmp_path, fixture):
    old, diagnostic, control, baseline, reservations, runtime = fixture
    return dict(old_override=old, old_override_sha256=_sha(old),
                output_override=tmp_path / "window-source-overrides-v9.json",
                output_root=tmp_path / "classification-sources-v9",
                diagnostic=diagnostic, diagnostic_sha256=_sha(diagnostic),
                successful_control=control, successful_control_sha256=_sha(control),
                baseline=baseline, reservations=reservations, runtime_root=runtime)


def _source_hold(tmp_path, fixture):
    old, diagnostic, control, baseline, reservations, runtime = fixture
    source = Path(json.loads(old.read_bytes())["92"])
    manifest_path = source / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    records = Path(manifest["records_path"])
    expected = manifest["records_sha256"]
    records.write_text("tampered-record-92\n", encoding="utf-8")
    hold = tmp_path / "unstarted-origin-92-source-hold-v1.json"
    hold.write_text(json.dumps({
        "version": "scale100k-unstarted-source-hold-v1",
        "origin_logical_window": 92,
        "origin_manifest_path": str(manifest_path.resolve()),
        "origin_manifest_sha256": _sha(manifest_path),
        "records_path": str(records.resolve()),
        "expected_records_sha256": expected,
        "observed_records_sha256": _sha(records),
        "reason": "current_records_hash_mismatch",
        "model_calls_added": 0,
        "original_artifacts_modified": False,
    }, sort_keys=True), encoding="utf-8")
    return hold


def test_builds_v9_wrappers_with_identity_and_preserves_v3_prefix(tmp_path):
    fixture = _fixture(tmp_path)
    old, diagnostic, control, baseline, reservations, runtime = fixture
    before_old = old.read_bytes()
    result = builder.build_wrappers(**_kwargs(tmp_path, fixture))
    assert result["wrapper_count"] == 20
    override = json.loads((tmp_path / "window-source-overrides-v9.json").read_bytes())
    assert sorted(map(int, override)) == list(range(42, 96))
    old_map = json.loads(old.read_bytes())
    for logical in range(42, 76):
        assert override[str(logical)] == old_map[str(logical)]
    for logical in (76, 95):
        manifest = json.loads((Path(override[str(logical)]) / "manifest.json").read_bytes())
        assert manifest["window_index"] == logical
        assert manifest["chunks"][0]["run_name"].endswith("-v9")
        assert manifest["recovery_lineage"]["new_logical_window"] == logical
        assert manifest["recovery_lineage"]["helper_code_path"] == str(Path(builder.__file__).resolve())
    assert old.read_bytes() == before_old


def test_malformed_timeout_diagnostic_is_rejected_before_output(tmp_path):
    fixture = _fixture(tmp_path)
    old, diagnostic, control, baseline, reservations, runtime = fixture
    value = json.loads(diagnostic.read_bytes())
    value["conservative_failure_rate"] = 0.05
    diagnostic.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="timeout diagnostic"):
        builder.build_wrappers(**_kwargs(tmp_path, fixture))
    assert not (tmp_path / "classification-sources-v9").exists()


def test_input_hash_mismatch_and_reservation_overlap_are_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    old, diagnostic, control, baseline, reservations, runtime = fixture
    source = Path(json.loads(old.read_bytes())["77"])
    mapping = json.loads((source / "work-mapping.json").read_bytes())
    mapping[0]["input_sha256"] = "f" * 64
    (source / "work-mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
    with pytest.raises(ValueError, match="input hash"):
        builder.build_wrappers(**_kwargs(tmp_path, fixture))

    fixture = _fixture(tmp_path / "overlap")
    old, diagnostic, control, baseline, reservations, runtime = fixture
    (reservations / "reuse.json").write_text(json.dumps({"qids": ["Q77"], "input_hashes": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="overlaps baseline/reservation"):
        builder.build_wrappers(**_kwargs(tmp_path / "overlap", fixture))


def test_createonly_and_started_origin_are_rejected(tmp_path):
    fixture = _fixture(tmp_path)
    kwargs = _kwargs(tmp_path, fixture)
    builder.build_wrappers(**kwargs)
    with pytest.raises(FileExistsError):
        builder.build_wrappers(**kwargs)

    fixture = _fixture(tmp_path / "started")
    old, diagnostic, control, baseline, reservations, runtime = fixture
    (runtime / "terminal-scale100k-content-3501-3600-v7").mkdir(parents=True)
    with pytest.raises(ValueError, match="origin already started"):
        builder.build_wrappers(**_kwargs(tmp_path / "started", fixture))


def test_valid_source_hold_skips_origin92_and_compacts_new_logical_slots(tmp_path):
    fixture = _fixture(tmp_path)
    hold = _source_hold(tmp_path, fixture)
    kwargs = _kwargs(tmp_path, fixture)
    kwargs.update(source_hold=hold, source_hold_sha256=_sha(hold))
    result = builder.build_wrappers(**kwargs)
    assert result["wrapper_count"] == 19
    assert result["held_origin_windows"] == [92]
    assert result["remaining_source_works"] == 19
    assert result["new_logical_windows"] == [76, 94]
    override = json.loads(kwargs["output_override"].read_bytes())
    assert sorted(map(int, override)) == list(range(42, 95))
    assert "95" not in override
    assert json.loads((Path(override["91"]) / "manifest.json").read_bytes())["recovery_lineage"]["source_hold_sha256"] == _sha(hold)


def test_source_hold_must_be_exact_and_origin92_must_be_unstarted(tmp_path):
    fixture = _fixture(tmp_path)
    hold = _source_hold(tmp_path, fixture)
    value = json.loads(hold.read_bytes())
    value["reason"] = "wrong"
    hold.write_text(json.dumps(value), encoding="utf-8")
    kwargs = _kwargs(tmp_path, fixture)
    kwargs.update(source_hold=hold, source_hold_sha256=_sha(hold))
    with pytest.raises(ValueError, match="source hold facts"):
        builder.build_wrappers(**kwargs)
    assert not kwargs["output_root"].exists()

    fixture = _fixture(tmp_path / "tampered")
    hold = _source_hold(tmp_path / "tampered", fixture)
    kwargs = _kwargs(tmp_path / "tampered", fixture)
    kwargs.update(source_hold=hold, source_hold_sha256="0" * 64)
    with pytest.raises(ValueError, match="source hold SHA"):
        builder.build_wrappers(**kwargs)

    fixture = _fixture(tmp_path / "started")
    hold = _source_hold(tmp_path / "started", fixture)
    _, _, _, _, _, runtime = fixture
    (runtime / "terminal-scale100k-content-5001-5100-v7").mkdir(parents=True)
    kwargs = _kwargs(tmp_path / "started", fixture)
    kwargs.update(source_hold=hold, source_hold_sha256=_sha(hold))
    with pytest.raises(ValueError, match="origin already started"):
        builder.build_wrappers(**kwargs)


@pytest.mark.parametrize('different_bytes',[False,True])
def test_distinct_top_input_path_requires_identical_verified_bytes(tmp_path,different_bytes):
    fixture=_fixture(tmp_path)
    old=fixture[0];source=Path(json.loads(old.read_bytes())['77'])
    manifest=json.loads((source/'manifest.json').read_bytes())
    chunk=Path(manifest['chunks'][0]['inputs_path'])
    alias=source/'same-input-alias.jsonl'
    alias.write_bytes(chunk.read_bytes()+(b'changed' if different_bytes else b''))
    manifest.update(ordered_inputs_path=str(alias),ordered_inputs_sha256=_sha(alias))
    (source/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    if different_bytes:
        with pytest.raises(ValueError,match='top-level ordered inputs'):
            builder.build_wrappers(**_kwargs(tmp_path,fixture))
        assert not (tmp_path/'classification-sources-v9').exists()
    else:
        assert builder.build_wrappers(**_kwargs(tmp_path,fixture))['wrapper_count']==20
