"""Fresh197 launch after failed195 and the model-zero196 order hold."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/run_scale20k_recovery195_v1.py"
PARENT_SHA = "6c3c2c4117be8d344f424aec245b9e87fab175df3a5fdd190139482a480365f9"


def configured_bridge():
    if hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA:
        raise ValueError("Original recovery195 bridge bytes differ")
    spec = importlib.util.spec_from_file_location("_recovery195_bridge_parent", PARENT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    if hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA:
        raise ValueError("Original recovery195 bridge changed during import")
    # The process actually runs this wrapper; process/export identity is pinned
    # to its real path, while the original adapter remains an immutable input.
    m.__file__ = str(Path(__file__).resolve())
    from recommend_book import scale20k_recovery195_v2 as proof
    m._load_recovery = lambda: proof
    original_patch = m._patch_run

    def patch_run(controller, entry_path, entry_sha):
        original_patch(controller, entry_path, entry_sha)

        def first_resume_index(prior, previous_control, previous_sha):
            entry = m._verify_bridge_entry(entry_path, entry_sha, before_launch=True)
            expected, digest = m._pin_from_entry(entry, "prior_control")
            if (prior.get("window_index") != 194 or entry["first_index"] != 197
                    or entry["failed_index"] != 195 or entry["unstarted_index"] != 196
                    or Path(previous_control).resolve(strict=True) != expected or previous_sha != digest):
                raise ValueError("Only the actual passed194/failed195/unstarted196/fresh197 bridge is authorized")
            return 197

        controller.first_resume_index = first_resume_index
        return controller

    m._patch_run = patch_run
    original_install = m._install_guards

    def install_guards(controller, entry_path, entry_sha):
        original_install(controller, entry_path, entry_sha)
        observed = controller.observed_command

        def observed_command(argv, directory, *args, **kwargs):
            argv = [str(item).replace("run_scale20k_content_classification_v1.py",
                                       "run_scale20k_content_classification_v4.py") for item in argv]
            return observed(argv, directory, *args, **kwargs)

        controller.observed_command = observed_command
        return controller

    m._install_guards = install_guards
    return m


if __name__ == "__main__":
    configured_bridge().main()
