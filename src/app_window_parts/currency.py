# src/app_window_parts/currency.py
from __future__ import annotations

import os
import datetime

from PySide6.QtCore import Qt

from ..config import (
    get_currency_context,
    set_currency_context,
    get_secondary_currencies,
)
from ..logging_setup import get_logger
from ..widgets import show_currency_dialog

log = get_logger(__name__)


class CurrencyMixin:
    # ---------------------------
    # Formato histórico por línea (ESTRICTO):
    #   YYYY-MM-DD HH:MM:SS CODE=RATE
    #
    # Ej:
    #   2025-12-16 09:31:05 ARS=12.200000
    #   2025-12-16 09:35:11 BRL=1.300000
    #
    # Reglas:
    # - SOLO se usan tasas del día actual.
    # - Para el mismo día y código, gana la ÚLTIMA por timestamp.
    # - Líneas legacy sin timestamp quedan ignoradas (histórico viejo).
    # ---------------------------

    def _parse_rate_line(
        self, line: str
    ) -> tuple[datetime.datetime | None, str | None, float | None]:
        """
        Devuelve (ts, code, rate) o (None, None, None) si no parsea o no cumple formato.

        Acepta:
          - "YYYY-MM-DD HH:MM:SS CODE=RATE"
          - "YYYY-MM-DD|HH:MM:SS|CODE=RATE"  (compat separador '|')
          - "YYYY-MM-DD HH:MM:SS|CODE=RATE"  (mixto)
        IGNORA (en modo estricto):
          - "YYYY-MM-DD CODE=RATE" (sin hora)
          - "CODE=RATE" (sin fecha)
          - "123.45" (solo número)
        """
        s = (line or "").strip()
        if not s or s.startswith("#"):
            return (None, None, None)

        # Normaliza separadores opcionales
        s = s.replace("|", " ")
        parts = s.split()
        if len(parts) < 3:
            # mínimo: fecha, hora, code=rate
            return (None, None, None)

        # Parse fecha
        date_s = parts[0].strip()
        time_s = parts[1].strip()
        rest = " ".join(parts[2:]).strip()

        try:
            d = datetime.date.fromisoformat(date_s)
        except Exception:
            return (None, None, None)

        # Parse hora (exigir HH:MM:SS)
        try:
            t = datetime.time.fromisoformat(time_s)  # requiere HH:MM:SS
        except Exception:
            return (None, None, None)

        if "=" not in rest:
            return (None, None, None)

        code, val = rest.split("=", 1)
        code = code.strip().upper()
        val = val.strip().replace(",", ".")
        if not code:
            return (None, None, None)

        try:
            num = float(val)
        except Exception:
            return (None, None, None)

        ts = datetime.datetime.combine(d, t)
        return (ts, code, num)

    def _load_exchange_rate_file(self) -> dict[str, float]:
        """
        Lee tasa.txt y devuelve SOLO las tasas del día actual.

        - Si no hay tasas hoy, devuelve {} (no usa días anteriores ni legacy sin fecha).
        - Si hay varias tasas hoy para el mismo código, toma la de mayor timestamp.
        """
        if not self._tasa_path or not os.path.exists(self._tasa_path):
            return {}

        today = datetime.date.today()
        # (opcional) para ordenar/log bonito
        sec_list = [c.upper() for c in (get_secondary_currencies() or []) if c]

        latest_ts_by_code: dict[str, datetime.datetime] = {}
        rates_today: dict[str, float] = {}

        try:
            with open(self._tasa_path, "r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue

                    ts, code, rate = self._parse_rate_line(line)
                    if ts is None or code is None or rate is None:
                        continue
                    if rate <= 0:
                        continue

                    # Solo HOY
                    if ts.date() != today:
                        continue

                    prev_ts = latest_ts_by_code.get(code)
                    if prev_ts is None or ts > prev_ts:
                        latest_ts_by_code[code] = ts
                        rates_today[code] = float(rate)

            # Orden “bonito”: secundarias primero, luego el resto
            if rates_today:
                ordered = {}
                for c in sec_list:
                    if c in rates_today:
                        ordered[c] = rates_today[c]
                for c in sorted(rates_today.keys()):
                    if c not in ordered:
                        ordered[c] = rates_today[c]

                log.info(
                    "Tasas cargadas (solo HOY=%s) desde %s: %s",
                    today.isoformat(),
                    self._tasa_path,
                    ordered,
                )
                return ordered

            log.info(
                "No hay tasas registradas para HOY=%s en %s. Se usarán como 'sin configurar'.",
                today.isoformat(),
                self._tasa_path,
            )
            return {}

        except Exception as e:
            log.warning("No se pudo leer tasa.txt (%s): %s", self._tasa_path, e)
            return {}

    def _save_exchange_rate_file(self, rates: dict[str, float]) -> None:
        """
        APPEND histórico: agrega entradas con timestamp completo.

        Formato:
          YYYY-MM-DD HH:MM:SS CODE=RATE

        Solo escribe tasas > 0.
        """
        try:
            os.makedirs(os.path.dirname(self._tasa_path), exist_ok=True)

            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            lines: list[str] = []
            for code, val in (rates or {}).items():
                if not code:
                    continue
                try:
                    num = float(val)
                except Exception:
                    continue
                if num <= 0:
                    continue
                lines.append(f"{now} {str(code).upper()}={num:.6f}")

            if not lines:
                return

            file_exists = os.path.exists(self._tasa_path)
            needs_leading_newline = False
            if file_exists:
                try:
                    # si el archivo no termina en \n, agregamos uno
                    with open(self._tasa_path, "rb") as fb:
                        fb.seek(0, os.SEEK_END)
                        if fb.tell() > 0:
                            fb.seek(-1, os.SEEK_END)
                            last = fb.read(1)
                            needs_leading_newline = (last != b"\n")
                except Exception:
                    needs_leading_newline = True

            with open(self._tasa_path, "a", encoding="utf-8") as f:
                if file_exists and needs_leading_newline:
                    f.write("\n")
                elif file_exists and os.path.getsize(self._tasa_path) > 0:
                    f.write("\n")
                f.write("\n".join(lines))

            log.info("Tasas append (%s) en %s: %s", now, self._tasa_path, rates)
        except Exception as e:
            log.warning("No se pudo guardar tasa.txt (%s): %s", self._tasa_path, e)

    def _update_currency_label(self):
        if not hasattr(self, "lbl_moneda"):
            return

        cur, _sec_principal, rate_ctx = get_currency_context()
        base = self.base_currency
        cur = (cur or "").upper()

        if cur == base:
            if self._rates:
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
            r = self._rates.get(cur)
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
            saved_rates=self._rates,
        )
        if not result:
            return
        self._apply_currency_settings(result)

    def _apply_currency_settings(self, settings: dict):
        base = self.base_currency

        cur, _sec_principal, _r = get_currency_context()
        old_currency = cur

        selected = (settings.get("currency") or base).upper()
        is_base = bool(settings.get("is_base", selected == base))
        try:
            selected_rate = float(settings.get("rate", 1.0))
        except Exception:
            selected_rate = 1.0
        if selected_rate <= 0:
            selected_rate = 1.0

        prev_rates = dict(getattr(self, "_rates", {}) or {})

        rates = settings.get("rates") or {}
        new_rates = {
            (str(code).upper()): float(val)
            for code, val in rates.items()
            if isinstance(code, str)
        }

        # Guardar histórico SOLO de lo que cambió
        changed = {
            code: val
            for code, val in new_rates.items()
            if float(val) > 0 and float(prev_rates.get(code, 0.0)) != float(val)
        }
        if changed:
            self._save_exchange_rate_file(changed)

        self._rates = new_rates

        if is_base or selected == base:
            set_currency_context(base, 1.0)
        else:
            set_currency_context(selected, selected_rate)

        self._update_currency_label()

        if self.model.rowCount() > 0:
            top = self.model.index(0, 0)
            bottom = self.model.index(
                self.model.rowCount() - 1, self.model.columnCount() - 1
            )
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])

        cur_new, _sec2, r_new = get_currency_context()
        log.info(
            "Cambio de moneda: %s → %s (rate=%s, tasas=%s)",
            old_currency,
            cur_new,
            r_new,
            self._rates,
        )
