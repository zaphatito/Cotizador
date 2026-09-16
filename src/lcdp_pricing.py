"""Importes comerciales de LCDP/precios, punto-venta.js (7222a88).

El Excel del cotizador ya incluye IVA: no volver a gravar sus precios.
Se redondea precio, bruto y total; el descuento es bruto menos total.
"""
from decimal import Decimal, ROUND_HALF_UP

from .country_rules import country_profile


def money(value) -> float:
    number = Decimal(str(value or 0))
    if not number.is_finite():
        raise ValueError("El importe debe ser finito")
    return float(number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def line_subtotal(price, quantity, factor=1) -> float:
    return money(Decimal(str(money(price))) * Decimal(str(quantity)) * Decimal(str(factor)))


def percentage_from_entered_amount(subtotal, amount) -> float:
    """Porcentaje entero más cercano; los empates se redondean hacia arriba."""
    gross = Decimal(str(money(subtotal)))
    entered = Decimal(str(money(amount)))
    if entered < 0 or entered > gross:
        raise ValueError("El importe descontado debe estar entre cero y el subtotal")
    if gross <= 0:
        return 0.0
    return float((entered * 100 / gross).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def line_discount(subtotal, mode=None, percentage=0, amount=0, country=None):
    """Devuelve porcentaje informativo, descuento y total con impuesto.

    El importe es autoritativo en modo amount; nunca se convierte a un
    porcentaje entero. En PE, solo la entrada porcentual es entera.
    """
    gross = money(subtotal)
    pct = float(percentage or 0)
    amt = money(amount)
    if not Decimal(str(pct)).is_finite():
        raise ValueError("El porcentaje debe ser finito")
    peru = str(country or "").upper() in {"PE", "PERU", "PERÚ"}
    if mode == "percent":
        if peru:
            pct = float(Decimal(str(pct)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if not 0 <= pct <= 99:
            raise ValueError("El descuento debe estar entre 0 y 99%")
        total = money(Decimal(str(gross)) * (1 - Decimal(str(pct)) / 100))
    elif mode == "amount":
        if amt < 0 or amt > money(gross * 0.99):
            raise ValueError("El descuento no puede superar el 99%")
        total = money(Decimal(str(gross)) - Decimal(str(amt)))
        pct = amt / gross * 100 if gross else 0.0
    else:
        pct, total = 0.0, gross
    discount = money(Decimal(str(gross)) - Decimal(str(total)))
    validate_line_minimum(gross, total, discount, country)
    return pct, discount, total


def validate_line_minimum(gross, total, discount, country=None):
    """El descuento no puede dejar una línea bajo una unidad de moneda base."""
    if discount > 0 and (gross < 1 or total < 1):
        currency = country_profile(country).base_currency if country else "en moneda base"
        raise ValueError(f"El total de la línea con impuestos debe ser como mínimo 1.00 {currency}")
