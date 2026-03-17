# sqlModels/quotes_repo.py

from __future__ import annotations

import re
import sqlite3
from typing import Any, Optional




# =========================

# Estado (guardado en quotes.estado)

# =========================

STATUS_PAGADO = "PAGADO"
STATUS_POR_PAGAR = "POR_PAGAR"
STATUS_PENDIENTE = "PENDIENTE"
STATUS_NO_APLICA = "NO_APLICA"
STATUS_REENVIADO = "REENVIADO"

ALL_STATUSES = {STATUS_PAGADO, STATUS_POR_PAGAR, STATUS_PENDIENTE, STATUS_NO_APLICA, STATUS_REENVIADO}

STATUS_LABELS = {
    STATUS_PAGADO: "Pagado",
    STATUS_POR_PAGAR: "Por pagar",
    STATUS_PENDIENTE: "Pendiente",
    STATUS_NO_APLICA: "No aplica",
    STATUS_REENVIADO: "Reenviado",
}

PRICE_ID_P_MAX = 1
PRICE_ID_P_MIN = 2
PRICE_ID_P_OFERTA = 3
PRICE_ID_PERSONALIZADO = 4

TIPO_PROD_PROD = "prod"
TIPO_PROD_SERV = "serv"
TIPO_PROD_PRES = "pres"

_DOC_RULES_BY_COUNTRY_CODE: dict[str, list[dict[str, Any]]] = {
    "VE": [
        {"id": 1, "nombre": "V", "descripcion": "CEDULA DE IDENTIDAD", "regex_validation": r"^[0-9]+$", "validation_pad": 0},
        {"id": 2, "nombre": "P", "descripcion": "PASAPORTE", "regex_validation": r"^[a-zA-Z0-9]+$", "validation_pad": 0},
        {"id": 3, "nombre": "J", "descripcion": "JURIDICO", "regex_validation": r"^[0-9]\d{8}$", "validation_pad": 9},
        {"id": 4, "nombre": "E", "descripcion": "CEDULA EXTRANJERA", "regex_validation": r"^[0-9]+$", "validation_pad": 0},
        {"id": 5, "nombre": "G", "descripcion": "GUBERNAMENTAL", "regex_validation": r"^[0-9]\d{8}$", "validation_pad": 9},
    ],
    "PE": [
        {"id": 1, "nombre": "DNI", "descripcion": "DOCUMENTO NACIONAL DE IDENTIDAD", "regex_validation": r"^[0-9]\d{7}$", "validation_pad": 8},
        {"id": 2, "nombre": "P", "descripcion": "PASAPORTE", "regex_validation": r"^[a-zA-Z0-9]+$", "validation_pad": 0},
        {"id": 3, "nombre": "RUC", "descripcion": "JURIDICO", "regex_validation": r"^[0-9]\d{10}$", "validation_pad": 11},
        {"id": 4, "nombre": "CE", "descripcion": "CARNET DE EXTRANJERIA", "regex_validation": r"^[0-9]\d{7}$", "validation_pad": 8},
    ],
    "PY": [
        {"id": 1, "nombre": "CI", "descripcion": "CEDULA DE IDENTIDAD", "regex_validation": r"^[0-9]+$", "validation_pad": 0},
        {"id": 2, "nombre": "P", "descripcion": "PASAPORTE", "regex_validation": r"^[a-zA-Z0-9]+$", "validation_pad": 0},
        {"id": 3, "nombre": "RUC", "descripcion": "JURIDICO", "regex_validation": r"^[0-9]+$", "validation_pad": 0},
    ],
}

_DOC_TYPE_ALIASES: dict[str, dict[str, str]] = {
    "VE": {
        "CI": "V",    # legacy
        "CE": "E",    # legacy
        "RIF": "J",   # legacy
        "PASAPORTE": "P",
    },
    "PE": {
        "CI": "DNI",  # legacy
        "PASAPORTE": "P",
    },
    "PY": {
        "CE": "CI",   # legacy
        "PASAPORTE": "P",
    },
}

_DEFAULT_DOC_TYPE_BY_COUNTRY: dict[str, str] = {
    "VE": "V",
    "PE": "DNI",
    "PY": "CI",
}


def _build_doc_indexes() -> tuple[dict[str, set[str]], dict[str, dict[str, str]], dict[str, dict[str, int]]]:
    by_country_types: dict[str, set[str]] = {}
    by_country_regex: dict[str, dict[str, str]] = {}
    by_country_pad: dict[str, dict[str, int]] = {}
    for cc, rows in (_DOC_RULES_BY_COUNTRY_CODE or {}).items():
        cc_u = str(cc or "").strip().upper()
        by_country_types[cc_u] = set()
        by_country_regex[cc_u] = {}
        by_country_pad[cc_u] = {}
        for r in (rows or []):
            name = str((r or {}).get("nombre") or "").strip().upper()
            if not name:
                continue
            by_country_types[cc_u].add(name)
            by_country_regex[cc_u][name] = str((r or {}).get("regex_validation") or "").strip()
            try:
                by_country_pad[cc_u][name] = int((r or {}).get("validation_pad") or 0)
            except Exception:
                by_country_pad[cc_u][name] = 0
    return by_country_types, by_country_regex, by_country_pad


_DOC_TYPES_BY_COUNTRY_CODE, _DOC_BODY_BY_COUNTRY_AND_TYPE, _DOC_VALIDATION_PAD_BY_COUNTRY_AND_TYPE = _build_doc_indexes()


def _country_code_norm(country_code: Any) -> str:
    c = str(country_code or "").strip().upper()
    if c in ("PE", "PERU"):
        return "PE"
    if c in ("VE", "VENEZUELA"):
        return "VE"
    if c in ("PY", "PARAGUAY"):
        return "PY"
    return c


