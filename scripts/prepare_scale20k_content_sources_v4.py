"""Offset-two recovery adapter with an explicit v4 builder identity.

The v3 builder remains frozen.  This module is the only new entry point for
the post-global-161 recovery generation: old source windows 163..215 map to
new windows 161..213 and use canonical ``-v3`` run names.  It delegates the
established SourceRecord/UsageGuard implementation to v3 under a temporary
mapping scope, then seals only the newly-created output with the v4 lineage
and builder pin.  Historical source/index files are never rewritten.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PATH = ROOT / "scripts" / "prepare_scale20k_content_sources_v3.py"
ORIGIN_FIRST = 163
ORIGIN_LAST = 215
NEW_FIRST = 161
NEW_LAST = 213
RECOVERY_GENERATION = "scale20k-recovery-gate161-v2"
GENERATION = "scale20k-content-recovery-v3"
POLICY_VERSION = "scale20k-source-recovery-eligibility-v4"
BUILDER_NAME = "prepare_scale20k_content_sources_v4.py"
LEGACY_SHA256 = "550c73527c993546629d96f347a09f8a4501fa826cd560247cca40987b41889e"
POLICY_FILENAME = "native-recovery-policy-v4.json"


def _load_legacy():
    observed = __import__("hashlib").sha256(LEGACY_PATH.read_bytes()).hexdigest()
    if observed != LEGACY_SHA256:
        raise ValueError("frozen v3 source builder bytes changed")
    spec = importlib.util.spec_from_file_location("prepare_scale20k_content_sources_v3_frozen", LEGACY_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("frozen v3 source builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_v3 = _load_legacy()
_shortfall_selection = _v3._shortfall_selection


def sha(path: Path | str) -> str:
    return _v3.sha(path)


def read_json(path: Path | str) -> Any:
    return _v3.read_json(path)


def canonical_run_name(window_index: int) -> str:
    if type(window_index) is not int or not NEW_FIRST <= window_index <= NEW_LAST:
        raise ValueError("v4 recovery window must map to new global 161..213")
    start = 6801 + (window_index - NEW_FIRST) * 100
    return f"scale20k-content-{start:04d}-{start + 99:04d}-v3"


def origin_window_for(window_index: int) -> int:
    if type(window_index) is not int or not NEW_FIRST <= window_index <= NEW_LAST:
        raise ValueError("v4 recovery window must map to new global 161..213")
    return window_index + 2


class _MappingScope:
    def __enter__(self):
        self.old_origin = _v3.origin_window_for
        self.old_name = _v3.canonical_run_name
        _v3.origin_window_for = origin_window_for
        _v3.canonical_run_name = canonical_run_name
        return self

    def __exit__(self, exc_type, exc, tb):
        _v3.origin_window_for = self.old_origin
        _v3.canonical_run_name = self.old_name
        return False


def validate_origin(**kwargs) -> dict[str, Any]:
    """Read-only origin verification with the v4 offset and run contract."""
    with _MappingScope():
        return _v3.validate_origin(**kwargs)


def _rewrite_new_policy(output: Path, *, window_index: int, run_name: str) -> str:
    """Rewrite only the fresh output's v3 sidecar to the v4 identity."""
    legacy_policy_path = output / "native-recovery-policy-v3.json"
    value = read_json(legacy_policy_path)
    manifest_sha = sha(output / "manifest.json")
    sidecar_path = output / "native-eligibility-policy.json"
    if not sidecar_path.is_file():
        raise ValueError("canonical source eligibility sidecar is required")
    sidecar = read_json(sidecar_path)
    sidecar["source_manifest_sha256"] = manifest_sha
    sidecar_path.write_bytes((json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    value.update({
        "version": POLICY_VERSION,
        "generation": GENERATION,
        "recovery_generation": RECOVERY_GENERATION,
        "new_window_index": window_index,
        "new_run_name": run_name,
        "source_builder_path": str(Path(__file__).resolve()),
        "source_builder_sha256": sha(__file__),
        "recovery_lineage_version": "scale20k-recovery-lineage-v3",
        "v4_offset": 2,
        "old_source_and_index_modified": False,
        "source_manifest_sha256": manifest_sha,
        "bindings": {**value.get("bindings", {}),
                      "manifest": {"path": str((output / "manifest.json").resolve()),
                                   "sha256": manifest_sha}},
        "canonical_source_policy": {"path": str(sidecar_path.resolve()),
                                     "sha256": sha(sidecar_path)},
    })
    legacy_policy_path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    policy_path = output / POLICY_FILENAME
    with policy_path.open("xb") as stream:
        stream.write((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return sha(policy_path)


def _rewrite_new_manifest(output: Path, *, window_index: int, origin_window_index: int) -> None:
    path = output / "manifest.json"
    value = read_json(path)
    value.update({
        "recovery_generation": RECOVERY_GENERATION,
        "upstream_window_index": origin_window_index,
        "recovery_lineage_version": "scale20k-recovery-lineage-v3",
        "source_builder_path": str(Path(__file__).resolve()),
        "source_builder_sha256": sha(__file__),
        "model_calls": 0,
        "web_requests": 0,
        "old_sources_modified": False,
        "old_classifications_modified": False,
    })
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def prepare_native_window(records_path: Path, catalogue: Path, output: Path, official_conditions: Path, *,
                          window_index: int, run_name: str, origin_source: Path,
                          origin_source_sha256: str, origin_index: Path,
                          origin_index_sha256: str, origin_window_index: int,
                          origin_run_name: str, observer: Path, subset_holds: Path | None = None,
                          exact_shortfall_receipt: Path | None = None,
                          exact_shortfall_receipt_sha256: str | None = None) -> dict[str, Any]:
    if run_name != canonical_run_name(window_index):
        raise ValueError("run name is not the canonical v4 recovery name")
    if origin_window_index != origin_window_for(window_index):
        raise ValueError("origin and v4 recovery window mapping differs")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("v4 recovery output is CREATEONLY")
    with _MappingScope():
        result = _v3.prepare_native_window(
            records_path, catalogue, output, official_conditions,
            window_index=window_index, run_name=run_name,
            origin_source=origin_source, origin_source_sha256=origin_source_sha256,
            origin_index=origin_index, origin_index_sha256=origin_index_sha256,
            origin_window_index=origin_window_index, origin_run_name=origin_run_name,
            observer=observer, subset_holds=subset_holds,
            exact_shortfall_receipt=exact_shortfall_receipt,
            exact_shortfall_receipt_sha256=exact_shortfall_receipt_sha256)
    _rewrite_new_manifest(output, window_index=window_index, origin_window_index=origin_window_index)
    policy_sha = _rewrite_new_policy(output, window_index=window_index, run_name=run_name)
    source_manifest_sha = sha(output / "manifest.json")
    result.update({
        "source_builder_path": str(Path(__file__).resolve()),
        "source_builder_sha256": sha(__file__),
        "source_manifest_sha256": source_manifest_sha,
        "eligibility_policy_path": str((output / POLICY_FILENAME).resolve()),
        "eligibility_policy_sha256": policy_sha,
        "recovery_policy_path": str((output / POLICY_FILENAME).resolve()),
        "recovery_policy_sha256": policy_sha,
    })
    return result


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
    parser.add_argument("--subset-holds", type=Path)
    parser.add_argument("--exact-shortfall-receipt", type=Path)
    parser.add_argument("--exact-shortfall-receipt-sha256")
    args = parser.parse_args()
    result = prepare_native_window(
        args.records, args.catalogue, args.output, args.official_conditions,
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
