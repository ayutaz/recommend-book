"""One fixed 86-work recovery after window 37; frozen v1 remains unchanged."""
from pathlib import Path
import hashlib

V1_HELPER_SHA = '9a8990410860e27a8b21ef7a7bb7d928fe3a450c01a6a98d31875881ee91455e'
V1_RUNNER_SHA = '04741162a8d91de30a3b87714187fa255785313576ac8770fe915d67db12702e'
V1_TAIL_SHA = '13ce07b5c14358ef7d4e9d0fda3da452cdf1f4e0e0617211623c6ec2992b8bc9'
SCOPE_SHA = '662a2035440298d72e1c9f83169b11d105238ea701ad96c21d1a9368e88096ce'
NEXT_SOURCE_SHA = '5d18708314ee6d429709f2ff2c67dd04cdcef73597296b70d74c3184e6aad298'


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V1_HELPER_SHA:
        raise ValueError('Frozen recovery v1 helper changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = [
        ("FAILED_SHA = 'ca64ee57f1ec8750ea58e8a66438a30af497d763c1e4967a5e2cc50f1ec60f91'", "FAILED_SHA = '6cfd082434806c7d852b8b12db2b28f9645d073b4f74d18759753d938de19ddb'", 1),
        ("DIAGNOSIS_SHA = '4e498a7ff5e595887bd88b20f81f33f6a892cb5e463b0001754aab7d47fe6006'", "DIAGNOSIS_SHA = 'fa5bb0b346f5019856b9491266ffd0f062d49836bbb40f3bc9bfa0eb12acadcd'", 1),
        ("SOURCE_SHA = '28a705ea17ab460bbd78f7af2bd0fb9553346f0dc776b0a7e48cc8a41810d6b4'", "SOURCE_SHA = 'd46531cc9f0bfa3cdef79b8da1763ef16327a0a88cf0b54cbaa0b3d26bdf7f35'", 1),
        ("STOP_SHA = 'ac0b1304d26b1a0c5c20ad1e281f5e63579c0698dd5eee146e0c27a607b79d91'", "STOP_SHA = '2355705b16fa648b0a7015bd6f3bb905923066508910a195d03a1c361713c882'", 1),
        ('FAILED_GATE = control.actual_gate(100, 94, 90, 78)', 'FAILED_GATE = control.actual_gate(100, 92, 88, 81)', 1),
        ("QUARANTINED = {'Q2850430', 'Q2849853', 'Q2878094', 'Q2869411', 'Q2870326', 'Q2898061'}", "QUARANTINED = {'Q3960523', 'Q4015950', 'Q4021656', 'Q4025942', 'Q4080241', 'Q4166770', 'Q4203837', 'Q4228990'}", 1),
        ("RUN_NAME = 'scale30k-content-2301-2400'", "RUN_NAME = 'scale30k-content-3701-3800'", 1),
        ("VERSION = 'scale-content-single-recovery-checkpoint-v1'", "VERSION = 'scale-content-single-recovery-checkpoint-v2'", 1),
        ("receipt['window_index'] != 22", "receipt['window_index'] != 36", 1),
        ("imported['result']['raw_classified'] != 94", "imported['result']['raw_classified'] != 92", 1),
        ("imported['result']['adopted'] != 90", "imported['result']['adopted'] != 88", 1),
        ("fixed['window_index'] != 23 or fixed['selected'] != 100", "fixed['window_index'] != 37 or fixed['selected'] != 86", 1),
        ("source_doc['window_index'] != 23 or source_doc['selected'] != 100", "source_doc['window_index'] != 37 or source_doc['selected'] != 86", 1),
        ("'window_index': 23, 'failed_window_index': 22", "'window_index': 37, 'failed_window_index': 36", 1),
        ("document['window_index'] != 23", "document['window_index'] != 37", 1),
        ("document['failed_window_index'] != 22", "document['failed_window_index'] != 36", 1),
        ("receipt['window_index'] != 23", "receipt['window_index'] != 37", 1),
        ('len(works) != 100', 'len(works) != 86', 2),
        ('len(current) != 100', 'len(current) != 86', 1),
        ('len(qids) != 100', 'len(qids) != 86', 2),
        ('len(hashes) != 100', 'len(hashes) != 86', 1),
        ("stop['session_id'] != 94137", "stop['session_id'] != 30949", 1),
        ('scripts/run_scale_content_recovery_v1.py', 'scripts/run_scale_content_recovery_v2.py', 2),
        ('content-recovery-tail-v1.py', 'content-recovery-tail-v2.py', 2),
        ("'scale-content-single-recovery-launch-v1'", "'scale-content-single-recovery-launch-v2'", 1),
        ("    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)", "    verify_frozen_reuse()\n    source, runtime = Path(source).resolve(strict=True), Path(runtime).resolve(strict=True)", 1),
        ("    path = Path(path).resolve(strict=True)\n    raw = path.read_bytes()", "    verify_frozen_reuse()\n    path = Path(path).resolve(strict=True)\n    raw = path.read_bytes()", 1),
        ("'ordinary_control': _pin(control.__file__), **census_pins}", "'ordinary_control': _pin(control.__file__), **frozen_reuse_pins(), **census_pins}", 1),
        ('exact six-percent', 'exact eight-percent', 1),
        ('window-23 failure', 'window-37 failure', 2),
        ('genuine 94/90 import', 'genuine 92/88 import', 1),
        ('one hundred unique', 'eighty-six unique', 1),
        ('actual next window index and admitted hundred', 'actual next window index and admitted eighty-six', 1),
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
        ('reused_tail_v1', BASE / 'content-recovery-tail-v1.py'),
        ('scope_supplement', BASE / 'run-summaries/scale30k-content-3601-3700.scope-supplement-v1.json'),
        ('next_source', BASE / 'classification-sources/window-3801-3900-withheld-v3-final/manifest.json'))}


def verify_frozen_reuse():
    expected = dict(zip(('reused_helper_v1', 'reused_runner_v1', 'reused_tail_v1', 'scope_supplement', 'next_source'),
                        (V1_HELPER_SHA, V1_RUNNER_SHA, V1_TAIL_SHA, SCOPE_SHA, NEXT_SOURCE_SHA)))
    if any(pin['sha256'] != expected[name] for name, pin in frozen_reuse_pins().items()):
        raise ValueError('Frozen v1 reuse or scope supplement changed')


exec(compile(adapted_source(Path(__file__).with_name('scale_content_recovery_v1.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