def _doc_body_matches(country_code: str, doc_type: str, body: str) -> bool:
    pat = (
        _DOC_BODY_BY_COUNTRY_AND_TYPE.get(str(country_code or "").upper(), {})
        .get(str(doc_type or "").upper(), "")
    )
    if not pat:
        return False
    doc_val = str(body or "").strip()
    if not re.fullmatch(pat, doc_val):
        return False

    pad = int(
        _DOC_VALIDATION_PAD_BY_COUNTRY_AND_TYPE.get(str(country_code or "").upper(), {})
        .get(str(doc_type or "").upper(), 0)
        or 0
    )
    if pad > 0 and len(doc_val) != pad:
        return False
    return True


def _normalize_doc_type(country_code: str, raw_doc_type: Any) -> str:
    cod = _country_code_norm(country_code)
    dt = str(raw_doc_type or "").strip().upper()
    if not dt:
        return ""
    dt = _DOC_TYPE_ALIASES.get(cod, {}).get(dt, dt)
    return dt


def document_type_rules_for_country(country_code: Any) -> list[dict[str, Any]]:
    cod = _country_code_norm(country_code)
    rows = _DOC_RULES_BY_COUNTRY_CODE.get(cod, []) or []
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            rid = int((r or {}).get("id") or 0)
        except Exception:
            rid = 0
        name = str((r or {}).get("nombre") or "").strip().upper()
        if not name:
            continue
        out.append(
            {
                "id": rid,
                "nombre": name,
                "descripcion": str((r or {}).get("descripcion") or "").strip().upper(),
                "regex_validation": str((r or {}).get("regex_validation") or "").strip(),
                "validation_pad": int((r or {}).get("validation_pad") or 0),
                "id_pais": cod,
            }
        )
    out.sort(key=lambda x: (int(x.get("id") or 0), str(x.get("nombre") or "")))
    return out


def document_type_rule(country_code: Any, doc_type: Any) -> dict[str, Any] | None:
    cod = _country_code_norm(country_code)
    dt = _normalize_doc_type(cod, doc_type)
    if not dt:
        return None
    for r in document_type_rules_for_country(cod):
        if str(r.get("nombre") or "").upper() == dt:
            return r
    return None


def _normalize_name_for_compare(value: Any) -> str:
    s = str(value or "").strip().lower()
    if not s:
        return ""
    return re.sub(r"\s+", " ", s)


def _normalize_phone_for_compare(value: Any) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    digits = re.sub(r"\D", "", s)
    if digits:
        return digits
    return re.sub(r"\s+", "", s).lower()


def _normalize_doc_for_compare(value: Any) -> str:
    s = str(value or "").strip().upper()
    if not s:
        return ""
    m = re.match(r"^([A-Z]+)-(.+)$", s)
    if m:
        s = str(m.group(2) or "").strip().upper()
    s = re.sub(r"\s+", "", s)
    # Para comparar identidad, usamos cuerpo alfanumerico sin separadores.
    return re.sub(r"[^0-9A-Z]", "", s)


