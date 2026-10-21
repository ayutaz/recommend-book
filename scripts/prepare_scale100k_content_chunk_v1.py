"""Isolate each v19 work in one model call without changing frozen v18 code."""
import hashlib
from pathlib import Path

V18_SHA = '481fea30c912ef08a6dff46ac0ad987f069264efca2927f2f56d79705aa5600b'
BUILDER_SHA = '53e9f11630a587eef82c532afa7eec952104b70f7779ad7aa7ad981858587159'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V18_SHA:
        raise ValueError('Frozen v18 content preparer changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    anchor = "    source=original.read_text(encoding='utf-8').replace('\\r\\n','\\n')"
    replacement = anchor + '''
    if hashlib.sha256(original.read_bytes()).hexdigest() != BUILDER_SHA:
        raise ValueError('Frozen chunk builder changed')
    for before_one, after_one in (
        ("load_taxonomy(), 5, execution_spec='provenance-v4')", "load_taxonomy(), 1, execution_spec='provenance-v4')"),
        ("controls = {'batch_size': 5, 'concurrency': concurrency", "controls = {'batch_size': 1, 'concurrency': concurrency"),
    ):
        if source.count(before_one) != 1:
            raise ValueError('One-work preparation anchor drift')
        source=source.replace(before_one,after_one)
'''
    for before, after in (
        (anchor, replacement),
        ('from prepare_scale_content_chunk_v4 import frozen_prepare',
         'from prepare_scale100k_content_chunk_v1 import frozen_prepare'),
    ):
        if source.count(before) != 1:
            raise ValueError('V19 preparer adapter anchor drift')
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('prepare_scale_content_chunk_v4.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
