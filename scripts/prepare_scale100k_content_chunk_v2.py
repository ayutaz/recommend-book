"""One-work v19 calls with the measured, bounded ten-million-token budget."""
import hashlib
from pathlib import Path
from types import ModuleType

V1_SHA = 'd68ec805c9f138fb2861831d7b9e56be5c0de0581c4714deeb3e6ab6299066db'
BUILDER_SHA = '53e9f11630a587eef82c532afa7eec952104b70f7779ad7aa7ad981858587159'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_SHA:
        raise ValueError('Frozen v19 preparer-v1 changed')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    anchor = 'exec(compile(adapted_source('
    if text.count(anchor) != 1:
        raise ValueError('V19 preparer-v1 recipe boundary differs')
    recipe = ModuleType('frozen_v19_preparer_recipe')
    exec(compile(text.split(anchor)[0], '<frozen-v19-preparer-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('prepare_scale_content_chunk_v4.py').read_bytes())
    changes = (
        ('    for before_one, after_one in (\n',
         '    for before_one, after_one in (\n'
         '        ("max_observed_tokens\': 2000000", "max_observed_tokens\': 10000000"),\n'),
        ('from prepare_scale100k_content_chunk_v1 import frozen_prepare',
         'from prepare_scale100k_content_chunk_v2 import frozen_prepare'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('V19 budget preparer anchor differs: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('prepare_scale100k_content_chunk_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
