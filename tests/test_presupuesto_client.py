import pytest

import src.api.presupuesto_client as pc


def test_infer_tipo_documento_for_api_handles_peru_legacy_numeric_docs():
    assert pc._infer_tipo_documento_for_api("12345678", "PE") == "DNI"
    assert pc._infer_tipo_documento_for_api("123456789", "PE") == ""
    assert pc._infer_tipo_documento_for_api("12345678901", "PE") == "RUC"


def test_infer_tipo_documento_for_api_matches_ve_py_regex_rules():
    # VE numerico es ambiguo entre V/E/J/G; fallback por defecto -> V
    assert pc._infer_tipo_documento_for_api("123456789", "VE") == "V"
    assert pc._infer_tipo_documento_for_api("1234567", "VE") == "V"
    assert pc._infer_tipo_documento_for_api("1234567", "PY") == "CI"
    assert pc._infer_tipo_documento_for_api("J-123456789", "VE") == "J"
    assert pc._infer_tipo_documento_for_api("G-123456789", "VE") == "G"
    assert pc._infer_tipo_documento_for_api("P-AB123", "PY") == "P"


def test_infer_tipo_documento_for_api_enforces_validation_pad():
    assert pc._infer_tipo_documento_for_api("12345678", "VE") == "V"
    assert pc._infer_tipo_documento_for_api("123456789", "VE") == "V"
    assert pc._infer_tipo_documento_for_api("1234567", "PE") == ""
    assert pc._infer_tipo_documento_for_api("12345678", "PE") == "DNI"
    assert pc._infer_tipo_documento_for_api("1234567890", "PE") == ""
    assert pc._infer_tipo_documento_for_api("12345678901", "PE") == "RUC"


def test_quantity_for_api_keeps_py_cats_positive_values_above_zero():
    assert pc._quantity_for_api({"categoria": "ESENCIAS", "cantidad": 0.001}, cod_pais="PY") == 1
    assert pc._quantity_for_api({"categoria": "ESENCIAS", "cantidad": 0.0}, cod_pais="PY") == 0


def test_quantity_for_api_keeps_py_unit_exception_codes_as_units():
    assert pc._quantity_for_api({"codigo": "FERO001", "categoria": "ESENCIAS", "cantidad": 3}, cod_pais="PY") == 3
    assert pc._quantity_for_api({"codigo": "FIJ002", "categoria": "ESENCIAS", "cantidad": 0.4}, cod_pais="PY") == 1


@pytest.mark.parametrize("categoria", ["ESENCIAS", "FEROMONAS", "FIJADOR"])
def test_quantity_for_api_keeps_peru_weight_quantity_in_kilos(categoria):
    item = {"codigo": "PE001", "categoria": categoria, "cantidad": 0.050}

    assert pc._quantity_for_api(item, cod_pais="PE") == pytest.approx(0.050)


def test_build_presupuesto_payload_includes_adjuntos():
    adjuntos = [
        {
            "tipo": "pdf",
            "nombre_archivo": "cotizacion_001.pdf",
            "mime_type": "application/pdf",
            "extension": "pdf",
        },
        {
            "tipo": "cmd",
            "nombre_archivo": "proceso_001.cmd",
            "mime_type": "text/plain",
            "extension": "cmd",
        },
    ]

    payload = pc.build_presupuesto_payload(
        quote_code="PE-001-0000001",
        cliente="Cliente Demo",
        cedula="DNI-12345678",
        telefono="555",
        metodo_pago="EFECTIVO",
        tipo_documento="DNI",
        cod_pais="PE",
        empresa="LA CASA DEL PERFUME",
        user_api="user_demo",
        tienda=True,
        id_cotizador="001",
        items_base=[],
        adjuntos=adjuntos,
    )

    assert payload["adjuntos"] == adjuntos
    assert payload["presupuesto"]["codigo"] == "PE-001-0000001"
    assert payload["presupuesto"]["tipo_documento"] == "DNI"
    assert payload["presupuesto"]["tienda"] is True
    assert payload["presupuesto"]["cantidad_items"] == 0


