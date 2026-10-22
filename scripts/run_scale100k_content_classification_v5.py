"""Coordinator v5: preserve coordinator v4 and route new reviews to review-v6."""
import hashlib
from pathlib import Path
from types import ModuleType


COORDINATOR_V4_SHA = '19064d826984e215a340e261ac566f622bc380c16af2abad4a5a1e7e08606daf'
REVIEW_SHA = 'f9b0f7538a7282b6781f54adbb03d8f7cec9324547978f54ac17619ee374ef20'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != COORDINATOR_V4_SHA:
        raise ValueError('Frozen coordinator-v4 changed')
    recipe = ModuleType('frozen_coordinator_v4_recipe')
    recipe.__file__ = str(Path(__file__).resolve())
    text = raw.decode('utf-8').replace('\r\n', '\n')
    boundary = '\nexec(compile(adapted_source('
    if text.count(boundary) != 1:
        raise ValueError('Coordinator-v4 recipe boundary differs')
    exec(compile(text.split(boundary)[0], '<frozen-coordinator-v4-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v3.py').read_bytes())
    changes = (
        ('from review_scale_content_v5 import execute;', 'from review_scale_content_v6 import execute;'),
        ('sha(ROOT / "scripts/review_scale_content_v5.py") != REVIEW_SHA',
         'sha(ROOT / "scripts/review_scale_content_v6.py") != REVIEW_SHA'),
        ('ROOT / "scripts/review_scale_content_v5.py", ROOT / "scripts/run_scale100k_native_v4.py",',
         'ROOT / "scripts/review_scale_content_v5.py", ROOT / "scripts/review_scale_content_v6.py", '
         'ROOT / "scripts/run_scale100k_native_v4.py", ROOT / "scripts/run_scale100k_native_v5.py",'),
        ('separate-review-sharded-v4', 'separate-review-sharded-v6'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Coordinator-v5 adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v4.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