def find_doc_identity_conflict(
    con: sqlite3.Connection,
    *,
    country_code: str | None = None,
    tipo_documento: str | None = None,
    cedula: str,
    cliente: str,
    telefono: str,
    exclude_quote_id: int | None = None,
    include_deleted: bool = False,
) -> dict[str, Any] | None:
    """
    Regla de negocio:
    - El mismo documento no puede quedar asociado a otro nombre o telefono.
    """
    doc_key = _normalize_doc_for_compare(cedula)
    if not doc_key:
        return None
    cc = _country_code_norm(country_code or "")
    incoming_tipo = _normalize_doc_type(cc, tipo_documento or "")

    # Esquema actual: clientes en tabla maestra y quotes con id_cliente.
    if _table_exists(con, "clients") and _has_column(con, "quotes", "id_cliente"):
        direccion_expr = "COALESCE(c.direccion, '') AS direccion" if _has_column(con, "clients", "direccion") else "'' AS direccion"
        email_expr = "COALESCE(c.email, '') AS email" if _has_column(con, "clients", "email") else "'' AS email"
        has_deleted = _has_column(con, "quotes", "deleted_at")
        exclude_sql = f" AND qx.id <> {int(exclude_quote_id)}" if exclude_quote_id is not None else ""
        active_sql = " AND qx.deleted_at IS NULL" if (not include_deleted and has_deleted) else ""

        where = ["UPPER(TRIM(COALESCE(c.documento_norm, ''))) = ?"]
        params: list[Any] = [doc_key.upper()]

        if cc and _has_column(con, "clients", "country_code"):
            where.append("UPPER(TRIM(COALESCE(c.country_code, ''))) = ?")
            params.append(cc)
        if (not include_deleted) and _has_column(con, "clients", "deleted_at"):
            where.append("c.deleted_at IS NULL")

        if not include_deleted:
            exists_filter = ""
            if has_deleted:
                exists_filter += " AND qa.deleted_at IS NULL"
            if exclude_quote_id is not None:
                exists_filter += f" AND qa.id <> {int(exclude_quote_id)}"
            where.append(
                "(c.source_quote_id IS NULL OR EXISTS ("
                "SELECT 1 FROM quotes qa WHERE qa.id_cliente = c.id"
                f"{exists_filter}))"
            )

        where_sql = " AND ".join(where)
        rows = con.execute(
            f"""
            SELECT
                c.id AS client_id,
                COALESCE(c.nombre, '') AS cliente,
                COALESCE(c.documento, '') AS cedula,
                COALESCE(c.tipo_documento, '') AS tipo_documento,
                COALESCE(c.telefono, '') AS telefono,
                {direccion_expr},
                {email_expr},
                (
                    SELECT qx.id
                    FROM quotes qx
                    WHERE qx.id_cliente = c.id
                    {active_sql}
                    {exclude_sql}
                    ORDER BY qx.created_at DESC, qx.id DESC
                    LIMIT 1
                ) AS quote_id,
                (
                    SELECT qx.quote_no
                    FROM quotes qx
                    WHERE qx.id_cliente = c.id
                    {active_sql}
                    {exclude_sql}
                    ORDER BY qx.created_at DESC, qx.id DESC
                    LIMIT 1
                ) AS quote_no,
                (
                    SELECT qx.created_at
                    FROM quotes qx
                    WHERE qx.id_cliente = c.id
                    {active_sql}
                    {exclude_sql}
                    ORDER BY qx.created_at DESC, qx.id DESC
                    LIMIT 1
                ) AS created_at
            FROM clients c
            WHERE {where_sql}
            ORDER BY COALESCE(created_at, c.source_created_at, c.updated_at, c.created_at) DESC, c.id DESC
            """,
            tuple(params),
        ).fetchall()

        cli_key = _normalize_name_for_compare(cliente)
        tel_key = _normalize_phone_for_compare(telefono)
        for r in rows:
            d = dict(r)
            other_tipo = _normalize_doc_type(cc, d.get("tipo_documento", ""))
            if incoming_tipo and other_tipo and incoming_tipo != other_tipo:
                continue

            other_cli_key = _normalize_name_for_compare(d.get("cliente", ""))
            other_tel_key = _normalize_phone_for_compare(d.get("telefono", ""))
            same_cliente = (other_cli_key == cli_key)
            same_telefono = (other_tel_key == tel_key)
            if same_cliente and same_telefono:
                continue

            return {
                "id": int(d.get("quote_id") or d.get("client_id") or 0),
                "quote_no": str(d.get("quote_no") or ""),
                "created_at": str(d.get("created_at") or ""),
                "cliente": str(d.get("cliente") or ""),
                "cedula": str(d.get("cedula") or ""),
                "tipo_documento": str(d.get("tipo_documento") or ""),
                "telefono": str(d.get("telefono") or ""),
                "direccion": str(d.get("direccion") or ""),
                "email": str(d.get("email") or ""),
                "same_cliente": bool(same_cliente),
                "same_telefono": bool(same_telefono),
            }
        return None

    where = []
    params: list[Any] = []
    if cc and _has_column(con, "quotes", "country_code"):
        where.append("UPPER(TRIM(COALESCE(country_code, ''))) = ?")
        params.append(cc)
    if not include_deleted and _has_column(con, "quotes", "deleted_at"):
        where.append("deleted_at IS NULL")
    if exclude_quote_id is not None:
        where.append("id <> ?")
        params.append(int(exclude_quote_id))
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    tipo_sel = "COALESCE(tipo_documento,'') AS tipo_documento" if _has_column(con, "quotes", "tipo_documento") else "'' AS tipo_documento"

    rows = con.execute(
        f"""
        SELECT id, quote_no, created_at, cliente, cedula, telefono, {tipo_sel}
        FROM quotes
        {where_sql}
        ORDER BY created_at DESC, id DESC
        """,
        tuple(params),
    ).fetchall()

    cli_key = _normalize_name_for_compare(cliente)
    tel_key = _normalize_phone_for_compare(telefono)
    for r in rows:
        d = dict(r)
        other_doc_key = _normalize_doc_for_compare(d.get("cedula", ""))
        if not other_doc_key or other_doc_key != doc_key:
            continue

        other_tipo = _normalize_doc_type(cc, d.get("tipo_documento", ""))
        if incoming_tipo and other_tipo and incoming_tipo != other_tipo:
            continue

        other_cli_key = _normalize_name_for_compare(d.get("cliente", ""))
        other_tel_key = _normalize_phone_for_compare(d.get("telefono", ""))
        same_cliente = (other_cli_key == cli_key)
        same_telefono = (other_tel_key == tel_key)
        if same_cliente and same_telefono:
            continue

        return {
            "id": int(d.get("id") or 0),
            "quote_no": str(d.get("quote_no") or ""),
            "created_at": str(d.get("created_at") or ""),
            "cliente": str(d.get("cliente") or ""),
            "cedula": str(d.get("cedula") or ""),
            "tipo_documento": str(d.get("tipo_documento") or ""),
            "telefono": str(d.get("telefono") or ""),
            "direccion": "-",
            "email": "-",
            "same_cliente": bool(same_cliente),
            "same_telefono": bool(same_telefono),
        }
    return None


def doc_regex_for_country(country_code: Any) -> str:
    cod = _country_code_norm(country_code)
    parts: list[str] = []
    for rule in document_type_rules_for_country(cod):
        pat = str(rule.get("regex_validation") or "").strip()
        if not pat:
            continue
        if pat.startswith("^"):
            pat = pat[1:]
        if pat.endswith("$"):
            pat = pat[:-1]
        if pat:
            parts.append(f"(?:{pat})")
    if not parts:
        return r"^[0-9A-Za-z\-]{4,20}$"
    return r"^(?:" + "|".join(parts) + r")$"


def validate_document_for_type(country_code: Any, doc_type: Any, doc_value: Any) -> tuple[bool, str]:
    cod = _country_code_norm(country_code)
    rule = document_type_rule(cod, doc_type)
    if not rule:
        return False, "Selecciona un tipo de documento valido."

    doc = str(doc_value or "").strip()
    if not doc:
        return False, "Documento vacio."

    dt = str(rule.get("nombre") or "").upper()
    if not _doc_body_matches(cod, dt, doc):
        pad = int(rule.get("validation_pad") or 0)
        desc = str(rule.get("descripcion") or "")
        if pad > 0:
            return False, f"{dt} ({desc}) requiere {pad} caracteres y formato valido."
        return False, f"{dt} ({desc}) no cumple el formato valido."
    return True, ""


