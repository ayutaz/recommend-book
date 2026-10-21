"""Continue global window order in v19, using one work per unchanged model call."""
import hashlib
from pathlib import Path

V18_SHA = '454f157102a2ce398a76052bd8daca4fcb3ed7fd08360230c601f3c5fe36cea2'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V18_SHA:
        raise ValueError('Frozen v18 content coordinator changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = (
        ('scale-30000-v1', 'scale-100000-v1', 3),
        ("'batch_size': 5", "'batch_size': 1", 1),
        ('from prepare_scale_content_chunk_v4 import trial_control',
         'from prepare_scale100k_content_chunk_v1 import trial_control', 1),
        ("full=(route_index == 0 or prior_control['next_review_mode'] == 'full')", 'full=True', 1),
        ("    source = json.loads((source_preparation / 'manifest.json').read_bytes())",
         "    from recommend_book.scale100k_native_v1 import verify_reservation, RUNTIME as V19_RUNTIME\n"
         "    if runtime_preparation != V19_RUNTIME.resolve(strict=True):\n"
         "        raise ValueError('V19 must reuse the exact existing frozen runtime')\n"
         "    v19_pins = verify_reservation(source_preparation, route_index)\n"
         "    source = json.loads((source_preparation / 'manifest.json').read_bytes())", 1),
        ("    pins = [output / 'dataset.sqlite'", "    pins = [*v19_pins, output / 'dataset.sqlite'", 1),
    )
    for before, after, count in changes:
        if source.count(before) != count:
            raise ValueError('V19 coordinator adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('run_scale_content_classification_v6.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
