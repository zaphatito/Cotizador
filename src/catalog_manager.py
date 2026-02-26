# src/catalog_manager.py
from __future__ import annotations

import pandas as pd
from PySide6.QtCore import QObject, Signal


class CatalogManager(QObject):
    """
    Fuente única de verdad del catálogo en runtime.
    Emite catalog_updated(df_productos, df_presentaciones) cuando se recarga.
    """
    catalog_updated = Signal(object, object)  # DataFrames

    def __init__(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame):
        super().__init__()
        self._df_productos = df_productos
        self._df_presentaciones = df_presentaciones
        self._catalog_health_cache_key: tuple[int, int, int] | None = None
        self._catalog_health_cache_value: tuple[bool, str] = (False, "No hay productos cargados.")

    @property
    def df_productos(self) -> pd.DataFrame:
        return self._df_productos

    @property
    def df_presentaciones(self) -> pd.DataFrame:
        return self._df_presentaciones

    def set_catalog(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame) -> None:
        self._df_productos = df_productos
        self._df_presentaciones = df_presentaciones
        self._catalog_health_cache_key = None
        self.catalog_updated.emit(df_productos, df_presentaciones)

    def _catalog_health_cache_token(self, df: pd.DataFrame) -> tuple[int, int, int]:
        try:
            rows, _cols = tuple(getattr(df, "shape", (0, 0)))
            col_count = len(getattr(df, "columns", []))
            return (id(df), int(rows), int(col_count))
        except Exception:
            return (id(df), 0, 0)

    def catalog_health(self) -> tuple[bool, str]:
        key = self._catalog_health_cache_token(self._df_productos)
        if key == self._catalog_health_cache_key:
            return self._catalog_health_cache_value

        from .catalog_sync import validate_products_catalog_df

        health = validate_products_catalog_df(self._df_productos)
        self._catalog_health_cache_key = key
        self._catalog_health_cache_value = health
        return health