def infer_tipo_documento_from_doc(
    country_code: Any,
    cedula: Any,
    *,
    explicit_tipo: Any = "",
) -> str:
    """
    Inferencia best effort por reglas de pais (con aliases legacy).
    Si hay multiples coincidencias y no viene tipo explicito,
    usa el tipo por defecto del pais.
    """
    cod = _country_code_norm(country_code)
    allowed = _DOC_TYPES_BY_COUNTRY_CODE.get(cod, set())

    explicit = _normalize_doc_type(cod, explicit_tipo)

    raw = str(cedula or "").strip().upper()
    if not raw:
        return ""
    compact = re.sub(r"\s+", "", raw)

    # Legacy con prefijo: <TIPO>-<NUMERO>
    m = re.match(r"^([A-Z]+)-(.+)$", compact)
    if m:
        pref = _normalize_doc_type(cod, m.group(1))
        body = str(m.group(2) or "").strip().upper()
        if explicit and (not allowed or explicit in allowed) and _doc_body_matches(cod, explicit, body):
            return explicit
        if (not allowed or pref in allowed) and _doc_body_matches(cod, pref, body):
            return pref
        compact = body

    if not compact:
        return ""

    if explicit and (not allowed or explicit in allowed) and _doc_body_matches(cod, explicit, compact):
        return explicit

    matches: list[str] = []
    for doc_type in sorted(allowed):
        if _doc_body_matches(cod, doc_type, compact):
            matches.append(doc_type)

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        default_type = _DEFAULT_DOC_TYPE_BY_COUNTRY.get(cod, "")
        if default_type and default_type in matches:
            return default_type
        return sorted(matches)[0]

    return ""


def _status_code_token(value: Any) -> str:
    try:
        from .quote_statuses_repo import normalize_status_code

        return str(normalize_status_code(value) or "")
    except Exception:
        s = str(value or "").strip().upper()
        if not s:
            return ""
        s = s.replace("-", "_")
        s = re.sub(r"\s+", "_", s)
        s = re.sub(r"[^A-Z0-9_]", "", s)
        s = re.sub(r"_+", "_", s).strip("_")
        return s


def _status_lookup_maps() -> tuple[dict[str, str], dict[str, str]]:
    labels_by_code = {str(k): str(v) for k, v in STATUS_LABELS.items()}
    lookup: dict[str, str] = {}
    for code, label in labels_by_code.items():
        tok = _status_code_token(code)
        if not tok:
            continue
        lookup[tok] = tok
        lookup[tok.replace("_", "")] = tok
        ltok = _status_code_token(label)
        if ltok:
            lookup[ltok] = tok
            lookup[ltok.replace("_", "")] = tok

    try:
        from src.db_path import resolve_db_path
        from .quote_statuses_repo import (
            get_quote_statuses_cached,
            build_status_lookup_from_rows,
        )

        rows = get_quote_statuses_cached(db_path=resolve_db_path())
        db_lookup = build_status_lookup_from_rows(rows)
        for k, v in (db_lookup or {}).items():
            tk = _status_code_token(k)
            tv = _status_code_token(v)
            if tk and tv:
                lookup[tk] = tv
        for row in rows or []:
            code = _status_code_token((row or {}).get("code"))
            if not code:
                continue
            label = str((row or {}).get("label") or "").strip()
            if label:
                labels_by_code[code] = label
    except Exception:
        pass

    return lookup, labels_by_code


def normalize_status(value: str | None) -> Optional[str]:
    if value is None:
        return None

    raw = str(value).strip()
    if not raw:
        return None

    tok = _status_code_token(raw)
    if not tok:
        return None

    lookup, _labels_by_code = _status_lookup_maps()
    mapped = _status_code_token(lookup.get(tok) or lookup.get(tok.replace("_", "")) or tok)
    if not mapped:
        return None

    if mapped in ALL_STATUSES:
        return mapped

    if mapped in lookup.values():
        return mapped

    return None


def status_label(status: str | None) -> str:
    raw = str(status or "").strip()
    if not raw:
        return ""

    st = normalize_status(raw)
    if not st:
        return raw

    _lookup, labels_by_code = _status_lookup_maps()
    lbl = str(labels_by_code.get(st) or "").strip()
    if lbl:
        return lbl
    return STATUS_LABELS.get(st, st.replace("_", " ").capitalize())





def _has_column(con: sqlite3.Connection, table: str, col: str) -> bool:
    try:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        cols = {str(r["name"]).lower() for r in rows}
        return col.lower() in cols
    except Exception:
        return False


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (str(table or ""),),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _normalize_tipo_prod(value: Any) -> str:
    t = str(value or "").strip().lower()
    aliases = {
        "serv": TIPO_PROD_SERV,
        "servicio": TIPO_PROD_SERV,
        "service": TIPO_PROD_SERV,
        "prod": TIPO_PROD_PROD,
        "producto": TIPO_PROD_PROD,
        "product": TIPO_PROD_PROD,
        "pres": TIPO_PROD_PRES,
        "presentacion": TIPO_PROD_PRES,
        "presentation": TIPO_PROD_PRES,
    }
    return aliases.get(t, "")


def _tipo_prod_from_categoria(categoria: Any) -> str:
    c = str(categoria or "").strip().lower()
    if c in ("serv", "servicio", "service"):
        return TIPO_PROD_SERV
    if c in ("pres", "presentacion", "presentation"):
        return TIPO_PROD_PRES
    return TIPO_PROD_PROD


def _resolve_tipo_prod(*, categoria: Any, explicit_tipo: Any) -> str:
    normalized = _normalize_tipo_prod(explicit_tipo)
    if normalized:
        return normalized
    return _tipo_prod_from_categoria(categoria)


