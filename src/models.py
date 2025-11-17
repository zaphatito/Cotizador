# src/models.py
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush

from .config import APP_COUNTRY, CATS, convert_from_base
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
    # Nueva columna de descuento entre Producto y Cantidad
    HEADERS = ["Código", "Producto", "Descuento", "Cantidad", "Precio Unitario", "Subtotal"]

    # ► Señal: emite el índice de la fila recién agregada
    item_added = Signal(int)

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items

    # === helpers internos para descuento / totales ===
    def _normalize_discount_and_totals(self, it: dict, unit_price: float):
        """
        Normaliza:
          - precio (unitario, en moneda base)
          - subtotal_base = precio * cantidad
          - descuento_pct / descuento_monto (según modo)
          - total = subtotal_base - descuento_monto

        Todos los cálculos se hacen en MONEDA BASE.

        Modo de descuento:
          - it['descuento_mode'] == 'percent' → el % es la verdad, el monto se recalcula.
          - it['descuento_mode'] == 'amount'  → el monto es la verdad, el % se deriva.
          - sin modo (None) → se infiere: si d_pct>0 → percent, si d_monto>0 → amount.
        """
        unit_price = float(nz(unit_price, 0.0))
        qty = float(nz(it.get("cantidad"), 0.0))

        subtotal = round(unit_price * qty, 2)
        it["precio"] = unit_price
        it["subtotal_base"] = subtotal

        mode = (it.get("descuento_mode") or "").lower()
        d_pct = float(nz(it.get("descuento_pct"), 0.0))
        d_monto = float(nz(it.get("descuento_monto"), 0.0))

        if subtotal <= 0:
            # Si no hay subtotal, no puede haber descuento
            it["descuento_mode"] = None
            d_pct = 0.0
            d_monto = 0.0
        else:
            # Si no hay modo aún, lo inferimos para mantener compatibilidad
            if not mode:
                if d_pct > 0:
                    mode = "percent"
                elif d_monto > 0:
                    mode = "amount"

            if mode == "percent":
                # Modo porcentaje → mantener precisión del % y recalcular monto
                if d_pct < 0:
                    d_pct = 0.0
                if d_pct > 100:
                    d_pct = 100.0
                d_monto = round(subtotal * d_pct / 100.0, 2)
            elif mode == "amount":
                # Modo monto → respetar monto, derivar % sin redondear extra
                d_monto = max(0.0, min(d_monto, subtotal))
                d_pct = (d_monto / subtotal) * 100.0 if subtotal > 0 else 0.0
            else:
                # Sin modo ni valores válidos → sin descuento
                d_pct = 0.0
                d_monto = 0.0

        it["descuento_mode"] = mode or None
        it["descuento_pct"] = d_pct
        it["descuento_monto"] = d_monto
        it["total"] = round(subtotal - d_monto, 2)

    # === QAbstractTableModel base ===
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

        # Columna 3 = Cantidad (editable)
        if index.column() == 3:
            return base | Qt.ItemIsEditable

        # Columna 2 = Descuento NO es editable desde la celda,
        # se manejará por diálogo externo y setData(programático).
        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        it = self._items[index.row()]
        col = index.column()

        # 🔴 Chequeo de stock usando float → columna Cantidad (3)
        if role == Qt.ForegroundRole and col == 3:
            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = float(nz(it.get("stock_disponible"), 0.0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
                if cant * mult > disp and disp >= 0:
                    return QBrush(Qt.red)
            except Exception:
                pass

        # Precio overridden → colorear columna de precio (4)
        if role == Qt.ForegroundRole and col == 4:
            if it.get("precio_override") is not None:
                return QBrush(Qt.darkMagenta)

        # Tooltips
        if role == Qt.ToolTipRole:
            if col == 4:
                # Precio unitario
                if it.get("precio_override") is not None:
                    return "Precio personalizado (override). Click derecho → 'Quitar precio personalizado'."
                tier = it.get("precio_tier")
                if tier:
                    return f"Usando precio de catálogo: {tier.capitalize()}"
            elif col == 2:
                # Descuento
                subtotal = float(
                    nz(it.get("subtotal_base"),
                       it.get("precio", 0.0) * nz(it.get("cantidad"), 0.0))
                )
                d_pct = float(nz(it.get("descuento_pct"), 0.0))
                d_monto = float(nz(it.get("descuento_monto"), 0.0))
                total = float(nz(it.get("total"), subtotal - d_monto))
                if subtotal <= 0 or (d_pct == 0 and d_monto == 0):
                    return "Sin descuento aplicado."
                return (
                    f"Subtotal base: {fmt_money_ui(convert_from_base(subtotal))}\n"
                    f"Descuento: {fmt_money_ui(convert_from_base(d_monto))} ({d_pct:.4f}%)\n"
                    f"Total neto: {fmt_money_ui(convert_from_base(total))}"
                )

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
                # Columna "Descuento"
                d_pct = float(nz(it.get("descuento_pct"), 0.0))
                d_monto = float(nz(it.get("descuento_monto"), 0.0))
                if d_pct > 0:
                    # ➜ Visualizamos SOLO 2 decimales, pero internamente hay más
                    return f"-{d_pct:.2f}%"
                if d_monto > 0:
                    return f"-{fmt_money_ui(convert_from_base(d_monto))}"
                return "—"
            elif col == 3:
                # Cantidad
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
            elif col == 4:
                # Precio unitario (almacenado en base → se convierte al vuelo)
                base_price = float(nz(it.get("precio")))
                shown_price = convert_from_base(base_price)
                base_text = fmt_money_ui(shown_price)
                return f"{base_text} ✏️" if it.get("precio_override") is not None else base_text
            elif col == 5:
                # Subtotal neto (ya con descuento aplicado)
                subtotal_base = float(nz(it.get("total")))
                return fmt_money_ui(convert_from_base(subtotal_base))

        if role == Qt.EditRole:
            # Cantidad
            if col == 3:
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
            # Precio unitario editable en ciertos países/categorías
            if col == 4 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO", "BOTELLAS")):
                try:
                    return f"{float(nz(it.get('precio'), 0.0)):.4f}"
                except Exception:
                    return "0.0000"

        return None

    # === lógica de precios / cantidades / descuento ===
    def _apply_price_and_total(self, it: dict, unit_price: float):
        # unit_price siempre en moneda base
        self._normalize_discount_and_totals(it, unit_price)

    def _recalc_price_for_qty(self, it: dict):


        cat = (it.get("categoria") or "").upper()
        qty = float(nz(it.get("cantidad"), 0.0))
        prod = it.get("_prod", {}) or {}

        # 1) Override manda (override guardado en base)
        if it.get("precio_override") is not None:
            self._apply_price_and_total(it, float(nz(it.get("precio_override"), 0.0)))
            return

        # 2) Si hay tier explícito, úsalo (en cualquier categoría)
        t = (it.get("precio_tier") or "").strip().lower()
        if t:
            p = _price_from_tier(prod, t)
            if p and p > 0:
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

        # ----- Columna de DESCUENTO (2) -----
        if col == 2:
            # Espera un payload dict desde el widget de descuento:
            # { "mode": "percent"|"amount"|"clear", "percent": float, "amount": float }
            if not isinstance(value, dict):
                return False

            mode = (value.get("mode") or "").lower()
            subtotal = float(nz(it.get("subtotal_base"), 0.0))
            if subtotal <= 0:
                # recomputar subtotal con precio actual si fuera necesario
                precio_base = float(nz(it.get("precio"), 0.0))
                qty = float(nz(it.get("cantidad"), 0.0))
                subtotal = round(precio_base * qty, 2)
                it["subtotal_base"] = subtotal

            d_pct = 0.0
            d_monto = 0.0
            d_mode = None

            if mode == "clear":
                # sin descuento
                d_mode = None
            elif mode == "percent":
                try:
                    d_pct = float(nz(value.get("percent"), 0.0))
                except Exception:
                    d_pct = 0.0
                if d_pct < 0:
                    d_pct = 0.0
                if d_pct > 100:
                    d_pct = 100.0
                d_monto = round(subtotal * d_pct / 100.0, 2)
                d_mode = "percent"
            elif mode == "amount":
                try:
                    d_monto = float(nz(value.get("amount"), 0.0))
                except Exception:
                    d_monto = 0.0
                if d_monto < 0:
                    d_monto = 0.0
                if d_monto > subtotal:
                    d_monto = subtotal
                # Derivamos el % SIN redondear (máxima precisión)
                d_pct = (d_monto / subtotal) * 100.0 if subtotal > 0 else 0.0
                d_mode = "amount"
            else:
                return False

            it["descuento_mode"] = d_mode
            it["descuento_pct"] = d_pct
            it["descuento_monto"] = d_monto
            it["total"] = round(subtotal - d_monto, 2)

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        # ----- Columna de CANTIDAD (3) -----
        if col == 3:
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

        # ----- Columna de PRECIO UNITARIO (4) -----
        if col == 4 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO", "BOTELLAS")):
            # Soporta payload dict del selector o entrada numérica directa
            if isinstance(value, dict):
                mode = (value.get("mode") or "").lower()
                if mode == "custom":
                    new_price = float(nz(value.get("price"), 0.0))
                    if new_price < 0:
                        new_price = 0.0
                    # override en MONEDA BASE
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
                        it["precio_tier"] = tier  # unitario|oferta|minimo|maximo|...
                    self._recalc_price_for_qty(it)
                else:
                    return False

                top = self.index(row, 0)
                bottom = self.index(row, self.columnCount() - 1)
                self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
                return True

            # 2) Entrada numérica directa (fallback) → se toma como MONEDA BASE
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

    # === Gestión de filas ===
    def add_item(self, item: dict):
        # Inicializar llaves de precio/discount si no existen
        if "precio_override" not in item:
            item["precio_override"] = None
        if "precio_tier" not in item:
            item["precio_tier"] = None
        if "descuento_mode" not in item:
            item["descuento_mode"] = None
        if "descuento_pct" not in item:
            item["descuento_pct"] = 0.0
        if "descuento_monto" not in item:
            item["descuento_monto"] = 0.0

        # Normalizar totales (subtotal / total) según precio y cantidad actuales
        try:
            unit_price = float(nz(item.get("precio"), 0.0))
        except Exception:
            unit_price = 0.0
        self._normalize_discount_and_totals(item, unit_price)

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