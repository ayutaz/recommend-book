"""Continue the sealed v3 controller normally from passed211 to exact212.

The earlier gap bridge remains immutable.  This entry point uses the frozen
v4 controller's ordinary adjacent-window loop and the authenticated v5
coordinator, retaining the complete failed195/unstarted196/197 proof chain.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "scripts/run_scale20k_native_v4.py"
DRIVER_SHA = "e34e14982f62df66e847dc8b4859cb95e4722ecfbd2880c7de4ad17ab61f77b5"
ENTRY = ROOT / "data/autonomous-next-v3/scale-20000-classifications-v1/recovery-gate195-v1/entry-v4.json"
ENTRY_SHA = "db2aa854cba7c248f87e327c2dc77c242e58f54eaadc603eadd6a2f80d3479fe"
HELPER_SHA = "d4f006590e31546aa69a11d2a9173a82f62d7c25b4c00930d2005754b1bca65f"
COORDINATOR = ROOT / "scripts/run_scale20k_content_classification_v5.py"
COORDINATOR_SHA = "8ba4ba4dc18c434dedd9e4a0e8fa949e720d8bcfede779c8d3b7c671a0ca6c29"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_chain():
    from recommend_book import scale20k_recovery195_v3 as recovery
    if sha(recovery.__file__) != HELPER_SHA or sha(COORDINATOR) != COORDINATOR_SHA:
        raise ValueError("Frozen continuation helper/coordinator changed")
    return recovery.verify_entry(ENTRY, ENTRY_SHA)


def configure_controller(controller):
    """Keep normal adjacency; change only child entry points and add pins."""
    observed = controller.observed_command
    owner = Path(__file__).resolve()
    owner_sha = sha(owner)

    def observed_command(argv, directory, *args, **kwargs):
        rewritten = list(map(str, argv))
        if len(rewritten) > 1:
            if Path(rewritten[1]).resolve() == DRIVER.resolve():
                rewritten[1] = str(owner)
            elif Path(rewritten[1]).name in (
                    "run_scale20k_content_classification_v1.py",
                    "run_scale20k_content_classification_v3.py"):
                rewritten[1] = str(COORDINATOR)
        fields = dict(kwargs.pop("request_fields", {}) or {})
        fields.update({"final_continuation_path": str(owner),
                       "final_continuation_sha256": owner_sha,
                       "recovery195_entry_path": str(ENTRY),
                       "recovery195_entry_sha256": ENTRY_SHA})
        return observed(rewritten, directory, *args, request_fields=fields, **kwargs)

    original_run = controller.run

    def run(inventory_path, inventory_sha, source_index, source_index_sha,
            previous_control, previous_sha):
        verify_chain()
        if sha(owner) != owner_sha:
            raise ValueError("Continuation owner changed")
        from recommend_book.scale_content_control_v4 import verify_control_receipt
        prior = verify_control_receipt(previous_control, previous_sha)
        if prior["window_index"] != 211 or prior["gate"]["passed"] is not True:
            raise ValueError("Final continuation requires genuinely passed211")
        return original_run(inventory_path, inventory_sha, source_index,
                            source_index_sha, previous_control, previous_sha)

    controller.observed_command = observed_command
    controller.run = run
    return controller


def main():
    verify_chain()
    if sha(DRIVER) != DRIVER_SHA:
        raise ValueError("Frozen controller changed")
    spec = importlib.util.spec_from_file_location("_final20k_driver4", DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    loader = driver._load_frozen_controller
    driver._load_frozen_controller = lambda: configure_controller(loader())
    os.environ["SCALE20K_RECOVERY195_ENTRY"] = str(ENTRY)
    os.environ["SCALE20K_RECOVERY195_ENTRY_SHA256"] = ENTRY_SHA
    driver.main()


if __name__ == "__main__":
    main()