def _normalize_price_tier(value: Any) -> str:
    t = str(value or "").strip().lower()
    aliases = {
        "offer": "oferta",
        "promo": "oferta",
        "promocion": "oferta",
        "promociÃ³n": "oferta",
        "minimum": "minimo",
        "mÃ­nimo": "minimo",
        "min": "minimo",
        "maximo": "unitario",
        "mÃ¡ximo": "unitario",
        "max": "unitario",
        "base": "unitario",
    }
    return aliases.get(t, t)


def _tier_from_price_id(price_id: int) -> Optional[str]:
    if int(price_id) == PRICE_ID_P_MIN:
        return "minimo"
    if int(price_id) == PRICE_ID_P_OFERTA:
        return "oferta"
    if int(price_id) == PRICE_ID_P_MAX:
        return "unitario"
    return None


def _resolve_price_id(
    *,
    categoria: Any,
    tipo_prod: Any,
    explicit_id: Any,
    precio_tier: Any,
    precio_override: Any,
) -> int:
    if _resolve_tipo_prod(categoria=categoria, explicit_tipo=tipo_prod) == TIPO_PROD_SERV:
        return PRICE_ID_PERSONALIZADO

    try:
        eid = int(explicit_id)
    except Exception:
        eid = 0
    if eid in (PRICE_ID_P_MAX, PRICE_ID_P_MIN, PRICE_ID_P_OFERTA):
        return eid

    tier = _normalize_price_tier(precio_tier)
    if tier == "minimo":
        return PRICE_ID_P_MIN
    if tier == "oferta":
        return PRICE_ID_P_OFERTA
    if tier in ("unitario", ""):
        return PRICE_ID_P_MAX

    if precio_override is not None:
        return PRICE_ID_P_MAX

    return PRICE_ID_P_MAX


def insert_quote(
    con: sqlite3.Connection,

    *,

    country_code: str,

    quote_no: str,

    created_at: str,
    cliente: str,
    cedula: str,
    telefono: str,
    direccion: str = "-",
    email: str = "-",
    tipo_documento: str = "",
    metodo_pago: str = "",
    currency_shown: str,

    tasa_shown: float | None,

    subtotal_bruto_base: float,

    descuento_total_base: float,

    total_neto_base: float,

    subtotal_bruto_shown: float,

    descuento_total_shown: float,

    total_neto_shown: float,

    pdf_path: str,

    items_base: list[dict],

    items_shown: list[dict],

) -> int:
    if len(items_base) != len(items_shown):

        raise ValueError("items_base y items_shown deben tener el mismo tamaÃ±o")



    has_mp = _has_column(con, "quotes", "metodo_pago")
    has_estado = _has_column(con, "quotes", "estado")
    has_id_cliente = _has_column(con, "quotes", "id_cliente")
    has_price_id = _has_column(con, "quote_items", "id_precioventa")
    has_tipo_prod = _has_column(con, "quote_items", "tipo_prod")
    tipo_documento_norm = infer_tipo_documento_from_doc(
        country_code,
        cedula,
        explicit_tipo=tipo_documento,
    )


    cols: list[str] = [
        "country_code", "quote_no", "created_at",
    ]
    vals: list[Any] = [
        country_code, quote_no, created_at,
    ]

    if has_id_cliente:
        cols.append("id_cliente")
        vals.append(None)

    if has_mp:
        cols.append("metodo_pago")
        vals.append(str(metodo_pago or ""))

    if has_estado:
        cols.append("estado")
        vals.append("")

    cols.extend(
        [
            "currency_shown", "tasa_shown",
            "subtotal_bruto_base", "descuento_total_base", "total_neto_base",
            "subtotal_bruto_shown", "descuento_total_shown", "total_neto_shown",
            "pdf_path",
        ]
    )
    vals.extend(
        [
            currency_shown, tasa_shown,
            float(subtotal_bruto_base), float(descuento_total_base), float(total_neto_base),
            float(subtotal_bruto_shown), float(descuento_total_shown), float(total_neto_shown),
            pdf_path,
        ]
    )


    placeholders = ",".join(["?"] * len(cols))

    sql = f"INSERT INTO quotes({', '.join(cols)}) VALUES({placeholders})"
    cur = con.execute(sql, tuple(vals))
    quote_id = int(cur.lastrowid)

    item_cols: list[str] = [
        "quote_id",
        "codigo", "producto", "categoria",
    ]
    if has_tipo_prod:
        item_cols.append("tipo_prod")
    item_cols.extend([
        "fragancia", "observacion",
        "cantidad",
        "precio_base", "subtotal_base",
        "descuento_mode", "descuento_pct", "descuento_monto_base",
        "total_base",
        "precio_override_base", "precio_tier",
    ])
    if has_price_id:
        item_cols.append("id_precioventa")
    item_cols.extend([
        "precio_shown", "subtotal_shown", "descuento_monto_shown", "total_shown",
    ])

    rows: list[tuple[Any, ...]] = []
    for b, s in zip(items_base, items_shown):
        categoria = str(b.get("categoria") or "")
        tipo_prod = _resolve_tipo_prod(categoria=categoria, explicit_tipo=b.get("tipo_prod"))
        precio_base = float(b.get("precio") or 0.0)
        precio_override_raw = (None if b.get("precio_override") is None else float(b.get("precio_override") or 0.0))
        price_id = _resolve_price_id(
            categoria=categoria,
            tipo_prod=tipo_prod,
            explicit_id=b.get("id_precioventa"),
            precio_tier=b.get("precio_tier"),
            precio_override=precio_override_raw,
        )
        if tipo_prod == TIPO_PROD_SERV:
            precio_override_store = precio_override_raw
            if precio_override_store is None:
                precio_override_store = precio_base
            precio_tier_store = None
        else:
            precio_override_store = None
            precio_tier_store = _tier_from_price_id(price_id)

        row_data: dict[str, Any] = {
            "quote_id": quote_id,
            "codigo": str(b.get("codigo") or ""),
            "producto": str(b.get("producto") or ""),
            "categoria": categoria,
            "tipo_prod": tipo_prod,
            "fragancia": str(b.get("fragancia") or ""),
            "observacion": str(b.get("observacion") or ""),
            "cantidad": float(b.get("cantidad") or 0.0),
            "precio_base": precio_base,
            "subtotal_base": float(b.get("subtotal_base") or 0.0),
            "descuento_mode": (b.get("descuento_mode") or None),
            "descuento_pct": float(b.get("descuento_pct") or 0.0),
            "descuento_monto_base": float(b.get("descuento_monto") or 0.0),
            "total_base": float(b.get("total") or 0.0),
            "precio_override_base": precio_override_store,
            "precio_tier": precio_tier_store,
            "id_precioventa": int(price_id),
            "precio_shown": float(s.get("precio") or 0.0),
            "subtotal_shown": float(s.get("subtotal") or 0.0),
            "descuento_monto_shown": float(s.get("descuento") or 0.0),
            "total_shown": float(s.get("total") or 0.0),
        }
        rows.append(tuple(row_data[c] for c in item_cols))

    placeholders_items = ",".join(["?"] * len(item_cols))
    sql_items = f"INSERT INTO quote_items({', '.join(item_cols)}) VALUES({placeholders_items})"
    con.executemany(sql_items, rows)

    # Mantiene maestro de clientes sincronizado con nuevas cotizaciones.
    try:
        from .clients_repo import upsert_client

        client_id = upsert_client(
            con,
            country_code=country_code,
            tipo_documento=tipo_documento_norm,
            documento=cedula,
            nombre=cliente,
            telefono=telefono,
            direccion=direccion,
            email=email,
            source_quote_id=quote_id,
            source_created_at=created_at,
            require_valid_document=False,
        )
        if has_id_cliente and client_id is not None:
            con.execute(
                "UPDATE quotes SET id_cliente = ? WHERE id = ?",
                (int(client_id), quote_id),
            )
    except Exception:
        pass

    return quote_id




