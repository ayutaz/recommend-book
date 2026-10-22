"""Coordinator v4: preserve coordinator v3 and bind the review-v5 protocol."""
import hashlib
from pathlib import Path
from types import ModuleType


COORDINATOR_V3_SHA = 'b11f9db9fdd1b285ba1a38685ebd3b9f09265a13128b36eb7d983ba3e0b00ef4'
REVIEW_SHA = '2208ffe025536d2985d15833fd9fdc36dabe37086ec0595d16a278fdf16436f6'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != COORDINATOR_V3_SHA:
        raise ValueError('Frozen coordinator-v3 changed')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    boundary = '\nexec(compile(adapted_source('
    if text.count(boundary) != 1:
        raise ValueError('Coordinator-v3 recipe boundary differs')
    recipe = ModuleType('frozen_coordinator_v3_recipe')
    recipe.__file__ = str(Path(__file__).resolve())
    exec(compile(text.split(boundary)[0], '<frozen-coordinator-v3-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v2.py').read_bytes())
    changes = (
        ('from review_scale_content_v4 import execute;', 'from review_scale_content_v5 import execute;'),
        ('ROOT / "scripts/review_scale_content_v4.py"]',
         'ROOT / "scripts/review_scale_content_v4.py", ROOT / "scripts/review_scale_content_v5.py", '
         'ROOT / "scripts/run_scale100k_native_v4.py", '
         'ROOT / "scripts/prepare_scale100k_review_recovery_sources_v1.py", '
         'ROOT / "data/autonomous-next-v3/scale-100000-v1/generation-v7-entry.json"]'),
        ('sha(ROOT / "scripts/review_scale_content_v4.py") != REVIEW_SHA',
         'sha(ROOT / "scripts/review_scale_content_v5.py") != REVIEW_SHA'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Coordinator-v4 adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v3.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
