# src/app_window_parts/add_items.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog
from PySide6.QtCore import QTimer, Qt

from ..config import (
    listing_allows_products,
    listing_allows_presentations,
    ALLOW_NO_STOCK,
    APP_COUNTRY,
    CATS,
)
from ..utils import nz
from ..pricing import (
    precio_unitario_por_categoria,
    factor_total_por_categoria,
    default_price_id_for_product,
)
from ..presentations import map_pc_to_bottle_code
from ..logging_setup import get_logger
from ..widgets import CustomProductDialog

log = get_logger(__name__)


def _normalize_tier_token(tier: str) -> str:
    """
    Normaliza tiers que pueden venir del chat (inglés/español) a los tiers internos.
    Retorna uno de: unitario|oferta|minimo|maximo|base|"" (si no reconoce)
    """
    t = (tier or "").strip().lower()
    mp = {
        # oferta
        "oferta": "oferta",
        "offer": "oferta",
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


def _tier_to_price_id(tier: str) -> int:
    t = _normalize_tier_token(tier)
    if t == "minimo":
        return 2
    if t == "oferta":
        return 3
    return 1


class AddItemsMixin:
    def _presentation_relation_parts(self, pres: dict) -> tuple[set[str], set[str]]:
        raw = str(
            pres.get("CODIGOS_PRODUCTO")
            or pres.get("codigos_producto")
            or ""
        ).strip()
        if not raw:
            return set(), set()

        generic_categories = {str(c or "").strip().upper() for c in (CATS or []) if str(c or "").strip()}
        for p in (self.productos or []):
            if hasattr(self, "_is_generic_category_row") and self._is_generic_category_row(p):
                dept = str(p.get("departamento", "") or p.get("categoria", "")).strip().upper()
                if dept:
                    generic_categories.add(dept)

        exact_codes: set[str] = set()
        wildcard_categories: set[str] = set()
        for tok in raw.split(","):
            t = str(tok or "").strip().upper()
            if not t:
                continue
            if t in generic_categories:
                wildcard_categories.add(t)
            else:
                exact_codes.add(t)

        return exact_codes, wildcard_categories

    def _presentation_base_codes(self, pres: dict) -> set[str]:
        exact_codes, _wildcard_categories = self._presentation_relation_parts(pres)
        return exact_codes

    def _presentation_wildcard_categories(self, pres: dict) -> set[str]:
        _exact_codes, wildcard_categories = self._presentation_relation_parts(pres)
        return wildcard_categories

    def _presentation_global_fixed_component_codes(self) -> set[str]:
        fixed_codes: set[str] = set()
        for pr in (self.presentaciones or []):
            exact_codes, wildcard_categories = self._presentation_relation_parts(pr)
            if wildcard_categories:
                fixed_codes.update(exact_codes)
        return fixed_codes

    def _find_presentacion_combo_match(self, cod_u: str):
        """
        Busca si `cod_u` viene como codigo combinado:
          <codigo_base><codigo_presentacion>
        Ejemplo: CC0370100
        """
        code = (cod_u or "").strip().upper()
        if not code:
            return None

        prod_map = {
            str(p.get("id", "")).strip().upper(): p
            for p in (self.productos or [])
            if str(p.get("id", "")).strip()
        }

        for pres in (self.presentaciones or []):
            pres_codes = []
            for k in ("CODIGO", "CODIGO_NORM", "codigo", "codigo_norm"):
                v = str(pres.get(k) or "").strip().upper()
                if v:
                    pres_codes.append(v)

            # Probar sufijo mas largo primero.
            for pcode in sorted(set(pres_codes), key=len, reverse=True):
                if not code.endswith(pcode):
                    continue

                base_code = code[: -len(pcode)]
                if not base_code:
                    continue

                base = prod_map.get(base_code)
                if not base:
                    continue

                base_codes_rel = self._presentation_base_codes(pres)
                wildcard_cats = self._presentation_wildcard_categories(pres)
                fixed_component_codes = self._presentation_global_fixed_component_codes()
                dep = str(pres.get("DEPARTAMENTO") or pres.get("departamento") or "").strip().upper()
                gen = str(pres.get("GENERO") or pres.get("genero") or "").strip().lower()
                base_dep = str(base.get("departamento") or base.get("categoria") or "").strip().upper()
                base_gen = str(base.get("genero") or "").strip().lower()
                essence_cats = {c.upper() for c in CATS}
                dep_is_presentation = dep in {"", "PRESENTACION", "PRESENTACIONES"}

                if hasattr(self, "_is_generic_category_row") and self._is_generic_category_row(base):
                    continue

                gen_match = (not gen) or (gen == base_gen)
                if dep_is_presentation and gen_match and (base_dep in essence_cats):
                    return pres, base

                if (
                    wildcard_cats
                    and (base_dep in wildcard_cats)
                    and (base_code not in base_codes_rel)
                    and (base_code not in fixed_component_codes)
                    and gen_match
                ):
                    return pres, base

                if (not wildcard_cats) and base_codes_rel and base_code in base_codes_rel and gen_match:
                    return pres, base

                dep_match = (not dep) or (dep == base_dep)
                if dep_match and gen_match:
                    return pres, base

                # Fallback tolerante: cuando no hay relacion cargada en Hoja 3,
                # permitir combinacion para bases de esencia.
                if (not base_codes_rel) and (base_dep in essence_cats):
                    return pres, base

        return None

    def _try_add_presentacion_by_combo_code(self, cod_u: str, *, silent: bool = False) -> bool:
        match = self._find_presentacion_combo_match(cod_u)
        if not match:
            return False

        if not listing_allows_presentations():
            if not silent:
                QMessageBox.warning(
                    self,
                    "Restringido por configuración",
                    "El tipo de listado actual no permite Presentaciones.",
                )
            return False

        pres, base = match
        if hasattr(self, "_agregar_presentacion_con_base"):
            return bool(self._agregar_presentacion_con_base(pres, base, silent=silent))

        # Fallback (no debería ocurrir): abre selector normal.
        self._selector_presentacion(pres)
        return True

    def agregar_producto_personalizado(self):
        dlg = CustomProductDialog(self, app_icon=self._app_icon)
        if dlg.exec() != QDialog.Accepted or not dlg.resultado:
            return
        data = dlg.resultado
        unit_price = float(nz(data["precio"], 0.0))  # siempre en moneda base
        qty = float(nz(data["cantidad"], 1))

        factor = factor_total_por_categoria("SERVICIO")
        subtotal_base = round(unit_price * qty * factor, 2)

        item = {
            "_prod": {
                "categoria": "SERVICIO",
                "p_max": unit_price,
                "p_min": unit_price,
                "p_oferta": unit_price,
                "precio_venta": 1,
            },
            "codigo": data["codigo"],
            "producto": data["nombre"],
            "categoria": "SERVICIO",
            "cantidad": qty,
            "ml": "",
            "precio": unit_price,
            "subtotal_base": subtotal_base,
            "total": subtotal_base,
            "observacion": data.get("observacion", ""),
            "stock_disponible": -1.0,
            "precio_override": unit_price,
            "precio_tier": None,
            "id_precioventa": 4,
            "factor_total": factor,
        }
        self.model.add_item(item)
        log.info(
            "Producto personalizado agregado: %s x%0.3f %0.2f",
            item["codigo"],
            qty,
            unit_price,
        )

    def _agregar_por_codigo(self, cod, *, silent: bool = False) -> bool:
        pres_payload = cod if isinstance(cod, dict) else None
        if pres_payload is not None:
            cod_u = str(
                pres_payload.get("codigo")
                or pres_payload.get("CODIGO")
                or ""
            ).strip().upper()
        else:
            cod_u = str(cod or "").strip().upper()
        if not cod_u:
            return False

        # 1) Código combinado base+presentación (ej: CC0370100)
        if self._try_add_presentacion_by_combo_code(cod_u, silent=silent):
            return True

        # 2) Presentación de Hoja 2
        # `PC*` se reserva para productos.
        pres = None
        if not cod_u.startswith("PC"):
            pres_candidates = [
                p
                for p in self.presentaciones
                if str(p.get("CODIGO", "")).upper() == cod_u
                or str(p.get("CODIGO_NORM", "")).upper() == cod_u
            ]
            if pres_candidates:
                if pres_payload is None:
                    pres = pres_candidates[0]
                else:
                    want_gen = str(
                        pres_payload.get("genero")
                        or pres_payload.get("GENERO")
                        or ""
                    ).strip().lower()
                    want_dep = str(
                        pres_payload.get("departamento")
                        or pres_payload.get("DEPARTAMENTO")
                        or pres_payload.get("categoria")
                        or ""
                    ).strip().upper()
                    want_name = str(
                        pres_payload.get("nombre")
                        or pres_payload.get("NOMBRE")
                        or ""
                    ).strip().upper()

                    def _score_pres(p: dict):
                        p_gen = str(p.get("GENERO") or p.get("genero") or "").strip().lower()
                        p_dep = str(p.get("DEPARTAMENTO") or p.get("departamento") or "").strip().upper()
                        p_name = str(p.get("NOMBRE") or p.get("nombre") or "").strip().upper()
                        p_stock = float(
                            nz(
                                p.get("STOCK_DISPONIBLE")
                                or p.get("stock_disponible")
                                or p.get("cantidad_disponible")
                                or 0.0
                            )
                        )
                        return (
                            0 if (want_gen and p_gen == want_gen) else (1 if want_gen else 0),
                            0 if (want_dep and p_dep == want_dep) else (1 if want_dep else 0),
                            0 if (want_name and p_name == want_name) else (1 if want_name else 0),
                            0 if p_gen else 1,
                            -p_stock,
                        )

                    pres = sorted(pres_candidates, key=_score_pres)[0]
        if pres:
            if not listing_allows_presentations():
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Restringido por configuración",
                        "El tipo de listado actual no permite Presentaciones.",
                    )
                return False
            self._selector_presentacion(pres)
            return True

        # 3) Producto de catálogo (incluye códigos PC* si existen como producto)
        prod = next(
            (p for p in self.productos if str(p.get("id", "")).upper() == cod_u),
            None,
        )
        if prod:
            if not listing_allows_products():
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Restringido por configuración",
                        "El tipo de listado actual no permite Productos.",
                    )
                return False

            if float(nz(prod.get("cantidad_disponible"), 0.0)) <= 0 and not ALLOW_NO_STOCK:
                if not silent:
                    QMessageBox.warning(
                        self, "Sin stock", "❌ Este producto no tiene stock disponible."
                    )
                return False

            cat = (prod.get("categoria") or "").upper()

            qty_default = 0.001 if (APP_COUNTRY == "PERU" and cat in CATS) else 1.0
            unit_price = precio_unitario_por_categoria(cat, prod, qty_default)
            default_pid = int(default_price_id_for_product(prod))
            default_tier = "unitario"
            if default_pid == 2:
                default_tier = "minimo"
            elif default_pid == 3:
                default_tier = "oferta"

            factor = factor_total_por_categoria(cat)
            subtotal_base = round(float(unit_price) * float(qty_default) * factor, 2)

            item = {
                "_prod": prod,
                "codigo": prod["id"],
                "producto": prod["nombre"],
                "categoria": cat,
                "cantidad": qty_default,
                "ml": prod.get("ml", ""),
                "precio": float(unit_price),
                "subtotal_base": subtotal_base,
                "total": subtotal_base,
                "observacion": "",
                "stock_disponible": float(nz(prod.get("cantidad_disponible"), 0.0)),
                "precio_override": None,
                "precio_tier": default_tier,
                "id_precioventa": default_pid,
                "factor_total": factor,
            }
            self.model.add_item(item)
            return True

        # 4) Legacy PC como presentación (solo si no existe como producto normal)
        if cod_u.startswith("PC"):
            if not listing_allows_presentations():
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Restringido por configuración",
                        "El tipo de listado actual no permite Presentaciones.",
                    )
                return False
            pc = next(
                (
                    p
                    for p in self._botellas_pc
                    if str(p.get("id", "")).upper() == cod_u
                ),
                None,
            )
            if pc:
                bot_code = map_pc_to_bottle_code(str(pc.get("id", "")))
                bot = next(
                    (
                        b
                        for b in self.productos
                        if str(b.get("id", "")).upper() == (bot_code or "").upper()
                        and (b.get("categoria", "").upper() == "BOTELLAS")
                    ),
                    None,
                )
                if (
                    bot is not None
                    and float(nz(bot.get("cantidad_disponible"), 0.0)) <= 0
                    and not ALLOW_NO_STOCK
                ):
                    if not silent:
                        QMessageBox.warning(
                            self,
                            "Sin botellas",
                            "❌ No hay botellas disponibles para esta presentación.",
                        )
                    return False
                self._selector_pc(pc)
                return True

        if not silent:
            QMessageBox.warning(self, "Advertencia", "❌ Producto no encontrado")
        return False

    def agregar_recomendado(
        self,
        codigo: str,
        *,
        qty: float | None = None,
        precio_override_base: float | str | None = None,
    ) -> bool:
        """
        Agrega un producto/presentación y luego aplica qty + precio recomendado.

        FIXES:
        - NO usar model.rowCount() porque incluye preview rows (_recs_preview).
          Se usa len(self.items) para detectar el/los ítems realmente agregados.
        - Soporta que el “precio” venga como tier string del chat: oferta/minimo/base/etc.
          En ese caso, se aplica como precio_tier y se recalcula.
        """
        items_list = getattr(self, "items", []) or []
        before = len(items_list)

        ok = self._agregar_por_codigo(codigo, silent=True)
        if not ok:
            return False

        items_list = getattr(self, "items", []) or []
        after = len(items_list)
        if after <= before:
            return False

        cod_u = (codigo or "").strip().upper()
        new_items = items_list[before:after]
        if not new_items:
            return False

        target = None
        for it in new_items:
            if str(it.get("codigo") or "").strip().upper() == cod_u:
                target = it
                break
        if target is None:
            target = new_items[-1]

        # Normaliza qty según reglas
        if qty is not None:
            try:
                q = float(qty)
            except Exception:
                q = 1.0

            cat_u = str(target.get("categoria") or "").upper()
            if APP_COUNTRY == "PERU" and cat_u in CATS:
                q = round(max(0.001, q), 3)
            else:
                q = int(round(q))
                if q < 1:
                    q = 1
            target["cantidad"] = q

        # ---- Precio: puede venir como tier string ("offer") o como número base ----
        tier_req = None
        if isinstance(precio_override_base, str):
            tier_req = _normalize_tier_token(precio_override_base)
            precio_override_base = None

        cat_u = str(target.get("categoria") or "").upper()

        if tier_req:
            if cat_u == "SERVICIO":
                target["precio_tier"] = None
                target["id_precioventa"] = 4
                if target.get("precio_override") is None:
                    target["precio_override"] = float(nz(target.get("precio"), 0.0))
            else:
                target["precio_override"] = None
                if tier_req in ("base", "unitario", "maximo", ""):
                    target["precio_tier"] = "unitario"
                elif tier_req == "minimo":
                    target["precio_tier"] = "minimo"
                elif tier_req == "oferta":
                    target["precio_tier"] = "oferta"
                else:
                    target["precio_tier"] = "unitario"
                target["id_precioventa"] = _tier_to_price_id(target["precio_tier"])

            try:
                self.model._recalc_price_for_qty(target)
            except Exception:
                pass

        elif precio_override_base is not None:
            try:
                p = float(precio_override_base)
            except Exception:
                p = 0.0

            if p > 0:
                if cat_u == "SERVICIO":
                    target["precio_override"] = p
                    target["precio_tier"] = None
                    target["id_precioventa"] = 4
                    try:
                        self.model._apply_price_and_total(target, p)
                    except Exception:
                        pass
                else:
                    applied = False
                    try:
                        row_target = self.items.index(target)
                    except Exception:
                        row_target = None

                    if row_target is not None:
                        try:
                            applied = bool(
                                self.model.setData(
                                    self.model.index(row_target, 4),
                                    {"mode": "custom", "price": p},
                                    Qt.EditRole,
                                )
                            )
                        except Exception:
                            applied = False

                    if not applied:
                        target["precio_override"] = None
                        target["id_precioventa"] = int(
                            default_price_id_for_product(target.get("_prod") or {})
                        )
                        if target["id_precioventa"] == 2:
                            target["precio_tier"] = "minimo"
                        elif target["id_precioventa"] == 3:
                            target["precio_tier"] = "oferta"
                        else:
                            target["precio_tier"] = "unitario"
                        try:
                            self.model._recalc_price_for_qty(target)
                        except Exception:
                            pass
            else:
                try:
                    self.model._recalc_price_for_qty(target)
                except Exception:
                    pass
        else:
            try:
                self.model._recalc_price_for_qty(target)
            except Exception:
                pass

        # refrescar fila(s) reales (0..len(items)-1), NO las preview
        try:
            row0 = before
            row1 = after - 1
            top = self.model.index(row0, 0)
            bottom = self.model.index(row1, self.model.columnCount() - 1)
            self.model.dataChanged.emit(top, bottom, [Qt.DisplayRole, Qt.EditRole])
        except Exception:
            pass

        return True
