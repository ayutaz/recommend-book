import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load():
    path = ROOT / "scripts" / "prepare_scale20k_content_sources_v4.py"
    spec = importlib.util.spec_from_file_location("prepare_scale20k_content_sources_v4", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v4_offset_and_canonical_run_contract():
    module = load()
    assert module.origin_window_for(161) == 163
    assert module.origin_window_for(213) == 215
    assert module.canonical_run_name(161) == "scale20k-content-6801-6900-v3"
    assert module.canonical_run_name(213) == "scale20k-content-12001-12100-v3"


def test_public_prepare_rebinds_manifest_policy_and_return_sha(tmp_path, monkeypatch):
    module = load()
    records = tmp_path / "records.jsonl"
    catalogue = tmp_path / "catalogue.sqlite"
    conditions = tmp_path / "conditions.json"
    observer = tmp_path / "observer.py"
    for path, value in ((records, b"records\n"), (catalogue, b"db"),
                        (conditions, b"{}"), (observer, b"# observer\n")):
        path.write_bytes(value)
    old_source = tmp_path / "old-source"
    old_source.mkdir()
    old_index = tmp_path / "old-index.json"
    output = tmp_path / "new-source"

    def fake_legacy_prepare(_records, _catalogue, destination, _conditions, **kwargs):
        destination.mkdir()
        manifest = {
            "version": "scale-content-classification-source-v1",
            "stage": "prepared_not_started", "window_index": kwargs["window_index"],
            "run_name": kwargs["run_name"], "selected": 1,
            "chunks": [{"run_name": kwargs["run_name"]}],
        }
        (destination / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        (destination / "native-recovery-policy-v3.json").write_text(json.dumps({
            "version": "scale20k-source-recovery-eligibility-v3",
            "bindings": {"manifest": {"path": "old", "sha256": "0" * 64}},
            "canonical_source_policy": None,
        }) + "\n", encoding="utf-8")
        (destination / "native-eligibility-policy.json").write_text(json.dumps({
            "source_manifest_sha256": "0" * 64
        }) + "\n", encoding="utf-8")
        return {"selected": 1, "source_manifest_sha256": "0" * 64,
                "model_calls": 0, "network_requests": 0}

    monkeypatch.setattr(module._v3, "prepare_native_window", fake_legacy_prepare)
    result = module.prepare_native_window(
        records, catalogue, output, conditions, window_index=161,
        run_name="scale20k-content-6801-6900-v3", origin_source=old_source,
        origin_source_sha256="a" * 64, origin_index=old_index,
        origin_index_sha256="b" * 64, origin_window_index=163,
        origin_run_name="scale20k-content-7001-7100-v1", observer=observer)
    manifest_sha = module.sha(output / "manifest.json")
    policy = json.loads((output / "native-recovery-policy-v4.json").read_text(encoding="utf-8"))
    assert result["source_manifest_sha256"] == manifest_sha
    assert policy["source_manifest_sha256"] == manifest_sha
    assert policy["bindings"]["manifest"]["sha256"] == manifest_sha
    assert (output / "native-recovery-policy-v3.json").is_file()
