"""Known-version LN export dispatch; cross-unit features fail closed."""
import hashlib
import json
from pathlib import Path
import re
import threading
from importlib import import_module

SERIES_VERSIONS = {'light-novel-series-feature-export-v1','light-novel-series-feature-export-v2'}
WORK_VERSION = 'light-novel-work-feature-export-v1'
ARCHIVE_VERSION = 'light-novel-work-archived-label-feature-export-v1'
_IMPORT_LOCK = threading.RLock()


def _resolve_verifier(version):
    if version not in SERIES_VERSIONS | {WORK_VERSION, ARCHIVE_VERSION}:
        raise ValueError('Unknown LN feature export recipe')
    # Mixed first requests otherwise initialize the classification package and
    # its runner from different threads, producing circular module-lock errors.
    with _IMPORT_LOCK:
        import_module('.classification', __package__)
        if version in SERIES_VERSIONS:
            from .light_novel_classification_v1 import verify_feature_export as verify
        elif version == WORK_VERSION:
            from .light_novel_work_classification_v1 import verify_feature_export as verify
        else:
            from .light_novel_work_archive_preflight_v1 import verify_feature_export as verify
        return verify


def verify_feature_export(path, expected_sha256):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:
        raise ValueError('LN feature export manifest SHA differs')
    version=json.loads(raw).get('version')
    try:
        verify = _resolve_verifier(version)
    except ImportError as exc:
        raise ValueError('LN feature export verifier unavailable') from exc
    return verify(path,expected_sha256)


def target_allowed(document, row, target):
    version=document.get('version');identity=row.get('entry_id',row.get('series_id'))
    if identity!=target.get('entry_id'):
        return False
    if version in SERIES_VERSIONS:
        # Preserve the original import gate for already verified archived rows.
        # Their archived verifier owns exact historical row-shape validation.
        return target.get('entry_kind')=='series' and row.get('scope')=='series'
    if version==ARCHIVE_VERSION:
        return (target.get('entry_kind')=='work' and row.get('scope')=='work'
            and row.get('entity_kind')=='light_novel_work'
            and identity=='lnw:wd:Q110895870' and row.get('work_id')==identity
            and 'series_id' not in row and row.get('fresh_classification') is False
            and row.get('archive_context')=='historical_mixed_metadata_lead_and_plot')
    if version==WORK_VERSION:
        return (target.get('entry_kind')=='work' and row.get('scope')=='work'
            and row.get('entity_kind')=='light_novel_work' and row.get('work_id')==identity
            and 'series_id' not in row and isinstance(identity,str)
            and re.fullmatch(r'lnw:wd:Q[1-9][0-9]*',identity) is not None)
    return False