def test_reserve_next_quote_code_uses_api_sequence(monkeypatch):
    from types import SimpleNamespace

    sent: dict[str, object] = {}

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_GET_NEXT_QUOTE_CODE):
            sent.update(kwargs)
            return SimpleNamespace(
                status_code=200,
                data={
                    "data": {
                        "quote_code": "PE-001-0000123",
                        "quote_no": "0000123",
                        "last_value": 123,
                    }
                },
                text='{"ok":true}',
            )
        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setattr(pc, "_load_api_identity", lambda: (1003, "cotizador-PE-2", "user_demo", "PERU", "LA CASA DEL PERFUME", "001", False))
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.reserve_next_quote_code(local_last_value=122)

    assert res["quote_code"] == "PE-001-0000123"
    assert res["quote_no"] == "0000123"
    assert sent.get("json_data") == {
        "id_cotizador": "001",
        "user": "user_demo",
        "cod_pais": "PE",
        "local_last_value": 122,
    }


def test_fetch_country_clients_returns_rows_normalized_for_ui(monkeypatch):
    from types import SimpleNamespace

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_GET_COUNTRY_CLIENTS):
            return SimpleNamespace(
                status_code=200,
                data={
                    "data": [
                        {
                            "id": 55,
                            "country_code": "PE",
                            "tipo_documento": "DNI",
                            "documento": "12345678",
                            "documento_norm": "12345678",
                            "nombre": "Cliente Demo",
                            "telefono": "555",
                            "direccion": "Lima",
                            "email": "demo@example.com",
                            "created_at": "2026-04-08 10:00:00",
                            "updated_at": "2026-04-08 11:00:00",
                        }
                    ]
                },
                text='{"ok":true}',
            )
        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setattr(pc, "_load_api_identity", lambda: (1003, "cotizador-PE-2", "user_demo", "PERU", "LA CASA DEL PERFUME", "001", False))
    monkeypatch.setattr(pc, "post", fake_post)

    rows = pc.fetch_country_clients(search_text="cliente")

    assert rows == [
        {
            "id": 55,
            "country_code": "PE",
            "tipo_documento": "DNI",
            "documento": "12345678",
            "documento_norm": "12345678",
            "nombre": "Cliente Demo",
            "telefono": "555",
            "direccion": "Lima",
            "email": "demo@example.com",
            "source_quote_id": None,
            "source_created_at": "",
            "created_at": "2026-04-08 10:00:00",
            "updated_at": "2026-04-08 11:00:00",
            "deleted_at": None,
        }
    ]


def test_build_adjuntos_for_quote_reads_pdf_and_cmd(tmp_path):
    pdf_name = "C-PE-001-0000001_20260224_101500.pdf"
    pdf_path = tmp_path / pdf_name
    pdf_path.write_bytes(b"%PDF-1.4\n")

    cmd_dir = tmp_path / "tickets"
    cmd_dir.mkdir(parents=True, exist_ok=True)
    cmd_path = cmd_dir / "C-PE-001-0000001_20260224_101500.IMPRIMIR_TICKET.cmd"
    cmd_path.write_text("@echo off\r\n", encoding="utf-8")

    out = pc._build_adjuntos_for_quote({"pdf_path": str(pdf_path)})

    assert out == [
        {
            "tipo": "pdf",
            "nombre_archivo": pdf_name,
            "mime_type": "application/pdf",
            "extension": "pdf",
        },
        {
            "tipo": "cmd",
            "nombre_archivo": cmd_path.name,
            "mime_type": "text/plain",
            "extension": "cmd",
        },
    ]


def test_build_multipart_form_data_with_presupuesto_and_files(tmp_path):
    pdf_path = tmp_path / "cotizacion_001.pdf"
    cmd_path = tmp_path / "proceso_001.cmd"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    cmd_path.write_text("@echo off\r\n", encoding="utf-8")

    file_parts = pc._read_adjunto_file_parts(
        [
            {
                "field_name": "adjunto_pdf",
                "path": str(pdf_path),
                "filename": pdf_path.name,
                "mime_type": "application/pdf",
            },
            {
                "field_name": "adjunto_cmd",
                "path": str(cmd_path),
                "filename": cmd_path.name,
                "mime_type": "text/plain",
            },
        ]
    )
    body, content_type = pc._build_multipart_form_data(
        presupuesto_json='{"adjuntos":[{"tipo":"pdf"},{"tipo":"cmd"}]}',
        files=file_parts,
    )

    assert content_type.startswith("multipart/form-data; boundary=")
    text = body.decode("latin-1")
    assert 'name="presupuesto"' in text
    assert '{"adjuntos":[{"tipo":"pdf"},{"tipo":"cmd"}]}' in text
    assert 'name="adjunto_pdf"; filename="cotizacion_001.pdf"' in text
    assert "Content-Type: application/pdf" in text
    assert 'name="adjunto_cmd"; filename="proceso_001.cmd"' in text
    assert "Content-Type: text/plain" in text