def update_quote_payment(con: sqlite3.Connection, quote_id: int, metodo_pago: str) -> None:

    if not _has_column(con, "quotes", "metodo_pago"):

        raise RuntimeError("La columna 'metodo_pago' no existe en la tabla 'quotes'.")

    sets = ["metodo_pago = ?"]
    params: list[Any] = [str(metodo_pago or "")]

    if _has_column(con, "quotes", "api_sent_at"):
        sets.append("api_sent_at = ''")
    if _has_column(con, "quotes", "api_error_at"):
        sets.append("api_error_at = ''")
    if _has_column(con, "quotes", "api_error_message"):
        sets.append("api_error_message = ''")

    con.execute(

        f"UPDATE quotes SET {', '.join(sets)} WHERE id = ?",

        tuple(params + [int(quote_id)]),

    )





def update_quote_status(con: sqlite3.Connection, quote_id: int, estado: str | None) -> None:

    if not _has_column(con, "quotes", "estado"):

        raise RuntimeError("La columna 'estado' no existe en la tabla 'quotes'.")

    st = normalize_status(estado) or ""

    sets = ["estado = ?"]
    params: list[Any] = [st]

    if _has_column(con, "quotes", "api_sent_at"):
        sets.append("api_sent_at = ''")
    if _has_column(con, "quotes", "api_error_at"):
        sets.append("api_error_at = ''")
    if _has_column(con, "quotes", "api_error_message"):
        sets.append("api_error_message = ''")

    con.execute(

        f"UPDATE quotes SET {', '.join(sets)} WHERE id = ?",

        tuple(params + [int(quote_id)]),

    )





def soft_delete_quote(con: sqlite3.Connection, quote_id: int, deleted_at_iso: str) -> None:
    con.execute(
        "UPDATE quotes SET deleted_at = ? WHERE id = ?",
        (deleted_at_iso, int(quote_id)),
    )
    try:
        from .clients_repo import rebuild_clients_from_quotes

        rebuild_clients_from_quotes(con)
    except Exception:
        pass




