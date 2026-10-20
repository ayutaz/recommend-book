"""Verified shared reference bytes, never linked to a mutable source inode.

Read-only mode prevents accidental writes, not writes by the filesystem owner.
Every use checks exact content hashes; corrupt entries are refused, never repaired.
Only fixed reference material belongs here, never model results or source code.
"""
import hashlib
import json
from pathlib import Path


BINDING_NAME = 'config/native-reference-bindings-v1.json'
REFERENCE_PREFIX = 'data/autonomous-next-v3/reference/research-codebook/'
REFERENCE_NAMES = tuple(REFERENCE_PREFIX + name for name in
    ('native-v3-source-contract.json', 'keywords.xml', 'book.pdf'))


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_binding(binding, *, relative=None):
    if binding.get('version') != 'native-reference-bindings-v1' or set(binding['artifacts']) != set(REFERENCE_NAMES):
        raise ValueError('Shared reference binding roster differs')
    root = Path(binding['cache_root']).resolve(strict=True)
    if str(root) != binding['cache_root']:
        raise ValueError('Shared reference cache root differs')
    paths = {}
    selected = binding['artifacts'] if relative is None else {relative: binding['artifacts'][relative]}
    for relative, item in selected.items():
        path = Path(item['path'])
        expected_path = root / item['sha256']
        if not path.is_absolute() or path != expected_path or path.resolve(strict=True) != expected_path:
            raise ValueError('Shared reference cache path differs')
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or _digest(raw) != item['sha256'] or path.stat().st_nlink != 1:
            raise ValueError('Shared reference content differs: ' + relative)
        paths[relative] = path
    return paths


def store_references(cache_root, originals):
    cache_root = Path(cache_root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    artifacts, allocated = {}, 0
    for relative in REFERENCE_NAMES:
        raw = originals[relative]
        digest = _digest(raw)
        path = cache_root / digest
        try:
            with path.open('xb') as stream:
                stream.write(raw)
            path.chmod(0o444)
            allocated += len(raw)
        except FileExistsError:
            pass
        artifacts[relative] = {'path': str(path), 'sha256': digest, 'bytes': len(raw)}
    binding = {'version': 'native-reference-bindings-v1', 'cache_root': str(cache_root), 'artifacts': artifacts}
    verify_binding(binding)
    return binding, allocated


def reference_artifact_path(root, relative):
    """Old runtimes retain independent copies; new runtimes pin shared bytes."""
    root = Path(root)
    binding_path = root / BINDING_NAME
    if relative not in REFERENCE_NAMES or not binding_path.exists():
        return root / relative
    binding = json.loads(binding_path.read_bytes())
    return verify_binding(binding, relative=relative)[relative]
