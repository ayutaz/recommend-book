"""Publish the one fresh five-work recovery and stop at exactly 5000."""
import hashlib
from pathlib import Path

V1_TAIL_SHA = '13ce07b5c14358ef7d4e9d0fda3da452cdf1f4e0e0617211623c6ec2992b8bc9'


def adapted_tail_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_TAIL_SHA:
        raise ValueError('Frozen recovery v1 tail changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    for before, after, count in (
        ('scale30k-content-2301-2400', 'scale30k-content-4101-4200', 2),
        ('from recommend_book.scale_content_recovery_v1 import (', 'from recommend_book.scale_content_recovery_v3 import (', 1),
        ('run_scale_content_recovery_v1.py', 'run_scale_content_recovery_v3.py', 1),
        ('lambda _: ordinary_continuation(control_path, document)', 'lambda _: finish_at_target(original)', 1),
    ):
        if source.count(before) != count:
            raise ValueError('Frozen recovery tail adapter anchor drift: ' + before)
        source = source.replace(before, after)
    start, end = 'def ordinary_continuation(', '\n\ndef execute('
    if source.count(start) != 1 or source.count(end) != 1:
        raise ValueError('Frozen ordinary continuation boundary drift')
    source = source[:source.index(start)] + source[source.index(end) + 2:]
    return source


def finish_at_target(ordinary):
    count = ordinary.public_eligible_count()
    if count != 5000:
        raise ValueError('Final recovery did not reach exactly 5000 eligible native works')
    result = {'stage': 'actual_native_target_reached', 'eligible_native_works': count,
              'ordinary_continuation_started': False}
    ordinary.emit(result)
    return result


exec(compile(adapted_tail_source(Path(__file__).with_name('content-recovery-tail-v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
