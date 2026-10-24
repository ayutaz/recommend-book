"""Run fresh198 with the scoped late-import coordinator and sealed holds."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/run_scale20k_recovery195_v2.py"
PARENT_SHA = "1afb10dd294bbc9854979e5b34551cbcb60240cad84f43af54eadfef657c7d29"


def configured_bridge():
    if hashlib.sha256(PARENT.read_bytes()).hexdigest() != PARENT_SHA:
        raise ValueError("Previous immutable bridge differs")
    spec = importlib.util.spec_from_file_location("_recovery195_bridge_v2", PARENT)
    parent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parent)
    m = parent.configured_bridge()
    m.__file__ = str(Path(__file__).resolve())
    from recommend_book import scale20k_recovery195_v3 as proof
    m._load_recovery = lambda: proof
    patch = m._patch_run

    def patch_run(controller, entry_path, entry_sha):
        patch(controller, entry_path, entry_sha)

        def first_resume_index(prior, previous_control, previous_sha):
            entry = m._verify_bridge_entry(entry_path, entry_sha, before_launch=True)
            expected, digest = m._pin_from_entry(entry, "prior_control")
            if (prior.get("window_index") != 194 or entry["first_index"] != 198
                    or entry["unstarted_indices"] != [196, 197]
                    or Path(previous_control).resolve(strict=True) != expected or previous_sha != digest):
                raise ValueError("Fresh198 must follow the complete passed194 hold chain")
            return 198

        controller.first_resume_index = first_resume_index
        return controller

    m._patch_run = patch_run
    install = m._install_guards

    def install_guards(controller, entry_path, entry_sha):
        install(controller, entry_path, entry_sha)
        observed = controller.observed_command

        def observed_command(argv, directory, *args, **kwargs):
            argv = [str(x).replace("run_scale20k_content_classification_v1.py",
                                  "run_scale20k_content_classification_v5.py") for x in argv]
            return observed(argv, directory, *args, **kwargs)

        controller.observed_command = observed_command
        return controller

    m._install_guards = install_guards
    return m


if __name__ == "__main__":
    configured_bridge().main()
