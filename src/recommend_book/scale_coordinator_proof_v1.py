"""Follow the actual terminal proof to its parent request and coordinator bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def coordinator_chain_pins(document):
    """Return fresh exact-byte pins; no saved flag substitutes for this chain."""
    pins = {}

    def checked(path, expected=None):
        path = Path(path)
        if not path.is_absolute():
            raise ValueError('Coordinator proof path must be absolute')
        path = path.resolve(strict=True)
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if expected is not None and digest != expected:
            raise ValueError('Coordinator proof artifact SHA differs: ' + str(path))
        pins[str(path)] = digest
        return path, raw, digest

    runtime = Path(document['runtime_preparation'])
    run_path = Path(document['run_manifest'])
    if not runtime.is_absolute() or not run_path.is_absolute():
        raise ValueError('Coordinator runtime/run paths must be absolute')
    runtime = runtime.resolve(strict=True)
    run_path = run_path.resolve(strict=True)
    relative = run_path.relative_to(runtime / 'runtime/data/autonomous-next-v3/classification')
    if len(relative.parts) != 3 or relative.parts[1:] != ('run', 'manifest.json'):
        raise ValueError('Coordinator proof does not identify one actual native window')
    artifacts = document['artifact_sha256']
    if str(run_path) not in artifacts:
        raise ValueError('Coordinator proof lacks pinned actual run manifest')
    _, raw, run_sha = checked(run_path, artifacts[str(run_path)])
    run = json.loads(raw)
    terminal_path = runtime / ('terminal-' + relative.parts[0]) / 'terminal-proof.json'
    if str(terminal_path) not in artifacts:
        raise ValueError('Coordinator proof lacks pinned actual terminal proof')
    _, raw, _ = checked(terminal_path, artifacts[str(terminal_path)])
    terminal = json.loads(raw)
    if (terminal['schema_version'] != 'classification-terminal-proof-v1'
            or terminal['run_id'] != run['run_id'] or terminal['manifest_sha256'] != run_sha
            or terminal['exit_code'] != 0 or terminal['process_exit_observed'] is not True
            or type(terminal['process_pid']) is not int or terminal['process_pid'] <= 0):
        raise ValueError('Coordinator terminal proof differs from actual successful run')
    _, raw, _ = checked(terminal_path.with_name('request.json'), terminal['parent_request_sha256'])
    request = json.loads(raw)
    if (request['version'] != 'native-snapshot-terminal-request-v1'
            or request['coordinator_sha256'] != document['coordinator_sha256']):
        raise ValueError('Parent request coordinator differs from exported coordinator')
    checked(request['coordinator_path'], request['coordinator_sha256'])
    _, raw, _ = checked(terminal_path.with_name('process-exit.json'), terminal['parent_exit_observation_sha256'])
    observed = json.loads(raw)
    if (observed['version'] != 'native-snapshot-terminal-exit-v1'
            or observed['process_pid'] != terminal['process_pid'] or observed['actual_exit_code'] != 0
            or observed['process_exit_observed'] is not True):
        raise ValueError('Parent observed exit differs from actual native terminal proof')
    checked(Path(__file__).resolve())
    # Recheck all exact bytes to close changes while traversing the chain.
    for path, digest in list(pins.items()):
        checked(path, digest)
    return [{'path': path, 'sha256': digest} for path, digest in pins.items()]
