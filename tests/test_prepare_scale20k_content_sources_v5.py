import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "final_sources_v5_test", ROOT / "scripts/prepare_scale20k_content_sources_v5.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_final_builder_namespace_and_origin():
    module = load()
    assert module.canonical_run_name(211) == "scale20k-content-11801-11900-v3"
    assert module.canonical_run_name(212) == "scale20k-content-11901-12000-v4"
    assert module.origin_window_for(212) == 214
    with pytest.raises(ValueError):
        module.origin_window_for(211)


def test_mapping_scope_restores_frozen_builder():
    module = load()
    original = module._v4.canonical_run_name
    with module._MappingScope():
        assert module._v4.canonical_run_name(212) == module.canonical_run_name(212)
    assert module._v4.canonical_run_name is original


def test_rewrite_output_seals_fresh_manifest_and_policy(tmp_path):
    module = load()
    output = tmp_path / "fresh"
    output.mkdir()
    (output / "manifest.json").write_text(
        '{"window_index":212,"chunks":[{"run_name":"old"}],"selected":7}', encoding="utf-8")
    (output / "native-eligibility-policy.json").write_text(
        '{"version":"source","source_manifest_sha256":"old"}', encoding="utf-8")
    (output / "native-recovery-policy-v4.json").write_text(
        '{"version":"old","bindings":{}}', encoding="utf-8")
    result = module._rewrite_output(output, source_result={"selected": 7})
    manifest = module.read_json(output / "manifest.json")
    assert manifest["chunks"][0]["run_name"] == module.canonical_run_name(212)
    assert result["cli_version"] == module.CLI_VERSION
    policy = module.read_json(output / module.POLICY_NAME)
    assert policy["version"] == "scale20k-final-source-policy-v1"
    assert policy["source_manifest_sha256"] == module.sha(output / "manifest.json")
