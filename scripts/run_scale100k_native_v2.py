"""Fresh-input v19 continuation; preserve the unadopted v1 budget stop."""
import hashlib
from pathlib import Path

V1_SHA = 'ca2c823fa1d822c2d69fda9d3ced266ae9246411783ec951373ade1a9d63c809'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_SHA:
        raise ValueError('Frozen v19 operational driver-v1 changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = (
        ("'window-source-overrides.json'", "'window-source-overrides-v2.json'"),
        ("BASE / 'classification-sources'", "BASE / 'classification-sources-v2'"),
        ('scripts/prepare_scale100k_content_chunk_v1.py', 'scripts/prepare_scale100k_content_chunk_v2.py'),
        ('scripts/run_scale100k_content_classification_v1.py', 'scripts/run_scale100k_content_classification_v2.py'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('V19 operational budget adapter anchor differs: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale100k_native_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
