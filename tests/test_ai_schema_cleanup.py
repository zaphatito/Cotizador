from sqlModels.db import connect, ensure_schema
from sqlModels.settings_repo import get_setting
from src.ai.search_index import (
    _FTS_CLIENTS,
    _FTS_PRODUCTS,
    _SEARCH_CACHE_TABLE,
    drop_ai_schema,
    ensure_ai_schema,
)
import src.config as config


def _table_exists(con, name: str) -> bool:
    row = con.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (name,),
    ).fetchone()
    return row is not None


def test_drop_ai_schema_removes_ai_tables(tmp_path):
    db_path = tmp_path / "ai.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)

    assert ensure_ai_schema(con) is True
    con.commit()

    assert _table_exists(con, _FTS_PRODUCTS)
    assert _table_exists(con, _FTS_CLIENTS)
    assert _table_exists(con, _SEARCH_CACHE_TABLE)

    drop_ai_schema(con)
    con.commit()

    assert not _table_exists(con, _FTS_PRODUCTS)
    assert not _table_exists(con, _FTS_CLIENTS)
    assert not _table_exists(con, _SEARCH_CACHE_TABLE)

    con.close()


def test_set_ai_enabled_false_drops_ai_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "ai-toggle.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)
    assert ensure_ai_schema(con) is True
    con.commit()
    con.close()

    monkeypatch.setattr(config, "_resolve_db_path_for_config", lambda: str(db_path))
    monkeypatch.setattr(config, "ENABLE_AI", True)
    monkeypatch.setitem(config.APP_CONFIG, "enable_ai", True)

    assert config.set_ai_enabled(False) is False

    con = connect(str(db_path))
    ensure_schema(con)
    assert get_setting(con, "enable_ai", "1") == "0"
    assert not _table_exists(con, _FTS_PRODUCTS)
    assert not _table_exists(con, _FTS_CLIENTS)
    assert not _table_exists(con, _SEARCH_CACHE_TABLE)
    con.close()


def test_set_recommendations_enabled_rechecks_cleanup_when_ai_is_off(tmp_path, monkeypatch):
    db_path = tmp_path / "recs-toggle.sqlite3"
    con = connect(str(db_path))
    ensure_schema(con)
    assert ensure_ai_schema(con) is True
    con.commit()
    con.close()

    monkeypatch.setattr(config, "_resolve_db_path_for_config", lambda: str(db_path))
    monkeypatch.setattr(config, "ENABLE_AI", False)
    monkeypatch.setattr(config, "ENABLE_RECOMMENDATIONS", True)
    monkeypatch.setitem(config.APP_CONFIG, "enable_ai", False)
    monkeypatch.setitem(config.APP_CONFIG, "enable_recommendations", True)

    assert config.set_recommendations_enabled(False) is False

    con = connect(str(db_path))
    ensure_schema(con)
    assert get_setting(con, "enable_recommendations", "1") == "0"
    assert not _table_exists(con, _FTS_PRODUCTS)
    assert not _table_exists(con, _FTS_CLIENTS)
    assert not _table_exists(con, _SEARCH_CACHE_TABLE)
    con.close()
