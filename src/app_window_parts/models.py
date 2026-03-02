# src/models.py
import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QFont

from ..config import APP_COUNTRY, CATS, convert_from_base
from ..pricing import precio_unitario_por_categoria, factor_total_por_categoria
from ..utils import fmt_money_ui, nz
from ..logging_setup import get_logger

log = get_logger(__name__)

CAN_EDIT_UNIT_PRICE = (APP_COUNTRY in ("PARAGUAY", "PERU"))

PY_CASH_BASE_PCT = 4.7619
_PCT_EPS = 1e-6

UNIT_ALIASES = ["p_max", "P_MAX", "unitario"]
OFFER_ALIASES = ["p_oferta", "P_OFERTA", "oferta"]
MIN_ALIASES = ["p_min", "P_MIN", "minimo"]
MAX_ALIASES = ["p_max", "P_MAX"]
BASE_ALIASES = ["p_max", "P_MAX"]

PRICE_ID_P_MAX = 1
PRICE_ID_P_MIN = 2
PRICE_ID_P_OFERTA = 3
PRICE_ID_PERSONALIZADO = 4

INLINE_PRICE_TIER_MAP = {
    "p_max": "unitario",
    "p_min": "minimo",
    "p_oferta": "oferta",
}

INLINE_PRICE_TIER_REVERSE = {
    "unitario": "p_max",
    "maximo": "p_max",
    "base": "p_max",
    "minimo": "p_min",
    "oferta": "p_oferta",
}


def _is_service_category(cat: str) -> bool:
    return str(cat or "").strip().upper() == "SERVICIO"


def _price_id_from_tier(tier: str) -> int:
    t = _norm_tier_name(str(tier or ""))
    if t == "minimo":
        return PRICE_ID_P_MIN
    if t == "oferta":
        return PRICE_ID_P_OFERTA
    return PRICE_ID_P_MAX


def _tier_from_price_id(price_id) -> str:
    try:
        pid = int(price_id)
    except Exception:
        pid = PRICE_ID_P_MAX
    if pid == PRICE_ID_P_MIN:
        return "minimo"
    if pid == PRICE_ID_P_OFERTA:
        return "oferta"
    return "unitario"


def _default_price_id_from_prod(prod: dict) -> int:
    # Regla de negocio: el precio por defecto siempre es p_max.
    return PRICE_ID_P_MAX


def _closest_allowed_tier(prod: dict, target_price: float) -> tuple[str, float]:
    candidates: list[tuple[str, float]] = []
    for tier in ("unitario", "oferta", "minimo"):
        p = _price_from_tier(prod, tier)
        if p and p > 0:
            candidates.append((tier, float(p)))

    if not candidates:
        return "unitario", 0.0

    try:
        target = float(target_price)
    except Exception:
        target = 0.0

    if target <= 0:
        return candidates[0]

    order = {"unitario": 0, "oferta": 1, "minimo": 2}
    return min(candidates, key=lambda kv: (abs(kv[1] - target), order.get(kv[0], 9)))


def _norm_tier_name(tier: str) -> str:
    """
    Normaliza tier (inglés/español) a los nombres internos:
    unitario|oferta|minimo|maximo|base
    """
    t = (tier or "").lower().strip()
    mp = {
        # oferta
        "offer": "oferta",
        "oferta": "oferta",
        "promo": "oferta",
        "promotion": "oferta",
        # minimo
        "min": "minimo",
        "minimum": "minimo",
        "minimo": "minimo",
        # unitario
        "unit": "unitario",
        "unitario": "unitario",
        "regular": "unitario",
        # maximo
        "max": "maximo",
        "maximum": "maximo",
        "maximo": "maximo",
        "lista": "maximo",
        "pvp": "maximo",
        # base
        "base": "base",
    }
    return mp.get(t, t)


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
    if not isinstance(prod, dict):
        return 0.0
    t = _norm_tier_name(tier)
    if t == "unitario":
        return _first_from_aliases(prod, UNIT_ALIASES)
    if t == "oferta":
        return _first_from_aliases(prod, OFFER_ALIASES)
    if t == "minimo":
        return _first_from_aliases(prod, MIN_ALIASES)
    if t == "maximo":
        return _first_from_aliases(prod, MAX_ALIASES)
    if t == "base":
        pid = _default_price_id_from_prod(prod)
        if pid == PRICE_ID_P_MIN:
            return _first_from_aliases(prod, MIN_ALIASES)
        if pid == PRICE_ID_P_OFERTA:
            return _first_from_aliases(prod, OFFER_ALIASES)
        return _first_from_aliases(prod, BASE_ALIASES)
    return 0.0


