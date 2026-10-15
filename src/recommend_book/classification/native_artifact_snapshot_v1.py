"""Future-batch executable check; does not authorize or adopt native results.

Original native v3 receipts still use their frozen verifier. A future recipe may
call this once for all its command receipts, and must wait for its return before
adoption. No process-global or cross-invocation cache is retained.
"""
import hashlib
from pathlib import Path


def _identity(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size


def verify_command_receipts(command, expected_receipts):
    """Compare all receipts to one byte snapshot, then reread content to close.

    The final comparison uses bytes, not timestamps. This detects a changed,
    removed or replaced source at closing, including same-size/mtime edits. It
    does not attest that an executable ran or prevent an adversarial transient
    edit restored before closing; executable execution/OS locking is separate.
    """
    if not command or not expected_receipts:
        raise ValueError("A command and at least one expected receipt are required")
    aliases = [Path(command[0]).absolute()]
    if len(command) > 1:
        aliases.append(Path(command[1]).absolute())
    resolved = {path: path.resolve(strict=True) for path in aliases}
    paths = list(resolved.values())
    paths = list(dict.fromkeys(paths))
    snapshots = {}
    identities = {}
    for path in paths:
        identities[path] = _identity(path)
        snapshots[path] = path.read_bytes()
        if _identity(path) != identities[path]:
            raise ValueError("Executable source changed during snapshot")
    hashes = {str(path): hashlib.sha256(value).hexdigest() for path, value in snapshots.items()}
    receipt_count = 0
    for expected in expected_receipts:
        if expected != hashes:
            raise ValueError("Executable receipt differs from fixed bytes")
        receipt_count += 1
    if not receipt_count:
        raise ValueError("At least one expected receipt is required")
    if any(path.resolve(strict=True) != target for path, target in resolved.items()):
        raise ValueError("Executable source alias changed before batch completion")
    for path, value in snapshots.items():
        if _identity(path) != identities[path] or path.read_bytes() != value or _identity(path) != identities[path]:
            raise ValueError("Executable source changed before batch completion")
    return {"version": "native-artifact-snapshot-v1", "executable_sha256": hashes,
            "receipt_count": receipt_count, "content_reads": len(paths) * 2,
            "content_bytes_read": sum(map(len, snapshots.values())) * 2,
            "bytes_hashed": sum(map(len, snapshots.values())), "result_adoption_authorized": False}
