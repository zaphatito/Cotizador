from __future__ import annotations

import datetime as _datetime
import json
import math
import os
import re
import tempfile
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pandas as pd

from .country_rules import SUPPORTED_COUNTRIES, country_code_for, normalize_country_name
from .dataio import _leer_inventario_xlsx
from .presentations import cargar_presentaciones, cargar_presentaciones_prod
from .server_identity import (
    has_complete_server_identity,
    validate_functional_identity,
    validate_server_identity_pair,
)


INITIAL_SETUP_SCHEMA_VERSION = 1
SETUP_PENDING_FILENAME = "initial_configuration.pending.json"
SETUP_RECEIPT_FILENAME = "initial_configuration.receipt.json"
SETUP_REQUIRED_FILENAME = "initial_configuration.required"

_COMPANY_TYPES = frozenset({"LA CASA DEL PERFUME", "EF PERFUMES"})
_LISTING_TYPES = frozenset({"AMBOS", "PRODUCTOS", "PRESENTACIONES"})
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True, slots=True)
class SetupStoreAssignment:
    country: str
    company_type: str
    store_code: str
    store_name: str
    inventory_path: str = ""
    is_default: bool = False

    def normalized(self) -> "SetupStoreAssignment":
        country = normalize_country_name(self.country)
        company = str(self.company_type or "").strip().upper()
        store_code = str(self.store_code or "").strip().upper()
        store_name = str(self.store_name or "").strip()
        inventory_path = str(self.inventory_path or "").strip()
        if country not in SUPPORTED_COUNTRIES:
            raise ValueError(f"País no soportado: {self.country!r}.")
        if company not in _COMPANY_TYPES:
            raise ValueError(f"Empresa no soportada: {self.company_type!r}.")
        if _IDENTIFIER_RE.fullmatch(store_code) is None:
            raise ValueError(
                "El código de tienda debe tener 1-64 letras, números, punto, guion o guion bajo."
            )
        if not store_name:
            raise ValueError("El nombre de tienda es obligatorio.")
        if inventory_path:
            inventory_path = os.path.abspath(os.path.expandvars(inventory_path))
            if not os.path.isfile(inventory_path):
                raise ValueError(f"No existe el inventario de {store_code}: {inventory_path}")
            if Path(inventory_path).suffix.lower() not in {".xlsx", ".xlsm"}:
                raise ValueError(
                    f"El inventario de {store_code} debe ser .xlsx o .xlsm."
                )
        return SetupStoreAssignment(
            country=country,
            company_type=company,
            store_code=store_code,
            store_name=store_name,
            inventory_path=inventory_path,
            is_default=bool(self.is_default),
        )

    @property
    def scope_key(self) -> tuple[str, str]:
        return (normalize_country_name(self.country), str(self.company_type).strip().upper())


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    products: tuple[dict[str, Any], ...]
    presentations: tuple[dict[str, Any], ...]
    presentation_products: tuple[dict[str, Any], ...]
    departments: tuple[dict[str, str], ...]
    genders: tuple[dict[str, str], ...]
    stock_items: tuple[dict[str, Any], ...]
    source_name: str


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    normalized = _text(value).casefold()
    if normalized in {"1", "true", "yes", "si", "sí", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"Valor booleano inválido: {value!r}.")


