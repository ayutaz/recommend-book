"""Normal passed211 continuation with a fresh CLI-context source namespace."""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import os
from pathlib import Path
from types import ModuleType
from recommend_book import scale20k_final_native_v1 as final_state

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/run_scale20k_native_v4.py"
PARENT = ROOT / "scripts/run_scale20k_native_v2.py"
PARENT_SHA = "ba52758a2026b8e46b3ffd5f9332672a5c893974167bfafda626ab84d8cf4f1b"
COORDINATOR = ROOT / "scripts/run_scale20k_content_classification_v6.py"
ENTRY = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1/recovery-gate195-v1/entry-v4.json"
ENTRY_SHA256 = "db2aa854cba7c248f87e327c2dc77c242e58f54eaadc603eadd6a2f80d3479fe"

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def verify_chain():
    from recommend_book import scale20k_recovery195_v3 as recovery
    recovery.verify_entry(ENTRY, ENTRY_SHA256)
    final_state._assert_final_hold_is_excluded()
    final_state.code_snapshot()

def load_controller():
    if sha(PARENT) != PARENT_SHA:
        raise ValueError("Immutable display-policy controller changed")
    tree = ast.parse(PARENT.read_bytes())
    counts = [0, 0, 0, 0]
    replacements = {
        "prepare_scale20k_content_sources_v2.py": (final_state.BUILDER_NAME, 1),
        "native-eligibility-policy.json": (final_state.FINAL_POLICY_NAME, 2),
        "scale20k-source-display-eligibility-v2": ("scale20k-final-source-policy-v1", 3),
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "recommend_book":
            for alias in node.names:
                if alias.name == "scale20k_native_v1":
                    alias.name = "scale20k_final_native_v1"; counts[0] += 1
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in replacements:
            node.value, index = replacements[node.value]; counts[index] += 1
    if counts != [1, 1, 1, 1]:
        raise ValueError("Final policy adapter boundaries differ: " + str(counts))
    module = ModuleType("final20k_display_controller")
    module.__file__ = str(Path(__file__).resolve())
    exec(compile(ast.fix_missing_locations(tree), module.__file__, "exec"), module.__dict__)
    controller = module.load_controller()
    return configure_controller(controller)

def configure_controller(controller):
    observed = controller.observed_command
    owner = Path(__file__).resolve()
    owner_sha = sha(owner)
    def observed_command(argv, directory, *args, **kwargs):
        rewritten = list(map(str, argv))
        if len(rewritten) > 1:
            name = Path(rewritten[1]).name
            if Path(rewritten[1]).resolve() == DRIVER.resolve(): rewritten[1] = str(owner)
            elif name in {"run_scale20k_content_classification_v1.py", "run_scale20k_content_classification_v3.py", "run_scale20k_content_classification_v5.py"}:
                rewritten[1] = str(COORDINATOR)
            elif name == "prepare_scale20k_content_chunk_v1.py":
                rewritten[1] = str(ROOT / "scripts/prepare_scale_content_chunk_v5.py")
        fields = dict(kwargs.pop("request_fields", {}) or {})
        fields.update({"final_continuation_path": str(owner), "final_continuation_sha256": owner_sha,
                       "recovery195_entry_path": str(ENTRY), "recovery195_entry_sha256": ENTRY_SHA256})
        return observed(rewritten, directory, *args, request_fields=fields, **kwargs)
    original = controller.run
    def run(inventory_path, inventory_sha, source_index, source_index_sha, previous_control, previous_sha):
        verify_chain()
        if sha(owner) != owner_sha: raise ValueError("Final controller changed")
        from recommend_book.scale_content_control_v4 import verify_control_receipt
        prior = verify_control_receipt(previous_control, previous_sha)
        if prior["window_index"] != 211 or prior["gate"]["passed"] is not True:
            raise ValueError("Final continuation requires genuinely passed211")
        return original(inventory_path, inventory_sha, source_index, source_index_sha, previous_control, previous_sha)
    controller.observed_command, controller.run = observed_command, run
    return controller

def main():
    verify_chain()
    os.environ["SCALE20K_RECOVERY195_ENTRY"] = str(ENTRY)
    os.environ["SCALE20K_RECOVERY195_ENTRY_SHA256"] = ENTRY_SHA256
    controller = load_controller()
    # Every worker calls this same pinned entry point; normal adjacency is retained.
    controller.main()

if __name__ == "__main__":
    main()
