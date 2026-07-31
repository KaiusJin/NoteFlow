from __future__ import annotations
import atexit
import threading
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from noteflow_worker.config import settings

class CleanConnection:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self._conn.__exit__(exc_type, exc_val, exc_tb)

    def execute(self, query, params=None, *, prepare=None):
        clean_params = self._clean_nuls(params)
        return self._conn.execute(query, clean_params, prepare=prepare)

    def _clean_nuls(self, params):
        if params is None:
            return None
        if isinstance(params, tuple):
            return tuple(self._clean_nuls(x) for x in params)
        if isinstance(params, list):
            return [self._clean_nuls(x) for x in params]
        if isinstance(params, dict):
            return {k: self._clean_nuls(v) for k, v in params.items()}
        if isinstance(params, str):
            return params.replace('\x00', '')
        return params

    def __getattr__(self, name):
        return getattr(self._conn, name)


# Process-wide connection pool. Created lazily (never at import time) so that
# spawned child processes build their own pool instead of inheriting sockets,
# and so scripts that never touch the DB pay nothing.
_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool(
                    settings.database_url,
                    min_size=settings.db_pool_min_size,
                    max_size=settings.db_pool_max_size,
                    kwargs={"row_factory": dict_row},
                    name="noteflow-worker",
                    open=True,
                )
                atexit.register(_pool.close)
    return _pool



class BaseRepository:
    @contextmanager
    def connect(self):
        with _get_pool().connection(
            timeout=settings.db_pool_acquire_timeout_seconds
        ) as conn:
            yield CleanConnection(conn)