def test_validate_required_adjunto_sources_requires_pdf_and_cmd():
    ok_sources = [
        {"field_name": "adjunto_pdf"},
        {"field_name": "adjunto_cmd"},
    ]
    pc._validate_required_adjunto_sources(ok_sources)

    try:
        pc._validate_required_adjunto_sources([{"field_name": "adjunto_pdf"}])
        assert False, "Debio fallar cuando falta adjunto_cmd"
    except pc.PresupuestoApiError as exc:
        assert "adjunto_cmd" in str(exc)


def test_login_and_send_presupuesto_sends_wrapped_presupuesto_payload(monkeypatch):
    from types import SimpleNamespace

    sent: dict[str, object] = {}
    items = [
        {
            "codigo": "SKU-001",
            "producto": "Producto Demo",
            "observacion": "Color ambar",
            "cantidad": 1,
            "precio": 10.0,
        }
    ]

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            assert kwargs.get("json_data", {}).get("password") == "123456"
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_POST_PRESUPUESTO):
            sent.update(kwargs)
            body = kwargs.get("json_data")
            assert isinstance(body, dict)
            assert "presupuesto" in body
            assert isinstance(body.get("presupuesto"), dict)
            return SimpleNamespace(status_code=201, data={"actualizado": False, "message": "ok"}, text='{"ok":true}')
        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setattr(pc, "_load_api_identity", lambda: (1, "user_demo", "PERU", "LA CASA DEL PERFUME", "001"))
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.login_and_send_presupuesto(
        quote_code="PE-001-0000001",
        cliente="Cliente Demo",
        cedula="DNI-12345678",
        telefono="555",
        metodo_pago="EFECTIVO",
        estado="PENDIENTE",
        items_base=items,
        adjuntos=[
            {
                "tipo": "pdf",
                "nombre_archivo": "cotizacion_001.pdf",
                "mime_type": "application/pdf",
                "extension": "pdf",
            },
            {
                "tipo": "cmd",
                "nombre_archivo": "proceso_001.cmd",
                "mime_type": "text/plain",
                "extension": "cmd",
            },
        ],
        adjunto_files=[
            {
                "field_name": "adjunto_pdf",
                "path": "C:/tmp/cotizacion_001.pdf",
                "filename": "cotizacion_001.pdf",
                "mime_type": "application/pdf",
            },
            {
                "field_name": "adjunto_cmd",
                "path": "C:/tmp/proceso_001.cmd",
                "filename": "proceso_001.cmd",
                "mime_type": "text/plain",
            },
        ],
    )

    assert res["post_status"] == 201
    body = sent.get("json_data") or {}
    pres = body.get("presupuesto") or {}
    assert pres.get("id_cotizador") == "001"
    assert pres.get("tipo_documento") == "DNI"
    assert pres.get("estado") == "PENDIENTE"
    assert pres.get("tienda") is False
    assert isinstance(pres.get("presupuesto_prod"), list)
    assert len(pres.get("presupuesto_prod")) == 1
    assert pres["presupuesto_prod"][0].get("observacion") == "Color ambar"


