import threading

import pytest

import api.db as db_module


@pytest.fixture
def captured_engines(monkeypatch):
    """Capture create_engine kwargs and isolate the module engine cache."""
    calls = []

    def fake_create_engine(url, **kwargs):
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr("sqlalchemy.create_engine", fake_create_engine)
    monkeypatch.setattr(db_module, "_engines", {})
    monkeypatch.setattr(db_module.settings, "database_url", "postgresql://user:pass@localhost/test")
    return calls


def test_api_engine_uses_pool_settings_and_statement_timeout(captured_engines):
    db_module.get_engine()

    url, kwargs = captured_engines[0]
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_size"] == 10
    assert kwargs["max_overflow"] == 20
    assert kwargs["pool_recycle"] == 1800
    assert kwargs["connect_args"] == {"options": "-c statement_timeout=30000"}


def test_writer_engine_has_no_statement_timeout(captured_engines):
    db_module.get_engine(role="writer")

    _, kwargs = captured_engines[0]
    assert kwargs["pool_pre_ping"] is True
    assert "options" not in kwargs.get("connect_args", {})


def test_engines_are_cached_per_role(captured_engines):
    api_engine = db_module.get_engine(role="api")
    writer_engine = db_module.get_engine(role="writer")

    assert db_module.get_engine() is api_engine
    assert db_module.get_engine(role="writer") is writer_engine
    assert api_engine is not writer_engine
    assert len(captured_engines) == 2


def test_get_engine_is_thread_safe_creating_one_engine_per_role(captured_engines):
    # All threads block on the barrier and then race get_engine() at once;
    # the lock must collapse them into a single create_engine call.
    thread_count = 8
    barrier = threading.Barrier(thread_count)
    engines = []
    engines_lock = threading.Lock()

    def race():
        barrier.wait()
        engine = db_module.get_engine()
        with engines_lock:
            engines.append(engine)

    threads = [threading.Thread(target=race) for _ in range(thread_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured_engines) == 1
    assert all(engine is engines[0] for engine in engines)


def test_get_engine_requires_database_url(captured_engines, monkeypatch):
    monkeypatch.setattr(db_module.settings, "database_url", "")

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        db_module.get_engine()
