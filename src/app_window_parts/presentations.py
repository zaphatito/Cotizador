# src/app_window_parts/presentations.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog

from sqlModels.db import connect

from ..config import ALLOW_NO_STOCK, CATS
from ..pricing import price_for_price_id, default_price_id_for_product
from ..utils import nz
from ..presentations import map_pc_to_bottle_code, extract_ml_from_text, ml_from_pres_code_norm
from ..widgets import SelectorTablaSimple


class PresentationsMixin:
    def _presentation_relation_parts(self, pres: dict) -> tuple[set[str], set[str]]:
        raw = str(
            pres.get("CODIGOS_PRODUCTO")
            or pres.get("codigos_producto")
            or ""
        ).strip()
        if not raw:
            return set(), set()

        generic_categories = self._generic_category_markers()

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
        cache = getattr(self, "_presentation_fixed_component_codes_cache", None)
        if cache is not None:
            return set(cache)

        fixed_codes: set[str] = set()
        for pr in (self.presentaciones or []):
            exact_codes, wildcard_categories = self._presentation_relation_parts(pr)
            if wildcard_categories:
                fixed_codes.update(exact_codes)
        self._presentation_fixed_component_codes_cache = set(fixed_codes)
        return set(fixed_codes)

    def _is_generic_category_row(self, prod: dict) -> bool:
        pid = str(prod.get("id", "")).strip().upper()
        name = str(prod.get("nombre", "")).strip().upper()
        cat = str(prod.get("categoria", "")).strip().upper()
        dept = str(prod.get("departamento", "") or prod.get("categoria", "")).strip().upper()
        if not pid:
            return False
        if pid == cat and name == cat:
            return True
        if pid == dept and name == dept:
            return True
        if cat in {c.upper() for c in CATS} and pid in {c.upper() for c in CATS} and name in {c.upper() for c in CATS}:
            return True
        return False

    def _product_department(self, prod: dict) -> str:
        return str(prod.get("departamento", "") or prod.get("categoria", "")).strip().upper()

    def _product_gender(self, prod: dict) -> str:
        return str(prod.get("genero", "")).strip().lower()

    def _generic_category_markers(self) -> set[str]:
        cache = getattr(self, "_presentation_generic_categories_cache", None)
        if cache is not None:
            return set(cache)

        markers = {str(c or "").strip().upper() for c in (CATS or []) if str(c or "").strip()}
        for p in (self.productos or []):
            if self._is_generic_category_row(p):
                dept = self._product_department(p)
                if dept:
                    markers.add(dept)

        self._presentation_generic_categories_cache = set(markers)
        return set(markers)

    def _product_lookup(self) -> dict[str, dict]:
        cache = getattr(self, "_presentation_product_map_cache", None)
        if cache is not None:
            return cache

        cache = {
            str(p.get("id", "")).strip().upper(): p
            for p in (self.productos or [])
            if str(p.get("id", "")).strip()
        }
        self._presentation_product_map_cache = cache
        return cache

    def _service_department_markers(self) -> set[str]:
        return self._generic_category_markers()

    def _get_presentation_relations(self, pres: dict) -> list[dict]:
        cache = getattr(self, "_presentation_rel_cache", None)
        if cache is None:
            cache = {}
            self._presentation_rel_cache = cache

        codes = []
        for k in ("CODIGO_NORM", "CODIGO", "codigo_norm", "codigo"):
            v = str(pres.get(k) or "").strip().upper()
            if v and v not in codes:
                codes.append(v)
        dep = str(pres.get("DEPARTAMENTO") or pres.get("departamento") or "").strip().upper()
        gen = str(pres.get("GENERO") or pres.get("genero") or "").strip().lower()
        key = (tuple(codes), dep, gen)
        if key in cache:
            return [dict(r) for r in cache[key]]

        if not codes:
            cache[key] = []
            return []

        db_path = str(getattr(self, "_db_path", "") or "").strip()
        if not db_path:
            cache[key] = []
            return []

        con = None
        rows_out: list[dict] = []
        try:
            con = connect(db_path)
            ph = ",".join(["?"] * len(codes))
            rows = con.execute(
                f"""
                SELECT
                    UPPER(COALESCE(cod_producto, '')) AS cod_producto,
                    COALESCE(cantidad, 0) AS cantidad,
                    UPPER(COALESCE(departamento, '')) AS departamento,
                    LOWER(COALESCE(genero, '')) AS genero
                FROM presentacion_prod_current
                WHERE UPPER(COALESCE(cod_presentacion, '')) IN ({ph})
                """,
                tuple(codes),
            ).fetchall()
            for r in rows:
                rel_dep = str(r["departamento"] or "").strip().upper()
                rel_gen = str(r["genero"] or "").strip().lower()
                if dep and rel_dep and rel_dep != dep:
                    continue
                if gen and rel_gen and rel_gen != gen:
                    continue
                rows_out.append(dict(r))
        except Exception:
            rows_out = []
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass

        cache[key] = [dict(r) for r in rows_out]
        return [dict(r) for r in rows_out]

    def _presentation_available_stock_for_base(self, pres: dict, base: dict) -> float:
        relations = self._get_presentation_relations(pres)
        if not relations:
            return float(
                nz(
                    pres.get("STOCK_DISPONIBLE")
                    or pres.get("stock_disponible")
                    or pres.get("cantidad_disponible")
                    or 0.0
                )
            )

        prod_map = self._product_lookup()
        base_dep = self._product_department(base)
        base_gen = self._product_gender(base)
        base_stock = float(nz(base.get("cantidad_disponible"), 0.0))
        service_markers = self._service_department_markers()

        ratios: list[float] = []
        for rel in relations:
            rel_code = str(rel.get("cod_producto") or "").strip().upper()
            rel_gen = str(rel.get("genero") or "").strip().lower()
            need_qty = float(nz(rel.get("cantidad"), 0.0))
            if not rel_code or need_qty <= 0:
                continue

            if rel_code in service_markers:
                if self._is_generic_category_row(base):
                    return 0.0
                if base_dep != rel_code:
                    return 0.0
                if rel_gen and base_gen != rel_gen:
                    return 0.0
                ratios.append(base_stock / need_qty)
                continue

            comp = prod_map.get(rel_code)
            if not comp:
                return 0.0
            ratios.append(float(nz(comp.get("cantidad_disponible"), 0.0)) / need_qty)

        if not ratios:
            return 0.0
        return round(max(0.0, min(ratios)), 6)

    def _select_default_bottle_for_presentacion(self, pres: dict):
        if not bool(pres.get("REQUIERE_BOTELLA", False)):
            return None

        ml_pres = ml_from_pres_code_norm(
            pres.get("CODIGO_NORM") or pres.get("CODIGO") or ""
        )
        bot_opts = []
        for b in self._botellas_pc:
            bot_code = map_pc_to_bottle_code(str(b.get("id", "")))
            bot = next(
                (
                    bb
                    for bb in self.productos
                    if str(bb.get("id", "")).upper() == (bot_code or "").upper()
                    and (bb.get("categoria", "").upper() == "BOTELLAS")
                ),
                None,
            )
            if not bot:
                continue
            if (
                float(nz(bot.get("cantidad_disponible"), 0.0)) <= 0
                and not ALLOW_NO_STOCK
            ):
                continue
            ml_b = extract_ml_from_text(bot.get("nombre", "")) or extract_ml_from_text(
                b.get("nombre", "")
            )
            if ml_b != ml_pres:
                continue
            bot_opts.append(b)
        return bot_opts[0] if bot_opts else None

    def _agregar_presentacion_con_base(self, pres: dict, base: dict, *, silent: bool = False) -> bool:
        dep = (pres.get("DEPARTAMENTO") or pres.get("departamento") or "").strip().upper()
        gen = (pres.get("GENERO") or pres.get("genero") or "").strip().lower()
        base_dep = self._product_department(base)
        base_gen = self._product_gender(base)
        base_id = str(base.get("id", "")).strip().upper()
        rel_codes = self._presentation_base_codes(pres)
        wildcard_cats = self._presentation_wildcard_categories(pres)
        fixed_component_codes = self._presentation_global_fixed_component_codes()
        linked_by_relation = bool(
            (rel_codes and base_id in rel_codes)
            or (
                wildcard_cats
                and base_dep in wildcard_cats
                and base_id not in rel_codes
                and base_id not in fixed_component_codes
            )
        )
        essence_cats = {c.upper() for c in CATS}
        dep_is_presentation = dep in {"", "PRESENTACION", "PRESENTACIONES"}

        if self._is_generic_category_row(base):
            if not silent:
                QMessageBox.warning(
                    self,
                    "Producto base inválido",
                    "Selecciona un producto base real de ESENCIAS, no la fila genérica.",
                )
            return False

        if dep_is_presentation:
            if (base_dep not in essence_cats) and (not linked_by_relation):
                if not silent:
                    QMessageBox.warning(
                        self,
                        "Sin coincidencias",
                        "El producto base debe ser de categoria ESENCIA/ESENCIAS.",
                    )
                return False
        elif dep and base_dep != dep and not linked_by_relation:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Sin coincidencias",
                    f"El producto base debe ser de la categoría '{dep}'.",
                )
            return False
        if gen and base_gen != gen and not linked_by_relation:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Sin coincidencias",
                    f"El género del producto base debe coincidir con '{gen}'.",
                )
            return False
        combo_stock = self._presentation_available_stock_for_base(pres, base)
        if (not ALLOW_NO_STOCK) and combo_stock < 1.0:
            if not silent:
                QMessageBox.warning(self, "Sin stock", "❌ No hay stock suficiente para esta presentación.")
            return False

        botella = self._select_default_bottle_for_presentacion(pres)
        if bool(pres.get("REQUIERE_BOTELLA", False)) and botella is None:
            if not silent:
                QMessageBox.warning(
                    self,
                    "Sin botellas PC",
                    "No hay botellas PC compatibles para esta presentación.",
                )
            return False

        precio_max = float(
            nz(
                pres.get("P_MAX", pres.get("p_max", 0.0)),
                0.0,
            )
        )
        precio_oferta = float(
            nz(
                pres.get("P_OFERTA", pres.get("p_oferta", 0.0)),
                0.0,
            )
        )
        precio_min = float(
            nz(
                pres.get("P_MIN", pres.get("p_min", 0.0)),
                0.0,
            )
        )
        precio_pres = precio_max if precio_max > 0 else 0.0
        precio_bot = (
            float(price_for_price_id(botella, default_price_id_for_product(botella))) if botella else 0.0
        )
        unit_price = precio_pres + precio_bot
        precio_oferta_total = (precio_oferta if precio_oferta > 0 else precio_pres) + precio_bot
        precio_min_total = (
            precio_min
            if precio_min > 0
            else (precio_oferta if precio_oferta > 0 else precio_pres)
        ) + precio_bot

        nombre_pres = (
            pres.get("NOMBRE") or pres.get("CODIGO_NORM") or pres.get("CODIGO")
        )
        nombre_final = f"A LA MODE {base.get('nombre', '')} {nombre_pres}".strip()

        if botella:
            codigo_final = f"{botella.get('id', '')}{base.get('id', '')}"
            ml = extract_ml_from_text(botella.get("nombre", ""))
        else:
            codigo_final = (
                f"{base.get('id', '')}{pres.get('CODIGO_NORM') or pres.get('CODIGO')}"
            )
            ml = ml_from_pres_code_norm(
                pres.get("CODIGO_NORM") or pres.get("CODIGO") or ""
            )

        stock_ref = float(combo_stock)

        if botella:
            stock_bot = float(
                nz(
                    next(
                        (
                            bb
                            for bb in self.productos
                            if str(bb.get("id", "")).upper()
                            == map_pc_to_bottle_code(str(botella.get("id", "")))
                            and (bb.get("categoria", "").upper() == "BOTELLAS")
                        ),
                        {},
                    ).get("cantidad_disponible", 0.0)
                )
            )
            if stock_ref > 0 and stock_bot > 0:
                stock_ref = min(stock_ref, stock_bot)
            elif stock_bot > 0:
                stock_ref = stock_bot

        item = {
            "_prod": {
                "categoria": "PRESENTACION",
                "p_max": unit_price,
                "p_oferta": precio_oferta_total,
                "p_min": precio_min_total,
                "precio_venta": 1,
            },
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre", "")
            if base_dep in essence_cats
            else "",
            "observacion": "",
            "stock_disponible": float(stock_ref),
            "precio_override": None,
            "precio_tier": None,
            "id_precioventa": 1,
        }
        self.model.add_item(item)
        return True

    def _selector_pc(self, pc: dict):
        mapped_code = map_pc_to_bottle_code(str(pc.get("id", "")))
        botella_ref = next(
            (
                b
                for b in self.productos
                if str(b.get("id", "")).upper() == (mapped_code or "")
                and b.get("categoria", "").upper() == "BOTELLAS"
            ),
            None,
        )
        ml_botella = (
            extract_ml_from_text(botella_ref.get("nombre", "")) if botella_ref else 0
        )
        if ml_botella == 0:
            ml_botella = extract_ml_from_text(pc.get("nombre", ""))
        if ml_botella == 0:
            QMessageBox.warning(
                self,
                "PC sin ML",
                "No pude inferir los ml de la botella asociada a este PC.",
            )
            return

        pres_ml_matches = [
            pr
            for pr in self.presentaciones
            if ml_from_pres_code_norm(pr.get("CODIGO_NORM") or pr.get("CODIGO"))
            == ml_botella
        ]
        deps_with_match: set[str] = set()
        deps_with_wildcard: set[str] = set()
        deps_with_exact_gender: set[tuple[str, str]] = set()
        for pr in pres_ml_matches:
            dep_match = (pr.get("DEPARTAMENTO", "") or "").upper()
            if not dep_match:
                continue
            deps_with_match.add(dep_match)
            pr_gen = (pr.get("GENERO", "") or "").strip().lower()
            if pr_gen:
                deps_with_exact_gender.add((dep_match, pr_gen))
            else:
                deps_with_wildcard.add(dep_match)

        def base_has_match(p):
            dep_base = self._product_department(p)
            if dep_base not in deps_with_match:
                return False
            gen_base = self._product_gender(p)
            if dep_base not in deps_with_wildcard and (dep_base, gen_base) not in deps_with_exact_gender:
                return False
            for pr in pres_ml_matches:
                if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                    pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                    if not pr_gen or pr_gen == gen_base:
                        if ALLOW_NO_STOCK or self._presentation_available_stock_for_base(pr, p) >= 1.0:
                            return True
            return False

        filas_base = [
            {
                "codigo": p.get("id", ""),
                "nombre": p.get("nombre", ""),
                "categoria": p.get("categoria", ""),
                "genero": p.get("genero", ""),
            }
            for p in self.productos
            if base_has_match(p)
            and (not self._is_generic_category_row(p))
        ]
        if not filas_base:
            QMessageBox.warning(self, "Sin bases", "No hay productos base compatibles para este PC.")
            return

        dlg_base = SelectorTablaSimple(
            self, "Seleccionar Producto Base", filas_base, self._app_icon
        )
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion:
            return
        cod_base = dlg_base.seleccion["codigo"]
        base = self._product_lookup().get(str(cod_base).strip().upper())
        if not base:
            return

        dep_base = self._product_department(base)
        gen_base = self._product_gender(base)
        pres_candidates = []
        for pr in pres_ml_matches:
            if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                if not pr_gen or pr_gen == gen_base:
                    pres_candidates.append(pr)
        if not pres_candidates:
            QMessageBox.warning(
                self,
                "Presentación no encontrada",
                f"No hay una presentación de {ml_botella} ml que coincida con '{dep_base}'.",
            )
            return

        pres_final = pres_candidates[0]
        precio_pres = float(nz(pres_final.get("P_MAX", pres_final.get("p_max", 0.0)), 0.0))
        precio_pres_oferta = float(
            nz(pres_final.get("P_OFERTA", pres_final.get("p_oferta", 0.0)), 0.0)
        )
        precio_pres_min = float(
            nz(pres_final.get("P_MIN", pres_final.get("p_min", 0.0)), 0.0)
        )
        precio_pc = float(price_for_price_id(pc, default_price_id_for_product(pc)))
        unit_price = precio_pres + precio_pc
        oferta_price = (
            (precio_pres_oferta if precio_pres_oferta > 0 else precio_pres) + precio_pc
        )
        min_price = (
            (precio_pres_min if precio_pres_min > 0 else (precio_pres_oferta if precio_pres_oferta > 0 else precio_pres))
            + precio_pc
        )

        nombre_pres = (
            pres_final.get("NOMBRE") or pres_final.get("CODIGO_NORM") or pres_final.get("CODIGO")
        )
        nombre_final = f"A LA MODE {base.get('nombre', '')} {nombre_pres}".strip()
        codigo_final = f"{pc.get('id', '')}{base.get('id', '')}"
        ml = ml_botella

        combo_stock = self._presentation_available_stock_for_base(pres_final, base)
        if (not ALLOW_NO_STOCK) and combo_stock < 1.0:
            QMessageBox.warning(self, "Sin stock", "❌ No hay stock suficiente para esta presentación.")
            return

        stock_bot = (
            float(nz(botella_ref.get("cantidad_disponible"), 0.0)) if botella_ref else None
        )
        stock_ref = float(combo_stock)
        if stock_bot is not None:
            if stock_bot > 0 and stock_ref > 0:
                stock_ref = min(stock_bot, stock_ref)
            elif stock_bot > 0:
                stock_ref = stock_bot

        item = {
            "_prod": {
                "categoria": "PRESENTACION",
                "p_max": unit_price,
                "p_oferta": oferta_price,
                "p_min": min_price,
                "precio_venta": 1,
            },
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre", "")
            if dep_base in ("ESENCIA", "ESENCIAS")
            else "",
            "observacion": "",
            "stock_disponible": float(stock_ref),
            "precio_override": None,
            "precio_tier": None,
            "id_precioventa": 1,
        }
        self.model.add_item(item)

    def _selector_presentacion(self, pres: dict):
        dep = (pres.get("DEPARTAMENTO") or pres.get("departamento") or "").strip().upper()
        gen = (pres.get("GENERO") or pres.get("genero") or "").strip().lower()
        essence_cats = {c.upper() for c in CATS}
        dep_is_presentation = dep in {"", "PRESENTACION", "PRESENTACIONES"}
        rel_codes = self._presentation_base_codes(pres)
        wildcard_cats = self._presentation_wildcard_categories(pres)
        fixed_component_codes = self._presentation_global_fixed_component_codes()
        base_pool = [
            p
            for p in self.productos
            if not self._is_generic_category_row(p)
        ]

        def _matches_dep_and_gen(p: dict) -> bool:
            p_dep = self._product_department(p)
            p_gen = self._product_gender(p)
            dep_ok = (p_dep in essence_cats) if dep_is_presentation else (p_dep == dep)
            gen_ok = (not gen) or (p_gen == gen)
            return dep_ok and gen_ok

        base_candidates = [p for p in base_pool if _matches_dep_and_gen(p)]

        if wildcard_cats:
            wild_filtered = [
                p
                for p in base_candidates
                if self._product_department(p) in wildcard_cats
                and str(p.get("id", "")).strip().upper() not in rel_codes
                and str(p.get("id", "")).strip().upper() not in fixed_component_codes
            ]
            if wild_filtered:
                base_candidates = wild_filtered
        elif rel_codes:
            rel_candidates = [
                p
                for p in base_pool
                if str(p.get("id", "")).strip().upper() in rel_codes
            ]
            rel_filtered = [p for p in rel_candidates if _matches_dep_and_gen(p)]
            if rel_filtered:
                base_candidates = rel_filtered

        if not ALLOW_NO_STOCK:
            base_candidates = [
                p
                for p in base_candidates
                if self._presentation_available_stock_for_base(pres, p) >= 1.0
            ]

        if not base_candidates:
            QMessageBox.warning(
                self,
                "Sin coincidencias",
                f"No hay productos base para {dep} / {pres.get('GENERO', '')}",
            )
            return

        filas_base = [
            {
                "codigo": p.get("id", ""),
                "nombre": p.get("nombre", ""),
                "categoria": p.get("categoria", ""),
                "genero": p.get("genero", ""),
            }
            for p in base_candidates
        ]
        dlg_base = SelectorTablaSimple(
            self, "Seleccionar Producto Base", filas_base, self._app_icon
        )
        if dlg_base.exec() != QDialog.Accepted or not dlg_base.seleccion:
            return
        cod_base = dlg_base.seleccion["codigo"]
        base = self._product_lookup().get(str(cod_base).strip().upper())
        if not base:
            return

        self._agregar_presentacion_con_base(pres, base)
