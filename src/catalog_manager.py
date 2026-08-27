from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
from PySide6.QtCore import QObject, Signal

from sqlModels.db import connect, ensure_schema, tx
from sqlModels.settings_repo import get_setting
from sqlModels import offline_catalogs_repo

from .catalog_context import CatalogScope
from .country_rules import country_code_for
from .db_path import resolve_db_path
from .logging_setup import get_logger
from .server_identity import has_complete_server_identity


log = get_logger(__name__)


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _lookup(rows: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        raw_key = row.get("id")
        key = str(raw_key or "").strip()
        try:
            numeric_key = float(key)
        except (TypeError, ValueError):
            numeric_key = float("nan")
        if pd.notna(numeric_key) and numeric_key.is_integer():
            key = str(int(numeric_key))
        if not key:
            continue
        result[key] = str(row.get("name") or row.get("nombre") or "").strip()
    return result


def _lookup_key_series(values: pd.Series) -> pd.Series:
    """Evita que IDs enteros con nulos se conviertan en claves como ``2.0``."""
    normalized = values.fillna("").astype(str).str.strip().copy()
    numeric = pd.to_numeric(normalized, errors="coerce")
    integer_mask = numeric.notna() & ((numeric % 1) == 0)
    if bool(integer_mask.any()):
        normalized.loc[integer_mask] = (
            numeric.loc[integer_mask].astype("Int64").astype(str)
        )
    return normalized


def _stock_totals(matrix: Mapping[str, Any] | None) -> dict[str, float]:
    totals: dict[str, float] = {}
    if not isinstance(matrix, Mapping):
        return totals
    for row in _records(matrix.get("rows")):
        code = str(row.get("codigo_norm") or row.get("codigo") or "").strip().upper()
        if not code:
            continue
        try:
            totals[code] = float(row.get("total_stock") or 0.0)
        except (TypeError, ValueError):
            totals[code] = 0.0
    return totals


def _products_frame(catalog: Mapping[str, Any], matrix: Mapping[str, Any] | None) -> pd.DataFrame:
    rows = _records(catalog.get("products"))
    if not rows:
        return pd.DataFrame()

    departments = _lookup(_records(catalog.get("departments")))
    genders = _lookup(_records(catalog.get("genders")))
    totals = _stock_totals(matrix)
    frame = pd.DataFrame.from_records(rows).copy()

    for column in ("codigo", "nombre", "nombre_corto"):
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame["codigo"] = frame["codigo"].str.upper()
    if bool((frame["codigo"] == "").any()):
        raise ValueError("El catalogo remoto contiene productos sin codigo.")
    if bool((frame["nombre"] == "").any()):
        raise ValueError("El catalogo remoto contiene productos sin nombre.")
    if bool(frame["codigo"].duplicated().any()):
        raise ValueError("El catalogo remoto contiene codigos de producto duplicados.")

    for column in ("p_max", "p_min", "p_oferta"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).round(2).astype(float)

    if "id_precioventa" not in frame.columns:
        frame["id_precioventa"] = 1
    price_ids = pd.to_numeric(frame["id_precioventa"], errors="coerce").fillna(1).round().astype(int)
    frame["precio_venta"] = price_ids.where(price_ids.isin((1, 2, 3)), 1)

    department_ids = _lookup_key_series(
        frame.get("id_departamento", pd.Series("", index=frame.index))
    )
    gender_ids = _lookup_key_series(
        frame.get("id_genero", pd.Series("", index=frame.index))
    )
    frame["departamento"] = department_ids.map(departments).fillna("").astype(str).str.strip().str.upper()
    frame["genero"] = gender_ids.map(genders).fillna("").astype(str).str.strip()
    frame["categoria"] = frame["departamento"].where(frame["departamento"] != "", "PRODUCTO")
    frame["cantidad_disponible"] = frame["codigo"].map(totals).fillna(0.0).astype(float)
    frame["id"] = frame["codigo"]
    frame["ml"] = ""
    frame["fuente"] = "EFAPI"

    aliases = {
        "CODIGO": "codigo",
        "NOMBRE": "nombre",
        "DEPARTAMENTO": "departamento",
        "GENERO": "genero",
        "CANTIDAD_DISPONIBLE": "cantidad_disponible",
        "P_MAX": "p_max",
        "P_MIN": "p_min",
        "P_OFERTA": "p_oferta",
        "PRECIO_VENTA": "precio_venta",
    }
    for alias, source in aliases.items():
        frame[alias] = frame[source]
    return frame.reset_index(drop=True)


def _presentations_frame(
    catalog: Mapping[str, Any],
    matrix: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    rows = _records(catalog.get("presentations"))
    if not rows:
        return pd.DataFrame()

    departments = _lookup(_records(catalog.get("departments")))
    genders = _lookup(_records(catalog.get("genders")))
    totals = _stock_totals(matrix)
    relations = _records(catalog.get("presentation_products") or catalog.get("relations"))
    relation_codes: dict[str, list[str]] = {}
    for relation in relations:
        key = str(
            relation.get("presentation_key")
            or relation.get("codigo_presentacion")
            or ""
        ).strip().upper()
        code = str(relation.get("codigo_producto") or "").strip().upper()
        if key and code and code not in relation_codes.setdefault(key, []):
            relation_codes[key].append(code)

    frame = pd.DataFrame.from_records(rows).copy()
    for column in ("presentation_key", "codigo", "nombre", "descripcion"):
        if column not in frame.columns:
            frame[column] = ""
        frame[column] = frame[column].fillna("").astype(str).str.strip()
    frame["codigo"] = frame["codigo"].str.upper()
    frame["presentation_key"] = frame["presentation_key"].str.upper()
    frame["presentation_key"] = frame["presentation_key"].where(
        frame["presentation_key"] != "", frame["codigo"]
    )
    if bool((frame["codigo"] == "").any()):
        raise ValueError("El catalogo remoto contiene presentaciones sin codigo.")

    for column in ("p_max", "p_min", "p_oferta"):
        if column not in frame.columns:
            frame[column] = 0.0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0.0).round(2).astype(float)

    department_ids = _lookup_key_series(
        frame.get("id_departamento", pd.Series("", index=frame.index))
    )
    gender_ids = _lookup_key_series(
        frame.get("id_genero", pd.Series("", index=frame.index))
    )
    frame["departamento"] = department_ids.map(departments).fillna("").astype(str).str.strip().str.upper()
    frame["genero"] = gender_ids.map(genders).fillna("").astype(str).str.strip()
    frame["codigos_producto"] = frame["presentation_key"].map(
        lambda key: ",".join(relation_codes.get(str(key).upper(), []))
    )
    frame["categoria"] = "PRESENTACION"
    if "id_precioventa" not in frame.columns:
        frame["id_precioventa"] = 1
    presentation_price_ids = pd.to_numeric(frame["id_precioventa"], errors="coerce").fillna(1).round().astype(int)
    frame["precio_venta"] = presentation_price_ids.where(presentation_price_ids.isin((1, 2, 3)), 1)
    frame["cantidad_disponible"] = (
        frame["presentation_key"].map(totals).fillna(0.0).astype(float)
    )

    frame["CODIGO"] = frame["codigo"]
    frame["CODIGO_NORM"] = frame["codigo"]
    frame["NOMBRE"] = frame["nombre"].where(frame["nombre"] != "", frame["codigo"])
    frame["DESCRIPCION"] = frame["descripcion"]
    frame["DEPARTAMENTO"] = frame["departamento"]
    frame["GENERO"] = frame["genero"]
    frame["P_MAX"] = frame["p_max"]
    frame["P_MIN"] = frame["p_min"]
    frame["P_OFERTA"] = frame["p_oferta"]
    frame["STOCK_DISPONIBLE"] = frame["cantidad_disponible"]
    frame["REQUIERE_BOTELLA"] = False
    frame["CODIGOS_PRODUCTO"] = frame["codigos_producto"]
    frame["codigo_norm"] = frame["codigo"]
    return frame.reset_index(drop=True)


def catalog_frames_from_cache(
    catalog: Mapping[str, Any] | None,
    matrix: Mapping[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not isinstance(catalog, Mapping):
        return pd.DataFrame(), pd.DataFrame()
    return _products_frame(catalog, matrix), _presentations_frame(catalog, matrix)


class CatalogManager(QObject):
    """Fuente de verdad de catalogos locales y remotos durante el runtime."""

    catalog_updated = Signal(object, object)
    scope_catalog_updated = Signal(object, object, object)
    stock_updated = Signal(object)
    scopes_updated = Signal(object)
    active_scope_changed = Signal(object)
    local_catalogs_updated = Signal(object)
    local_catalog_updated = Signal(object, object, object)
    active_local_catalog_changed = Signal(object)

    def __init__(
        self,
        df_productos: pd.DataFrame | None = None,
        df_presentaciones: pd.DataFrame | None = None,
        *,
        db_path: str | None = None,
        username: str = "",
        id_cotizador: str = "",
    ):
        super().__init__()
        self._df_productos = df_productos if isinstance(df_productos, pd.DataFrame) else pd.DataFrame()
        self._df_presentaciones = (
            df_presentaciones if isinstance(df_presentaciones, pd.DataFrame) else pd.DataFrame()
        )
        self._legacy_catalog = (self._df_productos, self._df_presentaciones)
        explicit_db_path = bool(str(db_path or "").strip())
        self._db_path = str(db_path or resolve_db_path())
        self._username = ""
        self._id_cotizador = ""
        self._scope_records: dict[CatalogScope, dict[str, Any]] = {}
        self._scope_catalogs: dict[CatalogScope, tuple[pd.DataFrame, pd.DataFrame]] = {}
        self._stock_matrices: dict[CatalogScope, dict[str, Any]] = {}
        self._catalog_signatures: dict[CatalogScope, str] = {}
        self._stock_signatures: dict[CatalogScope, tuple[tuple[str, str], ...]] = {}
        self._active_scope: CatalogScope | None = None
        self._preferred_scope: CatalogScope | None = None
        self._local_catalog_records: dict[int, dict[str, Any]] = {}
        self._active_local_catalog_id: int | None = None
        self._catalog_health_cache_key: object | None = None
        self._catalog_health_cache_value: tuple[bool, str] = (False, "No hay productos cargados.")
        if has_complete_server_identity(username, id_cotizador):
            self.configure_server_cache(
                db_path=self._db_path,
                username=username,
                id_cotizador=id_cotizador,
            )
        elif explicit_db_path:
            try:
                self.reload_local_catalogs(load_active=False, emit=False)
            except Exception as exc:
                log.warning("No se pudo leer la lista de catálogos offline: %s", exc)

    @property
    def df_productos(self) -> pd.DataFrame:
        return self._df_productos

    @property
    def df_presentaciones(self) -> pd.DataFrame:
        return self._df_presentaciones

    @property
    def server_mode(self) -> bool:
        """Solo una identidad completa activa catálogo remoto y bloquea Excel."""
        return has_complete_server_identity(self._username, self._id_cotizador)

    @property
    def server_identity_complete(self) -> bool:
        return self.server_mode

    @property
    def manual_catalog_allowed(self) -> bool:
        return not self.server_mode

    @property
    def active_scope(self) -> CatalogScope | None:
        return self._active_scope

    @property
    def available_scopes(self) -> tuple[CatalogScope, ...]:
        return tuple(self._scope_records.keys())

    @property
    def available_local_catalogs(self) -> tuple[dict[str, Any], ...]:
        return tuple(dict(record) for record in self._local_catalog_records.values())

    @property
    def active_local_catalog_id(self) -> int | None:
        return self._active_local_catalog_id

    @property
    def active_local_catalog_name(self) -> str:
        record = self._local_catalog_records.get(self._active_local_catalog_id or -1)
        return str((record or {}).get("name") or "").strip()

    @property
    def username(self) -> str:
        return self._username

    @property
    def id_cotizador(self) -> str:
        return self._id_cotizador

    def scope_record(self, scope: CatalogScope) -> dict[str, Any]:
        return dict(self._scope_records.get(scope) or {})

    def local_catalog_record(self, catalog_id: int) -> dict[str, Any]:
        return dict(self._local_catalog_records.get(int(catalog_id)) or {})

    def set_catalog(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame) -> None:
        self._legacy_catalog = (df_productos, df_presentaciones)
        if self.server_mode:
            return
        self._set_active_frames(df_productos, df_presentaciones, emit=True)

    def configure_local_catalog(self) -> None:
        self._username = ""
        self._id_cotizador = ""
        self._scope_records.clear()
        self._scope_catalogs.clear()
        self._stock_matrices.clear()
        self._active_scope = None
        self._preferred_scope = None
        self.scopes_updated.emit(tuple())
        try:
            if self.reload_local_catalogs(load_active=True, emit=True):
                return
        except Exception as exc:
            log.warning("No se pudo restaurar el catálogo offline activo: %s", exc)
        self._set_active_frames(*self._legacy_catalog, emit=True)

    def _set_local_records(
        self,
        records: list[dict[str, Any]],
        *,
        emit: bool,
    ) -> None:
        def _signature(values: Mapping[int, Mapping[str, Any]]) -> tuple[tuple, ...]:
            return tuple(
                (
                    int(catalog_id),
                    str(record.get("name") or ""),
                    str(record.get("source_hash") or ""),
                    str(record.get("updated_at") or ""),
                    int(record.get("product_count") or 0),
                    int(record.get("presentation_count") or 0),
                    bool(record.get("is_active")),
                )
                for catalog_id, record in values.items()
            )

        previous_signature = _signature(self._local_catalog_records)
        self._local_catalog_records = {
            int(record["id"]): dict(record)
            for record in records
            if record.get("id") is not None
        }
        active = next(
            (
                catalog_id
                for catalog_id, record in self._local_catalog_records.items()
                if bool(record.get("is_active"))
            ),
            next(iter(self._local_catalog_records), None),
        )
        self._active_local_catalog_id = active
        if emit and _signature(self._local_catalog_records) != previous_signature:
            self.local_catalogs_updated.emit(self.available_local_catalogs)

    def reload_local_catalogs(
        self,
        *,
        load_active: bool = True,
        emit: bool = True,
    ) -> bool:
        if self.server_mode:
            return False

        con = connect(self._db_path)
        try:
            ensure_schema(con)
            active = offline_catalogs_repo.get_active_catalog(con)
            if load_active and active is not None:
                with tx(con):
                    offline_catalogs_repo.materialize_catalog(con, int(active["id"]))
                from .catalog_sync import load_catalog_from_db

                frames = load_catalog_from_db(con)
            else:
                frames = None
            records = offline_catalogs_repo.list_catalogs(con)
        finally:
            con.close()

        previous_active = self._active_local_catalog_id
        self._set_local_records(records, emit=emit)
        if frames is not None:
            self._legacy_catalog = frames
            self._set_active_frames(*frames, emit=emit)
        if emit and previous_active != self._active_local_catalog_id:
            self.active_local_catalog_changed.emit(self._active_local_catalog_id)
        return bool(records)

    def import_local_catalog(
        self,
        excel_path: str,
        *,
        name: str | None = None,
        catalog_id: int | None = None,
    ) -> dict[str, Any]:
        if not self.manual_catalog_allowed:
            raise PermissionError(
                "Los catálogos manuales solo están disponibles sin identidad de servidor."
            )

        from .catalog_sync import import_offline_catalog_from_excel, load_catalog_from_db

        con = connect(self._db_path)
        try:
            ensure_schema(con)
            with tx(con):
                record = import_offline_catalog_from_excel(
                    con,
                    excel_path,
                    name=name,
                    catalog_id=catalog_id,
                )
            frames = load_catalog_from_db(con)
            records = offline_catalogs_repo.list_catalogs(con)
        finally:
            con.close()

        previous_active = self._active_local_catalog_id
        self._set_local_records(records, emit=True)
        active_id = int(record["id"])
        self._active_local_catalog_id = active_id
        self._legacy_catalog = frames
        self._set_active_frames(*frames, emit=True)
        self.local_catalog_updated.emit(active_id, frames[0], frames[1])
        if previous_active != active_id:
            self.active_local_catalog_changed.emit(active_id)
        return self.local_catalog_record(active_id)

    def set_active_local_catalog(self, catalog_id: int) -> dict[str, Any]:
        if self.server_mode:
            raise RuntimeError("No se puede activar un catálogo manual en modo servidor.")
        catalog_id = int(catalog_id)
        if (
            catalog_id == self._active_local_catalog_id
            and catalog_id in self._local_catalog_records
        ):
            return self.local_catalog_record(catalog_id)

        con = connect(self._db_path)
        try:
            ensure_schema(con)
            with tx(con):
                offline_catalogs_repo.materialize_catalog(con, catalog_id)
            from .catalog_sync import load_catalog_from_db

            frames = load_catalog_from_db(con)
            records = offline_catalogs_repo.list_catalogs(con)
        finally:
            con.close()

        previous_active = self._active_local_catalog_id
        self._set_local_records(records, emit=False)
        self._active_local_catalog_id = catalog_id
        self._legacy_catalog = frames
        self._set_active_frames(*frames, emit=True)
        if previous_active != catalog_id:
            self.active_local_catalog_changed.emit(catalog_id)
        return self.local_catalog_record(catalog_id)

    def catalog_for_local(
        self,
        catalog_id: int,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        catalog_id = int(catalog_id)
        if catalog_id == self._active_local_catalog_id:
            return self._df_productos, self._df_presentaciones

        from .catalog_sync import load_offline_catalog_from_db

        con = connect(self._db_path)
        try:
            ensure_schema(con)
            return load_offline_catalog_from_db(con, catalog_id)
        finally:
            con.close()

    def local_catalog_health(self, catalog_id: int) -> tuple[bool, str]:
        from .catalog_sync import validate_products_catalog_df

        try:
            products, _presentations = self.catalog_for_local(catalog_id)
        except KeyError:
            return False, "El catálogo offline seleccionado ya no existe."
        return validate_products_catalog_df(products)

    def configure_server_cache(self, *, db_path: str, username: str, id_cotizador: str) -> bool:
        new_db_path = str(db_path or resolve_db_path())
        new_username = str(username or "").strip()
        new_id_cotizador = str(id_cotizador or "").strip()
        identity_changed = (
            new_db_path != self._db_path
            or new_username.casefold() != self._username.casefold()
            or new_id_cotizador.casefold() != self._id_cotizador.casefold()
        )
        self._db_path = new_db_path
        self._username = new_username
        self._id_cotizador = new_id_cotizador
        if not self.server_mode:
            self.configure_local_catalog()
            return False
        if identity_changed:
            self._scope_records.clear()
            self._scope_catalogs.clear()
            self._stock_matrices.clear()
            self._catalog_signatures.clear()
            self._stock_signatures.clear()
            self._active_scope = None
            self._preferred_scope = None
            self._set_active_frames(pd.DataFrame(), pd.DataFrame(), emit=True)
            self.scopes_updated.emit(tuple())
        if not self.server_identity_complete:
            return False
        try:
            return self.reload_server_cache()
        except Exception as exc:
            log.warning(
                "No se pudo cargar el cache remoto para username=%s id_cotizador=%s: %s",
                self._username,
                self._id_cotizador,
                exc,
            )
            return False

    def reload_server_cache(self) -> bool:
        if not self.server_identity_complete:
            return False

        from sqlModels import catalog_cache_repo

        con = connect(self._db_path)
        try:
            ensure_schema(con)
            preferred_country = str(get_setting(con, "country", "") or "").strip()
            preferred_company = str(get_setting(con, "company_type", "") or "").strip()
            preferred_scope = None
            if preferred_country and preferred_company:
                try:
                    preferred_scope = CatalogScope(
                        country_code_for(preferred_country),
                        preferred_company,
                    )
                except (TypeError, ValueError):
                    preferred_scope = None
            scope_rows = catalog_cache_repo.list_scopes(con, self._username, self._id_cotizador)
            records: dict[CatalogScope, dict[str, Any]] = {}
            catalogs: dict[CatalogScope, tuple[pd.DataFrame, pd.DataFrame]] = {}
            matrices: dict[CatalogScope, dict[str, Any]] = {}
            catalog_signatures: dict[CatalogScope, str] = {}
            stock_signatures: dict[CatalogScope, tuple[tuple[str, str], ...]] = {}
            for raw_scope in scope_rows or []:
                scope_row = dict(raw_scope)
                scope = CatalogScope.from_mapping(scope_row)
                group_key = str(scope_row.get("group_key") or scope.group_key)
                catalog = catalog_cache_repo.load_scope_catalog(
                    con,
                    self._username,
                    self._id_cotizador,
                    group_key=group_key,
                )
                matrix = catalog_cache_repo.load_stock_matrix(
                    con,
                    self._username,
                    self._id_cotizador,
                    group_key=group_key,
                )
                records[scope] = scope_row
                matrices[scope] = dict(matrix or {})
                catalogs[scope] = catalog_frames_from_cache(catalog, matrix)
                catalog_signatures[scope] = str(scope_row.get("catalog_revision") or "")
                stock_signatures[scope] = tuple(
                    sorted(
                        (
                            str(store.get("id_tienda") or ""),
                            str(store.get("stock_revision") or ""),
                        )
                        for store in _records((matrix or {}).get("stores"))
                    )
                )
        finally:
            con.close()

        previous_scopes = tuple(self._scope_records.keys())
        previous_active = self._active_scope
        catalog_changed_scopes = {
            scope
            for scope in set(catalog_signatures) | set(self._catalog_signatures)
            if catalog_signatures.get(scope) != self._catalog_signatures.get(scope)
        }
        stock_changed_scopes = {
            scope
            for scope in set(stock_signatures) | set(self._stock_signatures)
            if stock_signatures.get(scope) != self._stock_signatures.get(scope)
        }
        self._scope_records = records
        self._scope_catalogs = catalogs
        self._stock_matrices = matrices
        self._catalog_signatures = catalog_signatures
        self._stock_signatures = stock_signatures

        preferred_changed = preferred_scope != self._preferred_scope
        self._preferred_scope = preferred_scope
        if preferred_changed and preferred_scope in records:
            self._active_scope = preferred_scope
        elif previous_active in records:
            self._active_scope = previous_active
        elif preferred_scope in records:
            self._active_scope = preferred_scope
        else:
            self._active_scope = next(iter(records), None)

        frames = catalogs.get(self._active_scope, (pd.DataFrame(), pd.DataFrame()))
        active_catalog_changed = bool(
            previous_active != self._active_scope or self._active_scope in catalog_changed_scopes
        )
        self._set_active_frames(*frames, emit=active_catalog_changed)
        if tuple(records.keys()) != previous_scopes:
            self.scopes_updated.emit(self.available_scopes)
        if previous_active != self._active_scope:
            self.active_scope_changed.emit(self._active_scope)
        for scope in catalog_changed_scopes:
            if scope in catalogs:
                products, presentations = catalogs[scope]
                self.scope_catalog_updated.emit(scope, products, presentations)
        for scope in stock_changed_scopes:
            self.stock_updated.emit(scope)
        return bool(records)

    def set_active_scope(self, scope: CatalogScope) -> None:
        if scope not in self._scope_catalogs:
            raise KeyError(f"Scope no disponible: {scope.group_key}")
        if scope == self._active_scope:
            return
        self._active_scope = scope
        self._set_active_frames(*self._scope_catalogs[scope], emit=True)
        self.active_scope_changed.emit(scope)

    def catalog_for_scope(self, scope: CatalogScope) -> tuple[pd.DataFrame, pd.DataFrame]:
        products, presentations = self._scope_catalogs.get(scope, (pd.DataFrame(), pd.DataFrame()))
        return products, presentations

    def stock_matrix(self, scope: CatalogScope | None = None) -> dict[str, Any]:
        selected = scope or self._active_scope
        return dict(self._stock_matrices.get(selected) or {})

    def _set_active_frames(
        self,
        df_productos: pd.DataFrame,
        df_presentaciones: pd.DataFrame,
        *,
        emit: bool,
    ) -> None:
        self._df_productos = df_productos
        self._df_presentaciones = df_presentaciones
        self._catalog_health_cache_key = None
        if emit:
            self.catalog_updated.emit(df_productos, df_presentaciones)

    def _catalog_health_cache_token(self, df: pd.DataFrame) -> tuple[int, int, int]:
        try:
            rows, _cols = tuple(getattr(df, "shape", (0, 0)))
            col_count = len(getattr(df, "columns", []))
            return (id(df), int(rows), int(col_count))
        except Exception:
            return (id(df), 0, 0)

    def catalog_health(self, scope: CatalogScope | None = None) -> tuple[bool, str]:
        if self.server_mode and not self.server_identity_complete:
            return (False, "Falta id_cotizador para cargar el catalogo remoto.")

        if self.server_mode and scope is not None:
            products = self._scope_catalogs.get(scope, (pd.DataFrame(), pd.DataFrame()))[0]
            key: object = ("scope", scope, self._catalog_health_cache_token(products))
        elif self.server_mode:
            key = (
                "server-any",
                tuple(
                    (candidate, self._catalog_health_cache_token(frames[0]))
                    for candidate, frames in self._scope_catalogs.items()
                ),
            )
        else:
            key = ("local", self._catalog_health_cache_token(self._df_productos))
        if key == self._catalog_health_cache_key:
            return self._catalog_health_cache_value

        from .catalog_sync import validate_products_catalog_df

        if self.server_mode and scope is not None:
            if scope not in self._scope_catalogs:
                health = (False, "El pais y empresa seleccionados no estan autorizados.")
            else:
                health = validate_products_catalog_df(self._scope_catalogs[scope][0])
        elif self.server_mode:
            if not self._scope_catalogs:
                health = (False, "No hay un catalogo remoto guardado para este usuario/cotizador.")
            else:
                failures: list[str] = []
                health = (False, "No hay catalogos remotos validos para cotizar.")
                for candidate, frames in self._scope_catalogs.items():
                    candidate_health = validate_products_catalog_df(frames[0])
                    if candidate_health[0]:
                        health = candidate_health
                        break
                    failures.append(f"{candidate.label}: {candidate_health[1]}")
                if not health[0] and failures:
                    health = (False, "No hay catalogos remotos validos. " + " | ".join(failures))
        else:
            health = validate_products_catalog_df(self._df_productos)
        self._catalog_health_cache_key = key
        self._catalog_health_cache_value = health
        return health


__all__ = ["CatalogManager", "catalog_frames_from_cache"]
