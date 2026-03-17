from __future__ import annotations

import datetime
import os
import re
import threading
from typing import Any

from sqlModels.api_identity import (
    API_LOGIN_PASSWORD,
    is_scrypt_hash,
    resolve_api_identity,
    verify_password_scrypt,
)
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.quotes_repo import (
    get_quote_header,
    get_quote_items,
    infer_tipo_documento_from_doc,
)
from sqlModels.settings_repo import get_setting

from ..db_path import resolve_db_path
from ..config import APP_CONFIG, CATS
from ..logging_setup import get_logger
from ..paths import resolve_pdf_path_portable
from ..utils import nz
from .cases import API_CASE_LOGIN, API_CASE_POST_PRESUPUESTO
from .controller import post
from .generic_controller import ApiRequestError

log = get_logger(__name__)

_API_QUOTE_CODE_RE = re.compile(r"^[A-Z0-9]+-\d{7,}$")
_CATS_UPPER = {str(x).strip().upper() for x in (CATS or []) if str(x).strip()}
_SCHEMA_READY = False
_SCHEMA_LOCK = threading.Lock()


class PresupuestoApiError(RuntimeError):
    pass


def _ensure_schema_once(con) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        ensure_schema(con)
        _SCHEMA_READY = True


def _has_column(con, table: str, col: str) -> bool:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return col.lower() in cols
    except Exception:
        return False


