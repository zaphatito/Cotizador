# src/widgets_parts/helpers.py
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from ..utils import nz


def _fmt_trim_decimal(x) -> str:
    try:
        d = Decimal(str(x)).normalize()
        s = format(d, "f")
        return "0" if s == "-0" else s
    except (InvalidOperation, Exception):
        try:
            f = float(x)
            return str(int(f)) if f.is_integer() else str(f)
        except Exception:
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