def normalize_seed_config(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("La configuración inicial debe ser un objeto.")
    raw = dict(value)
    username = _text(
        raw.get("username") if "username" in raw else raw.get("user_name")
    )
    id_cotizador = _text(
        raw.get("id_cotizador") if "id_cotizador" in raw else raw.get("store_id")
    ).upper()
    server_mode = validate_server_identity_pair(username, id_cotizador)
    if server_mode and _IDENTIFIER_RE.fullmatch(id_cotizador) is None:
        raise ValueError(
            "El ID del cotizador debe tener 1-64 letras, números, punto, guion o guion bajo."
        )
    if server_mode:
        validate_functional_identity(username, id_cotizador)

    listing_type = _text(raw.get("listing_type") or "AMBOS").upper()
    if listing_type not in _LISTING_TYPES:
        raise ValueError("El tipo de listado debe ser AMBOS, PRODUCTOS o PRESENTACIONES.")

    return {
        "username": username,
        "id_cotizador": id_cotizador,
        "telemarketing": _bool(raw.get("telemarketing", raw.get("tienda"))),
        "listing_type": listing_type,
        "allow_no_stock": _bool(raw.get("allow_no_stock")),
        "enable_ai": _bool(raw.get("enable_ai")),
        "enable_recommendations": _bool(
            raw.get("enable_recommendations"),
            default=True,
        ),
        "update_mode": _text(raw.get("update_mode") or "SILENT").upper(),
        "update_check_on_startup": _bool(
            raw.get("update_check_on_startup"),
            default=True,
        ),
        "update_manifest_url": _text(raw.get("update_manifest_url")),
        "update_flags": _text(raw.get("update_flags") or "/CLOSEAPPLICATIONS"),
    }


def _require_columns(df: pd.DataFrame, columns: Iterable[str], *, label: str) -> None:
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise ValueError(f"{label}: faltan columnas {', '.join(missing)}.")


def _clean_products(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    required = (
        "CODIGO",
        "NOMBRE",
        "DEPARTAMENTO",
        "GENERO",
        "CANTIDAD_DISPONIBLE",
        "P_MAX",
        "P_MIN",
        "P_OFERTA",
        "PRECIO_VENTA",
    )
    _require_columns(df, required, label=f"Inventario {source_name}")
    products = df.loc[:, list(required)].copy()
    initial_rows = len(products)
    products = products.dropna(how="all").reset_index(drop=True)
    for column in ("CODIGO", "NOMBRE", "DEPARTAMENTO", "GENERO"):
        products.loc[:, column] = products[column].astype("string").fillna("").str.strip()
    products.loc[:, "CODIGO"] = products["CODIGO"].str.upper()
    products.loc[:, "DEPARTAMENTO"] = products["DEPARTAMENTO"].str.upper()

    for column in (
        "CANTIDAD_DISPONIBLE",
        "P_MAX",
        "P_MIN",
        "P_OFERTA",
        "PRECIO_VENTA",
    ):
        products.loc[:, column] = pd.to_numeric(products[column], errors="coerce")

    invalid_identity = products["CODIGO"].eq("") | products["NOMBRE"].eq("")
    if bool(invalid_identity.any()):
        rows = (products.index[invalid_identity] + 1).tolist()[:8]
        raise ValueError(
            f"Inventario {source_name}: código o nombre vacío en filas {rows}."
        )
    numeric_columns = [
        "CANTIDAD_DISPONIBLE",
        "P_MAX",
        "P_MIN",
        "P_OFERTA",
        "PRECIO_VENTA",
    ]
    if bool(products[numeric_columns].isna().any().any()):
        raise ValueError(f"Inventario {source_name}: existen cantidades o precios no numéricos.")
    if bool((products["CANTIDAD_DISPONIBLE"] < 0).any()):
        raise ValueError(f"Inventario {source_name}: el stock no puede ser negativo.")
    if bool((products[["P_MAX", "P_MIN", "P_OFERTA"]] < 0).any().any()):
        raise ValueError(f"Inventario {source_name}: los precios no pueden ser negativos.")
    if not bool(products["PRECIO_VENTA"].isin([1, 2, 3]).all()):
        raise ValueError(f"Inventario {source_name}: PRECIO_VENTA debe ser 1, 2 o 3.")
    if bool(products["CODIGO"].duplicated(keep=False).any()):
        duplicates = sorted(
            products.loc[products["CODIGO"].duplicated(keep=False), "CODIGO"].unique()
        )[:8]
        raise ValueError(
            f"Inventario {source_name}: códigos duplicados: {', '.join(duplicates)}."
        )
    if len(products) != initial_rows:
        raise ValueError(
            f"Inventario {source_name}: se perdieron filas vacías durante la validación."
        )
    return products


def _clean_presentations(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "CODIGO",
                "NOMBRE",
                "DESCRIPCION",
                "DEPARTAMENTO",
                "GENERO",
                "P_MAX",
                "P_MIN",
                "P_OFERTA",
                "REQUIERE_BOTELLA",
            ]
        )
    required = (
        "CODIGO",
        "NOMBRE",
        "DESCRIPCION",
        "DEPARTAMENTO",
        "GENERO",
        "P_MAX",
        "P_MIN",
        "P_OFERTA",
        "REQUIERE_BOTELLA",
    )
    _require_columns(df, required, label=f"Presentaciones {source_name}")
    result = df.loc[:, list(required)].copy().reset_index(drop=True)
    for column in ("CODIGO", "NOMBRE", "DESCRIPCION", "DEPARTAMENTO", "GENERO"):
        result.loc[:, column] = result[column].astype("string").fillna("").str.strip()
    result.loc[:, "CODIGO"] = result["CODIGO"].str.upper()
    result.loc[:, "DEPARTAMENTO"] = result["DEPARTAMENTO"].str.upper()
    for column in ("P_MAX", "P_MIN", "P_OFERTA"):
        result.loc[:, column] = pd.to_numeric(result[column], errors="coerce")
    if bool((result["CODIGO"].eq("") | result["NOMBRE"].eq("")).any()):
        raise ValueError(f"Presentaciones {source_name}: código o nombre vacío.")
    if bool(result[["P_MAX", "P_MIN", "P_OFERTA"]].isna().any().any()):
        raise ValueError(f"Presentaciones {source_name}: existen precios no numéricos.")
    duplicate_key = ["CODIGO", "DEPARTAMENTO", "GENERO"]
    if bool(result.duplicated(subset=duplicate_key, keep=False).any()):
        raise ValueError(f"Presentaciones {source_name}: existen claves duplicadas.")
    return result


def _clean_relations(df: pd.DataFrame, *, source_name: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(
            columns=[
                "COD_PRODUCTO",
                "COD_PRESENTACION",
                "DEPARTAMENTO",
                "GENERO",
                "CANTIDAD",
            ]
        )
    required = (
        "COD_PRODUCTO",
        "COD_PRESENTACION",
        "DEPARTAMENTO",
        "GENERO",
        "CANTIDAD",
    )
    _require_columns(df, required, label=f"Relaciones {source_name}")
    result = df.loc[:, list(required)].copy().reset_index(drop=True)
    for column in ("COD_PRODUCTO", "COD_PRESENTACION", "DEPARTAMENTO", "GENERO"):
        result.loc[:, column] = result[column].astype("string").fillna("").str.strip()
    result.loc[:, "COD_PRODUCTO"] = result["COD_PRODUCTO"].str.upper()
    result.loc[:, "COD_PRESENTACION"] = result["COD_PRESENTACION"].str.upper()
    result.loc[:, "DEPARTAMENTO"] = result["DEPARTAMENTO"].str.upper()
    result.loc[:, "CANTIDAD"] = pd.to_numeric(result["CANTIDAD"], errors="coerce")
    if bool(
        (result["COD_PRODUCTO"].eq("") | result["COD_PRESENTACION"].eq("")).any()
    ):
        raise ValueError(f"Relaciones {source_name}: faltan códigos.")
    if bool(result["CANTIDAD"].isna().any()) or bool((result["CANTIDAD"] <= 0).any()):
        raise ValueError(f"Relaciones {source_name}: CANTIDAD debe ser positiva.")
    duplicate_key = [
        "COD_PRODUCTO",
        "COD_PRESENTACION",
        "DEPARTAMENTO",
        "GENERO",
    ]
    if bool(result.duplicated(subset=duplicate_key, keep=False).any()):
        raise ValueError(f"Relaciones {source_name}: existen claves duplicadas.")
    return result


def _optional_presentations(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    try:
        presentations = cargar_presentaciones(path)
    except RuntimeError as exc:
        if "Se esperaba la hoja" not in str(exc):
            raise
        presentations = pd.DataFrame()
    try:
        relations = cargar_presentaciones_prod(path)
    except RuntimeError as exc:
        if "Se esperaba la hoja" not in str(exc):
            raise
        relations = pd.DataFrame()
    return presentations, relations


def inventory_snapshot_from_excel(path: str) -> InventorySnapshot:
    inventory_path = os.path.abspath(os.path.expandvars(str(path or "").strip()))
    if not os.path.isfile(inventory_path):
        raise FileNotFoundError(f"No existe el archivo de inventario: {inventory_path}")
    source_name = os.path.basename(inventory_path)
    raw_products = _leer_inventario_xlsx(inventory_path, source_name)
    raw_presentations, raw_relations = _optional_presentations(inventory_path)

    products = _clean_products(raw_products, source_name=source_name)
    presentations = _clean_presentations(raw_presentations, source_name=source_name)
    relations = _clean_relations(raw_relations, source_name=source_name)

    # Fuerza el cálculo para detectar DataFrames accidentalmente gigantes antes
    # de convertirlos en el JSON de bootstrap.
    total_memory = sum(
        int(frame.memory_usage(index=True, deep=True).sum())
        for frame in (products, presentations, relations)
    )
    if total_memory > 512 * 1024 * 1024:
        raise ValueError(f"Inventario {source_name}: supera 512 MB en memoria normalizada.")

    product_records = (
        products.rename(
            columns={
                "CODIGO": "codigo",
                "NOMBRE": "nombre",
                "DEPARTAMENTO": "departamento",
                "GENERO": "genero",
                "P_MAX": "p_max",
                "P_MIN": "p_min",
                "P_OFERTA": "p_oferta",
                "PRECIO_VENTA": "precio_venta",
            }
        )
        .loc[
            :,
            [
                "codigo",
                "nombre",
                "departamento",
                "genero",
                "p_max",
                "p_min",
                "p_oferta",
                "precio_venta",
            ],
        ]
        .assign(precio_venta=lambda frame: frame["precio_venta"].astype(int))
        .to_dict(orient="records")
    )
    stock_records = (
        products.rename(
            columns={"CODIGO": "codigo", "CANTIDAD_DISPONIBLE": "cantidad"}
        )
        .loc[:, ["codigo", "cantidad"]]
        .to_dict(orient="records")
    )
    presentation_records = (
        presentations.rename(
            columns={
                "CODIGO": "codigo",
                "NOMBRE": "nombre",
                "DESCRIPCION": "descripcion",
                "DEPARTAMENTO": "departamento",
                "GENERO": "genero",
                "P_MAX": "p_max",
                "P_MIN": "p_min",
                "P_OFERTA": "p_oferta",
                "REQUIERE_BOTELLA": "requiere_botella",
            }
        )
        .to_dict(orient="records")
    )
    relation_records = (
        relations.rename(
            columns={
                "COD_PRODUCTO": "codigo_producto",
                "COD_PRESENTACION": "codigo_presentacion",
                "DEPARTAMENTO": "departamento",
                "GENERO": "genero",
                "CANTIDAD": "cantidad",
            }
        )
        .to_dict(orient="records")
    )

    departments = sorted(
        {
            _text(value).upper()
            for value in pd.concat(
                [
                    products["DEPARTAMENTO"],
                    presentations.get("DEPARTAMENTO", pd.Series(dtype="string")),
                ],
                ignore_index=True,
            ).tolist()
            if _text(value)
        }
    )
    genders = sorted(
        {
            _text(value)
            for value in pd.concat(
                [
                    products["GENERO"],
                    presentations.get("GENERO", pd.Series(dtype="string")),
                ],
                ignore_index=True,
            ).tolist()
            if _text(value)
        }
    )
    return InventorySnapshot(
        products=tuple(product_records),
        presentations=tuple(presentation_records),
        presentation_products=tuple(relation_records),
        departments=tuple({"code": item, "name": item} for item in departments),
        genders=tuple({"code": item.upper(), "name": item} for item in genders),
        stock_items=tuple(stock_records),
        source_name=source_name,
    )


def _catalog_identity(record: Mapping[str, Any], fields: Iterable[str]) -> tuple[str, ...]:
    return tuple(_text(record.get(field)).upper() for field in fields)


def _merge_catalog_records(
    snapshots: Iterable[InventorySnapshot],
    *,
    collection: str,
    key_fields: tuple[str, ...],
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    source_by_key: dict[tuple[str, ...], str] = {}
    for snapshot in snapshots:
        records = getattr(snapshot, collection)
        for raw in records:
            record = dict(raw)
            key = _catalog_identity(record, key_fields)
            previous = merged.get(key)
            if previous is not None and previous != record:
                raise ValueError(
                    "El catálogo difiere entre inventarios para "
                    f"{collection} {key}: {source_by_key[key]} / {snapshot.source_name}."
                )
            merged[key] = record
            source_by_key[key] = snapshot.source_name
    return [merged[key] for key in sorted(merged)]


def _normalize_assignments(
    assignments: Iterable[SetupStoreAssignment | Mapping[str, Any]],
) -> list[SetupStoreAssignment]:
    normalized: list[SetupStoreAssignment] = []
    for raw in assignments:
        if isinstance(raw, SetupStoreAssignment):
            assignment = raw.normalized()
        elif isinstance(raw, Mapping):
            assignment = SetupStoreAssignment(
                country=raw.get("country") or raw.get("country_code") or "",
                company_type=raw.get("company_type") or raw.get("company") or "",
                store_code=raw.get("store_code") or raw.get("code") or "",
                store_name=raw.get("store_name") or raw.get("name") or "",
                inventory_path=raw.get("inventory_path") or "",
                is_default=_bool(raw.get("is_default")),
            ).normalized()
        else:
            raise TypeError("Cada asignación debe ser SetupStoreAssignment u objeto.")
        normalized.append(assignment)

    if not normalized:
        raise ValueError("Debe configurar al menos una tienda.")
    unique_keys = [(*item.scope_key, item.store_code) for item in normalized]
    if len(unique_keys) != len(set(unique_keys)):
        raise ValueError("No se puede repetir una tienda dentro del mismo país y empresa.")
    defaults = [item for item in normalized if item.is_default]
    if len(defaults) > 1:
        raise ValueError("Solo una tienda puede marcarse como predeterminada.")
    if not defaults:
        normalized[0] = replace(normalized[0], is_default=True)
    return normalized


def build_initial_setup_payload(
    seed_config: Mapping[str, Any],
    assignments: Iterable[SetupStoreAssignment | Mapping[str, Any]],
    *,
    inventory_loader: Callable[[str], InventorySnapshot] = inventory_snapshot_from_excel,
    idempotency_key: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    seed = normalize_seed_config(seed_config)
    if not has_complete_server_identity(seed["username"], seed["id_cotizador"]):
        raise ValueError(
            "La configuración offline no se envía al servidor. "
            "Ingrese usuario e ID del cotizador para preparar una solicitud remota."
        )
    stores = _normalize_assignments(assignments)
    default_store = next(item for item in stores if item.is_default)

    snapshots_by_path: dict[str, InventorySnapshot] = {}
    for item in stores:
        if item.inventory_path and item.inventory_path not in snapshots_by_path:
            snapshots_by_path[item.inventory_path] = inventory_loader(item.inventory_path)

    groups: list[dict[str, Any]] = []
    scope_keys = sorted({item.scope_key for item in stores})
    for country, company in scope_keys:
        scoped_stores = [item for item in stores if item.scope_key == (country, company)]
        snapshots = [
            snapshots_by_path[item.inventory_path]
            for item in scoped_stores
            if item.inventory_path
        ]
        group: dict[str, Any] = {
            "country": {"code": country_code_for(country), "name": country},
            "company": {"name": company},
            "stores": [],
        }
        if snapshots:
            group["catalog"] = {
                "products": _merge_catalog_records(
                    snapshots,
                    collection="products",
                    key_fields=("codigo",),
                ),
                "presentations": _merge_catalog_records(
                    snapshots,
                    collection="presentations",
                    key_fields=("codigo", "departamento", "genero"),
                ),
                "presentation_products": _merge_catalog_records(
                    snapshots,
                    collection="presentation_products",
                    key_fields=(
                        "codigo_producto",
                        "codigo_presentacion",
                        "departamento",
                        "genero",
                    ),
                ),
                "departments": _merge_catalog_records(
                    snapshots,
                    collection="departments",
                    key_fields=("code",),
                ),
                "genders": _merge_catalog_records(
                    snapshots,
                    collection="genders",
                    key_fields=("code",),
                ),
            }
        for item in sorted(scoped_stores, key=lambda current: current.store_code):
            store: dict[str, Any] = {
                "code": item.store_code,
                "name": item.store_name,
            }
            if item.inventory_path:
                store["stock"] = {
                    "available": True,
                    "items": [
                        dict(record)
                        for record in snapshots_by_path[item.inventory_path].stock_items
                    ],
                }
            group["stores"].append(store)
        groups.append(group)

    request_id = str(idempotency_key or uuid.uuid4()).strip()
    try:
        request_id = str(uuid.UUID(request_id))
    except (ValueError, AttributeError) as exc:
        raise ValueError("idempotency_key debe ser un UUID.") from exc

    generated = _text(generated_at) or _datetime.datetime.now(
        _datetime.timezone.utc
    ).isoformat(timespec="seconds")
    return {
        "schema_version": INITIAL_SETUP_SCHEMA_VERSION,
        "idempotency_key": request_id,
        "generated_at": generated,
        "identity": {
            "username": seed["username"],
            "id_cotizador": seed["id_cotizador"],
            "telemarketing": seed["telemarketing"],
        },
        "preferences": {
            "listing_type": seed["listing_type"],
            "allow_no_stock": seed["allow_no_stock"],
            "enable_ai": seed["enable_ai"],
            "enable_recommendations": seed["enable_recommendations"],
        },
        "default_scope": {
            "country_code": country_code_for(default_store.country),
            "country": default_store.country,
            "company_type": default_store.company_type,
            "store_code": default_store.store_code,
        },
        "assignments": groups,
    }


def load_json_object(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} no contiene un objeto JSON.")
    return value


def save_json_atomic(path: str | os.PathLike[str], value: Mapping[str, Any]) -> None:
    target = os.path.abspath(os.fspath(path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{os.path.basename(target)}.",
        suffix=".tmp",
        dir=os.path.dirname(target),
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(dict(value), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def build_local_seed_config(
    seed_config: Mapping[str, Any],
    default_assignment: SetupStoreAssignment,
) -> dict[str, Any]:
    seed = normalize_seed_config(seed_config)
    if not has_complete_server_identity(seed["username"], seed["id_cotizador"]):
        raise ValueError("La configuración remota requiere usuario e ID del cotizador.")
    assignment = default_assignment.normalized()
    result = {
        **dict(seed_config),
        "country": assignment.country,
        "listing_type": seed["listing_type"],
        "company_type": assignment.company_type,
        # Nombre histórico de la llave; semánticamente es el ID del cotizador.
        "store_id": seed["id_cotizador"],
        "username": seed["username"],
        "telemarketing": seed["telemarketing"],
        "allow_no_stock": seed["allow_no_stock"],
        "enable_ai": seed["enable_ai"],
        "enable_recommendations": seed["enable_recommendations"],
        "update_mode": seed["update_mode"],
        "update_check_on_startup": seed["update_check_on_startup"],
        "update_manifest_url": seed["update_manifest_url"],
        "update_flags": seed["update_flags"],
    }
    result.pop("id_cotizador", None)
    result.pop("tienda", None)
    result.pop("user_name", None)
    return result


def build_offline_seed_config(seed_config: Mapping[str, Any]) -> dict[str, Any]:
    seed = normalize_seed_config(seed_config)
    if has_complete_server_identity(seed["username"], seed["id_cotizador"]):
        raise ValueError("La identidad indicada corresponde al modo servidor, no al modo offline.")

    country = normalize_country_name(seed_config.get("country") or "PARAGUAY")
    if country not in SUPPORTED_COUNTRIES:
        raise ValueError(f"País no soportado: {country!r}.")
    company_type = _text(
        seed_config.get("company_type")
        or seed_config.get("company")
        or "LA CASA DEL PERFUME"
    ).upper()
    if company_type not in _COMPANY_TYPES:
        raise ValueError(f"Empresa no soportada: {company_type!r}.")

    result = {
        **dict(seed_config),
        "country": country,
        "listing_type": seed["listing_type"],
        "company_type": company_type,
        "store_id": "",
        "username": "",
        "telemarketing": seed["telemarketing"],
        "allow_no_stock": seed["allow_no_stock"],
        "enable_ai": seed["enable_ai"],
        "enable_recommendations": seed["enable_recommendations"],
        "update_mode": seed["update_mode"],
        "update_check_on_startup": seed["update_check_on_startup"],
        "update_manifest_url": seed["update_manifest_url"],
        "update_flags": seed["update_flags"],
    }
    result.pop("id_cotizador", None)
    result.pop("tienda", None)
    result.pop("user_name", None)
    return result


def finalize_offline_initial_setup(
    seed_path: str | os.PathLike[str],
    seed_config: Mapping[str, Any],
    *,
    settings_applier: Callable[[Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Persiste el modo local y elimina solo estados generados de envío remoto."""

    target = os.path.abspath(os.fspath(seed_path))
    local_seed = build_offline_seed_config(seed_config)
    save_json_atomic(target, local_seed)
    if settings_applier is not None:
        settings_applier(local_seed)
    config_dir = os.path.dirname(target)
    for filename in (
        SETUP_PENDING_FILENAME,
        SETUP_RECEIPT_FILENAME,
        SETUP_REQUIRED_FILENAME,
    ):
        try:
            os.remove(os.path.join(config_dir, filename))
        except FileNotFoundError:
            pass
    return local_seed


__all__ = [
    "INITIAL_SETUP_SCHEMA_VERSION",
    "InventorySnapshot",
    "SETUP_PENDING_FILENAME",
    "SETUP_RECEIPT_FILENAME",
    "SETUP_REQUIRED_FILENAME",
    "SetupStoreAssignment",
    "build_offline_seed_config",
    "build_initial_setup_payload",
    "build_local_seed_config",
    "finalize_offline_initial_setup",
    "inventory_snapshot_from_excel",
    "load_json_object",
    "normalize_seed_config",
    "save_json_atomic",
]
