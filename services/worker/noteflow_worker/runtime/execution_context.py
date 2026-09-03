"""Per-thread/process task execution identity used by repository CAS updates."""

from __future__ import annotations

import threading
from contextlib import contextmanager


_execution = threading.local()


@contextmanager
def task_execution_scope(execution_id: str | None, workspace_id: str | None = None):
    previous = getattr(_execution, "execution_id", None)
    previous_workspace = getattr(_execution, "workspace_id", None)
    _execution.execution_id = execution_id
    _execution.workspace_id = workspace_id
    try:
        yield
    finally:
        _execution.execution_id = previous
        _execution.workspace_id = previous_workspace


def current_execution_id() -> str | None:
    return getattr(_execution, "execution_id", None)


def current_workspace_id() -> str | None:
    return getattr(_execution, "workspace_id", None)
