from __future__ import annotations

from contextlib import contextmanager
from threading import BoundedSemaphore, Lock


_registry_lock = Lock()
_semaphores: dict[str, tuple[int, BoundedSemaphore]] = {}


@contextmanager
def process_resource_slot(name: str, limit: int):
    """Enforce a process-wide limit across all concurrent document tasks."""
    resolved_limit = max(1, limit)
    with _registry_lock:
        configured = _semaphores.get(name)
        if configured is None:
            configured = (resolved_limit, BoundedSemaphore(resolved_limit))
            _semaphores[name] = configured
        elif configured[0] != resolved_limit:
            # Runtime settings are immutable in production. Retain the first
            # semaphore rather than replacing one while slots are in flight.
            resolved_limit = configured[0]
        semaphore = configured[1]
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()
