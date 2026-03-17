from __future__ import annotations

from sqlModels.db import connect, ensure_schema
from sqlModels.schema import SCHEMA_VERSION
from sqlModels.settings_repo import get_setting, set_setting


def test_settings_tienda_supports_null_and_migrates_existing_schema():
    con = connect(":memory:")
    con.execute(
        """
        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    con.execute(
        """
        CREATE TABLE settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    con.execute("INSERT INTO meta(key, value) VALUES('schema_version', '29')")
    con.execute("INSERT INTO settings(key, value) VALUES('country', 'PARAGUAY')")
    con.commit()

    ensure_schema(con)

    schema_version = con.execute(
        "SELECT value FROM meta WHERE key = 'schema_version'"
    ).fetchone()
    assert schema_version is not None
    assert str(schema_version["value"]) == str(SCHEMA_VERSION)

    cols = con.execute("PRAGMA table_info(settings)").fetchall()
    value_col = next(row for row in cols if str(row["name"]).lower() == "value")
    assert int(value_col["notnull"]) == 0

    assert get_setting(con, "country", "") == "PARAGUAY"
    assert get_setting(con, "tienda", None) is None

    set_setting(con, "tienda", None)
    row = con.execute(
        "SELECT value FROM settings WHERE key = 'tienda'"
    ).fetchone()
    assert row is not None
    assert row["value"] is None

    set_setting(con, "tienda", "1")
    assert get_setting(con, "tienda", None) == "1"