def list_quotes(
    con: sqlite3.Connection,

    *,

    search_text: str = "",

    contains_product: str = "",

    include_deleted: bool = False,

    limit: int = 200,

    offset: int = 0,

) -> tuple[list[dict], int]:
    st = (search_text or "").strip()

    cp = (contains_product or "").strip()



    where: list[str] = []

    params: list[Any] = []



    if not include_deleted:
        where.append("q.deleted_at IS NULL")

    has_mp = _has_column(con, "quotes", "metodo_pago")
    has_estado = _has_column(con, "quotes", "estado")
    has_client_ref = _has_column(con, "quotes", "id_cliente") and _table_exists(con, "clients")
    has_status_catalog = _table_exists(con, "quote_statuses")

    if has_client_ref:
        client_join = "LEFT JOIN clients c ON c.id = q.id_cliente"
        cliente_expr = "COALESCE(c.nombre, '')"
        cedula_expr = "COALESCE(c.documento, '')"
        telefono_expr = "COALESCE(c.telefono, '')"
    else:
        client_join = ""
        cliente_expr = "COALESCE(q.cliente, '')" if _has_column(con, "quotes", "cliente") else "''"
        cedula_expr = "COALESCE(q.cedula, '')" if _has_column(con, "quotes", "cedula") else "''"
        telefono_expr = "COALESCE(q.telefono, '')" if _has_column(con, "quotes", "telefono") else "''"

    status_join = "LEFT JOIN quote_statuses qs ON qs.code = q.estado" if (has_estado and has_status_catalog) else ""


    # âœ… Buscar por cualquier columna, pero usando valores FORMATEADOS como se ven en el histÃ³rico

    if st:

        like = f"%{st}%"



        # Normaliza created_at a "YYYY-MM-DD HH:MM:SS" (desde "YYYY-MM-DDTHH:MM:SS")

        dt = "replace(substr(q.created_at,1,19), 'T', ' ')"

        date_ddmm = f"(substr({dt},9,2) || '/' || substr({dt},6,2))"

        date_ddmmyyyy = f"({date_ddmm} || '/' || substr({dt},1,4))"



        h24 = f"CAST(substr({dt},12,2) AS INTEGER)"

        h12 = f"(({h24} + 11) % 12) + 1"

        h12_2 = f"printf('%02d', {h12})"          # 02

        h12_1 = f"CAST({h12} AS TEXT)"            # 2

        mm = f"substr({dt},15,2)"

        ampm = f"(CASE WHEN {h24} < 12 THEN 'am' ELSE 'pm' END)"



        # "02:15 pm" / "2:15 pm"

        time12_2 = f"({h12_2} || ':' || {mm} || ' ' || {ampm})"

        time12_1 = f"({h12_1} || ':' || {mm} || ' ' || {ampm})"



        # "23/01/2026 02:15 pm" / "23/01/2026 2:15 pm"  (igual que tu UI)

        dt_ui_2 = f"({date_ddmmyyyy} || ' ' || {time12_2})"

        dt_ui_1 = f"({date_ddmmyyyy} || ' ' || {time12_1})"



        # Total como se ve: "2256.06"

        total_2 = "printf('%.2f', q.total_neto_shown)"



        # Items como se ve: "9"

        items_txt = "CAST((SELECT COUNT(*) FROM quote_items qi2 WHERE qi2.quote_id = q.id) AS TEXT)"



        # Estado como se ve: "Pagado", "Por pagar", etc.

        estado_label_sql = (
            "COALESCE(NULLIF(TRIM(COALESCE(qs.label, '')), ''), q.estado)"
            if status_join
            else (
                "CASE q.estado "
                "WHEN 'PAGADO' THEN 'Pagado' "
                "WHEN 'POR_PAGAR' THEN 'Por pagar' "
                "WHEN 'PENDIENTE' THEN 'Pendiente' "
                "WHEN 'NO_APLICA' THEN 'No aplica' "
                "WHEN 'REENVIADO' THEN 'Reenviado' "
                "ELSE q.estado END"
            )
        )


        or_terms: list[str] = []



        # Fecha/hora (ISO + formato UI)

        or_terms.extend([

            "q.created_at LIKE ?",

            f"{date_ddmm} LIKE ?",

            f"{date_ddmmyyyy} LIKE ?",

            f"{dt_ui_2} LIKE ?",

            f"{dt_ui_1} LIKE ?",

            f"{time12_2} LIKE ?",

            f"{time12_1} LIKE ?",

        ])

        params.extend([like] * 7)



        # NÂ° (soporta legacy "0000001"/"PY-0000001" y nuevo "PY-STORE-0000001")

        quote_tail = (

            "CASE "

            "WHEN instr(q.quote_no,'-') = 0 THEN q.quote_no "

            "WHEN instr(substr(q.quote_no, instr(q.quote_no,'-') + 1), '-') = 0 "

            "THEN substr(q.quote_no, instr(q.quote_no,'-') + 1) "

            "ELSE substr("

            "substr(q.quote_no, instr(q.quote_no,'-') + 1), "

            "instr(substr(q.quote_no, instr(q.quote_no,'-') + 1), '-') + 1"

            ") END"

        )

        or_terms.extend([

            "q.quote_no LIKE ?",

            f"{quote_tail} LIKE ?",

            f"CAST(CAST({quote_tail} AS INTEGER) AS TEXT) LIKE ?",

        ])

        params.extend([like, like, like])



        # Texto base visible
        or_terms.extend([
            f"{cliente_expr} LIKE ?",
            f"{cedula_expr} LIKE ?",
            f"{telefono_expr} LIKE ?",
        ])
        params.extend([like, like, like])


        # Estado (raw + con espacios + label)

        if has_estado:

            or_terms.append("(q.estado LIKE ? OR REPLACE(q.estado,'_',' ') LIKE ? OR " + estado_label_sql + " LIKE ?)")

            params.extend([like, like, like])



        # Pago

        if has_mp:

            or_terms.append("q.metodo_pago LIKE ?")

            params.append(like)



        # Total (2 decimales + raw)

        or_terms.extend([

            f"{total_2} LIKE ?",

            "CAST(q.total_neto_shown AS TEXT) LIKE ?",

        ])

        params.extend([like, like])



        # Moneda

        or_terms.append("q.currency_shown LIKE ?")

        params.append(like)



        # Items (conteo como se ve)

        or_terms.append(f"{items_txt} LIKE ?")

        params.append(like)



        # PDF (en tu UI se ve el nombre; en DB normalmente ya guardas basename)

        or_terms.append("q.pdf_path LIKE ?")

        params.append(like)



        where.append("(" + " OR ".join(or_terms) + ")")



    # filtro por producto (cÃ³digo o nombre)

    if cp:

        where.append(

            """

            EXISTS (

                SELECT 1

                FROM quote_items qi

                WHERE qi.quote_id = q.id

                  AND (qi.codigo LIKE ? OR qi.producto LIKE ?)

            )

            """

        )

        likep = f"%{cp}%"

        params.extend([likep, likep])



    where_sql = ("WHERE " + " AND ".join(where)) if where else ""



    total = con.execute(
        f"SELECT COUNT(*) AS n FROM quotes q {client_join} {status_join} {where_sql}",
        tuple(params),
    ).fetchone()["n"]


    pago_expr = "q.metodo_pago" if has_mp else "'' AS metodo_pago"

    estado_expr = "q.estado" if has_estado else "'' AS estado"



    rows = con.execute(

        f"""

        SELECT

            q.id,
            q.created_at,
            q.quote_no,
            {cliente_expr} AS cliente,
            {cedula_expr} AS cedula,
            {telefono_expr} AS telefono,
            {estado_expr},
            {pago_expr},
            q.total_neto_shown AS total_shown,
            q.currency_shown,
            q.pdf_path,
            q.deleted_at,
            (SELECT COUNT(*) FROM quote_items qi WHERE qi.quote_id = q.id) AS items_count
        FROM quotes q
        {client_join}
        {status_join}
        {where_sql}
        ORDER BY q.created_at DESC
        LIMIT ? OFFSET ?
        """,
        tuple(params + [int(limit), int(offset)]),

    ).fetchall()



    return [dict(r) for r in rows], int(total)





