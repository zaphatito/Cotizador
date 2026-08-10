from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from typing import Any


def _items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _format_stock(value: Any) -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if number == 0:
        return "0"
    if number.is_integer():
        return str(int(number))
    return f"{number:.3f}".rstrip("0").rstrip(".")


def store_label(store: Mapping[str, Any]) -> str:
    store_id = str(store.get("id_tienda") or "").strip()
    code = str(store.get("code") or store.get("codigo") or "").strip()
    name = str(store.get("name") or store.get("nombre") or "").strip()
    label = name or code or (f"Tienda {store_id}" if store_id else "Tienda")
    if code and name and code.casefold() not in name.casefold():
        return f"{code} - {name}"
    return label


def _plain(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text.casefold())
        if not unicodedata.combining(char)
    )


def item_type_label(row: Mapping[str, Any]) -> str:
    explicit = next(
        (
            str(row.get(key) or "").strip()
            for key in ("item_type", "tipo_item", "tipo_prod", "tipo", "type")
            if str(row.get(key) or "").strip()
        ),
        "",
    )
    normalized = _plain(explicit)
    aliases = {
        "prod": "Productos",
        "product": "Productos",
        "producto": "Productos",
        "productos": "Productos",
        "pres": "Presentaciones",
        "presentation": "Presentaciones",
        "presentacion": "Presentaciones",
        "presentaciones": "Presentaciones",
        "serv": "Servicios",
        "service": "Servicios",
        "servicio": "Servicios",
        "servicios": "Servicios",
        "other": "Otros",
        "otro": "Otros",
        "otros": "Otros",
    }
    if normalized in aliases:
        return aliases[normalized]
    if explicit:
        return explicit

    category = _plain(row.get("categoria") or row.get("departamento"))
    if "serv" in category:
        return "Servicios"
    if "present" in category:
        return "Presentaciones"
    if category:
        return "Productos"
    return "Otros"


def build_store_stock_tabs(
    matrix: Mapping[str, Any] | None,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    """Crea vistas tienda -> tipo de item para la interfaz de stock."""

    if not isinstance(matrix, Mapping):
        return []
    needle = str(query or "").strip().casefold()
    rows = _items(matrix.get("rows"))
    result: list[dict[str, Any]] = []
    preferred_order = {
        "Productos": 0,
        "Presentaciones": 1,
        "Servicios": 2,
        "Otros": 99,
    }
    for store in _items(matrix.get("stores")):
        store_id = str(store.get("id_tienda") or "").strip()
        if not store_id:
            continue
        grouped: dict[str, list[list[str]]] = {}
        for row in rows:
            code = str(row.get("codigo") or row.get("codigo_norm") or "").strip().upper()
            name = str(row.get("nombre") or "").strip()
            category = str(row.get("categoria") or row.get("departamento") or "").strip()
            item_type = item_type_label(row)
            if needle and needle not in f"{code} {name} {category} {item_type}".casefold():
                continue
            stocks = row.get("stocks") if isinstance(row.get("stocks"), Mapping) else {}
            grouped.setdefault(item_type, []).append(
                [code, name, category, _format_stock(stocks.get(store_id))]
            )
        sections = [
            {
                "label": label,
                "headers": ["Código", "Nombre", "Categoría", "Stock"],
                "rows": grouped[label],
            }
            for label in sorted(
                grouped,
                key=lambda value: (preferred_order.get(value, 50), value.casefold()),
            )
        ]
        result.append(
            {
                "store_id": store_id,
                "label": store_label(store),
                "sections": sections,
            }
        )
    return result


def build_stock_table(
    matrix: Mapping[str, Any] | None,
    *,
    query: str = "",
) -> tuple[list[str], list[list[str]]]:
    """Convierte la matriz persistida en celdas listas para presentar."""

    if not isinstance(matrix, Mapping):
        return ["Código", "Nombre"], []
    stores = _items(matrix.get("stores"))
    headers = ["Código", "Nombre"]
    store_ids: list[str] = []
    for store in stores:
        store_id = str(store.get("id_tienda") or "").strip()
        if not store_id:
            continue
        store_ids.append(store_id)
        headers.append(store_label(store))

    needle = str(query or "").strip().casefold()
    rows_out: list[list[str]] = []
    for row in _items(matrix.get("rows")):
        code = str(row.get("codigo") or row.get("codigo_norm") or "").strip().upper()
        name = str(row.get("nombre") or "").strip()
        if needle and needle not in f"{code} {name}".casefold():
            continue
        stocks = row.get("stocks") if isinstance(row.get("stocks"), Mapping) else {}
        rows_out.append(
            [code, name, *[_format_stock(stocks.get(store_id)) for store_id in store_ids]]
        )
    return headers, rows_out


__all__ = [
    "build_stock_table",
    "build_store_stock_tabs",
    "item_type_label",
    "store_label",
]
