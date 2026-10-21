"""Fresh v19 windows after the preserved inherited-token-budget stop."""
import hashlib
from pathlib import Path
from types import ModuleType

V1_SHA = 'e9ed0dc490303a7d005a959bdc06fcdccc68a38d8a46ff1d03c2187f335ffd70'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_SHA:
        raise ValueError('Frozen v19 coordinator-v1 changed')
    text = raw.decode('utf-8').replace('\r\n', '\n')
    anchor = 'exec(compile(adapted_source('
    if text.count(anchor) != 1:
        raise ValueError('V19 coordinator-v1 recipe boundary differs')
    recipe = ModuleType('frozen_v19_coordinator_recipe')
    exec(compile(text.split(anchor)[0], '<frozen-v19-coordinator-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('run_scale_content_classification_v6.py').read_bytes())
    changes = (
        ('2000000', '10000000', 3),
        ('from prepare_scale100k_content_chunk_v1 import trial_control',
         'from prepare_scale100k_content_chunk_v2 import trial_control', 1),
        ('    v19_pins = verify_reservation(source_preparation, route_index)\n',
         '    v19_pins = verify_reservation(source_preparation, route_index)\n'
         '    v19_pins.extend([Path(__file__).resolve(), ROOT / "scripts/prepare_scale100k_content_chunk_v2.py",\n'
         '                     ROOT / "scripts/run_scale100k_native_v2.py"])\n', 1),
    )
    for before, after, count in changes:
        if source.count(before) != count:
            raise ValueError('V19 budget coordinator anchor differs: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_content_classification_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
