"""Preserve the native recipe and review at most five works per model call."""
import hashlib
from pathlib import Path
from types import ModuleType

V2_SHA = '82fb38f1e9b271b2a1f15fd0d9da02c2bf268bd39cdcd72fd8d8609996a83169'
REVIEW_SHA = '197191358eb71a2c4cf9e9d52145e59eccb5bcbf11a0040330cb082f1db43410'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V2_SHA:
        raise ValueError('Frozen v19 coordinator-v2 changed')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    boundary = '\nexec(compile(adapted_source('
    if text.count(boundary) != 1:
        raise ValueError('V19 coordinator-v2 recipe boundary differs')
    recipe = ModuleType('frozen_v19_coordinator_v2_recipe')
    recipe.__file__ = str(Path(__file__).resolve())
    exec(compile(text.split(boundary)[0], '<frozen-v19-coordinator-v2-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v1.py').read_bytes())
    changes = (
        ('separate-review-sharded-v1', 'separate-review-sharded-v4'),
        ('from review_scale_content_v3 import execute;', 'from review_scale_content_v4 import execute;'),
        ('min(900, max(1, int(remaining_seconds))), selection_path]',
         'min(900, max(1, int(remaining_seconds))), selection_path, manifest["final_concurrency"]]'),
        ('selection_path=Path(sys.argv[10])); ',
         'selection_path=Path(sys.argv[10]),max_review_concurrency=int(sys.argv[11])); '),
        ('    started = time.monotonic()\n    result = frozen_call',
         '    if sha(ROOT / "scripts/review_scale_content_v4.py") != REVIEW_SHA:\n'
         '        raise ValueError("Fixed five-work review code changed before model call")\n'
         '    started = time.monotonic()\n    result = frozen_call'),
        ('ROOT / "scripts/run_scale100k_native_v2.py"',
         'ROOT / "scripts/run_scale100k_native_v3.py", ROOT / "scripts/review_scale_content_v4.py"'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Five-work review coordinator anchor differs: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v2.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
