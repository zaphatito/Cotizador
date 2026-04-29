from sqlModels.db import connect, ensure_schema, tx
from sqlModels.sequences_repo import (
    ensure_quote_no_at_least,
    get_quote_no_value,
    next_quote_no,
)


def test_quote_no_read_does_not_open_implicit_transaction(tmp_path):
    db_path = str(tmp_path / "sequences.sqlite3")
    con = connect(db_path)
    ensure_schema(con)

    assert get_quote_no_value(con, "PY") == 0
    assert con.in_transaction is False

    with tx(con):
        ensure_quote_no_at_least(con, "PY", 122)
        assert next_quote_no(con, "PY") == "0000123"

    assert get_quote_no_value(con, "PY") == 123
    con.close()
