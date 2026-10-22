"""Keep review-v6 unchanged and pin the fresh timeout-continuation code."""
import hashlib
import importlib
from pathlib import Path

COORDINATOR_V5_SHA = 'a9e045dc57d67ea4a700ee6af213c9cce196ea20ce41e97302c297a77e6f9928'
REVIEW_SHA = 'f9b0f7538a7282b6781f54adbb03d8f7cec9324547978f54ac17619ee374ef20'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != COORDINATOR_V5_SHA:
        raise ValueError('Frozen coordinator-v5 changed')
    recipe = importlib.import_module('run_scale100k_content_classification_v5')
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v4.py').read_bytes())
    before = 'ROOT / "scripts/run_scale100k_native_v5.py",'
    after = (before + ' ROOT / "scripts/run_scale100k_native_v6.py", '
             'ROOT / "scripts/prepare_scale100k_timeout_recovery_sources_v1.py",')
    if source.count(before) != 1:
        raise ValueError('Timeout continuation context anchor differs')
    return source.replace(before, after)


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v5.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