def _parse_qty_peru_cats(value) -> float:
    s = str(value).strip()
    if not s:
        return 0.001

    if "." in s or "," in s:
        t = s.replace(",", ".")
        t = re.sub(r"[^\d\.\-]", "", t)
        try:
            x = float(t) if t else 0.0
        except Exception:
            x = 0.0
        x = round(x, 3)
        if x < 0.001:
            x = 0.001
        return x

    digits = re.sub(r"\D", "", s)
    if not digits:
        return 0.001

    try:
        x = int(digits) / 1000.0
    except Exception:
        x = 0.0

    x = round(x, 3)
    if x < 0.001:
        x = 0.001
    return x


class ItemsModel(QAbstractTableModel):
    HEADERS = ["Código", "Producto", "Descuento", "Cantidad", "Precio Unitario", "Subtotal"]

    item_added = Signal(int)
    toast_requested = Signal(str)

    def __init__(self, items: list[dict]):
        super().__init__()
        self._items = items
        self._py_cash_mode = False
        self._recs_preview: list[dict] = []
        self._code_edit_handler = None

    def set_code_edit_handler(self, handler) -> None:
        self._code_edit_handler = handler if callable(handler) else None

    def _price_tier_token_for_edit(self, it: dict) -> str:
        tier = _norm_tier_name(str(it.get("precio_tier") or ""))
        if not tier:
            tier = _tier_from_price_id(it.get("id_precioventa"))
        return INLINE_PRICE_TIER_REVERSE.get(tier, "p_max")

    def _parse_inline_discount(self, raw_value):
        txt = str(raw_value or "").strip()
        if not txt:
            return {"mode": "clear"}

        normalized = txt.lower().replace(",", ".").replace(" ", "")
        is_amount = normalized.startswith(("m:", "a:"))
        if is_amount:
            normalized = normalized.split(":", 1)[1] if ":" in normalized else ""
            normalized = re.sub(r"[^\d\.\-]", "", normalized)
            if not normalized:
                return None
            try:
                amount = float(normalized)
            except Exception:
                return None
            if amount < 0:
                amount = 0.0
            return {"mode": "amount", "amount": amount}

        if normalized.endswith("%"):
            normalized = normalized[:-1]
        normalized = re.sub(r"[^\d\.\-]", "", normalized)
        if not normalized:
            return None
        try:
            pct = float(normalized)
        except Exception:
            return None
        return {"mode": "percent", "percent": pct}

    def set_recommendations_preview(self, recs: list[dict]):
        recs = recs or []
        built: list[dict] = []

        for r in recs:
            try:
                code = str(r.get("codigo") or "").strip().upper()
                if not code:
                    continue

                nombre = str(r.get("nombre") or "").strip()
                pr = float(nz(r.get("price_base"), 0.0))
                if pr <= 0:
                    continue

                cat = str(r.get("categoria") or "").strip()
                cat_u = cat.upper()

                qty_in = float(nz(r.get("qty"), 0.0))
                if APP_COUNTRY == "PERU" and cat_u in CATS:
                    qty = round(qty_in, 3)
                    if qty < 0.001:
                        qty = 0.001
                else:
                    qty = int(round(qty_in)) if qty_in else 1
                    if qty < 1:
                        qty = 1

                score = float(nz(r.get("score"), 0.0))
                reason = str(r.get("reason") or "").strip()

                it = {
                    "__preview": True,
                    "_rec_reason": reason,
                    "_rec_score": score,
                    "_rec_kind": str(r.get("kind") or ""),
                    "codigo": code,
                    "producto": nombre or code,
                    "cantidad": qty,
                    "precio": pr,
                    "precio_override": None,
                    "precio_tier": None,
                    "descuento_mode": None,
                    "descuento_pct": 0.0,
                    "descuento_monto": 0.0,
                    "categoria": cat,
                }

                try:
                    self._normalize_discount_and_totals(it, float(pr))
                except Exception:
                    it["subtotal_base"] = round(float(pr) * float(qty), 2)
                    it["total"] = it["subtotal_base"]

                built.append(it)
            except Exception:
                continue

        if built == self._recs_preview:
            return

        self.beginResetModel()
        self._recs_preview = built
        self.endResetModel()

    def clear_recommendations_preview(self):
        if not self._recs_preview:
            return
        self.beginResetModel()
        self._recs_preview = []
        self.endResetModel()

    def _is_preview_row(self, row: int) -> bool:
        return row >= len(self._items)

    def _row_item(self, row: int) -> dict | None:
        if row < 0:
            return None
        if row < len(self._items):
            return self._items[row]
        i = row - len(self._items)
        if 0 <= i < len(self._recs_preview):
            return self._recs_preview[i]
        return None

    def is_preview_row(self, row: int) -> bool:
        return self._is_preview_row(row)

    def get_preview_payload(self, row: int) -> dict | None:
        it = self._row_item(row)
        if not it or not it.get("__preview"):
            return None
        return {
            "codigo": str(it.get("codigo") or "").strip().upper(),
            "qty": float(nz(it.get("cantidad"), 0.0)),
            "price_base": float(nz(it.get("precio"), 0.0)),
            "reason": str(it.get("_rec_reason") or "").strip(),
            "score": float(nz(it.get("_rec_score"), 0.0)),
            "kind": str(it.get("_rec_kind") or ""),
            "categoria": str(it.get("categoria") or "").strip(),
        }

    def is_py_cash_mode(self) -> bool:
        return bool(self._py_cash_mode) if APP_COUNTRY == "PARAGUAY" else False

    def _sync_py_cash_user_pct_from_loaded_items(self) -> bool:
        changed = False
        for it in self._items:
            unit = float(nz(it.get("precio"), 0.0))
            subtotal = float(nz(it.get("subtotal_base"), 0.0))
            if subtotal <= 0:
                subtotal = self._compute_subtotal_base(it, unit_price=unit)
                it["subtotal_base"] = subtotal

            mode = (it.get("descuento_mode") or "").lower()
            d_pct = float(nz(it.get("descuento_pct"), 0.0))
            d_monto = float(nz(it.get("descuento_monto"), 0.0))
            cur_total_pct = self._effective_discount_pct(subtotal, mode, d_pct, d_monto)

            user_pct = max(0.0, float(cur_total_pct) - PY_CASH_BASE_PCT)
            user_pct = self._clamp_pct(user_pct)

            prev = it.get("_py_user_disc_pct", None)
            if prev is None or abs(float(nz(prev, 0.0)) - user_pct) > 1e-9:
                it["_py_user_disc_pct"] = user_pct
                changed = True

            if subtotal > 0 and (cur_total_pct + _PCT_EPS) < PY_CASH_BASE_PCT:
                it["descuento_mode"] = "percent"
                it["descuento_pct"] = PY_CASH_BASE_PCT
                it["descuento_monto"] = round(subtotal * PY_CASH_BASE_PCT / 100.0, 2)
                it["total"] = round(subtotal - float(nz(it.get("descuento_monto"), 0.0)), 2)
                changed = True

        return changed

    def set_py_cash_mode(self, enabled: bool, *, assume_items_already: bool = False):
        if APP_COUNTRY != "PARAGUAY":
            return
        enabled = bool(enabled)

        if self._py_cash_mode == enabled:
            if enabled and assume_items_already:
                changed = self._sync_py_cash_user_pct_from_loaded_items()
                if changed and self.rowCount() > 0:
                    top = self.index(0, 0)
                    bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
                    self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return

        changed = False

        if assume_items_already:
            self._py_cash_mode = enabled
            if enabled:
                changed = self._sync_py_cash_user_pct_from_loaded_items()
        else:
            if enabled:
                self._py_cash_mode = True
                changed = self._apply_cash_base_to_all()
            else:
                self._py_cash_mode = False
                changed = self._remove_cash_base_from_all()

        if changed and self.rowCount() > 0:
            top = self.index(0, 0)
            bottom = self.index(self.rowCount() - 1, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

    def _get_factor_total(self, it: dict) -> float:
        try:
            f = float(nz(it.get("factor_total"), 0.0))
        except Exception:
            f = 0.0
        if f > 0:
            return f
        cat = (it.get("categoria") or "").upper()
        return float(factor_total_por_categoria(cat))

    def _compute_subtotal_base(self, it: dict, unit_price: float | None = None) -> float:
        if unit_price is None:
            unit_price = float(nz(it.get("precio"), 0.0))
        qty = float(nz(it.get("cantidad"), 0.0))
        factor = self._get_factor_total(it)
        return round(float(unit_price) * qty * factor, 2)

    def _effective_discount_pct(self, subtotal: float, mode: str, d_pct: float, d_monto: float) -> float:
        if subtotal <= 0:
            return 0.0

        mode_u = str(mode or "").strip().lower()
        if mode_u == "amount":
            if d_monto > 0:
                return (float(d_monto) / float(subtotal)) * 100.0
            return 0.0

        if d_pct > 0:
            return float(d_pct)

        if d_monto > 0:
            return (float(d_monto) / float(subtotal)) * 100.0

        return 0.0

    def _clamp_pct(self, pct: float) -> float:
        try:
            pct = float(pct)
        except Exception:
            pct = 0.0
        if pct < 0:
            pct = 0.0
        if pct > 100:
            pct = 100.0
        return pct

    def _maybe_snap_override_to_tier(self, it: dict, unit_price: float) -> float:
        cat = (it.get("categoria") or "").upper()
        prod = it.get("_prod", {}) or {}
        if _is_service_category(cat):
            it["id_precioventa"] = PRICE_ID_PERSONALIZADO
            it["precio_tier"] = None
            return float(unit_price)

        try:
            if it.get("precio_override") is None:
                pid = int(nz(it.get("id_precioventa"), 0) or 0)
                if pid not in (PRICE_ID_P_MAX, PRICE_ID_P_MIN, PRICE_ID_P_OFERTA):
                    tier_raw = str(it.get("precio_tier") or "").strip()
                    if tier_raw:
                        pid = _price_id_from_tier(tier_raw)
                    else:
                        pid = _default_price_id_from_prod(prod)
                it["id_precioventa"] = pid
                it["precio_tier"] = _tier_from_price_id(pid)
                it["precio_override"] = None
                return float(unit_price)
        except Exception:
            return float(unit_price)
        try:
            up = float(unit_price)
        except Exception:
            up = 0.0

        def eq2(a: float, b: float) -> bool:
            try:
                return round(float(a), 2) == round(float(b), 2)
            except Exception:
                return False

        for tier in ("unitario", "oferta", "minimo"):
            p = _price_from_tier(prod, tier)
            if p and p > 0 and eq2(p, up):
                it["precio_override"] = None
                it["precio_tier"] = tier
                it["id_precioventa"] = _price_id_from_tier(tier)
                return float(p)

        tier_best, p_best = _closest_allowed_tier(prod, up)
        it["precio_override"] = None
        it["precio_tier"] = tier_best
        it["id_precioventa"] = _price_id_from_tier(tier_best)
        if p_best > 0:
            return float(p_best)

        p_base = _price_from_tier(prod, "unitario")
        return float(p_base if p_base > 0 else 0.0)

    def _apply_cash_base_to_all(self) -> bool:
        changed = False
        for it in self._items:
            unit = float(nz(it.get("precio"), 0.0))
            subtotal = self._compute_subtotal_base(it, unit_price=unit)

            mode = (it.get("descuento_mode") or "").lower()
            d_pct = float(nz(it.get("descuento_pct"), 0.0))
            d_monto = float(nz(it.get("descuento_monto"), 0.0))
            cur_total_pct = self._effective_discount_pct(subtotal, mode, d_pct, d_monto)

            user_pct = self._clamp_pct(cur_total_pct)
            it["_py_user_disc_pct"] = user_pct

            total_pct = self._clamp_pct(user_pct + PY_CASH_BASE_PCT)
            it["descuento_mode"] = "percent"
            it["descuento_pct"] = total_pct
            it["descuento_monto"] = round(subtotal * total_pct / 100.0, 2)

            it["subtotal_base"] = subtotal
            it["total"] = round(subtotal - float(nz(it.get("descuento_monto"), 0.0)), 2)

            changed = True
        return changed

    def _remove_cash_base_from_all(self) -> bool:
        changed = False
        for it in self._items:
            unit = float(nz(it.get("precio"), 0.0))
            subtotal = self._compute_subtotal_base(it, unit_price=unit)

            mode = (it.get("descuento_mode") or "").lower()
            d_pct = float(nz(it.get("descuento_pct"), 0.0))
            d_monto = float(nz(it.get("descuento_monto"), 0.0))
            cur_total_pct = self._effective_discount_pct(subtotal, mode, d_pct, d_monto)

            user_pct = max(0.0, float(cur_total_pct) - PY_CASH_BASE_PCT)
            user_pct = self._clamp_pct(user_pct)

            it["_py_user_disc_pct"] = user_pct

            if user_pct <= _PCT_EPS:
                it["descuento_mode"] = None
                it["descuento_pct"] = 0.0
                it["descuento_monto"] = 0.0
            else:
                it["descuento_mode"] = "percent"
                it["descuento_pct"] = user_pct
                it["descuento_monto"] = round(subtotal * user_pct / 100.0, 2)

            it["subtotal_base"] = subtotal
            it["total"] = round(subtotal - float(nz(it.get("descuento_monto"), 0.0)), 2)

            changed = True
        return changed

    def _normalize_discount_and_totals(self, it: dict, unit_price: float):
        unit_price = float(nz(unit_price, 0.0))
        unit_price = self._maybe_snap_override_to_tier(it, unit_price)
        it["precio"] = unit_price

        factor = self._get_factor_total(it)
        it["factor_total"] = factor

        subtotal = self._compute_subtotal_base(it, unit_price=unit_price)
        it["subtotal_base"] = subtotal

        mode = (it.get("descuento_mode") or "").lower()
        d_pct = float(nz(it.get("descuento_pct"), 0.0))
        d_monto = float(nz(it.get("descuento_monto"), 0.0))

        if subtotal <= 0:
            it["descuento_mode"] = None
            it["descuento_pct"] = 0.0
            it["descuento_monto"] = 0.0
            it["total"] = 0.0
            return

        if self.is_py_cash_mode():
            try:
                user_pct = float(nz(it.get("_py_user_disc_pct"), None))
            except Exception:
                user_pct = None

            if user_pct is None:
                cur_total_pct = self._effective_discount_pct(subtotal, mode, d_pct, d_monto)
                user_pct = max(0.0, float(cur_total_pct) - PY_CASH_BASE_PCT)

            user_pct = self._clamp_pct(user_pct)
            it["_py_user_disc_pct"] = user_pct

            total_pct = self._clamp_pct(user_pct + PY_CASH_BASE_PCT)

            it["descuento_mode"] = "percent"
            it["descuento_pct"] = total_pct
            it["descuento_monto"] = round(subtotal * total_pct / 100.0, 2)
            it["total"] = round(subtotal - float(nz(it.get("descuento_monto"), 0.0)), 2)
            return

        if not mode:
            if d_pct > 0:
                mode = "percent"
            elif d_monto > 0:
                mode = "amount"

        if mode == "percent":
            d_pct = self._clamp_pct(d_pct)
            d_monto = round(subtotal * d_pct / 100.0, 2)
        elif mode == "amount":
            d_monto = max(0.0, min(d_monto, subtotal))
            d_pct = (d_monto / subtotal) * 100.0 if subtotal > 0 else 0.0
        else:
            d_pct = 0.0
            d_monto = 0.0
            mode = ""

        it["descuento_mode"] = mode or None
        it["descuento_pct"] = d_pct
        it["descuento_monto"] = d_monto
        it["total"] = round(subtotal - d_monto, 2)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._items) + len(self._recs_preview)

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

        row = index.row()
        col = index.column()

        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable

        if self._is_preview_row(row):
            return base

        can_edit_price = (
            col == 4
            and (
                CAN_EDIT_UNIT_PRICE
                or (self._items[row].get("categoria") or "").upper() in ("SERVICIO", "BOTELLAS")
            )
        )

        if col in (0, 1, 2, 3) or can_edit_price:
            return base | Qt.ItemIsEditable

        return base

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()
        it = self._row_item(row)
        if it is None:
            return None

        is_preview = bool(it.get("__preview"))

        if is_preview:
            if role == Qt.ForegroundRole:
                return QBrush(Qt.darkGray)
            if role == Qt.FontRole:
                f = QFont()
                f.setItalic(True)
                return f
            if role == Qt.ToolTipRole:
                rsn = str(it.get("_rec_reason") or "").strip()
                sc = float(nz(it.get("_rec_score"), 0.0))
                if rsn:
                    return f"Doble clic / Enter para agregar.\nRecomendación ({sc:.0%}): {rsn}"
                return f"Doble clic / Enter para agregar.\nRecomendación ({sc:.0%})."

        if role == Qt.DisplayRole:
            if col == 0:
                return it["codigo"]
            elif col == 1:
                prod = it["producto"]
                if (not is_preview) and it.get("fragancia"):
                    prod += f" ({it['fragancia']})"
                if (not is_preview) and it.get("observacion"):
                    prod += f" | {it['observacion']}"
                if is_preview:
                    sc = float(nz(it.get("_rec_score"), 0.0))
                    if sc > 0:
                        prod = f"{prod}  •  Recomendado {sc:.0%}"
                return prod
            elif col == 2:
                d_pct = float(nz(it.get("descuento_pct"), 0.0))
                d_monto = float(nz(it.get("descuento_monto"), 0.0))
                if d_pct > 0:
                    return f"-{d_pct:.2f}%"
                if d_monto > 0:
                    return f"-{fmt_money_ui(convert_from_base(d_monto))}"
                return "—"
            elif col == 3:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and cat in CATS:
                    try:
                        return f"{float(nz(it.get('cantidad'), 0.0)):.3f}"
                    except Exception:
                        return "0.001"
                else:
                    try:
                        return str(int(round(float(nz(it.get('cantidad'), 0)))))
                    except Exception:
                        return "1"
            elif col == 4:
                base_price = float(nz(it.get("precio"), 0.0))
                shown_price = convert_from_base(base_price)
                base_text = fmt_money_ui(shown_price)
                return f"{base_text} ✏️" if it.get("precio_override") is not None else base_text
            elif col == 5:
                total_base = float(nz(it.get("total"), 0.0))
                return fmt_money_ui(convert_from_base(total_base))

        if role == Qt.EditRole:
            if is_preview:
                return None

            if col == 0:
                return str(it.get("codigo") or "").strip().upper()

            if col == 1:
                return str(it.get("observacion") or "")

            if col == 2:
                d_pct = float(nz(it.get("descuento_pct"), 0.0))
                d_monto = float(nz(it.get("descuento_monto"), 0.0))
                if d_pct > 0:
                    return f"{d_pct:.4f}%"
                if d_monto > 0:
                    return f"m:{d_monto:.2f}"
                return ""

            if col == 3:
                cat = (it.get("categoria") or "").upper()
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    try:
                        return f"{float(nz(it.get('cantidad'), 0.0)):.3f}"
                    except Exception:
                        return "0.001"
                try:
                    return str(int(round(float(nz(it.get("cantidad"), 0)))))
                except Exception:
                    return "1"

            if col == 4 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO", "BOTELLAS")):
                return self._price_tier_token_for_edit(it)

        return None

    def _apply_price_and_total(self, it: dict, unit_price: float):
        self._normalize_discount_and_totals(it, unit_price)

    def _recalc_price_for_qty(self, it: dict):
        cat = (it.get("categoria") or "").upper()
        qty = float(nz(it.get("cantidad"), 0.0))
        prod = it.get("_prod", {}) or {}

        if _is_service_category(cat):
            it["id_precioventa"] = PRICE_ID_PERSONALIZADO
            it["precio_tier"] = None
            if it.get("precio_override") is None:
                it["precio_override"] = float(nz(it.get("precio"), 0.0))
            self._apply_price_and_total(it, float(nz(it.get("precio_override"), 0.0)))
            return

        # No se permite precio personalizado para no-servicios.
        it["precio_override"] = None
        pid = int(nz(it.get("id_precioventa"), 0) or 0)
        if pid not in (PRICE_ID_P_MAX, PRICE_ID_P_MIN, PRICE_ID_P_OFERTA):
            tier_raw = str(it.get("precio_tier") or "").strip()
            if tier_raw:
                pid = _price_id_from_tier(tier_raw)
            else:
                pid = _default_price_id_from_prod(prod)
        it["id_precioventa"] = pid

        t = _tier_from_price_id(pid)
        it["precio_tier"] = t
        p = _price_from_tier(prod, t)
        if p and p > 0:
            self._apply_price_and_total(it, p)
            return

        p = precio_unitario_por_categoria(cat, prod, qty)
        self._apply_price_and_total(it, p)

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False

        row = index.row()
        col = index.column()

        if self._is_preview_row(row):
            return False

        it = self._items[row]

        if col == 0:
            code_u = str(value or "").strip().upper()
            if not code_u:
                return False

            old_code = str(it.get("codigo") or "").strip().upper()
            if code_u == old_code:
                return True

            if not callable(self._code_edit_handler):
                return False
            try:
                return bool(self._code_edit_handler(row, code_u))
            except Exception:
                return False

        if col == 1:
            it["observacion"] = str(value or "").strip()
            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col == 2:
            if not isinstance(value, dict):
                parsed = self._parse_inline_discount(value)
                if not isinstance(parsed, dict):
                    return False
                value = parsed

            unit = float(nz(it.get("precio"), 0.0))
            subtotal = float(nz(it.get("subtotal_base"), 0.0))
            if subtotal <= 0:
                subtotal = self._compute_subtotal_base(it, unit_price=unit)
                it["subtotal_base"] = subtotal

            mode = (value.get("mode") or "").lower()

            if self.is_py_cash_mode():
                if mode == "clear":
                    total_pct = 0.0
                elif mode == "percent":
                    try:
                        total_pct = float(nz(value.get("percent"), 0.0))
                    except Exception:
                        total_pct = 0.0
                elif mode == "amount":
                    try:
                        amt = float(nz(value.get("amount"), 0.0))
                    except Exception:
                        amt = 0.0
                    amt = max(0.0, min(amt, subtotal))
                    total_pct = (amt / subtotal) * 100.0 if subtotal > 0 else 0.0
                else:
                    return False

                if subtotal > 0:
                    if total_pct + _PCT_EPS < PY_CASH_BASE_PCT:
                        total_pct = PY_CASH_BASE_PCT

                total_pct = self._clamp_pct(total_pct)

                user_pct = max(0.0, total_pct - PY_CASH_BASE_PCT)
                user_pct = self._clamp_pct(user_pct)
                it["_py_user_disc_pct"] = user_pct

                it["descuento_mode"] = "percent"
                it["descuento_pct"] = total_pct
                it["descuento_monto"] = round(subtotal * total_pct / 100.0, 2)
                it["total"] = round(subtotal - float(nz(it.get("descuento_monto"), 0.0)), 2)

                top = self.index(row, 0)
                bottom = self.index(row, self.columnCount() - 1)
                self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
                return True

            d_pct = 0.0
            d_monto = 0.0
            d_mode = None

            if mode == "clear":
                d_mode = None
            elif mode == "percent":
                try:
                    d_pct = float(nz(value.get("percent"), 0.0))
                except Exception:
                    d_pct = 0.0
                d_pct = max(0.0, min(d_pct, 100.0))
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

        if col == 3:
            old_qty = float(nz(it.get("cantidad"), 0.0))
            cat = (it.get("categoria") or "").upper()
            txt_raw = str(value).strip()

            try:
                if APP_COUNTRY == "PERU" and (cat in CATS):
                    new_qty = _parse_qty_peru_cats(txt_raw)
                else:
                    txt = txt_raw.lower().replace(",", ".")
                    txt = re.sub(r"[^\d\.\-]", "", txt)
                    new_qty = int(float(txt)) if txt else 1
                    if new_qty < 1:
                        new_qty = 1
            except Exception:
                return False

            it["cantidad"] = new_qty
            self._recalc_price_for_qty(it)
            try:
                if cat == "BOTELLAS" and old_qty <= 12 and float(new_qty) >= 12:
                    self.toast_requested.emit("Recuerda revisar si el producto tiene descuento por cantidad")
            except Exception:
                pass

            top = self.index(row, 0)
            bottom = self.index(row, self.columnCount() - 1)
            self.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
            return True

        if col == 4 and (CAN_EDIT_UNIT_PRICE or (it.get("categoria") or "").upper() in ("SERVICIO", "BOTELLAS")):
            cat = (it.get("categoria") or "").upper()
            prod = it.get("_prod", {}) or {}
            qty = float(nz(it.get("cantidad"), 0.0))
            if not isinstance(value, dict):
                token = str(value or "").strip().lower()
                tier = INLINE_PRICE_TIER_MAP.get(token)
                if not tier:
                    return False
                value = {"mode": "tier", "tier": tier}

            mode = (value.get("mode") or "").lower()
            if mode == "custom":
                new_price = float(nz(value.get("price"), 0.0))
                if new_price < 0:
                    new_price = 0.0

                if _is_service_category(cat):
                    it["precio_override"] = new_price
                    it["precio_tier"] = None
                    it["id_precioventa"] = PRICE_ID_PERSONALIZADO
                    self._apply_price_and_total(it, new_price)
                else:
                    tier, tier_price = _closest_allowed_tier(prod, new_price)
                    if tier_price <= 0:
                        tier_price = float(precio_unitario_por_categoria(cat, prod, qty) or 0.0)
                    it["precio_override"] = None
                    it["precio_tier"] = tier
                    it["id_precioventa"] = _price_id_from_tier(tier)
                    self._apply_price_and_total(it, tier_price)

            elif mode == "tier":
                tier_raw = (value.get("tier") or "")
                tier = _norm_tier_name(str(tier_raw))

                if tier in ("base", "unitario", "oferta", "minimo", "maximo"):
                    if _is_service_category(cat):
                        tier_price = float(_price_from_tier(prod, tier) or nz(it.get("precio"), 0.0))
                        it["precio_override"] = tier_price
                        it["precio_tier"] = None
                        it["id_precioventa"] = PRICE_ID_PERSONALIZADO
                        self._apply_price_and_total(it, tier_price)
                    else:
                        it["precio_override"] = None
                        if tier == "base":
                            it["id_precioventa"] = _default_price_id_from_prod(prod)
                        else:
                            it["id_precioventa"] = _price_id_from_tier(tier)
                        it["precio_tier"] = _tier_from_price_id(it["id_precioventa"])
                        self._recalc_price_for_qty(it)
                else:
                    return False
            else:
                return False

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
        if "id_precioventa" not in item:
            item["id_precioventa"] = None
        if "descuento_mode" not in item:
            item["descuento_mode"] = None
        if "descuento_pct" not in item:
            item["descuento_pct"] = 0.0
        if "descuento_monto" not in item:
            item["descuento_monto"] = 0.0

        cat = (item.get("categoria") or "").upper()
        if _is_service_category(cat):
            item["id_precioventa"] = PRICE_ID_PERSONALIZADO
            item["precio_tier"] = None
            if item.get("precio_override") is None:
                item["precio_override"] = float(nz(item.get("precio"), 0.0))
        else:
            pid = int(nz(item.get("id_precioventa"), 0) or 0)
            if pid not in (PRICE_ID_P_MAX, PRICE_ID_P_MIN, PRICE_ID_P_OFERTA):
                tier_raw = str(item.get("precio_tier") or "").strip()
                if tier_raw:
                    pid = _price_id_from_tier(tier_raw)
                else:
                    pid = _default_price_id_from_prod(item.get("_prod", {}) or {})
            item["id_precioventa"] = pid
            item["precio_tier"] = _tier_from_price_id(pid)
            item["precio_override"] = None

        if "factor_total" not in item:
            try:
                item["factor_total"] = float(factor_total_por_categoria((item.get("categoria") or "").upper()))
            except Exception:
                item["factor_total"] = 1.0

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
            if r >= len(self._items):
                continue
            if 0 <= r < len(self._items):
                self.beginRemoveRows(QModelIndex(), r, r)
                removed = self._items.pop(r)
                self.endRemoveRows()
                log.debug("Item removido: %s", removed.get("codigo"))
