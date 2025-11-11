# src/models.py
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush

from .config import APP_COUNTRY, CATS
from .pricing import precio_unitario_por_categoria
from .utils import fmt_money_ui, nz
from .logging_setup import get_logger

log = get_logger(__name__)

# Permitir edición de precio unitario en Paraguay y Perú
CAN_EDIT_UNIT_PRICE = (APP_COUNTRY in ("PARAGUAY", "PERU"))

# ===== Alias de llaves de precios (para datasets heterogéneos) =====
UNIT_ALIASES = ["precio_unidad", "precio_unitario", "precio_venta", "unitario"]
OFFER_ALIASES = [
    "precio_oferta", "precio_oferta_base", "oferta",
    ">12 unidades", "precio_12", "precio_12_unidades", "mayor_12", "mayor12", "docena",
    "precio_mayorista"
]
MIN_ALIASES = [
    "precio_minimo", "precio_minimo_base", "minimo",
    ">100 unidades", "precio_100", "precio_100_unidades", "mayor_100", "ciento"
]
MAX_ALIASES = [
    "precio_maximo", "precio_tope", "precio_lista", "pvp", "pvpr", "PRECIO",
    "precio_publico", "precio_retail", "precio_mostrador",
    # 'precio_venta' al final como último fallback:
    "precio_venta"
]
BASE_ALIASES = ["precio_unitario", "precio_unidad", "precio_base_50g", "precio_venta"]


def _first_price(d: dict, *keys):
    for k in keys:
        v = float(nz(d.get(k), 0.0))
        if v > 0:
            return v
    return 0.0


def _first_from_aliases(d: dict, aliases: list[str]) -> float:
    for k in aliases:
        try:
            v = float(nz(d.get(k), 0.0))
            if v > 0:
                return v
        except Exception:
            continue
    return 0.0


def _price_from_tier(prod: dict, tier: str) -> float:
    """Obtiene precio por 'tier': unitario | oferta | minimo | maximo | base."""
    if not isinstance(prod, dict):
        return 0.0
    t = (tier or "").lower().strip()
    if t == "unitario":
        return _first_from_aliases(prod, UNIT_ALIASES)
    if t == "oferta":
        return _first_from_aliases(prod, OFFER_ALIASES)
    if t == "minimo":
        return _first_from_aliases(prod, MIN_ALIASES)
    if t == "maximo":
        return _first_from_aliases(prod, MAX_ALIASES)
    if t == "base":
        return _first_from_aliases(prod, BASE_ALIASES)
    return 0.0


