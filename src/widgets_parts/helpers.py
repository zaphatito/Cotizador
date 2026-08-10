# src/widgets_parts/helpers.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..utils import nz


def _fmt_trim_decimal(x, *, max_decimal_places: int = 3) -> str:
    """Formatea cantidades sin exponer residuos binarios de ``float``."""
    try:
        places = max(0, int(max_decimal_places))
        d = Decimal(str(x))
        if not d.is_finite():
            return str(x)
        quantum = Decimal(1).scaleb(-places)
        d = d.quantize(quantum, rounding=ROUND_HALF_UP).normalize()
        s = format(d, "f")
        return "0" if s == "-0" else s
    except (InvalidOperation, TypeError, ValueError):
        try:
            f = float(x)
            return str(int(f)) if f.is_integer() else str(f)
        except (TypeError, ValueError, OverflowError):
            return str(x)


def _first_nonzero(d: dict, keys: list[str]) -> float:
    for k in keys:
        try:
            v = float(nz(d.get(k, 0.0), 0.0))
        except Exception:
            v = 0.0
        if v > 0:
            return v
    return 0.0
