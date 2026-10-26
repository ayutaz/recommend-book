"""Bounded cold preflight for the existing archived-label bridge, never a cache.

Authenticates all eagerly loaded projection source files before starting the
unchanged full verifier, then repeats the checks before returning its result.
The v3 code authority contributes CODE ONLY, never labels or work identity.
"""
import hashlib
import json
from pathlib import Path
import stat

VERSION = 'light-novel-work-archive-cold-preflight-v1'
BRIDGE_VERSION = 'light-novel-work-archived-label-feature-export-v1'
BRIDGE_SHA256 = '3abe399955c474b60bfb189acc26ddc5f9340348dc8c5aef3400d429faa940f8'
V3_AUTHORITY_SHA256 = 'a33c1cf3c9aada7e45990aab2f0cf4c80aa6bdd2d671f89fe83bbcab6a999d7b'
ROOT = Path(__file__).resolve().parents[2]
V3_AUTHORITY = 'data/autonomous-next-v3/scale-30000-v1/classification-exports-v3/scale30k-content-3701-3800/manifest.json'


def _require(value, message):
    if not value:
        raise ValueError(message)


def _read(path, expected, pins):
    path = Path(path)
    _require(path.is_absolute() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode),
             'Preflight dependency must be an absolute regular file')
    _require(isinstance(expected, str) and len(expected) == 64
             and all(c in '0123456789abcdef' for c in expected), 'Preflight SHA malformed')
    raw = path.read_bytes()
    _require(hashlib.sha256(raw).hexdigest() == expected, 'Preflight dependency SHA differs: ' + str(path))
    key = str(path.resolve(strict=True))
    _require(key not in pins or pins[key] == expected, 'Preflight dependency binding conflicts')
    pins[key] = expected
    return raw


def _json(path, expected, pins):
    return json.loads(_read(path, expected, pins))


def check_preflight(path, expected_sha256):
    """Authenticate added source-code and nested metadata closure, without replay.

    This is NOT a claim that this closure alone suffices for memoization.
    Existing complete archived native/source/rights replay is still mandatory.
    """
    _require(expected_sha256 == BRIDGE_SHA256, 'Unknown bounded bridge export pin')
    pins = {}
    bridge = _json(path, expected_sha256, pins)
    _require(bridge.get('version') == BRIDGE_VERSION, 'Unknown bridge version')
    source = bridge['source']
    archive = _json(source['manifest_path'], source['manifest_sha256'], pins)
    _require(archive.get('version') == 'scale-content-native-classification-export-v2',
             'Bounded primary archive must be reviewed v2')
    runtime = Path(archive['runtime_preparation']) / 'runtime/src/recommend_book'
    code = ROOT / 'src/recommend_book'
    for name in ('scale_classification_v1.py', 'scale_content_classification_v1.py'):
        frozen = runtime / name
        expected = archive['artifact_sha256'][str(frozen)]
        _require(_read(frozen, expected, pins) == _read(code / name, expected, pins),
                 'Primary frozen projection code differs')
    current = code / 'scale_content_classification_v2.py'
    expected = archive['output_verifier_sha256']
    _require(archive['artifact_sha256'][str(current)] == expected
             and archive['artifact_sha256'][archive['frozen_output_verifier']] == expected,
             'Primary v2 expected code authority differs')
    _require(_read(current, expected, pins) == _read(archive['frozen_output_verifier'], expected, pins),
             'Primary frozen v2 code differs')
    authority = _json(ROOT / V3_AUTHORITY, V3_AUTHORITY_SHA256, pins)
    _require(authority.get('version') == 'scale-content-native-classification-export-v3',
             'Independent v3 code authority version differs')
    current = code / 'scale_content_classification_v3.py'
    expected = authority['output_verifier_sha256']
    _require(authority['artifact_sha256'][str(current)] == expected
             and authority['artifact_sha256'][authority['frozen_output_verifier']] == expected,
             'Independent v3 expected code authority differs')
    _require(_read(current, expected, pins) == _read(authority['frozen_output_verifier'], expected, pins),
             'Independent frozen v3 code differs')
    metadata = _json(source['metadata_path'], source['metadata_sha256'], pins)
    _require(metadata.get('id') == 'Q110895870'
             and metadata.get('schema_version') == 'light-novel-work-metadata-v3',
             'Bounded current work metadata differs')
    provenance = metadata['provenance']
    native = [provenance, *provenance['linked_provenance'].values(),
              *metadata['work_admission_proof']['type_authorities'].values()]
    conditions = {}
    for item in native:
        _read(item['raw_path'], item['raw_sha256'], pins)
        if 'conditions_path' in item:
            conditions[item['conditions_path']] = _json(item['conditions_path'], item['conditions_sha256'], pins)
    for document in conditions.values():
        _require(document.get('version') == 'light-novel-source-conditions-v1', 'Unknown metadata conditions version')
        for snapshot in document['snapshots']:
            _read(snapshot['raw_path'], snapshot['raw_sha256'], pins)
    # Detect changes during closure construction; no stat-only acceptance.
    for filename, expected in list(pins.items()):
        _read(filename, expected, {})
    return {'version': VERSION, 'bridge_manifest_sha256': expected_sha256,
            'artifact_sha256': pins, 'cache_allowed': False, 'full_replay_required': True,
            'independent_v3_authority_use': 'projection_source_code_only'}


def verify_feature_export(path, expected_sha256):
    before = check_preflight(path, expected_sha256)
    from .light_novel_work_archive_bridge_v1 import verify_feature_export as verify
    result = verify(path, expected_sha256)
    _require(before == check_preflight(path, expected_sha256), 'Preflight closure changed during full replay')
    return result
