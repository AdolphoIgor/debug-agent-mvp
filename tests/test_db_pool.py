from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest
from psycopg2.extensions import STATUS_READY

from src.db_pool import DatabasePool


@pytest.fixture(autouse=True)
def reset_pool_singleton():
    DatabasePool._pool = None
    yield
    DatabasePool._pool = None


def test_initialize_creates_threaded_pool():
    with patch("src.db_pool.pool.ThreadedConnectionPool") as mock_pool_cls:
        mock_instance = MagicMock()
        mock_pool_cls.return_value = mock_instance

        DatabasePool.initialize(
            minconn=2,
            maxconn=5,
            dsn="postgresql://test:test@localhost:5432/db",
        )

        mock_pool_cls.assert_called_once_with(
            minconn=2,
            maxconn=5,
            dsn="postgresql://test:test@localhost:5432/db",
        )
        assert DatabasePool._pool is mock_instance


def test_initialize_idempotent_double_checked():
    with patch("src.db_pool.pool.ThreadedConnectionPool") as mock_pool_cls:
        mock_instance = MagicMock()
        mock_pool_cls.return_value = mock_instance

        DatabasePool.initialize()
        DatabasePool.initialize()

        mock_pool_cls.assert_called_once()


def test_initialize_thread_concurrency():
    with patch("src.db_pool.pool.ThreadedConnectionPool") as mock_pool_cls:
        mock_pool_cls.return_value = MagicMock()

        threads = [threading.Thread(target=DatabasePool.initialize) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        mock_pool_cls.assert_called_once()


def test_get_connection_success():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.status = STATUS_READY
    mock_pool.getconn.return_value = mock_conn

    DatabasePool._pool = mock_pool

    with DatabasePool.get_connection() as conn:
        assert conn is mock_conn

    mock_pool.getconn.assert_called_once()
    mock_pool.putconn.assert_called_once_with(mock_conn)
    mock_conn.rollback.assert_not_called()


def test_get_connection_auto_initializes_when_none():
    with (
        patch.object(DatabasePool, "initialize") as mock_init,
        patch("src.db_pool.pool.ThreadedConnectionPool"),
    ):
        mock_pool = MagicMock()
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_conn.status = STATUS_READY
        mock_pool.getconn.return_value = mock_conn

        def set_pool(*args, **kwargs):
            DatabasePool._pool = mock_pool

        mock_init.side_effect = set_pool

        with DatabasePool.get_connection() as conn:
            assert conn is mock_conn

        mock_init.assert_called_once()
        mock_pool.putconn.assert_called_once_with(mock_conn)


def test_get_connection_raises_runtime_error_if_pool_still_none():
    with patch.object(DatabasePool, "initialize"):
        DatabasePool._pool = None
        with pytest.raises(RuntimeError, match="Failed to initialize PostgreSQL connection pool"):
            with DatabasePool.get_connection():
                pass


def test_get_connection_rollback_on_exception():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.status = STATUS_READY
    mock_pool.getconn.return_value = mock_conn
    DatabasePool._pool = mock_pool

    with pytest.raises(ValueError, match="Operation failure"):
        with DatabasePool.get_connection():
            raise ValueError("Operation failure")

    mock_conn.rollback.assert_called_once()
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_get_connection_rollback_on_unready_status():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_conn.closed = False
    mock_conn.status = 999
    mock_pool.getconn.return_value = mock_conn
    DatabasePool._pool = mock_pool

    with DatabasePool.get_connection():
        pass

    mock_conn.rollback.assert_called_once()
    mock_pool.putconn.assert_called_once_with(mock_conn)


def test_close_all_closes_pool_and_resets():
    mock_pool = MagicMock()
    DatabasePool._pool = mock_pool

    DatabasePool.close_all()

    mock_pool.closeall.assert_called_once()
    assert DatabasePool._pool is None


def test_close_all_when_already_none_noop():
    DatabasePool._pool = None
    DatabasePool.close_all()
    assert DatabasePool._pool is None
