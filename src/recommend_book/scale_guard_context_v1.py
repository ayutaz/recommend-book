"""Scale-only exact-byte JSON decode reuse; all file reads/SHA checks still run.

No permission decision is cached. Reuse requires identical complete JSON bytes;
decoded containers are recursively read-only. Changed raw, policy or revocation
bytes therefore take the ordinary decoder and ordinary admission path.
"""
from collections import OrderedDict
from contextlib import contextmanager
import importlib
import json
import threading


def _readonly(*args, **kwargs):
    raise TypeError('Scale admission cached JSON is read-only')


class ReadOnlyDict(dict):
    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly


class ReadOnlyList(list):
    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _readonly


def freeze(value):
    if isinstance(value, dict):
        return ReadOnlyDict((key, freeze(item)) for key, item in value.items())
    if isinstance(value, list):
        return ReadOnlyList(freeze(item) for item in value)
    return value


class ExactJSONCache:
    def __init__(self, *, maximum_bytes=128 * 1024 * 1024):
        self.maximum_bytes = maximum_bytes
        self.entries = OrderedDict()
        self.bytes = self.hits = self.misses = 0
        self.lock = threading.RLock()

    def loads(self, raw, *args, **kwargs):
        if args or kwargs or not isinstance(raw, (bytes, str)):
            return json.loads(raw, *args, **kwargs)
        key = (type(raw), raw)
        size = len(raw) if isinstance(raw, bytes) else len(raw.encode('utf-8'))
        with self.lock:
            if key in self.entries:
                self.hits += 1
                self.entries.move_to_end(key)
                return self.entries[key]
            self.misses += 1
            result = freeze(json.loads(raw))
            if size <= self.maximum_bytes:
                self.entries[key] = result
                self.bytes += size
                while self.bytes > self.maximum_bytes:
                    old, _ = self.entries.popitem(last=False)
                    self.bytes -= len(old[1]) if isinstance(old[1], bytes) else len(old[1].encode('utf-8'))
            return result

    def __getattr__(self, name):
        return getattr(json, name)


@contextmanager
def guard_json_cache(*, maximum_bytes=128 * 1024 * 1024):
    cache = ExactJSONCache(maximum_bytes=maximum_bytes)
    modules = []
    for name in ('recommend_book.usage_guard', 'recommend_book.usage_guard_v4'):
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name != name:
                raise
            continue
        modules.append((module, module.json))
        module.json = cache
    try:
        yield cache
    finally:
        for module, original in modules:
            module.json = original


def enable_process_cache():
    """Explicit new scale CLI/validator lifetime only; never installed globally."""
    scope = guard_json_cache()
    cache = scope.__enter__()
    # Hold the context manager for this process; no caller outside this explicit
    # scale entry point is affected. Interpreter shutdown discards the cache.
    cache.scope = scope
    return cache