def test_login_and_send_presupuesto_retries_with_flat_payload_when_wrapper_is_not_allowed(monkeypatch):
    from types import SimpleNamespace
    from src.api.generic_controller import ApiRequestError, ApiResponse

    sent_json: list[dict] = []
    items = [
        {
            "codigo": "SKU-001",
            "producto": "Producto Demo",
            "cantidad": 1,
            "precio": 10.0,
        }
    ]

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_POST_PRESUPUESTO):
            body = kwargs.get("json_data")
            sent_json.append(dict(body or {}))

            # Primer intento: wrapper -> backend plano lo rechaza.
            if len(sent_json) == 1:
                resp = ApiResponse(
                    ok=False,
                    status_code=422,
                    method="POST",
                    case=int(pc.API_CASE_POST_PRESUPUESTO),
                    url="http://localhost:3000/service/db/postPresupuesto",
                    elapsed_ms=1,
                    headers={},
                    data={
                        "message": "Datos incompletos o incorrectos.",
                        "details": [
                            '"presupuesto" is not allowed',
                        ],
                    },
                    text='{"details":["\\"presupuesto\\" is not allowed"]}',
                )
                raise ApiRequestError("legacy schema", response=resp)

            # Segundo intento: debe venir plano.
            assert "presupuesto" not in body
            assert body.get("id_cotizador") == "001"
            return SimpleNamespace(status_code=201, data={"actualizado": False, "message": "ok"}, text='{"ok":true}')

        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setattr(pc, "_load_api_identity", lambda: (1, "user_demo", "PERU", "LA CASA DEL PERFUME", "001"))
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.login_and_send_presupuesto(
        quote_code="PE-001-0000001",
        cliente="Cliente Demo",
        cedula="DNI-12345678",
        telefono="555",
        metodo_pago="EFECTIVO",
        items_base=items,
    )

    assert res["post_status"] == 201
    assert len(sent_json) == 2
    assert "presupuesto" in sent_json[0]
    assert "id_cotizador" in sent_json[1]


def test_login_and_send_presupuesto_does_not_call_api_when_items_are_empty(monkeypatch):
    calls: list[int] = []

    def fake_post(case, **kwargs):
        calls.append(int(case))
        raise AssertionError("No debe invocar API cuando no hay items.")

    monkeypatch.setattr(pc, "_load_api_identity", lambda: (1, "user_demo", "PERU", "LA CASA DEL PERFUME", "001"))
    monkeypatch.setattr(pc, "post", fake_post)

    try:
        pc.login_and_send_presupuesto(
            quote_code="PE-001-0000001",
            cliente="Cliente Demo",
            cedula="DNI-12345678",
            telefono="555",
            metodo_pago="EFECTIVO",
            items_base=[],
        )
        assert False, "Debio fallar antes de llamar al API."
    except pc.PresupuestoApiError as exc:
        assert "No hay items para enviar al API" in str(exc)

    assert calls == []


def test_verify_cotizador_signature_returns_blocked_when_api_marks_it(monkeypatch, tmp_path):
    from types import SimpleNamespace

    db_path = str(tmp_path / "verify_blocked.sqlite3")

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_VERIFY_COTIZADOR):
            return SimpleNamespace(
                status_code=200,
                data={
                    "allowed": False,
                    "message": "Bloqueado por administrador",
                    "data": {"estatus": "bloqueado"},
                },
                text='{"allowed":false}',
            )
        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1, "user_demo", "app_demo", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.verify_cotizador_signature_once()

    assert res["status"] == "BLOCKED"
    assert res["blocked"] is True
    assert "Bloqueado por administrador" in res["message"]


def test_verify_cotizador_signature_soft_fails_inside_grace_window(monkeypatch, tmp_path):
    from src.api.generic_controller import ApiRequestError

    db_path = str(tmp_path / "verify_soft_fail.sqlite3")

    def fake_post(case, **kwargs):
        raise ApiRequestError("network down")

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1, "user_demo", "app_demo", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.verify_cotizador_signature_once()

    assert res["status"] == "SOFT_FAIL"
    assert res["blocked"] is False
    assert res["allowed"] is True


