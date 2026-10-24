"""Complete the frozen final facade's inherited constants before execution.

The previous entry stopped while constructing publication, before reserving
any work.  Its code and witnesses stay immutable.  This version adds only
the five independently pinned legacy recipe constants and retains its exact
policy, preparation, classification and import implementation.
"""
from __future__ import annotations
import hashlib
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "scripts/run_scale20k_final_continuation_v2.py"
PARENT_SHA = "5399e646b666fc19aa82b0c34aa8759d1207394bb90770e197e7faed15c00db5"
STATE_SHA = "a7f01e3837dbd9f77fdc1055921aca945a22e9fcc24ec472dc76cf80c0658d25"
LEGACY_STATE_SHA = "9c817259f430de90f7117c59fe520a246d2f58bd016f842c16ed50ff8c0e42b5"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_adapter():
    from recommend_book import scale20k_final_native_v1 as final
    from recommend_book import scale20k_native_v3 as legacy
    if sha(PARENT) != PARENT_SHA or sha(final.__file__) != STATE_SHA or sha(legacy.__file__) != LEGACY_STATE_SHA:
        raise ValueError("Immutable final continuation dependencies differ")
    for name in ("PREPARER_SHA", "COORDINATOR_SHA", "REVIEW_SHA", "TERMINAL_SHA", "OPERATION_SHA"):
        value = getattr(legacy, name)
        if hasattr(final, name) and getattr(final, name) != value:
            raise ValueError("Final inherited recipe constant differs: " + name)
        setattr(final, name, value)
    spec = importlib.util.spec_from_file_location("_final_driver2_fixed", PARENT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original = module.configure_controller
    owner = Path(__file__).resolve()
    digest = sha(owner)
    def configure(controller):
        controller = original(controller)
        observed = controller.observed_command
        def observed_command(argv, directory, *args, **kwargs):
            argv = list(map(str, argv))
            if len(argv) > 1 and Path(argv[1]).resolve() == PARENT:
                argv[1] = str(owner)
            fields = dict(kwargs.pop("request_fields", {}) or {})
            fields.update({"final_driver_entry_path": str(owner), "final_driver_entry_sha256": digest})
            return observed(argv, directory, *args, request_fields=fields, **kwargs)
        controller.observed_command = observed_command
        return controller
    module.configure_controller = configure
    return module


if __name__ == "__main__":
    load_adapter().main()
