"""Reuse authentic v1 replay receipts and freshly check the coordinator chain."""
from pathlib import Path
import hashlib
import json

from .scale_coordinator_proof_v1 import coordinator_chain_pins
from .scale_gate_replay_v1 import verify_prior_export as _verify_v1


def verify_prior_export(manifest_path, expected_sha256, cache_directory):
    path = Path(manifest_path).resolve(strict=True)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('Prior export manifest SHA differs')
    document = json.loads(raw)
    # Fail changed coordinator/request bytes before considering a cached replay.
    additional = coordinator_chain_pins(document)
    additional.append({'path': str(Path(__file__).resolve()),
                       'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    result = _verify_v1(path, expected_sha256, cache_directory)
    if path.read_bytes() != raw:
        raise ValueError('Prior export changed during coordinator-chain verification')
    for item in additional:
        if hashlib.sha256(Path(item['path']).read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('Prior coordinator chain changed during native replay')
    return {**result, 'additional_required_artifacts': additional}
