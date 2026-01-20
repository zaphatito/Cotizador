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

    @property
    def df_productos(self) -> pd.DataFrame:
        return self._df_productos

    @property
    def df_presentaciones(self) -> pd.DataFrame:
        return self._df_presentaciones

    def set_catalog(self, df_productos: pd.DataFrame, df_presentaciones: pd.DataFrame) -> None:
        self._df_productos = df_productos
        self._df_presentaciones = df_presentaciones
        self.catalog_updated.emit(df_productos, df_presentaciones)