def get_quote_header(con: sqlite3.Connection, quote_id: int) -> dict:
    if _has_column(con, "quotes", "id_cliente") and _table_exists(con, "clients"):
        client_direccion_expr = "COALESCE(c.direccion, '') AS client_direccion" if _has_column(con, "clients", "direccion") else "'' AS client_direccion"
        client_email_expr = "COALESCE(c.email, '') AS client_email" if _has_column(con, "clients", "email") else "'' AS client_email"
        sql = f"""
            SELECT
                q.*,
                COALESCE(c.nombre, '') AS client_nombre,
                COALESCE(c.documento, '') AS client_documento,
                COALESCE(c.tipo_documento, '') AS client_tipo_documento,
                COALESCE(c.telefono, '') AS client_telefono,
                {client_direccion_expr},
                {client_email_expr}
            FROM quotes q
            LEFT JOIN clients c ON c.id = q.id_cliente
            WHERE q.id = ?
            LIMIT 1
            """
        r = con.execute(
            sql,
            (int(quote_id),),
        ).fetchone()
    else:
        r = con.execute("SELECT * FROM quotes WHERE id = ?", (int(quote_id),)).fetchone()
    if not r:
        raise KeyError(f"Cotización no encontrada: {quote_id}")
    out = dict(r)
    if "client_nombre" in out:
        out["cliente"] = str(out.pop("client_nombre") or "")
    if "client_documento" in out:
        out["cedula"] = str(out.pop("client_documento") or "")
    if "client_tipo_documento" in out:
        out["tipo_documento"] = str(out.pop("client_tipo_documento") or "")
    if "client_telefono" in out:
        out["telefono"] = str(out.pop("client_telefono") or "")
    if "client_direccion" in out:
        out["direccion"] = str(out.pop("client_direccion") or "")
    if "client_email" in out:
        out["email"] = str(out.pop("client_email") or "")
    if not str(out.get("direccion") or "").strip():
        out["direccion"] = "-"
    if not str(out.get("email") or "").strip():
        out["email"] = "-"
    return out



def get_quote_items(con: sqlite3.Connection, quote_id: int) -> tuple[list[dict], list[dict]]:
    has_price_id = _has_column(con, "quote_items", "id_precioventa")
    has_tipo_prod = _has_column(con, "quote_items", "tipo_prod")
    rows = con.execute(
        "SELECT * FROM quote_items WHERE quote_id = ? ORDER BY id ASC",
        (int(quote_id),),
    ).fetchall()


    base_items: list[dict] = []

    shown_items: list[dict] = []



    for r in rows:
        d = dict(r)
        categoria = d.get("categoria", "")
        tipo_prod = _resolve_tipo_prod(
            categoria=categoria,
            explicit_tipo=(d.get("tipo_prod", None) if has_tipo_prod else None),
        )
        precio_base = float(d.get("precio_base", 0.0) or 0.0)
        precio_override_raw = d.get("precio_override_base", None)
        price_id = _resolve_price_id(
            categoria=categoria,
            tipo_prod=tipo_prod,
            explicit_id=(d.get("id_precioventa", None) if has_price_id else None),
            precio_tier=d.get("precio_tier", None),
            precio_override=precio_override_raw,
        )
        if tipo_prod == TIPO_PROD_SERV:
            precio_override = precio_override_raw if precio_override_raw is not None else precio_base
            precio_tier = None
        else:
            precio_override = None
            precio_tier = _tier_from_price_id(price_id)

        base_items.append({
            "codigo": d.get("codigo", ""),
            "producto": d.get("producto", ""),
            "categoria": categoria,
            "tipo_prod": tipo_prod,
            "fragancia": d.get("fragancia", ""),
            "observacion": d.get("observacion", ""),
            "cantidad": d.get("cantidad", 0.0),

            "precio": precio_base,
            "subtotal_base": d.get("subtotal_base", 0.0),

            "descuento_mode": d.get("descuento_mode") or None,
            "descuento_pct": d.get("descuento_pct", 0.0),
            "descuento_monto": d.get("descuento_monto_base", 0.0),
            "total": d.get("total_base", 0.0),

            "precio_override": precio_override,
            "precio_tier": precio_tier,
            "id_precioventa": int(price_id),
        })


        shown_items.append({

            "codigo": d.get("codigo", ""),
            "producto": d.get("producto", ""),
            "categoria": d.get("categoria", ""),
            "tipo_prod": tipo_prod,
            "fragancia": d.get("fragancia", ""),
            "observacion": d.get("observacion", ""),
            "cantidad": d.get("cantidad", 0.0),


            "precio": d.get("precio_shown", 0.0),

            "subtotal": d.get("subtotal_shown", 0.0),

            "descuento": d.get("descuento_monto_shown", 0.0),

            "total": d.get("total_shown", 0.0),

        })



    return base_items, shown_items



