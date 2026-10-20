"""Publish the one 86-work recovery, then enter sealed index 38 at four slots."""
import hashlib
from pathlib import Path

V1_TAIL_SHA = '13ce07b5c14358ef7d4e9d0fda3da452cdf1f4e0e0617211623c6ec2992b8bc9'


def adapted_tail_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_TAIL_SHA:
        raise ValueError('Frozen recovery v1 tail changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    for before, after, count in (
        ('scale30k-content-2301-2400', 'scale30k-content-3701-3800', 2),
        ('from recommend_book.scale_content_recovery_v1 import (', 'from recommend_book.scale_content_recovery_v2 import (', 1),
        ('run_scale_content_recovery_v1.py', 'run_scale_content_recovery_v2.py', 1),
        ('    index = 24', '    index = 38', 1),
        ('index==24', 'index==38', 1),
        ('    return result.main()', '    return _continue_with_fixed_next(result, document)', 1),
    ):
        if source.count(before) != count:
            raise ValueError('Frozen recovery tail adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


def _continue_with_fixed_next(ordinary, document):
    pin = document['artifacts']['next_source']
    expected = Path(pin['path']).resolve(strict=True)
    previous = ordinary.selected_source

    def selected_source(index, default):
        selected = previous(index, default)
        if index == 38 and (Path(selected).resolve() != expected.parent
                            or ordinary.sha(expected) != pin['sha256']):
            raise ValueError('First ordinary source differs from sealed index 38')
        return selected

    ordinary.selected_source = selected_source
    return ordinary.main()


exec(compile(adapted_tail_source(Path(__file__).with_name('content-recovery-tail-v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
