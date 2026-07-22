from __future__ import annotations

import sqlite3

from sqlModels.settings_repo import recover_settings_from_readonly_db


def _create_settings_table(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )


def test_recover_settings_from_readonly_db_restores_complete_identity(tmp_path):
    source_path = tmp_path / "primary.sqlite3"
    source = sqlite3.connect(source_path)
    _create_settings_table(source)
    source.executemany(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        [
            ("country", "PERU"),
            ("company_type", "LA CASA DEL PERFUME"),
            ("store_id", "123"),
            ("username", "user_demo"),
            ("tienda", "1"),
        ],
    )
    source.commit()
    source.close()

    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    _create_settings_table(target)
    target.executemany(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        [("store_id", ""), ("username", "")],
    )

    recovered = recover_settings_from_readonly_db(
        target,
        source_db_path=str(source_path),
        keys=("country", "company_type", "store_id", "username", "tienda"),
        required_keys=("store_id", "username"),
    )

    rows = dict(target.execute("SELECT key, value FROM settings"))
    target.close()

    assert recovered is True
    assert rows["store_id"] == "123"
    assert rows["username"] == "user_demo"
    assert rows["country"] == "PERU"


def test_recover_settings_from_readonly_db_does_not_copy_partial_identity(tmp_path):
    source_path = tmp_path / "primary.sqlite3"
    source = sqlite3.connect(source_path)
    _create_settings_table(source)
    source.executemany(
        "INSERT INTO settings(key, value) VALUES(?, ?)",
        [("store_id", "123"), ("username", "")],
    )
    source.commit()
    source.close()

    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    _create_settings_table(target)

    recovered = recover_settings_from_readonly_db(
        target,
        source_db_path=str(source_path),
        keys=("store_id", "username"),
        required_keys=("store_id", "username"),
    )

    rows = target.execute("SELECT key, value FROM settings").fetchall()
    target.close()

    assert recovered is False
    assert rows == []