def _parse_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None

    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("1", "true", "yes", "on", "si"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return None


def _normalize_tipo_prod(value: Any) -> str:
    t = str(value or "").strip().lower()
    if t in ("serv", "servicio", "service"):
        return "serv"
    if t in ("pres", "presentacion", "presentation"):
        return "pres"
    if t in ("prod", "producto", "product"):
        return "prod"
    return ""


def _tipo_prod_from_item(item: dict) -> str:
    t = _normalize_tipo_prod(item.get("tipo_prod"))
    if t:
        return t
    cat = str(item.get("categoria") or "").strip().upper()
    if cat == "SERVICIO":
        return "serv"
    if cat == "PRESENTACION":
        return "pres"
    return "prod"


def _price_id_from_item(item: dict, tipo_prod: str) -> int:
    try:
        pid = int(item.get("id_precioventa") or 0)
    except Exception:
        pid = 0
    if tipo_prod == "serv":
        return 4
    if pid in (1, 2, 3):
        return pid
    tier = str(item.get("precio_tier") or "").strip().lower()
    if tier == "minimo":
        return 2
    if tier == "oferta":
        return 3
    return 1


def _extract_id_cotizador(quote_code: str, store_id: str) -> str:
    sid = str(store_id or "").strip().upper()
    if sid:
        return sid
    qc = str(quote_code or "").strip().upper()
    if qc.startswith("C-"):
        qc = qc[2:]
    m = re.match(r"^[A-Z]{2}-([A-Z0-9]+)-\d+$", qc)
    if m:
        return str(m.group(1) or "001")
    m2 = re.match(r"^([A-Z0-9]+)-\d+$", qc)
    if m2:
        return str(m2.group(1) or "001")
    return "001"


def _normalize_quote_code_for_api(raw_quote_code: Any, *, store_id: str = "") -> str:
    code = str(raw_quote_code or "").strip().upper()
    if code.startswith("C-"):
        code = code[2:].strip()

    # Soporta codigos historicos y normaliza SIEMPRE a STORE-########.
    m_new = re.match(r"^[A-Z]{2}-[A-Z0-9]+-(\d+)$", code)
    if m_new:
        digits = str(m_new.group(1) or "")
    else:
        m_store = re.match(r"^[A-Z0-9]+-(\d+)$", code)
        if m_store:
            digits = str(m_store.group(1) or "")
        else:
            m_legacy = re.match(r"^[A-Z]{2}-(\d+)$", code)
            if m_legacy:
                digits = str(m_legacy.group(1) or "")
            else:
                groups = re.findall(r"\d+", code)
                digits = str(groups[-1] or "") if groups else ""

    if not digits:
        return ""

    try:
        digits = str(int(digits)).zfill(7)
    except Exception:
        return ""

    sid = _extract_id_cotizador(code, store_id)
    out = f"{sid}-{digits}"
    if not _API_QUOTE_CODE_RE.match(out):
        return ""
    return out


def _quantity_for_api(item: dict, *, cod_pais: str) -> int | float:
    try:
        qty = float(nz(item.get("cantidad"), 0.0))
    except Exception:
        qty = 0.0

    country = str(cod_pais or "").strip().upper()
    cat = str(item.get("categoria") or "").strip().upper()

    # Paraguay: para categorías CATS, el API espera gramos enteros.
    if country == "PY" and cat in _CATS_UPPER:
        grams = int(round(qty * 50.0))
        if qty > 0 and grams <= 0:
            return 1
        return max(0, grams)

    return qty


def _build_presupuesto_items(items_base: list[dict], *, cod_pais: str) -> list[dict]:
    out: list[dict] = []
    for it in (items_base or []):
        tipo_prod = _tipo_prod_from_item(it)
        nombre = str(it.get("nombre") or it.get("producto") or "").strip()
        observacion = str(it.get("observacion") or "").strip()
        if not nombre:
            nombre = str(it.get("codigo") or "").strip()
        if not nombre:
            nombre = "ITEM"
        out.append(
            {
                "codigo": str(it.get("codigo") or ""),
                "nombre": nombre,
                "prc_descuento": float(nz(it.get("descuento_pct"), 0.0)),
                "monto_descuento": float(nz(it.get("descuento_monto"), 0.0)),
                "monto_unitario": float(nz(it.get("precio"), 0.0)),
                "cantidad": _quantity_for_api(it, cod_pais=cod_pais),
                "id_precioventa": int(_price_id_from_item(it, tipo_prod)),
                "tipo_prod": tipo_prod,
                "observacion": observacion,
            }
        )
    return out


def _extract_access_token(payload: Any) -> str:
    if isinstance(payload, str):
        txt = payload.strip()
        if txt:
            return txt
        return ""
    if isinstance(payload, dict):
        for k in ("access_token", "accessToken", "token", "jwt", "bearer", "authToken"):
            v = payload.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for k in ("data", "result", "payload"):
            nested = payload.get(k)
            tok = _extract_access_token(nested)
            if tok:
                return tok
    return ""


def _extract_bool_flag(payload: Any, *, key: str) -> bool | None:
    if isinstance(payload, dict):
        v = payload.get(key)
        if isinstance(v, bool):
            return v
        for nested_key in ("data", "result", "payload"):
            nested = payload.get(nested_key)
            b = _extract_bool_flag(nested, key=key)
            if b is not None:
                return b
    return None


def _extract_message(payload: Any) -> str:
    if isinstance(payload, dict):
        v = payload.get("message")
        if isinstance(v, str) and v.strip():
            return v.strip()
        for nested_key in ("data", "result", "payload"):
            nested = payload.get(nested_key)
            m = _extract_message(nested)
            if m:
                return m
    return ""


def _api_requires_wrapped_presupuesto_payload(err: ApiRequestError) -> bool:
    """
    Detecta el contrato legacy del backend:
      - espera {"presupuesto": {...}}
      - rechaza campos directos (id_cotizador, user, codigo, etc.)
    """
    resp = getattr(err, "response", None)
    if resp is None:
        return False

    chunks: list[str] = []
    try:
        txt = str(getattr(resp, "text", "") or "").strip()
        if txt:
            chunks.append(txt.lower())
    except Exception:
        pass

    try:
        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            msg = str(data.get("message") or "").strip()
            if msg:
                chunks.append(msg.lower())
            details = data.get("details")
            if isinstance(details, list):
                for d in details:
                    dt = str(d or "").strip()
                    if dt:
                        chunks.append(dt.lower())
    except Exception:
        pass

    blob = " | ".join(chunks)
    if not blob:
        return False

    asks_presupuesto = ('"presupuesto" is required' in blob) or ("'presupuesto' is required" in blob)
    rejects_flat = ("id_cotizador" in blob and "not allowed" in blob) or ("user" in blob and "not allowed" in blob)
    return bool(asks_presupuesto and rejects_flat)


def _api_rejects_wrapped_presupuesto_payload(err: ApiRequestError) -> bool:
    """
    Detecta APIs que esperan payload plano y rechazan el wrapper {"presupuesto": {...}}.
    """
    resp = getattr(err, "response", None)
    if resp is None:
        return False

    chunks: list[str] = []
    try:
        txt = str(getattr(resp, "text", "") or "").strip()
        if txt:
            chunks.append(txt.lower())
    except Exception:
        pass

    try:
        data = getattr(resp, "data", None)
        if isinstance(data, dict):
            msg = str(data.get("message") or "").strip()
            if msg:
                chunks.append(msg.lower())
            details = data.get("details")
            if isinstance(details, list):
                for d in details:
                    dt = str(d or "").strip()
                    if dt:
                        chunks.append(dt.lower())
    except Exception:
        pass

    blob = " | ".join(chunks)
    if not blob:
        return False

    return (
        ('"presupuesto" is not allowed' in blob)
        or ("'presupuesto' is not allowed" in blob)
        or ("presupuesto is not allowed" in blob)
    )


def _load_api_identity() -> tuple[int, str, str, str, str, str, bool]:
    db_path = resolve_db_path()
    con = connect(db_path)
    _ensure_schema_once(con)
    try:
        country = get_setting(con, "country", "PARAGUAY")
        company = get_setting(con, "company_type", "LA CASA DEL PERFUME")
        store_id = get_setting(con, "store_id", "").strip().upper()
        tienda_raw = get_setting(con, "tienda", None)
        default_id, default_user = resolve_api_identity(country, company)

        id_raw = get_setting(con, "id_user_api", str(default_id)).strip()
        user_raw = get_setting(con, "user_api", default_user).strip()
        app_user_raw = get_setting(con, "username", "").strip()
        pass_hash = get_setting(con, "password_api_hash", "").strip()
    finally:
        con.close()

    try:
        user_id = int(id_raw)
    except Exception:
        user_id = int(default_id)
    api_username = user_raw or default_user
    app_username = app_user_raw or api_username
    tienda_cfg = _parse_optional_bool(tienda_raw)
    if tienda_cfg is None:
        tienda_cfg = _parse_optional_bool(APP_CONFIG.get("tienda"))

    if pass_hash:
        expected_secret = str(API_LOGIN_PASSWORD or "").strip()
        mismatch = False
        if is_scrypt_hash(expected_secret):
            mismatch = (pass_hash != expected_secret)
        else:
            mismatch = not verify_password_scrypt(expected_secret, pass_hash)
        if mismatch:
            log.warning("password_api_hash no coincide con la clave API esperada.")

    return (
        int(user_id),
        str(api_username),
        str(app_username),
        str(country or ""),
        str(company or ""),
        str(store_id or ""),
        bool(tienda_cfg),
    )


def _country_code_from_country(country: str) -> str:
    c = str(country or "").strip().upper()
    if c in ("PERU", "PE"):
        return "PE"
    if c in ("VENEZUELA", "VE"):
        return "VE"
    return "PY"


def _infer_tipo_documento_for_api(doc_cliente: str, cod_pais: str) -> str:
    return infer_tipo_documento_from_doc(
        str(cod_pais or "").strip().upper(),
        str(doc_cliente or ""),
    )


def _build_adjunto_entry(path: str) -> dict[str, str]:
    filename = os.path.basename(path)
    ext = os.path.splitext(filename)[1].lstrip(".").strip().lower()

    mime_map = {
        "pdf": "application/pdf",
        "cmd": "text/plain",
    }
    mime = mime_map.get(ext) or "application/octet-stream"
    tipo = ext or "bin"

    return {
        "tipo": tipo,
        "nombre_archivo": filename,
        "mime_type": mime,
        "extension": ext or "bin",
    }


def _adjunto_field_name_from_extension(ext: str) -> str:
    e = str(ext or "").strip().lower()
    if e == "pdf":
        return "adjunto_pdf"
    if e == "cmd":
        return "adjunto_cmd"
    return f"adjunto_{e or 'bin'}"


def _build_adjunto_source(path: str) -> dict[str, Any]:
    meta = _build_adjunto_entry(path)
    return {
        "field_name": _adjunto_field_name_from_extension(meta.get("extension", "")),
        "path": str(path or ""),
        "meta": meta,
    }


def _ticket_cmd_path_from_pdf(pdf_path: str) -> str:
    if not pdf_path:
        return ""
    abs_pdf = os.path.abspath(pdf_path)
    pdf_folder = os.path.dirname(abs_pdf)
    base = os.path.splitext(os.path.basename(abs_pdf))[0]
    if not base:
        return ""
    return os.path.join(pdf_folder, "tickets", f"{base}.IMPRIMIR_TICKET.cmd")


def _build_adjunto_sources_for_quote(header: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    pdf_path = resolve_pdf_path_portable(header.get("pdf_path"))
    if pdf_path:
        abs_pdf = os.path.abspath(pdf_path)
        if os.path.exists(abs_pdf):
            seen.add(abs_pdf.lower())
            out.append(_build_adjunto_source(abs_pdf))

        cmd_path = _ticket_cmd_path_from_pdf(abs_pdf)
        if cmd_path:
            abs_cmd = os.path.abspath(cmd_path)
            key = abs_cmd.lower()
            if os.path.exists(abs_cmd) and key not in seen:
                seen.add(key)
                out.append(_build_adjunto_source(abs_cmd))

    return out


def _build_adjuntos_for_quote(header: dict[str, Any]) -> list[dict[str, str]]:
    return [dict(src.get("meta") or {}) for src in _build_adjunto_sources_for_quote(header)]


def _validate_required_adjunto_sources(sources: list[dict[str, Any]]) -> None:
    fields = {str((x or {}).get("field_name") or "").strip().lower() for x in (sources or [])}
    missing: list[str] = []
    if "adjunto_pdf" not in fields:
        missing.append("adjunto_pdf (.pdf)")
    if "adjunto_cmd" not in fields:
        missing.append("adjunto_cmd (.cmd)")
    if missing:
        raise PresupuestoApiError(
            "Faltan archivos requeridos para envio multipart: " + ", ".join(missing)
        )


def _multipart_boundary() -> str:
    return f"----CotizadorBoundary{os.urandom(12).hex()}"


def _header_param_safe(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\"", "'")


def _read_adjunto_file_parts(adjunto_files: list[dict[str, str]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for entry in (adjunto_files or []):
        path = os.path.abspath(str((entry or {}).get("path") or "").strip())
        if not path:
            continue
        if not os.path.exists(path):
            log.warning("Adjunto no existe y se omitira del multipart: %s", path)
            continue

        filename = str((entry or {}).get("filename") or os.path.basename(path))
        field_name = str((entry or {}).get("field_name") or "").strip() or "adjunto_bin"
        mime_type = str((entry or {}).get("mime_type") or "application/octet-stream").strip()

        try:
            with open(path, "rb") as f:
                content = f.read()
        except Exception as exc:
            log.warning("No se pudo leer adjunto %s: %s", path, exc)
            continue

        out.append(
            {
                "field_name": field_name,
                "filename": filename,
                "mime_type": mime_type,
                "content": content,
            }
        )
    return out


def _build_multipart_form_data(
    *,
    presupuesto_json: str,
    files: list[dict[str, Any]],
) -> tuple[bytes, str]:
    boundary = _multipart_boundary()
    b = bytearray()
    br = "\r\n"

    b.extend(f"--{boundary}{br}".encode("utf-8"))
    b.extend(f'Content-Disposition: form-data; name="presupuesto"{br}'.encode("utf-8"))
    b.extend(f"Content-Type: application/json; charset=utf-8{br}{br}".encode("utf-8"))
    b.extend(str(presupuesto_json or "").encode("utf-8"))
    b.extend(br.encode("utf-8"))

    for fpart in (files or []):
        field_name = _header_param_safe(str(fpart.get("field_name") or "adjunto_bin"))
        filename = _header_param_safe(str(fpart.get("filename") or "archivo.bin"))
        mime_type = str(fpart.get("mime_type") or "application/octet-stream").strip()
        content = fpart.get("content")
        if not isinstance(content, (bytes, bytearray)):
            content = bytes(str(content or ""), encoding="utf-8")

        b.extend(f"--{boundary}{br}".encode("utf-8"))
        b.extend(
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"{br}'
            ).encode("utf-8")
        )
        b.extend(f"Content-Type: {mime_type}{br}{br}".encode("utf-8"))
        b.extend(bytes(content))
        b.extend(br.encode("utf-8"))

    b.extend(f"--{boundary}--{br}".encode("utf-8"))
    content_type = f"multipart/form-data; boundary={boundary}"
    return bytes(b), content_type


def build_presupuesto_payload(
    *,
    quote_code: str,
    fecha_emision_ts: Any = None,
    cliente: str,
    cedula: str,
    telefono: str,
    metodo_pago: str,
    direccion: str = "-",
    email: str = "-",
    estado: str = "",
    tipo_documento: str = "",
    cod_pais: str,
    empresa: str,
    id_cotizador: str,
    items_base: list[dict],
    app_username: str = "",
    user_api: str = "",
    tienda: bool = False,
    adjuntos: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    presupuesto_items = _build_presupuesto_items(items_base, cod_pais=cod_pais)
    user_for_payload = str(app_username or "").strip() or str(user_api or "").strip()
    fecha_emision_num = _normalize_issue_timestamp(fecha_emision_ts)
    return {
        "presupuesto": {
            "id_cotizador": str(id_cotizador or ""),
            "user": user_for_payload,
            "codigo": str(quote_code or ""),
            "fecha_emision": int(fecha_emision_num),
            "nombre_cliente": str(cliente or ""),
            "doc_cliente": str(cedula or ""),
            "tipo_documento": str(tipo_documento or "").strip().upper(),
            "tlf_cliente": str(telefono or ""),
            "direccion_cliente": str(direccion or ""),
            "email_cliente": str(email or ""),
            "pago": (str(metodo_pago or "").strip() or None),
            "estado": (str(estado or "").strip() or None),
            "cod_pais": str(cod_pais or ""),
            "empresa": str(empresa or ""),
            "tienda": bool(tienda),
            "cantidad_items": int(len(presupuesto_items)),
            "presupuesto_prod": presupuesto_items,
        },
        "adjuntos": list(adjuntos or []),
    }


def login_and_send_presupuesto(
    *,
    quote_code: str,
    fecha_emision_ts: Any = None,
    cliente: str,
    cedula: str,
    telefono: str,
    metodo_pago: str,
    direccion: str = "-",
    email: str = "-",
    estado: str = "",
    tipo_documento: str = "",
    items_base: list[dict],
    adjuntos: list[dict[str, str]] | None = None,
    adjunto_files: list[dict[str, str]] | None = None,
    login_password: str | None = None,
) -> dict[str, Any]:
    identity = _load_api_identity()
    tienda = False
    if len(identity) >= 7:
        user_id, api_username, app_username, country, company_type, store_id, tienda = identity[:7]
    elif len(identity) >= 6:
        user_id, api_username, app_username, country, company_type, store_id = identity[:6]
    else:
        # Compatibilidad con versiones/mocks antiguos de _load_api_identity.
        user_id, api_username, country, company_type, store_id = identity  # type: ignore[misc]
        app_username = str(api_username or "")

    cod_pais = _country_code_from_country(country)
    tipo_documento_norm = str(tipo_documento or "").strip().upper()
    if not tipo_documento_norm:
        tipo_documento_norm = _infer_tipo_documento_for_api(cedula, cod_pais)

    payload = build_presupuesto_payload(
        quote_code=quote_code,
        fecha_emision_ts=fecha_emision_ts,
        cliente=cliente,
        cedula=cedula,
        telefono=telefono,
        direccion=direccion,
        email=email,
        metodo_pago=metodo_pago,
        estado=estado,
        tipo_documento=tipo_documento_norm,
        cod_pais=cod_pais,
        empresa=(str(company_type or "").strip() or "LA CASA DEL PERFUME"),
        app_username=app_username,
        user_api=api_username,
        tienda=bool(tienda),
        id_cotizador=_extract_id_cotizador(quote_code, store_id),
        items_base=items_base,
        adjuntos=adjuntos,
    )

    # Contrato principal: enviar solo el objeto "presupuesto" (plano).
    # Compatibilidad: algunos backends legacy exigen {"presupuesto": {...}}.
    presupuesto_part = payload.get("presupuesto")
    if not isinstance(presupuesto_part, dict):
        raise PresupuestoApiError("Payload de presupuesto invalido para JSON.")
    presupuesto_items = presupuesto_part.get("presupuesto_prod")
    if (not isinstance(presupuesto_items, list)) or (not presupuesto_items):
        raise PresupuestoApiError("No hay items para enviar al API.")

    plain_password = str(login_password or API_LOGIN_PASSWORD or "").strip()
    login_payload = {
        "id": int(user_id),
        "username": str(api_username),
        "password": plain_password,
    }

    try:
        login_resp = post(
            API_CASE_LOGIN,
            json_data=login_payload,
            expected_status=(200, 201, 202),
            timeout=12,
            raise_for_status=True,
        )
    except ApiRequestError as e:
        detail = ""
        if getattr(e, "response", None) is not None:
            detail = str(getattr(e.response, "text", "") or "").strip()
        raise PresupuestoApiError(f"Login API fallido. {detail}".strip()) from e

    token = _extract_access_token(login_resp.data)
    if not token:
        token = _extract_access_token(login_resp.text)
    if not token:
        raise PresupuestoApiError("No se pudo obtener access token desde busLogin.")

    headers = {
        "Authorization": f"Bearer {token}",
    }

    def _post_presupuesto(json_body: dict[str, Any]) -> Any:
        return post(
            API_CASE_POST_PRESUPUESTO,
            json_data=json_body,
            headers=headers,
            expected_status=(200, 201, 202),
            timeout=15,
            raise_for_status=True,
        )

    wrapped_payload = {"presupuesto": presupuesto_part}
    try:
        # 1) Prioridad: wrapper {"presupuesto": {...}}
        post_resp = _post_presupuesto(wrapped_payload)
    except ApiRequestError as e_wrapped:
        should_fallback_flat = _api_rejects_wrapped_presupuesto_payload(e_wrapped)
        if not should_fallback_flat:
            # Compat adicional: si el backend pide wrapper, no tiene sentido fallback plano.
            if _api_requires_wrapped_presupuesto_payload(e_wrapped):
                detail_w = ""
                if getattr(e_wrapped, "response", None) is not None:
                    detail_w = str(getattr(e_wrapped.response, "text", "") or "").strip()
                raise PresupuestoApiError(f"Envio de presupuesto fallido. {detail_w}".strip()) from e_wrapped
            should_fallback_flat = False

        if should_fallback_flat:
            try:
                log.info("API con payload plano detectada: reintentando postPresupuesto sin wrapper.")
                post_resp = _post_presupuesto(presupuesto_part)
            except ApiRequestError as e_flat:
                detail_f = ""
                if getattr(e_flat, "response", None) is not None:
                    detail_f = str(getattr(e_flat.response, "text", "") or "").strip()
                raise PresupuestoApiError(f"Envio de presupuesto fallido. {detail_f}".strip()) from e_flat
        else:
            detail = ""
            if getattr(e_wrapped, "response", None) is not None:
                detail = str(getattr(e_wrapped.response, "text", "") or "").strip()
            raise PresupuestoApiError(f"Envio de presupuesto fallido. {detail}".strip()) from e_wrapped

    raw_response = post_resp.data if post_resp.data is not None else post_resp.text
    actualizado = _extract_bool_flag(post_resp.data, key="actualizado")
    if actualizado is True:
        api_action = "UPDATED"
    elif actualizado is False:
        api_action = "CREATED"
    elif int(post_resp.status_code) == 201:
        api_action = "CREATED"
    elif int(post_resp.status_code) == 200:
        api_action = "UPDATED"
    else:
        api_action = "UNKNOWN"

    return {
        "login_status": int(login_resp.status_code),
        "post_status": int(post_resp.status_code),
        "token_prefix": str(token)[:12],
        "response": raw_response,
        "api_actualizado": actualizado,
        "api_action": api_action,
        "api_message": _extract_message(post_resp.data),
        "payload": payload,
    }


def _now_iso_local() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _normalize_error_message(value: Any, *, max_len: int = 1800) -> str:
    msg = str(value or "").strip()
    if not msg:
        return ""
    if len(msg) <= max_len:
        return msg
    return msg[: max(64, int(max_len) - 3)].rstrip() + "..."


def _normalize_issue_timestamp(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return int(datetime.datetime.now().timestamp() * 1000)

    if re.fullmatch(r"\d+(\.\d+)?", raw):
        try:
            n = float(raw)
            if n <= 0:
                return int(datetime.datetime.now().timestamp() * 1000)
            # Heuristica: segundos vs milisegundos.
            if n < 10_000_000_000:
                return int(round(n * 1000.0))
            return int(round(n))
        except Exception:
            pass

    s = raw
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(s)
        return int(dt.timestamp() * 1000)
    except Exception:
        pass

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.datetime.strptime(raw, fmt)
            return int(dt.timestamp() * 1000)
        except Exception:
            continue

    if len(raw) >= 19:
        core = raw[:19].replace(" ", "T")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", core):
            try:
                dt = datetime.datetime.fromisoformat(core)
                return int(dt.timestamp() * 1000)
            except Exception:
                pass

    return int(datetime.datetime.now().timestamp() * 1000)


def _mark_quote_api_sent(quote_id: int, *, sent_at_iso: str) -> None:
    db_path = resolve_db_path()
    con = connect(db_path)
    _ensure_schema_once(con)
    try:
        sets: list[str] = []
        params: list[Any] = []
        if _has_column(con, "quotes", "api_sent_at"):
            sets.append("api_sent_at = ?")
            params.append(str(sent_at_iso or "").strip())
        if _has_column(con, "quotes", "api_error_at"):
            sets.append("api_error_at = ''")
        if _has_column(con, "quotes", "api_error_message"):
            sets.append("api_error_message = ''")
        if not sets:
            return
        with tx(con):
            con.execute(
                f"UPDATE quotes SET {', '.join(sets)} WHERE id = ?",
                tuple(params + [int(quote_id)]),
            )
    finally:
        con.close()


def _mark_quote_api_error(quote_id: int, *, error_at_iso: str, error_message: str) -> None:
    db_path = resolve_db_path()
    con = connect(db_path)
    _ensure_schema_once(con)
    try:
        sets: list[str] = []
        params: list[Any] = []
        if _has_column(con, "quotes", "api_error_at"):
            sets.append("api_error_at = ?")
            params.append(str(error_at_iso or "").strip())
        if _has_column(con, "quotes", "api_error_message"):
            sets.append("api_error_message = ?")
            params.append(_normalize_error_message(error_message))
        if not sets:
            return
        with tx(con):
            con.execute(
                f"UPDATE quotes SET {', '.join(sets)} WHERE id = ?",
                tuple(params + [int(quote_id)]),
            )
    finally:
        con.close()


def send_quote_from_history_once(
    *,
    quote_id: int,
    force: bool = False,
    login_password: str | None = None,
) -> dict[str, Any]:
    qid = int(quote_id)
    if not force:
        store_id_cfg = str(APP_CONFIG.get("store_id", "") or "").strip()
        username_cfg = str(APP_CONFIG.get("username", "") or "").strip()
        if (not store_id_cfg) or (not username_cfg):
            return {
                "quote_id": qid,
                "status": "SKIPPED_SYNC_DISABLED",
                "reason": "missing_username_or_store_id",
            }
    db_path = resolve_db_path()
    con = connect(db_path)
    _ensure_schema_once(con)
    tipo_documento_api = ""
    fecha_emision_ts = ""
    try:
        header = get_quote_header(con, qid)
        has_api_sent_at = _has_column(con, "quotes", "api_sent_at")
        has_api_error_at = _has_column(con, "quotes", "api_error_at")
        has_api_error_msg = _has_column(con, "quotes", "api_error_message")
        api_sent_at = str(header.get("api_sent_at") or "").strip() if has_api_sent_at else ""
        _api_error_at = str(header.get("api_error_at") or "").strip() if has_api_error_at else ""
        _api_error_message = str(header.get("api_error_message") or "").strip() if has_api_error_msg else ""
        # Si tiene marca de error, se permite reintento aunque exista api_sent_at historico.
        if api_sent_at and not force and (not _api_error_at):
            return {
                "quote_id": qid,
                "status": "SKIPPED_ALREADY_SENT",
                "api_sent_at": api_sent_at,
            }
        if str(header.get("deleted_at") or "").strip():
            return {
                "quote_id": qid,
                "status": "SKIPPED_DELETED",
            }
        raw_quote_code = str(header.get("quote_no") or "").strip()
        store_id_for_code = str(get_setting(con, "store_id", "") or "").strip().upper()
        api_quote_code = _normalize_quote_code_for_api(raw_quote_code, store_id=store_id_for_code)
        if not api_quote_code:
            return {
                "quote_id": qid,
                "status": "SKIPPED_INVALID_CODE",
                "quote_code": raw_quote_code,
                "detail": "Formato de codigo viejo o invalido.",
            }
        items_base, _items_shown = get_quote_items(con, qid)
        if not items_base:
            return {
                "quote_id": qid,
                "status": "SKIPPED_EMPTY_ITEMS",
                "reason": "no_quote_items",
            }
        cod_pais_hdr = _country_code_from_country(str(header.get("country_code") or ""))
        tipo_documento_api = str(header.get("tipo_documento") or "").strip().upper()
        if not tipo_documento_api:
            tipo_documento_api = _infer_tipo_documento_for_api(
                str(header.get("cedula") or ""),
                cod_pais_hdr,
            )
        fecha_emision_ts = _normalize_issue_timestamp(header.get("created_at"))
    finally:
        con.close()

    try:
        result = login_and_send_presupuesto(
            quote_code=api_quote_code,
            fecha_emision_ts=fecha_emision_ts,
            cliente=str(header.get("cliente") or ""),
            cedula=str(header.get("cedula") or ""),
            telefono=str(header.get("telefono") or ""),
            direccion=str(header.get("direccion") or "-"),
            email=str(header.get("email") or "-"),
            metodo_pago=str(header.get("metodo_pago") or ""),
            estado=str(header.get("estado") or ""),
            tipo_documento=tipo_documento_api,
            items_base=items_base,
            login_password=login_password,
        )
    except Exception as e:
        err_at = _now_iso_local()
        err_msg = _normalize_error_message(e)
        try:
            _mark_quote_api_error(qid, error_at_iso=err_at, error_message=err_msg)
        except Exception as mark_err:
            log.warning("No se pudo marcar error API para quote_id=%s: %s", qid, mark_err)
        return {
            "quote_id": qid,
            "status": "SENT_ERROR",
            "api_error_at": err_at,
            "api_error_message": err_msg,
        }

    sent_at = _now_iso_local()
    _mark_quote_api_sent(qid, sent_at_iso=sent_at)

    out = dict(result)
    out["quote_id"] = qid
    out["api_sent_at"] = sent_at
    out["status"] = "SENT"
    return out


def sync_pending_history_quotes_once(
    *,
    created_before_iso: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    db_path = resolve_db_path()
    cutoff = str(created_before_iso or "").strip()
    batch_size = max(1, int(limit))

    # Guard rapido: si la app no tiene identidad minima configurada,
    # no tocar DB ni red para evitar impacto de rendimiento.
    store_id_cfg = str(APP_CONFIG.get("store_id", "") or "").strip()
    username_cfg = str(APP_CONFIG.get("username", "") or "").strip()
    if (not store_id_cfg) or (not username_cfg):
        return {
            "found": 0,
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "disabled": True,
            "reason": "missing_username_or_store_id",
        }

    con_cfg = connect(db_path)
    _ensure_schema_once(con_cfg)
    try:
        store_id_db = str(get_setting(con_cfg, "store_id", "") or "").strip()
        username_db = str(get_setting(con_cfg, "username", "") or "").strip()
    finally:
        con_cfg.close()

    if (not store_id_db) or (not username_db):
        return {
            "found": 0,
            "sent": 0,
            "skipped": 0,
            "failed": 0,
            "disabled": True,
            "reason": "missing_username_or_store_id",
        }

    found = 0
    sent = 0
    skipped = 0
    error_marked = 0
    failed = 0
    last_id = 0

    while True:
        con = connect(db_path)
        _ensure_schema_once(con)
        try:
            if not _has_column(con, "quotes", "api_sent_at"):
                return {
                    "found": 0,
                    "sent": 0,
                    "skipped": 0,
                    "failed": 0,
                }
            has_api_error_at = _has_column(con, "quotes", "api_error_at")

            where = [
                "deleted_at IS NULL",
                "id > ?",
                "EXISTS (SELECT 1 FROM quote_items qi WHERE qi.quote_id = quotes.id)",
            ]
            retry_failed_before_iso = (
                datetime.datetime.now() - datetime.timedelta(hours=3)
            ).isoformat(timespec="seconds")
            params: list[Any] = [int(last_id)]
            if has_api_error_at:
                where.append(
                    "("
                    "(COALESCE(TRIM(api_sent_at), '') = '' AND COALESCE(TRIM(api_error_at), '') = '') "
                    "OR "
                    "(COALESCE(TRIM(api_error_at), '') <> '' AND COALESCE(TRIM(api_error_at), '') <= ?)"
                    ")"
                )
                params.append(retry_failed_before_iso)
            else:
                where.append("COALESCE(TRIM(api_sent_at), '') = ''")
            if cutoff:
                where.append("created_at <= ?")
                params.append(cutoff)
            params.append(batch_size)

            rows = con.execute(
                f"""
                SELECT id
                FROM quotes
                WHERE {" AND ".join(where)}
                ORDER BY id ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        finally:
            con.close()

        if not rows:
            break

        quote_ids = [int(r["id"]) for r in rows]
        found += len(quote_ids)
        last_id = int(quote_ids[-1])

        for qid in quote_ids:
            try:
                res = send_quote_from_history_once(quote_id=qid, force=False)
                status = str(res.get("status") or "").strip().upper()
                if status.startswith("SKIPPED"):
                    skipped += 1
                elif status == "SENT_ERROR":
                    error_marked += 1
                    failed += 1
                else:
                    sent += 1
            except Exception as e:
                failed += 1
                log.warning("Sync API historico fallo para quote_id=%s: %s", qid, e)

        if len(quote_ids) < batch_size:
            break

    return {
        "found": int(found),
        "sent": int(sent),
        "error_marked": int(error_marked),
        "skipped": int(skipped),
        "failed": int(failed),
    }