class ItemsModel(QAbstractTableModel):
    HEADERS = ["Código", "Producto", "Cantidad", "Precio Unitario", "Subtotal"]

    # ► Señal: emite el índice de la fila recién agregada
    item_added = Signal(int)

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return str(section + 1)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemIsEnabled
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        if index.column() == 2:
            return base | Qt.ItemIsEditable  # cantidad

        if index.column() == 3:
            # La vista usa NoEditTriggers, pero mantenemos el flag por compatibilidad con delegate/menú
            try:
                it = self._items[index.row()]
                cat = (it.get("categoria") or "").upper()
            except Exception:
                cat = ""
            if CAN_EDIT_UNIT_PRICE or cat == "SERVICIO" or cat == "BOTELLAS":
                return base | Qt.ItemIsEditable

        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self._items[index.row()]
        col = index.column()

        # 🔴 Chequeo de stock usando float (no truncar decimales)
        if role == Qt.ForegroundRole and col == 2:
            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = float(nz(it.get("stock_disponible"), 0.0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
                if cant * mult > disp and disp >= 0:
                    return QBrush(Qt.red)
            except Exception:
                pass

        if role == Qt.ForegroundRole and col == 3:
            if it.get("precio_override") is not None:
                return QBrush(Qt.darkMagenta)

        if role == Qt.ToolTipRole and col == 3:
            if it.get("precio_override") is not None:
                return "Precio personalizado (override). Click derecho → 'Quitar reescritura de precio'."
            tier = it.get("precio_tier")
            if tier:
                return f"Usando precio de catálogo: {tier.capitalize()}"

        if role == Qt.DisplayRole:
            if col == 0:
                return it["codigo"]
            elif col == 1:
                prod = it["producto"]
                if it.get("fragancia"):
                    prod += f" ({it['fragancia']})"
                if it.get("observacion"):
                    prod += f" | {it['observacion']}"
                return prod
            elif col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and cat in CATS:
                    try:
                        return f"{float(it.get('cantidad', 0.0)):.3f}"
                    except Exception:
                        return "0.000"
                else:
                    try:
                        return str(int(round(float(it.get('cantidad', 0)))))
                    except Exception:
                        return "1"
            elif col == 3:
                base_text = fmt_money_ui(float(nz(it.get("precio"))))
                return f"{base_text} ✏️" if it.get("precio_override") is not None else base_text
            elif col == 4:
                return fmt_money_ui(float(nz(it.get("total"))))

        if role == Qt.EditRole:
            if col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    try:
                        return f"{float(nz(it.get('cantidad'), 0.0)):.3f}"
                    except Exception:
                        return "0.000"
                try:
                    return str(int(round(float(nz(it.get("cantidad"), 0)))))
                except Exception:
                    return "1"
            if col == 3 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO","BOTELLAS")):
                try:
                    return f"{float(nz(it.get('precio'), 0.0)):.4f}"
                except Exception:
                    return "0.0000"

        return None

    def _apply_price_and_total(self, it: dict, unit_price: float):
        it["precio"] = float(unit_price)
        qty = float(nz(it.get("cantidad"), 0.0))
        it["total"] = round(float(unit_price) * qty, 2)

    def _recalc_price_for_qty(self, it: dict):
        """Recalcula precio respetando override o tier (para cualquier categoría). Sin tramos por cantidad."""
        cat = (it.get("categoria") or "").upper()
        qty = float(nz(it.get("cantidad"), 0.0))
        prod = it.get("_prod", {}) or {}

        # 1) Override manda
        if it.get("precio_override") is not None:
            self._apply_price_and_total(it, float(nz(it.get("precio_override"), 0.0)))
            return

        # 2) Si hay tier explícito, úsalo (en cualquier categoría)
        t = (it.get("precio_tier") or "").strip().lower()
        if t:
            p = _price_from_tier(prod, t)
            if p > 0:
                self._apply_price_and_total(it, p)
                return

        # 3) Precio por categoría (sin tramos automáticos)
        p = precio_unitario_por_categoria(cat, prod, qty)
        self._apply_price_and_total(it, p)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False

        row = index.row()
        it = self._items[row]
        col = index.column()

        import re
        if col == 2:
            cat = (it.get("categoria") or "").upper()
            txt = str(value).strip().lower().replace(",", ".")
            txt = re.sub(r"[^\d\.\-]", "", txt)

            try:
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    new_qty = float(txt) if txt else 0.001
                    if new_qty < 0.001:
                        new_qty = 0.001
                    new_qty = round(new_qty, 3)
                else:
                    new_qty = int(float(txt)) if txt else 1
                    if new_qty < 1:
                        new_qty = 1
            except Exception:
                return False

            it["cantidad"] = new_qty
            self._recalc_price_for_qty(it)

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col == 3 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO","BOTELLAS")):
            # Soporta payload dict del selector o entrada numérica directa
            if isinstance(value, dict):
                mode = (value.get("mode") or "").lower()
                if mode == "custom":
                    new_price = float(nz(value.get("price"), 0.0))
                    if new_price < 0:
                        new_price = 0.0
                    it["precio_override"] = new_price
                    it["precio_tier"] = None
                    self._apply_price_and_total(it, new_price)

                elif mode == "tier":
                    tier = (value.get("tier") or "").lower().strip()
                    if tier == "base":
                        it["precio_override"] = None
                        it["precio_tier"] = None
                    else:
                        it["precio_override"] = None
                        it["precio_tier"] = tier  # unitario|oferta|minimo|maximo
                    # Recalcular con _price_from_tier (robusto aunque no nos manden 'price')
                    self._recalc_price_for_qty(it)
                else:
                    return False

                top = self.index(row, 0)
                bottom = self.index(row, self.columnCount() - 1)
                self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
                return True

            # 2) Entrada numérica directa (fallback)
            txt = str(value).strip().replace(",", "").replace(" ", "")
            txt = re.sub(r"[^\d\.\-]", "", txt)
            try:
                new_price = float(txt) if txt else 0.0
                if new_price < 0:
                    new_price = 0.0
            except Exception:
                return False

            it["precio_override"] = new_price
            it["precio_tier"] = None
            self._apply_price_and_total(it, new_price)

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        return False

    def add_item(self, item: dict):
        if "precio_override" not in item:
            item["precio_override"] = None
        if "precio_tier" not in item:
            item["precio_tier"] = None
        self.beginInsertRows(QModelIndex(), len(self._items), len(self._items))
        self._items.append(item)
        self.endInsertRows()
        log.debug("Item agregado: %s", item.get("codigo"))
        self.item_added.emit(len(self._items) - 1)

    def remove_rows(self, rows: list[int]):
        for r in sorted(set(rows), reverse=True):
            if 0 <= r < len(self._items):
                self.beginRemoveRows(QModelIndex(), r, r)
                removed = self._items.pop(r)
                self.endRemoveRows()
                log.debug("Item removido: %s", removed.get("codigo"))
