"""Execute only the reserved window-index-37 recovery: 86 works, two slots."""
import hashlib
from pathlib import Path

V1_RUNNER_SHA = '04741162a8d91de30a3b87714187fa255785313576ac8770fe915d67db12702e'


def adapted_runner_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_RUNNER_SHA:
        raise ValueError('Frozen recovery v1 runner changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    for before, after, count in (
        ('scale_content_recovery_v1', 'scale_content_recovery_v2', 2),
        ("document['runtime_preparation'], 23, control_receipt=checkpoint", "document['runtime_preparation'], 37, control_receipt=checkpoint", 1),
        ('one reserved window-24 recovery', 'one reserved window-38 recovery', 1),
    ):
        if source.count(before) != count:
            raise ValueError('Frozen recovery runner adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_runner_source(Path(__file__).with_name('run_scale_content_recovery_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
