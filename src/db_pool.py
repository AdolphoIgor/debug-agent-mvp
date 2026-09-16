from __future__ import annotations

import os
import threading
from collections.abc import Generator
from contextlib import contextmanager

from psycopg2 import pool
from psycopg2.extensions import STATUS_READY, connection


class DatabasePool:
    _pool: pool.ThreadedConnectionPool | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def initialize(cls, minconn: int = 1, maxconn: int = 10, dsn: str | None = None) -> None:
        if cls._pool is None:
            with cls._lock:
                if cls._pool is None:
                    db_dsn = dsn or os.environ.get(
                        "DATABASE_URL", "postgresql://mvp_user:mvp_password@postgres:5432/mvp_db"
                    )
                    cls._pool = pool.ThreadedConnectionPool(
                        minconn=minconn, maxconn=maxconn, dsn=db_dsn
                    )

    @classmethod
    @contextmanager
    def get_connection(cls) -> Generator[connection, None, None]:
        if cls._pool is None:
            cls.initialize()

        if cls._pool is None:
            raise RuntimeError("Failed to initialize PostgreSQL connection pool.")

        conn = cls._pool.getconn()
        try:
            yield conn
        except Exception:
            if conn and not conn.closed:
                conn.rollback()
            raise
        finally:
            if conn and not conn.closed:
                if conn.status != STATUS_READY:
                    conn.rollback()
                cls._pool.putconn(conn)

    @classmethod
    def close_all(cls) -> None:
        with cls._lock:
            if cls._pool is not None:
                cls._pool.closeall()
                cls._pool = None
