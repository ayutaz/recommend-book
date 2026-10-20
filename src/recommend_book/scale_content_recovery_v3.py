"""One five-work recovery after the sealed 84-work native-hash failure."""
from pathlib import Path
import hashlib
import sqlite3

V1_HELPER_SHA = '9a8990410860e27a8b21ef7a7bb7d928fe3a450c01a6a98d31875881ee91455e'
V1_RUNNER_SHA = '04741162a8d91de30a3b87714187fa255785313576ac8770fe915d67db12702e'
V1_TAIL_SHA = '13ce07b5c14358ef7d4e9d0fda3da452cdf1f4e0e0617211623c6ec2992b8bc9'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_HELPER_SHA:
        raise ValueError('Frozen recovery v1 helper changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = [
        ("FAILED_SHA = 'ca64ee57f1ec8750ea58e8a66438a30af497d763c1e4967a5e2cc50f1ec60f91'", "FAILED_SHA = 'd0648305f33cfb002457168ed8c1ef3f2e0c62a9f29ce7ccc04a5dcffb9c9ec7'", 1),
        ("DIAGNOSIS_SHA = '4e498a7ff5e595887bd88b20f81f33f6a892cb5e463b0001754aab7d47fe6006'", "DIAGNOSIS_SHA = '9e5e4db5d707c3075c09ffde789f4ed4483fbb4a9925b1054bad1b70e95c1673'", 1),
        ("SOURCE_SHA = '28a705ea17ab460bbd78f7af2bd0fb9553346f0dc776b0a7e48cc8a41810d6b4'", "SOURCE_SHA = '7a5861088d368f222cfa3d0e7e072bb61900dbcd2b9f0858e6a3de57605026af'", 1),
        ("STOP_SHA = 'ac0b1304d26b1a0c5c20ad1e281f5e63579c0698dd5eee146e0c27a607b79d91'", "STOP_SHA = '487953c8d77b82fc288ca21e5c878bcdb6711dbbed215af268a2f92f424ed2b1'", 1),
        ('FAILED_GATE = control.actual_gate(100, 94, 90, 78)', 'FAILED_GATE = control.actual_gate(84, 79, 74, 69)', 1),
        ("QUARANTINED = {'Q2850430', 'Q2849853', 'Q2878094', 'Q2869411', 'Q2870326', 'Q2898061'}", "QUARANTINED = {'Q2653468', 'Q2660166', 'Q2657795', 'Q2662175', 'Q2655605'}", 1),
        ("RUN_NAME = 'scale30k-content-2301-2400'", "RUN_NAME = 'scale30k-content-4101-4200'", 1),
        ("VERSION = 'scale-content-single-recovery-checkpoint-v1'", "VERSION = 'scale-content-single-recovery-checkpoint-v3'", 1),
        ("receipt['window_index'] != 22", "receipt['window_index'] != 40", 1),
        ("imported['result']['raw_classified'] != 94", "imported['result']['raw_classified'] != 79", 1),
        ("imported['result']['adopted'] != 90", "imported['result']['adopted'] != 74", 1),
        ("if gate != receipt['gate'] or gate['passed'] or not public['gate']['passed']:", "if gate != receipt['gate'] or gate['passed'] or public['gate']['passed']:", 1),
        ("fixed['window_index'] != 23 or fixed['selected'] != 100", "fixed['window_index'] != 41 or fixed['selected'] != 5", 1),
        ("source_doc['window_index'] != 23 or source_doc['selected'] != 100", "source_doc['window_index'] != 41 or source_doc['selected'] != 5", 1),
        ("'window_index': 23, 'failed_window_index': 22", "'window_index': 41, 'failed_window_index': 40", 1),
        ("document['window_index'] != 23", "document['window_index'] != 41", 1),
        ("document['failed_window_index'] != 22", "document['failed_window_index'] != 40", 1),
        ("receipt['window_index'] != 23", "receipt['window_index'] != 41", 1),
        ('len(works) != 100', 'len(works) != 5', 2),
        ('len(current) != 100', 'len(current) != 5', 1),
        ('len(qids) != 100', 'len(qids) != 5', 2),
        ('len(hashes) != 100', 'len(hashes) != 5', 1),
        ("stop['session_id'] != 94137", "stop['session_id'] != 12426", 1),
        ('scripts/run_scale_content_recovery_v1.py', 'scripts/run_scale_content_recovery_v3.py', 2),
        ('content-recovery-tail-v1.py', 'content-recovery-tail-v3.py', 2),
        ("'scale-content-single-recovery-launch-v1'", "'scale-content-single-recovery-launch-v3'", 1),
        ("    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)", "    verify_frozen_reuse()\n    _assert_exact_deficit()\n    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)", 1),
        ("    path = Path(path).resolve(strict=True)\n    raw = path.read_bytes()", "    verify_frozen_reuse()\n    _assert_exact_deficit()\n    path = Path(path).resolve(strict=True)\n    raw = path.read_bytes()", 1),
        ("'ordinary_control': _pin(control.__file__), **census_pins}", "'ordinary_control': _pin(control.__file__), **frozen_reuse_pins(), **census_pins}", 1),
        ('exact six-percent', 'exact five-of-eighty-four', 1),
        ('window-23 failure', 'window-41 failure', 2),
        ('genuine 94/90 import', 'genuine 79/74 import', 1),
        ('one hundred unique', 'five unique', 1),
        ('actual next window index and admitted hundred', 'actual next window index and admitted five', 1),
    ]
    for before, after, count in changes:
        if source.count(before) != count:
            raise ValueError('Frozen recovery v1 adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


def frozen_reuse_pins():
    return {name: _pin(path) for name, path in (
        ('reused_helper_v1', ROOT / 'src/recommend_book/scale_content_recovery_v1.py'),
        ('reused_runner_v1', ROOT / 'scripts/run_scale_content_recovery_v1.py'),
        ('reused_tail_v1', BASE / 'content-recovery-tail-v1.py'))}


def verify_frozen_reuse():
    expected = dict(zip(('reused_helper_v1', 'reused_runner_v1', 'reused_tail_v1'),
                        (V1_HELPER_SHA, V1_RUNNER_SHA, V1_TAIL_SHA)))
    if any(pin['sha256'] != expected[name] for name, pin in frozen_reuse_pins().items()):
        raise ValueError('Frozen recovery v1 reuse changed')


def _assert_exact_deficit(database=None):
    database = Path(database or ROOT / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite')
    with sqlite3.connect(database.resolve(strict=True).as_uri() + '?mode=ro', uri=True) as db:
        count = db.execute('SELECT count(*) FROM native_classifications n JOIN works w USING(qid) '
                           'WHERE w.display_allowed=1 AND w.recommendable=1').fetchone()[0]
    if count != 4995:
        raise ValueError('Final recovery requires exactly 4995 eligible native works')


exec(compile(adapted_source(Path(__file__).with_name('scale_content_recovery_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