def test_verify_cotizador_signature_hard_fails_after_three_days(monkeypatch, tmp_path):
    import datetime

    from sqlModels.db import connect, ensure_schema, tx
    from sqlModels.settings_repo import set_setting
    from src.api.generic_controller import ApiRequestError

    db_path = str(tmp_path / "verify_hard_fail.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        set_setting(con, pc._COTIZADOR_PID_KEY, "pid-demo")
        set_setting(
            con,
            pc._VERIFICATION_GRACE_STARTED_KEY,
            (datetime.datetime.now() - datetime.timedelta(days=4)).isoformat(timespec="seconds"),
        )
    con.close()

    def fake_post(case, **kwargs):
        raise ApiRequestError("network down")

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1, "user_demo", "app_demo", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.verify_cotizador_signature_once()

    assert res["status"] == "HARD_FAIL"
    assert res["blocked"] is True
    assert "3 dias" in res["message"]


def test_sync_pending_history_quotes_once_ignores_quotes_without_items(tmp_path, monkeypatch):
    from sqlModels.db import connect, ensure_schema, tx
    from sqlModels.settings_repo import set_setting

    db_path = str(tmp_path / "sync_pending.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        set_setting(con, "store_id", "001")
        set_setting(con, "username", "demo_user")

        cur_without = con.execute(
            """
            INSERT INTO quotes(country_code, quote_no, created_at, currency_shown, pdf_path)
            VALUES('PE', 'PE-001-0000001', '2026-02-25T10:00:00', 'PEN', 'sin_items.pdf')
            """
        )
        quote_without_items = int(cur_without.lastrowid)

        cur_with = con.execute(
            """
            INSERT INTO quotes(country_code, quote_no, created_at, currency_shown, pdf_path)
            VALUES('PE', 'PE-001-0000002', '2026-02-25T10:05:00', 'PEN', 'con_items.pdf')
            """
        )
        quote_with_items = int(cur_with.lastrowid)

        con.execute(
            """
            INSERT INTO quote_items(
                quote_id, codigo, producto, cantidad,
                precio_base, subtotal_base, total_base,
                precio_shown, subtotal_shown, total_shown
            )
            VALUES(?, 'SKU-001', 'Producto Demo', 1, 10, 10, 10, 10, 10, 10)
            """,
            (quote_with_items,),
        )
    con.close()

    processed_ids: list[int] = []

    def fake_send(*, quote_id: int, force: bool = False, login_password=None):
        processed_ids.append(int(quote_id))
        return {"status": "SENT"}

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(pc, "send_quote_from_history_once", fake_send)
    monkeypatch.setitem(pc.APP_CONFIG, "store_id", "001")
    monkeypatch.setitem(pc.APP_CONFIG, "username", "demo_user")

    res = pc.sync_pending_history_quotes_once(limit=50)

    assert quote_without_items != quote_with_items
    assert processed_ids == [quote_with_items]
    assert int(res.get("found") or 0) == 1
    assert int(res.get("sent") or 0) == 1


def test_record_and_send_label_print_log_groups_items_and_marks_local_sent(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from sqlModels.db import connect, ensure_schema

    db_path = str(tmp_path / "label_logs.sqlite3")
    sent_payloads: list[dict] = []

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_POST_LABEL_PRINT_LOG):
            sent_payloads.append(kwargs["json_data"]["registro_etiquetas"])
            return SimpleNamespace(
                status_code=201,
                data={"data": {"id": 7}, "message": "ok"},
                text='{"ok":true}',
            )
        raise AssertionError(f"case inesperado: {case}")

    labels = [
        {"codigo": "dd001", "nombre": "LCDP DD212MUJER", "gramos": "50g", "copias": 1},
        {"codigo": "DD001", "nombre": "LCDP DD212MUJER", "gramos": "50g", "copias": 2},
    ]

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1003, "cotizador_pe", "samuel", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.record_and_send_label_print_log(quote_code="PE-001-0000001", labels=labels)

    assert res["status"] == "SENT"
    assert len(sent_payloads) == 1
    payload = sent_payloads[0]
    assert payload["codigo"] == "PE-001-0000001"
    assert payload["user"] == "samuel"
    assert payload["cod_pais"] == "PE"
    assert payload["total_etiquetas"] == 3
    assert payload["total_etiquetas_solicitadas"] == 3
    assert payload["total_etiquetas_impresas"] == 3
    assert payload["etiquetas"] == [
        {
            "codigo": "DD001",
            "nombre": "LCDP DD212MUJER",
            "gramos": "50g",
            "cantidad": 3,
        }
    ]

    con = connect(db_path)
    ensure_schema(con)
    try:
        row = con.execute(
            """
            SELECT
                quote_code,
                user,
                api_username,
                cod_pais,
                total_labels,
                total_labels_requested,
                total_labels_printed,
                api_sent_at,
                api_error_at
            FROM label_print_logs
            WHERE event_id = ?
            """,
            (res["event_id"],),
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert row["quote_code"] == "PE-001-0000001"
    assert row["user"] == "samuel"
    assert row["api_username"] == "cotizador_pe"
    assert row["cod_pais"] == "PE"
    assert row["total_labels"] == 3
    assert row["total_labels_requested"] == 3
    assert row["total_labels_printed"] == 3
    assert row["api_sent_at"]
    assert not row["api_error_at"]


def test_build_label_print_log_payload_can_report_requested_and_printed_totals(monkeypatch):
    labels = [{"codigo": "DD001", "nombre": "LCDP DD212MUJER", "gramos": "50g", "copias": 25}]

    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1003, "cotizador_pe", "samuel", "PERU", "LA CASA DEL PERFUME", "001", False),
    )

    payload = pc.build_label_print_log_payload(
        quote_code="PE-001-0000001",
        labels=labels,
        requested_labels=25,
        printed_labels=26,
        printer_counter_before=100,
        printer_counter_after=113,
        printer_counter_delta=13,
        printer_status="ready",
        printer_ip="192.168.1.50",
        printer_port=9100,
    )

    assert payload["total_etiquetas"] == 25
    assert payload["total_etiquetas_solicitadas"] == 25
    assert payload["total_etiquetas_impresas"] == 26
    assert payload["printer_counter_before"] == 100
    assert payload["printer_counter_after"] == 113
    assert payload["printer_counter_delta"] == 13
    assert payload["printer_status"] == "ready"
    assert payload["printer_ip"] == "192.168.1.50"
    assert payload["printer_port"] == 9100
    assert payload["printer_event_key"]

    same_physical_print = pc.build_label_print_log_payload(
        quote_code="PE-001-0000001",
        labels=labels,
        event_id="otro-event-id",
        requested_labels=25,
        printed_labels=26,
        printer_counter_before=100,
        printer_counter_after=113,
        printer_counter_delta=13,
        printer_ip="192.168.1.50",
        printer_port=9100,
    )
    assert same_physical_print["printer_event_key"] == payload["printer_event_key"]


def test_record_and_send_label_print_log_sends_confirmed_printer_payload(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from sqlModels.db import connect, ensure_schema

    db_path = str(tmp_path / "label_confirmed_send.sqlite3")
    sent_payloads: list[dict] = []

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_POST_LABEL_PRINT_LOG):
            sent_payloads.append(kwargs["json_data"]["registro_etiquetas"])
            return SimpleNamespace(
                status_code=201,
                data={"data": {"id": 22}, "message": "ok"},
                text='{"ok":true}',
            )
        raise AssertionError(f"case inesperado: {case}")

    labels = [{"codigo": "DD001", "nombre": "LCDP DD212MUJER", "gramos": "50g", "copias": 25}]

    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1003, "cotizador_pe", "samuel", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    res = pc.record_and_send_label_print_log(
        quote_code="PE-001-0000001",
        labels=labels,
        requested_labels=25,
        printed_labels=26,
        printer_counter_before=100,
        printer_counter_after=113,
        printer_counter_delta=13,
        printer_status="ready",
        printer_ip="192.168.1.50",
        printer_port=9100,
    )

    assert res["status"] == "SENT"
    assert len(sent_payloads) == 1
    payload = sent_payloads[0]
    assert payload["total_etiquetas_solicitadas"] == 25
    assert payload["total_etiquetas_impresas"] == 26
    assert payload["printer_counter_before"] == 100
    assert payload["printer_counter_after"] == 113
    assert payload["printer_counter_delta"] == 13
    assert payload["printer_ip"] == "192.168.1.50"
    assert payload["printer_port"] == 9100
    assert payload["printer_event_key"]

    con = connect(db_path)
    ensure_schema(con)
    try:
        row = con.execute(
            """
            SELECT
                total_labels_requested,
                total_labels_printed,
                printer_counter_before,
                printer_counter_after,
                printer_event_key,
                api_sent_at
            FROM label_print_logs
            WHERE event_id = ?
            """,
            (res["event_id"],),
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert row["total_labels_requested"] == 25
    assert row["total_labels_printed"] == 26
    assert row["printer_counter_before"] == 100
    assert row["printer_counter_after"] == 113
    assert row["printer_event_key"] == payload["printer_event_key"]
    assert row["api_sent_at"]


def test_insert_label_print_log_dedupes_same_printer_event_key(tmp_path):
    from sqlModels.db import connect, ensure_schema, tx
    from sqlModels.label_print_logs_repo import insert_label_print_log

    db_path = str(tmp_path / "label_event_key.sqlite3")
    payload = {
        "event_id": "event-1",
        "quote_code": "PE-001-0000001",
        "printed_at": "2026-05-21T10:00:00",
        "user": "samuel",
        "api_username": "cotizador_pe",
        "id_user_api": 1003,
        "cod_pais": "PE",
        "id_cotizador": "001",
        "empresa": "LA CASA DEL PERFUME",
        "total_etiquetas_solicitadas": 25,
        "total_etiquetas_impresas": 26,
        "etiquetas": [{"codigo": "DD001", "nombre": "Demo", "gramos": "50g", "cantidad": 25}],
        "printer_event_key": "same-printer-event",
    }

    con = connect(db_path)
    ensure_schema(con)
    try:
        with tx(con):
            first_id = insert_label_print_log(con, payload)
            second_id = insert_label_print_log(con, {**payload, "event_id": "event-2"})
        count = con.execute("SELECT COUNT(*) AS n FROM label_print_logs").fetchone()["n"]
    finally:
        con.close()

    assert first_id == second_id
    assert count == 1


def test_sync_pending_label_print_logs_once_retries_failed_record(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from sqlModels.db import connect, ensure_schema, tx
    from sqlModels.settings_repo import set_setting

    db_path = str(tmp_path / "label_retry.sqlite3")
    con = connect(db_path)
    ensure_schema(con)
    with tx(con):
        set_setting(con, "store_id", "001")
        set_setting(con, "username", "samuel")
    con.close()

    state = {"fail_first_label_post": True}
    sent_payloads: list[dict] = []

    def fake_post(case, **kwargs):
        if int(case) == int(pc.API_CASE_LOGIN):
            return SimpleNamespace(status_code=201, data={"access_token": "tok_123"}, text="")
        if int(case) == int(pc.API_CASE_POST_LABEL_PRINT_LOG):
            sent_payloads.append(kwargs["json_data"]["registro_etiquetas"])
            if state["fail_first_label_post"]:
                state["fail_first_label_post"] = False
                raise RuntimeError("timed out")
            return SimpleNamespace(
                status_code=201,
                data={"data": {"id": 9}, "message": "ok"},
                text='{"ok":true}',
            )
        raise AssertionError(f"case inesperado: {case}")

    monkeypatch.setitem(pc.APP_CONFIG, "store_id", "001")
    monkeypatch.setitem(pc.APP_CONFIG, "username", "samuel")
    monkeypatch.setattr(pc, "resolve_db_path", lambda: db_path)
    monkeypatch.setattr(
        pc,
        "_load_api_identity",
        lambda: (1003, "cotizador_pe", "samuel", "PERU", "LA CASA DEL PERFUME", "001", False),
    )
    monkeypatch.setattr(pc, "post", fake_post)

    first = pc.record_and_send_label_print_log(
        quote_code="PE-001-0000001",
        labels=[{"codigo": "DD001", "nombre": "LCDP DD212MUJER", "gramos": "50g", "copias": 1}],
    )

    assert first["status"] == "SENT_ERROR"
    assert len(sent_payloads) == 1

    retry = pc.sync_pending_label_print_logs_once(limit=10)

    assert retry == {"found": 1, "sent": 1, "failed": 0}
    assert len(sent_payloads) == 2
    assert sent_payloads[0]["event_id"] == sent_payloads[1]["event_id"]

    con = connect(db_path)
    ensure_schema(con)
    try:
        row = con.execute(
            """
            SELECT api_sent_at, api_error_at, api_error_message
            FROM label_print_logs
            WHERE event_id = ?
            """,
            (first["event_id"],),
        ).fetchone()
    finally:
        con.close()

    assert row is not None
    assert row["api_sent_at"]
    assert not row["api_error_at"]
    assert not row["api_error_message"]
