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


class CommandArtifactSnapshot:
    """One explicit publication scope; no cache or authorization survives close."""

    def __init__(self, command):
        if not command:
            raise ValueError("A command is required")
        self.command = list(command)
        aliases = [Path(command[0]).absolute()]
        if len(command) > 1:
            aliases.append(Path(command[1]).absolute())
        self.resolved = {path: path.resolve(strict=True) for path in aliases}
        self.snapshots, self.identities = {}, {}
        for path in dict.fromkeys(self.resolved.values()):
            self.identities[path] = _identity(path)
            self.snapshots[path] = path.read_bytes()
            if _identity(path) != self.identities[path]:
                raise ValueError("Executable source changed during snapshot")
        self.hashes = {str(path): hashlib.sha256(value).hexdigest() for path, value in self.snapshots.items()}
        self.receipt_count = 0
        self.closed = False

    def check_receipt(self, command, expected):
        if self.closed:
            raise ValueError("Executable snapshot scope is closed")
        if list(command) != self.command or expected != self.hashes:
            raise ValueError("Executable receipt differs from fixed bytes")
        self.receipt_count += 1

    def close(self):
        if self.closed:
            raise ValueError("Executable snapshot scope is closed")
        self.closed = True
        if not self.receipt_count:
            raise ValueError("At least one expected receipt is required")
        if any(path.resolve(strict=True) != target for path, target in self.resolved.items()):
            raise ValueError("Executable source alias changed before batch completion")
        for path, value in self.snapshots.items():
            if _identity(path) != self.identities[path] or path.read_bytes() != value or _identity(path) != self.identities[path]:
                raise ValueError("Executable source changed before batch completion")
        report = {"version": "native-artifact-snapshot-v1", "executable_sha256": self.hashes,
                  "receipt_count": self.receipt_count, "content_reads": len(self.snapshots) * 2,
                  "content_bytes_read": sum(map(len, self.snapshots.values())) * 2,
                  "bytes_hashed": sum(map(len, self.snapshots.values())), "result_adoption_authorized": False}
        self.snapshots.clear()
        return report


def verify_command_receipts(command, expected_receipts):
    """Compare all receipts to one byte snapshot, then reread content to close.

    The final comparison uses bytes, not timestamps. This detects a changed,
    removed or replaced source at closing, including same-size/mtime edits. It
    does not attest that an executable ran or prevent an adversarial transient
    edit restored before closing; executable execution/OS locking is separate.
    """
    if not command or not expected_receipts:
        raise ValueError("A command and at least one expected receipt are required")
    snapshot = CommandArtifactSnapshot(command)
    for expected in expected_receipts:
        snapshot.check_receipt(command, expected)
    return snapshot.close()
