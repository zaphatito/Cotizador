# src/app_window_parts/currency.py
from __future__ import annotations

from PySide6.QtCore import Qt

from ..config import (
    APP_CURRENCY,
    get_currency_context,
    set_currency_context,
    get_secondary_currencies,
)
from ..logging_setup import get_logger
from ..widgets import show_currency_dialog

from ..db_path import resolve_db_path
from sqlModels.db import connect, ensure_schema, tx
from sqlModels.rates_repo import load_rates, set_rate

log = get_logger(__name__)


class CurrencyMixin:
    """
    Moneda + tasas 100% en SQLite (exchange_rates).
    Además: dispara rates_updated si existe self._quote_events.
    """

    def _load_exchange_rate_file(self) -> dict[str, float]:
        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)
            rates = load_rates(con, self.base_currency)
            con.close()
            out: dict[str, float] = {}
            for k, v in (rates or {}).items():
                try:
                    out[str(k).upper()] = float(v)
                except Exception:
                    continue
            return out
        except Exception:
            return {}

    def _save_exchange_rate_file(self, rates: dict[str, float] | None = None) -> None:
        payload = rates if rates is not None else (getattr(self, "_rates", None) or {})
        if not payload:
            return
        try:
            db_path = resolve_db_path()
            con = connect(db_path)
            ensure_schema(con)
            with tx(con):
                for cur, rate in payload.items():
                    if not cur:
                        continue
                    try:
                        r = float(rate)
                    except Exception:
                        continue
                    if r <= 0:
                        continue
                    set_rate(con, self.base_currency, str(cur).upper(), r)
            con.close()
        except Exception:
            pass

    def _update_currency_label(self):
        if not hasattr(self, "lbl_moneda"):
            return

        cur, _sec_principal, rate_ctx = get_currency_context()
        base = self.base_currency
        cur = (cur or "").upper()

        if cur == base:
            if getattr(self, "_rates", None):
                parts = []
                for code, val in sorted(self._rates.items()):
                    try:
                        parts.append(f"{base}→{code}: {float(val):.4f}")
                    except Exception:
                        continue
                txt_rates = "; ".join(parts) if parts else "sin configurar"
                txt = f"Moneda: {base} (tasas {txt_rates})"
            else:
                txt = f"Moneda: {base} (tasas secundarias sin configurar)"
        else:
            r = (getattr(self, "_rates", {}) or {}).get(cur)
            if not r or r <= 0:
                try:
                    r = float(rate_ctx)
                except Exception:
                    r = 0.0
            if r and r > 0:
                txt = f"Moneda: {cur} (1 {base} = {r:.4f} {cur})"
            else:
                txt = f"Moneda: {cur} (tasa sin configurar)"

        self.lbl_moneda.setText(txt)

    def abrir_dialogo_moneda_y_tasa(self):
        base = self.base_currency
        cur, _sec_principal, rate_ctx = get_currency_context()
        cur = (cur or "").upper()

        exchange_rate = rate_ctx if cur and cur != base else None

        result = show_currency_dialog(
            self,
            self._app_icon,
            self.base_currency,
            self.secondary_currency,
            exchange_rate,
            saved_rates=getattr(self, "_rates", None) or {},
        )
        if not result:
            return
        self._apply_currency_settings(result)

    def _apply_currency_settings(self, settings: dict):
        base = self.base_currency

        cur_old, _sec_principal, _r = get_currency_context()
        old_currency = (cur_old or "").upper()

        selected = (settings.get("currency") or base).upper()
        is_base = bool(settings.get("is_base", selected == base))

        rates = settings.get("rates") or {}
        new_rates: dict[str, float] = {}
        for code, val in rates.items():
            if not isinstance(code, str):
                continue
            try:
                f = float(val)
            except Exception:
                f = 0.0
            new_rates[code.upper()] = f if f > 0 else 0.0

        # guardar cambios en DB
        prev_rates = dict(getattr(self, "_rates", {}) or {})
        changed = {
            code: val
            for code, val in new_rates.items()
            if float(val) > 0 and float(prev_rates.get(code, 0.0)) != float(val)
        }
        if changed:
            self._save_exchange_rate_file(changed)

        self._rates = new_rates

        # aplicar contexto de moneda
        if is_base or selected == base:
            set_currency_context(base, 1.0)
        else:
            r = float(new_rates.get(selected, 0.0))
            if r <= 0:
                # si no hay tasa válida, forzar 1.0 para no romper UI
                r = 1.0
            set_currency_context(selected, r)

        self._update_currency_label()

        # refrescar tabla
        if self.model.rowCount() > 0:
            top = self.model.index(0, 0)
            bottom = self.model.index(self.model.rowCount() - 1, self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

        # ✅ disparar rates_updated para que otras ventanas recarguen tasas
        qe = getattr(self, "_quote_events", None)
        if qe is not None:
            try:
                qe.rates_updated.emit()
            except Exception:
                pass

        cur_new, _sec2, r_new = get_currency_context()
        log.info(
            "Cambio de moneda: %s → %s (rate=%s, tasas=%s)",
            old_currency,
            cur_new,
            r_new,
            self._rates,
        )
