"""Build the fresh seven-work final source namespace.

This adapter delegates source admission to the immutable v4 builder and
rewrites only the newly-created directory with a v5 identity and the current
CLI ledger marker.  Existing source directories, source-index-v55, and the
old global-212 seven-work precheck remain read-only inputs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "scripts" / "prepare_scale20k_content_sources_v4.py"
LEGACY_SHA256 = "c20d091e52920b725ac9d9f77a3fd94c99aab5c58f7b81a8e4b2af10f232414f"
FINAL_WINDOW = 212
ORIGIN_WINDOW = 214
GENERATION = "scale20k-final-shortfall-v1"
BUILDER_NAME = "prepare_scale20k_content_sources_v5.py"
POLICY_NAME = "native-final-policy-v1.json"
CLI_VERSION = "codex-cli 0.164.0"


def sha(path: Path | str) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_v4():
    if sha(LEGACY) != LEGACY_SHA256:
        raise ValueError("Frozen v4 source builder bytes changed")
    spec = importlib.util.spec_from_file_location("final_v4_source_builder", LEGACY)
    if spec is None or spec.loader is None:
        raise ImportError("Frozen v4 source builder cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_v4 = _load_v4()
_V4_CANONICAL_RUN_NAME = _v4.canonical_run_name


def read_json(path: Path | str):
    return json.loads(Path(path).read_bytes())


def canonical_run_name(window_index: int) -> str:
    if type(window_index) is not int or window_index not in (211, FINAL_WINDOW):
        raise ValueError("Final source builder accepts prior211 and global212 only")
    if window_index == 211:
        return _V4_CANONICAL_RUN_NAME(window_index)
    return "scale20k-content-11901-12000-v4"


def origin_window_for(window_index: int) -> int:
    if window_index != FINAL_WINDOW:
        raise ValueError("Final source builder accepts global212 only")
    return ORIGIN_WINDOW


class _MappingScope:
    def __enter__(self):
        self.old_name = _v4.canonical_run_name
        self.old_origin = _v4.origin_window_for
        _v4.canonical_run_name = canonical_run_name
        _v4.origin_window_for = origin_window_for
        return self

    def __exit__(self, exc_type, exc, tb):
        _v4.canonical_run_name = self.old_name
        _v4.origin_window_for = self.old_origin
        return False


def _rewrite_output(output: Path, *, source_result: dict) -> dict:
    manifest_path = output / "manifest.json"
    manifest = read_json(manifest_path)
    manifest.update({
        "final_namespace": GENERATION,
        "source_builder_path": str(Path(__file__).resolve()),
        "source_builder_sha256": sha(__file__),
        "upstream_window_index": ORIGIN_WINDOW,
        "upstream_source_index": "source-index-v55.json",
        "old_sources_modified": False,
        "old_classifications_modified": False,
        "model_calls": 0,
        "network_requests": 0,
    })
    if len(manifest.get("chunks", [])) != 1:
        raise ValueError("Final source must contain one bounded chunk")
    manifest["chunks"][0]["run_name"] = canonical_run_name(FINAL_WINDOW)
    manifest_path.write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    manifest_sha = sha(manifest_path)

    sidecar_path = output / "native-eligibility-policy.json"
    sidecar = read_json(sidecar_path)
    sidecar.update({"source_manifest_sha256": manifest_sha,
                    "source_builder_path": str(Path(__file__).resolve()),
                    "source_builder_sha256": sha(__file__),
                    "cli_version": CLI_VERSION})
    sidecar_path.write_bytes((json.dumps(sidecar, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())

    legacy_path = output / "native-recovery-policy-v4.json"
    legacy = read_json(legacy_path)
    legacy.update({"final_namespace": GENERATION, "new_run_name": canonical_run_name(FINAL_WINDOW),
                   "source_builder_path": str(Path(__file__).resolve()),
                   "source_builder_sha256": sha(__file__), "source_manifest_sha256": manifest_sha,
                   "cli_version": CLI_VERSION, "selected": source_result["selected"],
                   "model_calls": 0, "network_requests": 0})
    legacy["bindings"] = {**legacy.get("bindings", {}),
                           "manifest": {"path": str(manifest_path.resolve()),
                                        "sha256": manifest_sha}}
    legacy["canonical_source_policy"] = {
        "path": str(sidecar_path.resolve()), "sha256": sha(sidecar_path)}
    legacy_path.write_bytes((json.dumps(legacy, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    policy = {**legacy, "version": "scale20k-final-source-policy-v1",
              "generation": GENERATION, "new_window_index": FINAL_WINDOW,
              "new_run_name": canonical_run_name(FINAL_WINDOW), "selected": source_result["selected"],
              "source_builder_path": str(Path(__file__).resolve()),
              "source_builder_sha256": sha(__file__), "cli_version": CLI_VERSION,
              "source_manifest_sha256": manifest_sha, "policy_source": str(legacy_path.resolve())}
    policy_path = output / POLICY_NAME
    with policy_path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(policy, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return {**source_result, "source_builder_path": str(Path(__file__).resolve()),
            "source_builder_sha256": sha(__file__), "source_manifest_sha256": manifest_sha,
            "eligibility_policy_path": str(policy_path.resolve()),
            "eligibility_policy_sha256": sha(policy_path),
            "final_policy_path": str(policy_path.resolve()),
            "final_policy_sha256": sha(policy_path), "cli_version": CLI_VERSION}


def prepare_native_window(records_path: Path, catalogue: Path, output: Path,
                          official_conditions: Path, **kwargs) -> dict:
    if kwargs.get("window_index") != FINAL_WINDOW:
        raise ValueError("Final source builder accepts global212 only")
    if kwargs.get("run_name") != canonical_run_name(FINAL_WINDOW):
        raise ValueError("Final source run namespace differs")
    if kwargs.get("origin_window_index") != ORIGIN_WINDOW:
        raise ValueError("Final source must retain origin214")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("Final source output is CREATEONLY")
    with _MappingScope():
        result = _v4.prepare_native_window(records_path, catalogue, output, official_conditions, **kwargs)
    return _rewrite_output(output, source_result=result)


def main():
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
