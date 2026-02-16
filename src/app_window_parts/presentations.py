# src/app_window_parts/presentations.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog

from ..config import ALLOW_NO_STOCK, CATS
from ..utils import nz
from ..presentations import map_pc_to_bottle_code, extract_ml_from_text, ml_from_pres_code_norm
from ..widgets import SelectorTablaSimple


class PresentationsMixin:
    def _presentation_base_codes(self, pres: dict) -> set[str]:
        raw = str(
            pres.get("CODIGOS_PRODUCTO")
            or pres.get("codigos_producto")
            or ""
        ).strip()
        if not raw:
            return set()
        out = set()
        for tok in raw.split(","):
            t = str(tok or "").strip().upper()
            if t:
                out.add(t)
        return out

    def _is_generic_category_row(self, prod: dict) -> bool:
        pid = str(prod.get("id", "")).strip().upper()
        name = str(prod.get("nombre", "")).strip().upper()
        cat = str(prod.get("categoria", "")).strip().upper()
        if not pid:
            return False
        if pid == cat and name == cat:
            return True
        if cat in {c.upper() for c in CATS} and pid in {c.upper() for c in CATS} and name in {c.upper() for c in CATS}:
            return True
        return False

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
        base_dep = str(base.get("categoria", "")).strip().upper()
        base_gen = str(base.get("genero", "")).strip().lower()
        base_id = str(base.get("id", "")).strip().upper()
        rel_codes = self._presentation_base_codes(pres)
        linked_by_relation = bool(rel_codes and base_id in rel_codes)
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
        if (not ALLOW_NO_STOCK) and float(nz(base.get("cantidad_disponible"), 0.0)) <= 0:
            if not silent:
                QMessageBox.warning(self, "Sin stock", "❌ El producto base no tiene stock disponible.")
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
                pres.get("P_MAX", pres.get("p_max", pres.get("PRECIO_PRESENT", pres.get("precio_present", 0.0)))),
                0.0,
            )
        )
        precio_oferta = float(
            nz(
                pres.get("P_OFERTA", pres.get("p_oferta", pres.get("precio_oferta", pres.get("precio_oferta_base", 0.0)))),
                0.0,
            )
        )
        precio_min = float(
            nz(
                pres.get("P_MIN", pres.get("p_min", pres.get("precio_minimo", pres.get("precio_minimo_base", 0.0)))),
                0.0,
            )
        )
        precio_pres = precio_max if precio_max > 0 else float(nz(pres.get("PRECIO_PRESENT"), 0.0))
        precio_bot = (
            float(nz(botella.get("precio_unitario"), 0.0)) if botella else 0.0
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

        stock_base = float(nz(base.get("cantidad_disponible"), 0.0))
        stock_pres = float(
            nz(
                pres.get("STOCK_DISPONIBLE")
                or pres.get("stock_disponible")
                or pres.get("cantidad_disponible")
                or 0.0
            )
        )
        stock_ref = stock_base
        if stock_pres > 0 and stock_base > 0:
            stock_ref = min(stock_base, stock_pres)
        elif stock_pres > 0:
            stock_ref = stock_pres

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
            if stock_base > 0 and stock_bot > 0:
                stock_ref = min(stock_base, stock_bot)
            elif stock_bot > 0:
                stock_ref = stock_bot

        item = {
            "_prod": {
                "categoria": "PRESENTACION",
                "precio_unitario": unit_price,
                "precio_venta": unit_price,
                "precio_oferta": precio_oferta_total,
                "precio_oferta_base": precio_oferta_total,
                "precio_minimo": precio_min_total,
                "precio_minimo_base": precio_min_total,
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

        def base_has_match(p):
            dep_base = (p.get("categoria", "") or "").upper()
            gen_base = (p.get("genero", "") or "").strip().lower()
            for pr in pres_ml_matches:
                if (pr.get("DEPARTAMENTO", "") or "").upper() == dep_base:
                    pr_gen = (pr.get("GENERO", "") or "").strip().lower()
                    if not pr_gen or pr_gen == gen_base:
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
            if (ALLOW_NO_STOCK or float(nz(p.get("cantidad_disponible"), 0.0)) > 0.0)
            and base_has_match(p)
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
        base = next((p for p in self.productos if str(p.get("id")) == cod_base), None)
        if not base:
            return

        dep_base = (base.get("categoria", "") or "").upper()
        gen_base = (base.get("genero", "") or "").strip().lower()
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
        precio_pres = float(nz(pres_final.get("PRECIO_PRESENT"), 0.0))
        precio_pc = float(nz(pc.get("precio_unitario", pc.get("precio_venta")), 0.0))
        unit_price = precio_pres + precio_pc

        nombre_pres = (
            pres_final.get("NOMBRE") or pres_final.get("CODIGO_NORM") or pres_final.get("CODIGO")
        )
        nombre_final = f"A LA MODE {base.get('nombre', '')} {nombre_pres}".strip()
        codigo_final = f"{pc.get('id', '')}{base.get('id', '')}"
        ml = ml_botella

        stock_bot = (
            float(nz(botella_ref.get("cantidad_disponible"), 0.0)) if botella_ref else None
        )
        stock_base = float(nz(base.get("cantidad_disponible"), 0.0))
        if stock_bot is not None:
            if stock_bot > 0 and stock_base > 0:
                stock_ref = min(stock_bot, stock_base)
            elif stock_bot > 0:
                stock_ref = stock_bot
            elif stock_base > 0:
                stock_ref = stock_base
            else:
                stock_ref = 0.0
        else:
            stock_ref = stock_base if stock_base > 0 else 0.0

        item = {
            "_prod": {"precio_unitario": unit_price},
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
        }
        self.model.add_item(item)

    def _selector_presentacion(self, pres: dict):
        dep = (pres.get("DEPARTAMENTO") or pres.get("departamento") or "").strip().upper()
        gen = (pres.get("GENERO") or pres.get("genero") or "").strip().lower()
        essence_cats = {c.upper() for c in CATS}
        dep_is_presentation = dep in {"", "PRESENTACION", "PRESENTACIONES"}
        rel_codes = self._presentation_base_codes(pres)
        base_candidates = [
            p
            for p in self.productos
            if (
                (
                    str(p.get("categoria", "")).strip().upper() in essence_cats
                    if dep_is_presentation
                    else (str(p.get("categoria", "")).strip().upper() == dep)
                )
            )
            and ((not gen) or (str(p.get("genero", "")).strip().lower() == gen))
            and (ALLOW_NO_STOCK or float(nz(p.get("cantidad_disponible"), 0.0)) > 0.0)
            and (not self._is_generic_category_row(p))
        ]

        if rel_codes and base_candidates:
            base_candidates.sort(
                key=lambda p: (
                    0 if str(p.get("id", "")).strip().upper() in rel_codes else 1,
                    str(p.get("id", "")).strip().upper(),
                )
            )

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
        base = next((p for p in base_candidates if str(p.get("id")) == cod_base), None)
        if not base:
            return

        self._agregar_presentacion_con_base(pres, base)
