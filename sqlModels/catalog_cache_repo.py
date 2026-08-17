from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator


_REVISION_RE = re.compile(r"^[0-9a-f]{64}$")


def _revision(value: Any, *, label: str, required: bool = True) -> str:
    revision = str(value or "").strip()
    if not revision:
        if required:
            raise ValueError(f"{label} es obligatoria.")
        return ""
    if _REVISION_RE.fullmatch(revision) is None:
        raise ValueError(f"{label} debe ser un SHA-256 hexadecimal valido.")
    return revision


CATALOG_CACHE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_owners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        id_cotizador TEXT NOT NULL,
        manifest_revision TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (username, id_cotizador)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_sync_state (
        owner_id INTEGER PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'never',
        last_attempt_at TEXT,
        last_success_at TEXT,
        last_error_at TEXT,
        last_error_message TEXT NOT NULL DEFAULT '',
        FOREIGN KEY (owner_id) REFERENCES catalog_cache_owners(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_id INTEGER NOT NULL,
        group_key TEXT NOT NULL,
        country_id TEXT NOT NULL DEFAULT '',
        country_code TEXT NOT NULL,
        country_name TEXT NOT NULL DEFAULT '',
        company_id TEXT NOT NULL DEFAULT '',
        company_type TEXT NOT NULL,
        base_currency TEXT NOT NULL DEFAULT '',
        catalog_revision TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (owner_id, group_key),
        UNIQUE (owner_id, country_code, company_type),
        FOREIGN KEY (owner_id) REFERENCES catalog_cache_owners(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_stores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        id_tienda INTEGER NOT NULL,
        code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        stock_revision TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE (group_id, id_tienda),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_departments (
        group_id INTEGER NOT NULL,
        key_norm TEXT NOT NULL,
        remote_id TEXT NOT NULL DEFAULT '',
        code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (group_id, key_norm),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_genders (
        group_id INTEGER NOT NULL,
        key_norm TEXT NOT NULL,
        remote_id TEXT NOT NULL DEFAULT '',
        code TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        data_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (group_id, key_norm),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_products (
        group_id INTEGER NOT NULL,
        codigo_norm TEXT NOT NULL,
        remote_id TEXT NOT NULL DEFAULT '',
        codigo TEXT NOT NULL,
        nombre TEXT NOT NULL DEFAULT '',
        categoria TEXT NOT NULL DEFAULT '',
        departamento TEXT NOT NULL DEFAULT '',
        genero TEXT NOT NULL DEFAULT '',
        ml TEXT NOT NULL DEFAULT '',
        p_max REAL NOT NULL DEFAULT 0,
        p_min REAL NOT NULL DEFAULT 0,
        p_oferta REAL NOT NULL DEFAULT 0,
        precio_venta INTEGER NOT NULL DEFAULT 1,
        data_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (group_id, codigo_norm),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_presentations (
        group_id INTEGER NOT NULL,
        codigo_norm TEXT NOT NULL,
        departamento TEXT NOT NULL DEFAULT '',
        genero TEXT NOT NULL DEFAULT '',
        remote_id TEXT NOT NULL DEFAULT '',
        codigo TEXT NOT NULL,
        nombre TEXT NOT NULL DEFAULT '',
        descripcion TEXT NOT NULL DEFAULT '',
        p_max REAL NOT NULL DEFAULT 0,
        p_min REAL NOT NULL DEFAULT 0,
        p_oferta REAL NOT NULL DEFAULT 0,
        requiere_botella INTEGER NOT NULL DEFAULT 0,
        data_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (group_id, codigo_norm, departamento, genero),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_presentation_products (
        group_id INTEGER NOT NULL,
        cod_producto_norm TEXT NOT NULL,
        cod_presentacion_norm TEXT NOT NULL,
        departamento TEXT NOT NULL DEFAULT '',
        genero TEXT NOT NULL DEFAULT '',
        cod_producto TEXT NOT NULL,
        cod_presentacion TEXT NOT NULL,
        cantidad REAL NOT NULL DEFAULT 0,
        data_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (
            group_id,
            cod_producto_norm,
            cod_presentacion_norm,
            departamento,
            genero
        ),
        FOREIGN KEY (group_id) REFERENCES catalog_cache_groups(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE TABLE IF NOT EXISTS catalog_cache_store_stock (
        store_id INTEGER NOT NULL,
        codigo_norm TEXT NOT NULL,
        codigo TEXT NOT NULL,
        cantidad REAL NOT NULL,
        PRIMARY KEY (store_id, codigo_norm),
        FOREIGN KEY (store_id) REFERENCES catalog_cache_stores(id) ON DELETE CASCADE
    ) WITHOUT ROWID
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_catalog_cache_groups_owner
    ON catalog_cache_groups(owner_id, country_code, company_type)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_catalog_cache_stores_group
    ON catalog_cache_stores(group_id, id_tienda)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_catalog_cache_products_code
    ON catalog_cache_products(codigo_norm)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_catalog_cache_stock_code
    ON catalog_cache_store_stock(codigo_norm)
    """,
)


_BASE_CURRENCY_BY_COUNTRY = {
    "BO": "BOB",
    "PE": "PEN",
    "PY": "PYG",
    "VE": "USD",
}
_MISSING = object()


def ensure_catalog_cache_schema(con: sqlite3.Connection) -> None:
    """Crea el esquema final de caché; se usa también desde la migración."""
    for statement in CATALOG_CACHE_DDL:
        con.execute(statement)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _code(value: Any) -> str:
    return _text(value).upper()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(str(value or "{}"))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _number(
    value: Any,
    *,
    default: float = 0.0,
    label: str = "valor numerico",
) -> float:
    if value is None or value is _MISSING:
        return float(default)
    if isinstance(value, bool):
        raise ValueError(f"{label} debe ser numerico.")
    raw = str(value).strip()
    if not raw:
        raise ValueError(f"{label} debe ser numerico.")
    try:
        parsed = float(raw.replace(",", "."))
    except (TypeError, ValueError):
        raise ValueError(f"{label} debe ser numerico.") from None
    if not math.isfinite(parsed):
        raise ValueError(f"{label} debe ser finito.")
    return parsed


def _integer(
    value: Any,
    *,
    default: int = 0,
    label: str = "valor entero",
) -> int:
    if value is None or value is _MISSING:
        return int(default)
    if isinstance(value, bool):
        raise ValueError(f"{label} debe ser entero.")
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{label} debe ser entero.") from None
    if not math.isfinite(parsed) or not parsed.is_integer():
        raise ValueError(f"{label} debe ser entero.")
    return int(parsed)


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _flag(value: Any, *, default: bool, label: str = "indicador") -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{label} debe ser booleano.")
    normalized = _text(value).lower()
    if normalized in {"1", "true", "yes", "si", "sí"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{label} debe ser booleano.")


@contextmanager
def _atomic(con: sqlite3.Connection) -> Iterator[None]:
    """Transacción propia o SAVEPOINT cuando el llamador ya abrió una."""
    if con.in_transaction:
        savepoint = f"catalog_cache_{uuid.uuid4().hex}"
        con.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
            con.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            con.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            con.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        return

    con.execute("BEGIN")
    try:
        yield
        con.commit()
    except Exception:
        con.rollback()
        raise


def _identity(username: str, id_cotizador: str) -> tuple[str, str]:
    user = _text(username)
    cotizador = _text(id_cotizador)
    if not user:
        raise ValueError("username es obligatorio para la caché remota.")
    if not cotizador:
        raise ValueError("id_cotizador es obligatorio para la caché remota.")
    return user, cotizador


def _owner_id(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    *,
    create: bool,
) -> int | None:
    user, cotizador = _identity(username, id_cotizador)
    row = con.execute(
        """
        SELECT id
        FROM catalog_cache_owners
        WHERE username = ? AND id_cotizador = ?
        """,
        (user, cotizador),
    ).fetchone()
    if row is not None:
        return int(row["id"])
    if not create:
        return None
    cur = con.execute(
        """
        INSERT INTO catalog_cache_owners(username, id_cotizador)
        VALUES(?, ?)
        """,
        (user, cotizador),
    )
    owner_id = int(cur.lastrowid)
    con.execute(
        """
        INSERT INTO catalog_cache_sync_state(owner_id, status)
        VALUES(?, 'never')
        """,
        (owner_id,),
    )
    return owner_id


def _response_body(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("La respuesta de catálogo debe ser un objeto JSON.")
    nested = payload.get("data")
    if isinstance(nested, dict) and "groups" in nested:
        return nested
    return payload


def _required_list(
    mapping: dict[str, Any],
    aliases: tuple[str, ...],
    *,
    label: str,
) -> list[Any]:
    for key in aliases:
        if key in mapping:
            value = mapping[key]
            if not isinstance(value, list):
                raise ValueError(f"{label} debe ser una lista.")
            return value
    raise ValueError(f"Falta {label} en el catálogo completo.")


def _normalize_named_rows(rows: list[Any], *, label: str) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError(f"Cada elemento de {label} debe ser un objeto.")
        remote_id = _text(_first(raw, "id", f"id_{label[:-1]}", "remote_id"))
        code = _text(_first(raw, "code", "codigo", "abreviatura"))
        name = _text(_first(raw, "name", "nombre", "descripcion"))
        key_norm = _code(code or remote_id or name)
        if not key_norm:
            raise ValueError(f"Un elemento de {label} no tiene identidad.")
        if key_norm in result:
            raise ValueError(f"{label} repite la identidad {key_norm}.")
        result[key_norm] = {
            "key_norm": key_norm,
            "remote_id": remote_id,
            "code": code,
            "name": name,
            "data_json": _json_dump(raw),
        }
    return list(result.values())


def _normalize_products(rows: list[Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Cada producto debe ser un objeto.")
        codigo = _text(_first(raw, "codigo", "code", "sku", "codigo_base"))
        codigo_norm = _code(codigo)
        if not codigo_norm:
            raise ValueError("Un producto del catálogo no tiene código.")
        if codigo_norm in result:
            raise ValueError(f"El catalogo repite el producto {codigo_norm}.")
        nombre = _text(_first(raw, "nombre", "name", "producto", "descripcion"))
        if not nombre:
            raise ValueError(f"El producto {codigo_norm} no tiene nombre valido.")
        precio_venta = _integer(
            _first(
                raw,
                "precio_venta",
                "id_precioventa",
                "price_type_id",
                default=_MISSING,
            ),
            default=1,
            label=f"precio_venta de {codigo_norm}",
        )
        if precio_venta not in (1, 2, 3):
            raise ValueError(f"precio_venta de {codigo_norm} debe ser 1, 2 o 3.")
        result[codigo_norm] = {
            "codigo_norm": codigo_norm,
            "remote_id": _text(_first(raw, "id", "id_producto", "remote_id")),
            "codigo": codigo,
            "nombre": nombre,
            "categoria": _text(_first(raw, "categoria", "category")),
            "departamento": _text(_first(raw, "departamento", "department")),
            "genero": _text(_first(raw, "genero", "gender")),
            "ml": _text(_first(raw, "ml", "mililitros")),
            "p_max": _number(
                _first(raw, "p_max", "precio_max", "precio_maximo", "precio1", default=_MISSING),
                label=f"p_max de {codigo_norm}",
            ),
            "p_min": _number(
                _first(raw, "p_min", "precio_min", "precio_minimo", "precio2", default=_MISSING),
                label=f"p_min de {codigo_norm}",
            ),
            "p_oferta": _number(
                _first(raw, "p_oferta", "precio_oferta", "precio3", default=_MISSING),
                label=f"p_oferta de {codigo_norm}",
            ),
            "precio_venta": precio_venta,
            "data_json": _json_dump(raw),
        }
    return list(result.values())


def _normalize_presentations(rows: list[Any]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Cada presentación debe ser un objeto.")
        codigo = _text(_first(raw, "codigo", "code", "sku"))
        presentation_key = _text(_first(raw, "presentation_key", "clave_presentacion"))
        codigo_norm = _code(presentation_key or codigo)
        if not codigo or not codigo_norm:
            raise ValueError("Una presentación del catálogo no tiene código.")
        departamento = _text(
            _first(raw, "departamento", "department", "id_departamento")
        )
        genero = _text(_first(raw, "genero", "gender", "id_genero"))
        key = (codigo_norm, departamento, genero)
        if key in result:
            raise ValueError(f"El catalogo repite la presentacion {codigo_norm}.")
        nombre = _text(_first(raw, "nombre", "name"))
        if not nombre:
            raise ValueError(f"La presentacion {codigo_norm} no tiene nombre valido.")
        price_type_raw = _first(
            raw,
            "precio_venta",
            "id_precioventa",
            "price_type_id",
            default=_MISSING,
        )
        if price_type_raw is not _MISSING:
            price_type = _integer(
                price_type_raw,
                default=1,
                label=f"precio_venta de {codigo_norm}",
            )
            if price_type not in (1, 2, 3):
                raise ValueError(f"precio_venta de {codigo_norm} debe ser 1, 2 o 3.")
        result[key] = {
            "codigo_norm": codigo_norm,
            "departamento": departamento,
            "genero": genero,
            "remote_id": _text(_first(raw, "id", "id_presentacion", "remote_id")),
            "codigo": codigo,
            "nombre": nombre,
            "descripcion": _text(_first(raw, "descripcion", "description")),
            "p_max": _number(
                _first(raw, "p_max", "precio_max", "precio_maximo", "precio1", default=_MISSING),
                label=f"p_max de {codigo_norm}",
            ),
            "p_min": _number(
                _first(raw, "p_min", "precio_min", "precio_minimo", "precio2", default=_MISSING),
                label=f"p_min de {codigo_norm}",
            ),
            "p_oferta": _number(
                _first(raw, "p_oferta", "precio_oferta", "precio3", default=_MISSING),
                label=f"p_oferta de {codigo_norm}",
            ),
            "requiere_botella": 1
            if _flag(
                _first(raw, "requiere_botella", "requires_bottle"),
                default=False,
                label=f"requiere_botella de {codigo_norm}",
            )
            else 0,
            "data_json": _json_dump(raw),
        }
    return list(result.values())


def _normalize_relations(rows: list[Any]) -> list[dict[str, Any]]:
    result: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("Cada relación de presentación debe ser un objeto.")
        product_code = _text(
            _first(raw, "cod_producto", "codigo_producto", "product_code", "producto_codigo")
        )
        presentation_code = _text(
            _first(
                raw,
                "cod_presentacion",
                "codigo_presentacion",
                "presentation_code",
                "presentacion_codigo",
            )
        )
        product_norm = _code(product_code)
        presentation_key = _text(_first(raw, "presentation_key", "clave_presentacion"))
        presentation_norm = _code(presentation_key or presentation_code)
        if not product_norm or not presentation_norm:
            raise ValueError("Una relación de presentación no tiene ambos códigos.")
        departamento = _text(
            _first(raw, "departamento", "department", "id_departamento")
        )
        genero = _text(_first(raw, "genero", "gender", "id_genero"))
        key = (product_norm, presentation_norm, departamento, genero)
        if key in result:
            raise ValueError(
                f"El catalogo repite la relacion {presentation_norm}/{product_norm}."
            )
        result[key] = {
            "cod_producto_norm": product_norm,
            "cod_presentacion_norm": presentation_norm,
            "departamento": departamento,
            "genero": genero,
            "cod_producto": product_code,
            "cod_presentacion": presentation_code,
            "cantidad": _number(
                _first(raw, "cantidad", "quantity", default=_MISSING),
                label=f"cantidad de {presentation_norm}/{product_norm}",
            ),
            "data_json": _json_dump(raw),
        }
    return list(result.values())


def _normalize_stock_items(rows: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Un elemento inválido invalida el snapshot completo y conserva el anterior."""
    if not isinstance(rows, list):
        return False, []
    aggregated: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            return False, []
        codigo = _text(_first(raw, "codigo", "code", "sku", "codigo_base"))
        codigo_norm = _code(codigo)
        quantity = _first(
            raw,
            "cantidad",
            "quantity",
            "existdisp",
            "cantidad_disponible",
            "existencia",
        )
        if not codigo_norm or quantity is None or isinstance(quantity, bool):
            return False, []
        try:
            parsed = float(str(quantity).strip().replace(",", "."))
        except (TypeError, ValueError):
            return False, []
        if not math.isfinite(parsed):
            return False, []
        if codigo_norm in aggregated:
            aggregated[codigo_norm]["cantidad"] += parsed
        else:
            aggregated[codigo_norm] = {
                "codigo_norm": codigo_norm,
                "codigo": codigo,
                "cantidad": parsed,
            }
    return True, list(aggregated.values())


def _normalize_catalog(catalog: Any) -> dict[str, Any]:
    if catalog is None:
        raise ValueError("Falta catalog en un grupo del manifiesto.")
    if not isinstance(catalog, dict):
        raise ValueError("catalog debe ser un objeto.")
    has_content = any(
        key in catalog
        for key in (
            "products",
            "productos",
            "departments",
            "departamentos",
            "genders",
            "generos",
            "presentations",
            "presentaciones",
            "presentation_products",
            "relations",
            "presentacion_prod",
        )
    )
    if "changed" not in catalog:
        raise ValueError("catalog.changed es obligatorio.")
    changed = _flag(
        catalog.get("changed"),
        default=has_content,
        label="catalog.changed",
    )
    revision = _revision(
        _first(catalog, "revision", "catalog_revision"),
        label="catalog.revision",
    )
    if not changed and has_content:
        raise ValueError("catalog.changed=false no puede incluir contenido de catalogo.")
    normalized: dict[str, Any] = {
        "changed": changed,
        "revision": revision,
    }
    if not changed:
        return normalized

    normalized["products"] = _normalize_products(
        _required_list(catalog, ("products", "productos"), label="products")
    )
    normalized["departments"] = _normalize_named_rows(
        _required_list(catalog, ("departments", "departamentos"), label="departments"),
        label="departments",
    )
    normalized["genders"] = _normalize_named_rows(
        _required_list(catalog, ("genders", "generos"), label="genders"),
        label="genders",
    )
    normalized["presentations"] = _normalize_presentations(
        _required_list(catalog, ("presentations", "presentaciones"), label="presentations")
    )
    normalized["presentation_products"] = _normalize_relations(
        _required_list(
            catalog,
            ("presentation_products", "relations", "presentacion_prod"),
            label="presentation_products",
        )
    )
    product_codes = {
        str(product["codigo_norm"])
        for product in normalized["products"]
    }
    presentation_codes = {
        str(presentation["codigo_norm"])
        for presentation in normalized["presentations"]
    }
    for relation in normalized["presentation_products"]:
        product_code = str(relation["cod_producto_norm"])
        presentation_code = str(relation["cod_presentacion_norm"])
        if product_code not in product_codes:
            raise ValueError(
                f"La relacion referencia el producto ausente {product_code}."
            )
        if presentation_code not in presentation_codes:
            raise ValueError(
                "La relacion referencia una presentacion ausente: "
                f"{presentation_code}."
            )
    return normalized


def _normalize_store(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Cada tienda debe ser un objeto.")
    id_tienda = _integer(
        _first(raw, "id_tienda", "store_id", "id"),
        label="id_tienda",
    )
    if id_tienda == 0:
        raise ValueError("Una tienda asignada no tiene id_tienda válido.")

    stock_raw = raw.get("stock")
    if isinstance(stock_raw, list):
        stock_obj: dict[str, Any] = {"changed": True, "items": stock_raw}
    elif stock_raw is None:
        stock_obj = {}
    elif isinstance(stock_raw, dict):
        stock_obj = stock_raw
    else:
        stock_obj = {"changed": True, "items": None}

    direct_items_present = "items" in raw or "stock_items" in raw
    nested_items_present = any(key in stock_obj for key in ("items", "stock", "stockprod"))
    stock_changed = _flag(
        _first(stock_obj, "changed", default=raw.get("stock_changed")),
        default=direct_items_present or nested_items_present,
        label=f"stock.changed de tienda {id_tienda}",
    )
    stock_revision = _revision(
        _first(
            stock_obj,
            "revision",
            "stock_revision",
            default=raw.get("stock_revision"),
        ),
        label=f"stock.revision de tienda {id_tienda}",
        required=False,
    )
    stock_valid = False
    stock_items: list[dict[str, Any]] = []
    if not stock_changed and (direct_items_present or nested_items_present):
        raise ValueError(
            f"stock.changed=false de tienda {id_tienda} no puede incluir items."
        )
    if stock_changed:
        explicit_valid = _first(
            stock_obj,
            "valid",
            "stock_valid",
            "available",
            default=raw.get("stock_valid"),
        )
        if explicit_valid is not None and not _flag(
            explicit_valid,
            default=False,
            label=f"stock.valid de tienda {id_tienda}",
        ):
            stock_valid = False
        else:
            items = _first(
                stock_obj,
                "items",
                "stock",
                "stockprod",
                default=_first(raw, "items", "stock_items"),
            )
            stock_valid, stock_items = _normalize_stock_items(items)
        if stock_valid and not stock_revision:
            raise ValueError(
                f"stock.revision de tienda {id_tienda} es obligatoria cuando changed=true."
            )

    return {
        "id_tienda": id_tienda,
        "code": _text(_first(raw, "code", "codigo", "store_code")),
        "name": _text(_first(raw, "name", "nombre", "store_name")),
        "stock_changed": stock_changed,
        "stock_revision": stock_revision,
        "stock_valid": stock_valid,
        "stock_items": stock_items,
    }


def _normalize_group(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Cada grupo debe ser un objeto.")
    country_raw = raw.get("country") if isinstance(raw.get("country"), dict) else {}
    company_raw = raw.get("company") if isinstance(raw.get("company"), dict) else {}

    country_id = _text(_first(country_raw, "id", "id_pais", default=raw.get("country_id")))
    country_code = _code(
        _first(country_raw, "code", "codigo", "cod_pais", default=raw.get("country_code"))
    )
    country_name = _text(
        _first(country_raw, "name", "nombre", default=raw.get("country_name"))
    )
    company_id = _text(
        _first(company_raw, "id", "id_empresa", default=raw.get("company_id"))
    )
    company_type = _text(
        _first(
            company_raw,
            "name",
            "nombre",
            "type",
            default=_first(raw, "company_type", "company_name"),
        )
    )
    if not country_code or not company_type:
        raise ValueError("Cada grupo requiere país y empresa.")

    group_key = _text(_first(raw, "group_key", "key"))
    if not group_key:
        group_key = (
            f"{country_id}:{company_id}"
            if country_id and company_id
            else f"{country_code}:{company_type}"
        )

    manifest_currency = _code(
        _first(
            raw,
            "base_currency",
            default=_first(country_raw, "base_currency", "currency"),
        )
    )
    country_currency = _BASE_CURRENCY_BY_COUNTRY.get(country_code, "")

    # La moneda base de Bolivia es BOB. Algunos manifiestos antiguos enviaban
    # PYG para este grupo, lo que hacía que el cotizador mostrara guaraníes aun
    # cuando el país seleccionado era Bolivia.
    if country_code == "BO":
        base_currency = country_currency
    else:
        base_currency = manifest_currency or country_currency

    stores_raw = _first(raw, "stores", "shops", "tiendas")
    if not isinstance(stores_raw, list):
        raise ValueError(f"stores debe ser una lista en el grupo {group_key}.")
    stores = [_normalize_store(store) for store in stores_raw]
    store_ids = [int(store["id_tienda"]) for store in stores]
    if len(store_ids) != len(set(store_ids)):
        raise ValueError(f"El grupo {group_key} repite una tienda.")

    return {
        "group_key": group_key,
        "country_id": country_id,
        "country_code": country_code,
        "country_name": country_name,
        "company_id": company_id,
        "company_type": company_type,
        "base_currency": base_currency,
        "catalog": _normalize_catalog(raw.get("catalog")),
        "stores": stores,
    }


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    body = _response_body(payload)
    manifest_revision = _revision(
        _first(body, "manifest_revision", "revision", "manifestRevision"),
        label="manifest_revision",
    )
    groups_raw = _first(body, "groups", "scopes", "grupos")
    if not isinstance(groups_raw, list):
        raise ValueError("La respuesta no contiene un manifiesto groups válido.")
    groups = [_normalize_group(group) for group in groups_raw]
    group_keys = [str(group["group_key"]) for group in groups]
    if len(group_keys) != len(set(group_keys)):
        raise ValueError("El manifiesto repite un group_key.")
    logical_keys = [
        (str(group["country_code"]).upper(), str(group["company_type"]).upper())
        for group in groups
    ]
    if len(logical_keys) != len(set(logical_keys)):
        raise ValueError("El manifiesto repite un grupo país+empresa.")
    manifest_changed_raw = _first(
        body,
        "manifest_changed",
        "manifestChanged",
        default=_MISSING,
    )
    manifest_changed = (
        None
        if manifest_changed_raw is _MISSING
        else _flag(
            manifest_changed_raw,
            default=False,
            label="manifest_changed",
        )
    )
    return {
        "manifest_revision": manifest_revision,
        "manifest_changed": manifest_changed,
        "groups": groups,
    }


def build_known_state(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
) -> dict[str, Any]:
    owner_id = _owner_id(con, username, id_cotizador, create=False)
    if owner_id is None:
        return {"manifest_revision": "", "known_groups": []}
    owner = con.execute(
        "SELECT manifest_revision FROM catalog_cache_owners WHERE id = ?",
        (owner_id,),
    ).fetchone()
    groups = con.execute(
        """
        SELECT id, group_key, catalog_revision
        FROM catalog_cache_groups
        WHERE owner_id = ?
        ORDER BY group_key
        """,
        (owner_id,),
    ).fetchall()
    known_groups: list[dict[str, Any]] = []
    for group in groups:
        stores = con.execute(
            """
            SELECT id_tienda, stock_revision
            FROM catalog_cache_stores
            WHERE group_id = ?
            ORDER BY id_tienda
            """,
            (int(group["id"]),),
        ).fetchall()
        known_groups.append(
            {
                "group_key": str(group["group_key"]),
                "catalog_revision": str(group["catalog_revision"] or ""),
                "stores": [
                    {
                        "id_tienda": int(store["id_tienda"]),
                        "stock_revision": str(store["stock_revision"] or ""),
                    }
                    for store in stores
                ],
            }
        )
    return {
        "manifest_revision": str(owner["manifest_revision"] or "") if owner else "",
        "known_groups": known_groups,
    }


def _replace_catalog(
    con: sqlite3.Connection,
    group_id: int,
    catalog: dict[str, Any],
) -> None:
    for table in (
        "catalog_cache_presentation_products",
        "catalog_cache_presentations",
        "catalog_cache_products",
        "catalog_cache_departments",
        "catalog_cache_genders",
    ):
        con.execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))

    con.executemany(
        """
        INSERT INTO catalog_cache_products(
            group_id, codigo_norm, remote_id, codigo, nombre, categoria,
            departamento, genero, ml, p_max, p_min, p_oferta,
            precio_venta, data_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                group_id,
                item["codigo_norm"],
                item["remote_id"],
                item["codigo"],
                item["nombre"],
                item["categoria"],
                item["departamento"],
                item["genero"],
                item["ml"],
                item["p_max"],
                item["p_min"],
                item["p_oferta"],
                item["precio_venta"],
                item["data_json"],
            )
            for item in catalog["products"]
        ],
    )
    for table, items in (
        ("catalog_cache_departments", catalog["departments"]),
        ("catalog_cache_genders", catalog["genders"]),
    ):
        con.executemany(
            f"""
            INSERT INTO {table}(
                group_id, key_norm, remote_id, code, name, data_json
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    group_id,
                    item["key_norm"],
                    item["remote_id"],
                    item["code"],
                    item["name"],
                    item["data_json"],
                )
                for item in items
            ],
        )
    con.executemany(
        """
        INSERT INTO catalog_cache_presentations(
            group_id, codigo_norm, departamento, genero, remote_id,
            codigo, nombre, descripcion, p_max, p_min, p_oferta,
            requiere_botella, data_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                group_id,
                item["codigo_norm"],
                item["departamento"],
                item["genero"],
                item["remote_id"],
                item["codigo"],
                item["nombre"],
                item["descripcion"],
                item["p_max"],
                item["p_min"],
                item["p_oferta"],
                item["requiere_botella"],
                item["data_json"],
            )
            for item in catalog["presentations"]
        ],
    )
    con.executemany(
        """
        INSERT INTO catalog_cache_presentation_products(
            group_id, cod_producto_norm, cod_presentacion_norm,
            departamento, genero, cod_producto, cod_presentacion,
            cantidad, data_json
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                group_id,
                item["cod_producto_norm"],
                item["cod_presentacion_norm"],
                item["departamento"],
                item["genero"],
                item["cod_producto"],
                item["cod_presentacion"],
                item["cantidad"],
                item["data_json"],
            )
            for item in catalog["presentation_products"]
        ],
    )


def _upsert_group(
    con: sqlite3.Connection,
    owner_id: int,
    group: dict[str, Any],
    synced_at: str,
) -> int:
    catalog = group["catalog"]
    existing = con.execute(
        """
        SELECT id, catalog_revision
        FROM catalog_cache_groups
        WHERE owner_id = ? AND group_key = ?
        """,
        (owner_id, group["group_key"]),
    ).fetchone()
    current_revision = str(existing["catalog_revision"] or "") if existing else ""
    revision_changed = str(catalog["revision"]) != current_revision
    if existing is None and not catalog["changed"]:
        raise ValueError(
            f"El grupo nuevo {group['group_key']} requiere catalog.changed=true."
        )
    if existing is not None and bool(catalog["changed"]) != revision_changed:
        state = "changed=true" if catalog["changed"] else "changed=false"
        raise ValueError(
            f"catalog.revision de {group['group_key']} no es coherente con {state}."
        )

    persisted_revision = catalog["revision"] if catalog["changed"] else ""
    con.execute(
        """
        INSERT INTO catalog_cache_groups(
            owner_id, group_key, country_id, country_code, country_name,
            company_id, company_type, base_currency, catalog_revision, updated_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(owner_id, group_key) DO UPDATE SET
            country_id = excluded.country_id,
            country_code = excluded.country_code,
            country_name = excluded.country_name,
            company_id = excluded.company_id,
            company_type = excluded.company_type,
            base_currency = excluded.base_currency,
            catalog_revision = CASE
                WHEN TRIM(excluded.catalog_revision) <> '' THEN excluded.catalog_revision
                ELSE catalog_cache_groups.catalog_revision
            END,
            updated_at = excluded.updated_at
        """,
        (
            owner_id,
            group["group_key"],
            group["country_id"],
            group["country_code"],
            group["country_name"],
            group["company_id"],
            group["company_type"],
            group["base_currency"],
            persisted_revision,
            synced_at,
        ),
    )
    row = con.execute(
        "SELECT id FROM catalog_cache_groups WHERE owner_id = ? AND group_key = ?",
        (owner_id, group["group_key"]),
    ).fetchone()
    if row is None:
        raise RuntimeError("No se pudo persistir el grupo de catálogo.")
    group_id = int(row["id"])
    if catalog["changed"]:
        _replace_catalog(con, group_id, catalog)
    return group_id


def _upsert_stores(
    con: sqlite3.Connection,
    group_id: int,
    stores: list[dict[str, Any]],
    synced_at: str,
) -> None:
    existing_rows = con.execute(
        """
        SELECT id, id_tienda, stock_revision
        FROM catalog_cache_stores
        WHERE group_id = ?
        """,
        (group_id,),
    ).fetchall()
    existing_by_store = {int(row["id_tienda"]): row for row in existing_rows}
    keep_ids: list[int] = []
    for store in stores:
        id_tienda = int(store["id_tienda"])
        keep_ids.append(id_tienda)
        existing = existing_by_store.get(id_tienda)
        current_revision = str(existing["stock_revision"] or "") if existing else ""
        incoming_revision = str(store["stock_revision"] or "")
        if store["stock_changed"] and store["stock_valid"]:
            if existing is not None and incoming_revision == current_revision:
                raise ValueError(
                    f"stock.revision de tienda {id_tienda} no cambio con changed=true."
                )
        elif not store["stock_changed"] and incoming_revision:
            if existing is None or incoming_revision != current_revision:
                raise ValueError(
                    f"stock.revision de tienda {id_tienda} no es coherente con changed=false."
                )
        # Un snapshot inválido no puede avanzar known_state. En un INSERT no
        # existe una revisión previa que conservar; en un UPDATE el CASE deja
        # intacta la existente.
        persisted_revision = (
            store["stock_revision"]
            if store["stock_changed"] and store["stock_valid"]
            else ""
        )
        con.execute(
            """
            INSERT INTO catalog_cache_stores(
                group_id, id_tienda, code, name, stock_revision, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(group_id, id_tienda) DO UPDATE SET
                code = excluded.code,
                name = excluded.name,
                stock_revision = CASE
                    WHEN ? THEN excluded.stock_revision
                    ELSE catalog_cache_stores.stock_revision
                END,
                updated_at = excluded.updated_at
            """,
            (
                group_id,
                id_tienda,
                store["code"],
                store["name"],
                persisted_revision,
                synced_at,
                1 if (store["stock_changed"] and store["stock_valid"]) else 0,
            ),
        )
        row = con.execute(
            """
            SELECT id
            FROM catalog_cache_stores
            WHERE group_id = ? AND id_tienda = ?
            """,
            (group_id, id_tienda),
        ).fetchone()
        if row is None:
            raise RuntimeError("No se pudo persistir una tienda asignada.")
        store_id = int(row["id"])
        if store["stock_changed"] and store["stock_valid"]:
            con.execute(
                "DELETE FROM catalog_cache_store_stock WHERE store_id = ?",
                (store_id,),
            )
            con.executemany(
                """
                INSERT INTO catalog_cache_store_stock(
                    store_id, codigo_norm, codigo, cantidad
                ) VALUES(?, ?, ?, ?)
                """,
                [
                    (
                        store_id,
                        item["codigo_norm"],
                        item["codigo"],
                        item["cantidad"],
                    )
                    for item in store["stock_items"]
                ],
            )

    if keep_ids:
        placeholders = ",".join("?" for _ in keep_ids)
        con.execute(
            f"""
            DELETE FROM catalog_cache_stores
            WHERE group_id = ? AND id_tienda NOT IN ({placeholders})
            """,
            (group_id, *keep_ids),
        )
    else:
        con.execute("DELETE FROM catalog_cache_stores WHERE group_id = ?", (group_id,))


def apply_sync_payload(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    payload: dict[str, Any],
    *,
    synced_at: str | None = None,
) -> dict[str, int]:
    """Valida primero y aplica manifiesto, catálogos y stocks de forma atómica."""
    user, cotizador = _identity(username, id_cotizador)
    normalized = _normalize_payload(payload)
    applied_at = _text(synced_at) or _now_iso()

    with _atomic(con):
        ensure_catalog_cache_schema(con)
        owner_id = _owner_id(con, user, cotizador, create=True)
        assert owner_id is not None
        owner_row = con.execute(
            "SELECT manifest_revision FROM catalog_cache_owners WHERE id = ?",
            (owner_id,),
        ).fetchone()
        current_manifest_revision = str(owner_row["manifest_revision"] or "")
        incoming_manifest_revision = str(normalized["manifest_revision"])
        manifest_revision_changed = incoming_manifest_revision != current_manifest_revision
        declared_manifest_changed = normalized.get("manifest_changed")
        if (
            declared_manifest_changed is not None
            and bool(declared_manifest_changed) != manifest_revision_changed
        ):
            raise ValueError(
                "manifest_revision no es coherente con manifest_changed."
            )
        if not manifest_revision_changed:
            cached_assignments: dict[str, list[int]] = {}
            for row in con.execute(
                """
                SELECT g.group_key, s.id_tienda
                FROM catalog_cache_groups g
                LEFT JOIN catalog_cache_stores s ON s.group_id = g.id
                WHERE g.owner_id = ?
                ORDER BY g.group_key, s.id_tienda
                """,
                (owner_id,),
            ).fetchall():
                stores = cached_assignments.setdefault(str(row["group_key"]), [])
                if row["id_tienda"] is not None:
                    stores.append(int(row["id_tienda"]))
            incoming_assignments = {
                str(group["group_key"]): sorted(
                    int(store["id_tienda"]) for store in group["stores"]
                )
                for group in normalized["groups"]
            }
            if cached_assignments != incoming_assignments:
                raise ValueError(
                    "manifest_revision no cambio aunque cambiaron grupos o tiendas."
                )
        keep_group_keys: list[str] = []
        for group in normalized["groups"]:
            keep_group_keys.append(str(group["group_key"]))
            group_id = _upsert_group(con, owner_id, group, applied_at)
            _upsert_stores(con, group_id, group["stores"], applied_at)

        if keep_group_keys:
            placeholders = ",".join("?" for _ in keep_group_keys)
            con.execute(
                f"""
                DELETE FROM catalog_cache_groups
                WHERE owner_id = ? AND group_key NOT IN ({placeholders})
                """,
                (owner_id, *keep_group_keys),
            )
        else:
            con.execute("DELETE FROM catalog_cache_groups WHERE owner_id = ?", (owner_id,))

        manifest_revision = str(normalized["manifest_revision"])
        con.execute(
            """
            UPDATE catalog_cache_owners
            SET manifest_revision = CASE
                    WHEN TRIM(?) <> '' THEN ?
                    ELSE manifest_revision
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (manifest_revision, manifest_revision, applied_at, owner_id),
        )
        con.execute(
            """
            INSERT INTO catalog_cache_sync_state(
                owner_id, status, last_attempt_at, last_success_at,
                last_error_at, last_error_message
            ) VALUES(?, 'success', ?, ?, NULL, '')
            ON CONFLICT(owner_id) DO UPDATE SET
                status = 'success',
                last_attempt_at = excluded.last_attempt_at,
                last_success_at = excluded.last_success_at,
                last_error_at = NULL,
                last_error_message = ''
            """,
            (owner_id, applied_at, applied_at),
        )

        counts_row = con.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM catalog_cache_groups WHERE owner_id = ?) AS scopes,
                (SELECT COUNT(*)
                 FROM catalog_cache_stores s
                 JOIN catalog_cache_groups g ON g.id = s.group_id
                 WHERE g.owner_id = ?) AS stores,
                (SELECT COUNT(*)
                 FROM catalog_cache_products p
                 JOIN catalog_cache_groups g ON g.id = p.group_id
                 WHERE g.owner_id = ?) AS products,
                (SELECT COUNT(*)
                 FROM catalog_cache_store_stock ss
                 JOIN catalog_cache_stores s ON s.id = ss.store_id
                 JOIN catalog_cache_groups g ON g.id = s.group_id
                 WHERE g.owner_id = ?) AS stock_items
            """,
            (owner_id, owner_id, owner_id, owner_id),
        ).fetchone()
        return {
            "scopes": int(counts_row["scopes"] or 0),
            "stores": int(counts_row["stores"] or 0),
            "products": int(counts_row["products"] or 0),
            "stock_items": int(counts_row["stock_items"] or 0),
        }


def record_sync_attempt(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    *,
    attempted_at: str | None = None,
) -> None:
    timestamp = _text(attempted_at) or _now_iso()
    with _atomic(con):
        ensure_catalog_cache_schema(con)
        owner_id = _owner_id(con, username, id_cotizador, create=True)
        assert owner_id is not None
        con.execute(
            """
            INSERT INTO catalog_cache_sync_state(owner_id, status, last_attempt_at)
            VALUES(?, 'syncing', ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                status = 'syncing',
                last_attempt_at = excluded.last_attempt_at
            """,
            (owner_id, timestamp),
        )


def record_sync_error(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    error_message: str,
    *,
    attempted_at: str | None = None,
) -> None:
    timestamp = _text(attempted_at) or _now_iso()
    message = _text(error_message)[:1000]
    with _atomic(con):
        ensure_catalog_cache_schema(con)
        owner_id = _owner_id(con, username, id_cotizador, create=True)
        assert owner_id is not None
        con.execute(
            """
            INSERT INTO catalog_cache_sync_state(
                owner_id, status, last_attempt_at, last_error_at, last_error_message
            ) VALUES(?, 'error', ?, ?, ?)
            ON CONFLICT(owner_id) DO UPDATE SET
                status = 'error',
                last_attempt_at = excluded.last_attempt_at,
                last_error_at = excluded.last_error_at,
                last_error_message = excluded.last_error_message
            """,
            (owner_id, timestamp, timestamp, message),
        )


def get_sync_state(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
) -> dict[str, Any]:
    owner_id = _owner_id(con, username, id_cotizador, create=False)
    if owner_id is None:
        return {
            "status": "never",
            "manifest_revision": "",
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error_message": "",
        }
    row = con.execute(
        """
        SELECT
            COALESCE(s.status, 'never') AS status,
            o.manifest_revision,
            s.last_attempt_at,
            s.last_success_at,
            s.last_error_at,
            COALESCE(s.last_error_message, '') AS last_error_message
        FROM catalog_cache_owners o
        LEFT JOIN catalog_cache_sync_state s ON s.owner_id = o.id
        WHERE o.id = ?
        """,
        (owner_id,),
    ).fetchone()
    return dict(row) if row else {
        "status": "never",
        "manifest_revision": "",
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error_at": None,
        "last_error_message": "",
    }


def list_scopes(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
) -> list[dict[str, Any]]:
    owner_id = _owner_id(con, username, id_cotizador, create=False)
    if owner_id is None:
        return []
    rows = con.execute(
        """
        SELECT
            g.group_key,
            g.country_id,
            g.country_code,
            g.country_name,
            g.company_id,
            g.company_type,
            g.base_currency,
            g.catalog_revision,
            COUNT(s.id) AS stores_count
        FROM catalog_cache_groups g
        JOIN catalog_cache_stores s ON s.group_id = g.id
        WHERE g.owner_id = ?
        GROUP BY g.id
        ORDER BY g.country_code, g.company_type
        """,
        (owner_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _find_group(
    con: sqlite3.Connection,
    owner_id: int,
    *,
    group_key: str | None,
    country_code: str | None,
    company_type: str | None,
) -> sqlite3.Row | None:
    key = _text(group_key)
    if key:
        return con.execute(
            "SELECT * FROM catalog_cache_groups WHERE owner_id = ? AND group_key = ?",
            (owner_id, key),
        ).fetchone()
    country = _code(country_code)
    company = _text(company_type)
    if not country or not company:
        raise ValueError("Indica group_key o el par country_code+company_type.")
    return con.execute(
        """
        SELECT *
        FROM catalog_cache_groups
        WHERE owner_id = ?
          AND UPPER(country_code) = ?
          AND UPPER(company_type) = UPPER(?)
        """,
        (owner_id, country, company),
    ).fetchone()


def _load_named_rows(
    con: sqlite3.Connection,
    table: str,
    group_id: int,
) -> list[dict[str, Any]]:
    rows = con.execute(
        f"SELECT * FROM {table} WHERE group_id = ? ORDER BY name, key_norm",
        (group_id,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        raw = _json_load(data.pop("data_json", "{}"))
        data.pop("group_id", None)
        raw.update(data)
        result.append(raw)
    return result


def load_scope_catalog(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    *,
    group_key: str | None = None,
    country_code: str | None = None,
    company_type: str | None = None,
) -> dict[str, Any] | None:
    owner_id = _owner_id(con, username, id_cotizador, create=False)
    if owner_id is None:
        return None
    group = _find_group(
        con,
        owner_id,
        group_key=group_key,
        country_code=country_code,
        company_type=company_type,
    )
    if group is None:
        return None
    group_data = dict(group)
    group_id = int(group_data.pop("id"))
    group_data.pop("owner_id", None)
    group_data.pop("updated_at", None)

    products: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT *
        FROM catalog_cache_products
        WHERE group_id = ?
        ORDER BY codigo_norm
        """,
        (group_id,),
    ).fetchall():
        data = dict(row)
        raw = _json_load(data.pop("data_json", "{}"))
        data.pop("group_id", None)
        raw.update(data)
        raw["id"] = raw.get("remote_id") or raw.get("codigo_norm")
        raw["cantidad_disponible"] = 0.0
        products.append(raw)

    presentations: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT *
        FROM catalog_cache_presentations
        WHERE group_id = ?
        ORDER BY codigo_norm, departamento, genero
        """,
        (group_id,),
    ).fetchall():
        data = dict(row)
        raw = _json_load(data.pop("data_json", "{}"))
        data.pop("group_id", None)
        raw.update(data)
        raw["stock_disponible"] = 0.0
        presentations.append(raw)

    relations: list[dict[str, Any]] = []
    for row in con.execute(
        """
        SELECT *
        FROM catalog_cache_presentation_products
        WHERE group_id = ?
        ORDER BY cod_presentacion_norm, cod_producto_norm, departamento, genero
        """,
        (group_id,),
    ).fetchall():
        data = dict(row)
        raw = _json_load(data.pop("data_json", "{}"))
        data.pop("group_id", None)
        raw.update(data)
        relations.append(raw)

    group_data.update(
        {
            "products": products,
            "departments": _load_named_rows(
                con, "catalog_cache_departments", group_id
            ),
            "genders": _load_named_rows(con, "catalog_cache_genders", group_id),
            "presentations": presentations,
            "presentation_products": relations,
            "relations": relations,
        }
    )
    return group_data


def load_stock_matrix(
    con: sqlite3.Connection,
    username: str,
    id_cotizador: str,
    *,
    group_key: str,
) -> dict[str, Any] | None:
    owner_id = _owner_id(con, username, id_cotizador, create=False)
    if owner_id is None:
        return None
    group = _find_group(
        con,
        owner_id,
        group_key=group_key,
        country_code=None,
        company_type=None,
    )
    if group is None:
        return None
    group_id = int(group["id"])
    stores = [
        dict(row)
        for row in con.execute(
            """
            SELECT id_tienda, code, name, stock_revision
            FROM catalog_cache_stores
            WHERE group_id = ?
            ORDER BY name, id_tienda
            """,
            (group_id,),
        ).fetchall()
    ]
    product_rows = con.execute(
        """
        SELECT codigo_norm, codigo, nombre, categoria, departamento, data_json
        FROM catalog_cache_products
        WHERE group_id = ?
        """,
        (group_id,),
    ).fetchall()
    products: dict[str, dict[str, Any]] = {}
    for row in product_rows:
        raw = _json_load(row["data_json"])
        category = str(row["categoria"] or row["departamento"] or "").strip()
        explicit_type = _text(
            _first(raw, "tipo_prod", "tipo_item", "item_type", "tipo", "type")
        )
        if not explicit_type:
            explicit_type = "Servicio" if "serv" in category.casefold() else "Producto"
        code_norm = str(row["codigo_norm"])
        products[code_norm] = {
            "codigo_norm": code_norm,
            "codigo": str(row["codigo"] or code_norm),
            "nombre": str(row["nombre"] or ""),
            "categoria": category,
            "item_type": explicit_type,
        }

    presentation_rows = con.execute(
        """
        SELECT codigo_norm, codigo, nombre, departamento
        FROM catalog_cache_presentations
        WHERE group_id = ?
        ORDER BY codigo_norm, departamento, genero
        """,
        (group_id,),
    ).fetchall()
    presentations_by_code: dict[str, dict[str, Any]] = {}
    for row in presentation_rows:
        code_norm = str(row["codigo_norm"])
        presentations_by_code.setdefault(
            code_norm,
            {
                "codigo_norm": code_norm,
                "codigo": str(row["codigo"] or code_norm),
                "nombre": str(row["nombre"] or ""),
                "categoria": str(row["departamento"] or "PRESENTACION").strip(),
                "item_type": "Presentación",
            },
        )
    quantities: dict[tuple[int, str], float] = {}
    stock_rows = con.execute(
        """
        SELECT s.id_tienda, ss.codigo_norm, ss.codigo, ss.cantidad
        FROM catalog_cache_store_stock ss
        JOIN catalog_cache_stores s ON s.id = ss.store_id
        WHERE s.group_id = ?
        """,
        (group_id,),
    ).fetchall()
    for row in stock_rows:
        code_norm = str(row["codigo_norm"])
        products.setdefault(
            code_norm,
            presentations_by_code.get(code_norm)
            or {
                "codigo_norm": code_norm,
                "codigo": str(row["codigo"] or code_norm),
                "nombre": "",
                "categoria": "",
                "item_type": "Otros",
            },
        )
        quantities[(int(row["id_tienda"]), code_norm)] = float(row["cantidad"])

    store_ids = [int(store["id_tienda"]) for store in stores]
    rows: list[dict[str, Any]] = []
    for code_norm in sorted(products):
        product = products[code_norm]
        stock_by_store = {
            str(store_id): quantities.get((store_id, code_norm))
            for store_id in store_ids
        }
        rows.append(
            {
                **product,
                "stocks": stock_by_store,
                "total_stock": float(
                    sum(value for value in stock_by_store.values() if value is not None)
                ),
            }
        )

    return {
        "group_key": str(group["group_key"]),
        "country_id": str(group["country_id"] or ""),
        "country_code": str(group["country_code"]),
        "country_name": str(group["country_name"] or ""),
        "company_id": str(group["company_id"] or ""),
        "company_type": str(group["company_type"]),
        "base_currency": str(group["base_currency"] or ""),
        "stores": stores,
        "rows": rows,
    }
