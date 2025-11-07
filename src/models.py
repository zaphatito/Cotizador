# src/models.py
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush

from .config import APP_COUNTRY, CATS
from .pricing import precio_unitario_por_categoria, reglas_cantidad
from .utils import fmt_money_ui, nz
from .logging_setup import get_logger

log = get_logger(__name__)

CAN_EDIT_UNIT_PRICE = (APP_COUNTRY == "PARAGUAY")


class ItemsModel(QAbstractTableModel):
    HEADERS = ["Código", "Producto", "Cantidad", "Precio Unitario", "Subtotal"]

    # ► Señal: emite el índice de la fila recién agregada
    item_added = Signal(int)

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items

    def rowCount(self, parent=QModelIndex()) -> int: return len(self._items)
    def columnCount(self, parent=QModelIndex()) -> int: return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole: return None
        if orientation == Qt.Horizontal: return self.HEADERS[section]
        return str(section + 1)

    def flags(self, index):
        if not index.isValid(): return Qt.ItemIsEnabled
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        if index.column() == 2:
            return base | Qt.ItemIsEditable

        if index.column() == 3:
            # Editable si: Paraguay (cualquier ítem) o categoría SERVICIO (siempre)
            try:
                it = self._items[index.row()]
                cat = (it.get("categoria") or "").upper()
            except Exception:
                cat = ""
            if CAN_EDIT_UNIT_PRICE or cat == "SERVICIO":
                return base | Qt.ItemIsEditable

        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return None
        it = self._items[index.row()]
        col = index.column()

        if role == Qt.ForegroundRole and col == 2:
            try:
                cat_u = (it.get("categoria") or "").upper()
                disp = int(nz(it.get("stock_disponible"), 0))
                cant = float(nz(it.get("cantidad"), 0))
                mult = 50.0 if (APP_COUNTRY in ("VENEZUELA", "PARAGUAY") and cat_u in CATS) else 1.0
                cant_efectiva = cant * mult
                if cant_efectiva > disp and disp >= 0:
                    return QBrush(Qt.red)
            except Exception:
                pass

        if role == Qt.ForegroundRole and col == 3:
            if it.get("precio_override") is not None:
                return QBrush(Qt.darkMagenta)

        if role == Qt.ToolTipRole and col == 3 and it.get("precio_override") is not None:
            return "Precio reescrito manualmente. Click derecho → 'Quitar reescritura de precio' para restaurar."

        if role == Qt.DisplayRole:
            if col == 0:
                return it["codigo"]
            elif col == 1:
                prod = it["producto"]
                if it.get("fragancia"):   prod += f" ({it['fragancia']})"
                if it.get("observacion"): prod += f" | {it['observacion']}"
                return prod
            elif col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and cat in CATS:
                    try: return f"{float(it.get('cantidad', 0.0)):.3f}"
                    except Exception: return "0.000"
                else:
                    try: return str(int(round(float(it.get('cantidad', 0)))))
                    except Exception: return "1"
            elif col == 3:
                base_text = fmt_money_ui(float(nz(it.get("precio"))))
                return f"{base_text} ✏️" if it.get("precio_override") is not None else base_text
            elif col == 4:
                return fmt_money_ui(float(nz(it.get("total"))))

        if role == Qt.EditRole:
            if col == 2:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    return f"{float(nz(it.get('cantidad'), 0.0)):.3f}"
                try: return str(int(round(float(nz(it.get("cantidad"), 0)))))
                except Exception: return "1"
            if col == 3 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() == "SERVICIO"):
                try: return f"{float(nz(it.get('precio'), 0.0)):.4f}"
                except Exception: return "0.0000"

        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False

        row = index.row()
        it = self._items[row]
        col = index.column()

        import re
        if col == 2:
            cat = (it.get("categoria") or "").upper()
            min_u, step = reglas_cantidad(cat)

            txt = str(value).strip().lower().replace(",", ".")
            txt = re.sub(r"[^\d\.\-]", "", txt)

            try:
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    new_qty = float(txt) if txt else float(min_u)
                    if new_qty < min_u: new_qty = min_u
                    new_qty = round(new_qty, 3)
                else:
                    new_qty = int(float(txt)) if txt else int(min_u)
                    if new_qty < int(min_u): new_qty = int(min_u)
            except Exception:
                return False

            it["cantidad"] = new_qty

            # Si hay override de precio, lo respetamos; si no, recalculamos
            override = it.get("precio_override", None)
            unit_price = float(override) if override is not None and override >= 0 \
                else precio_unitario_por_categoria(cat, it.get("_prod", {}), float(new_qty))
            it["precio"] = float(unit_price)
            it["total"]  = round(float(unit_price) * float(new_qty), 2)

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col == 3 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() == "SERVICIO"):
            txt = str(value).strip().replace(",", "").replace(" ", "")
            txt = re.sub(r"[^\d\.\-]", "", txt)
            try:
                new_price = float(txt) if txt else 0.0
                if new_price < 0: new_price = 0.0
            except Exception:
                return False

            it["precio_override"] = new_price
            it["precio"] = new_price
            qty = float(nz(it.get("cantidad"), 0.0))
            it["total"] = round(new_price * qty, 2)

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        return False

    def add_item(self, item: dict):
        if "precio_override" not in item:
            item["precio_override"] = None
        self.beginInsertRows(QModelIndex(), len(self._items), len(self._items))
        self._items.append(item)
        self.endInsertRows()
        log.debug("Item agregado: %s", item.get("codigo"))
        # ► Notificar a la vista para scroll/foco en el último
        self.item_added.emit(len(self._items) - 1)

    def remove_rows(self, rows: list[int]):
        for r in sorted(set(rows), reverse=True):
            if 0 <= r < len(self._items):
                self.beginRemoveRows(QModelIndex(), r, r)
                removed = self._items.pop(r)
                self.endRemoveRows()
                log.debug("Item removido: %s", removed.get("codigo"))
