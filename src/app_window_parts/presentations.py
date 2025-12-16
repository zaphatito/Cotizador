# src/app_window_parts/presentations.py
from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QDialog

from ..config import ALLOW_NO_STOCK
from ..utils import nz
from ..presentations import map_pc_to_bottle_code, extract_ml_from_text, ml_from_pres_code_norm
from ..widgets import SelectorTablaSimple


class PresentationsMixin:
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
        dep = (pres.get("DEPARTAMENTO") or "").upper()
        gen = (pres.get("GENERO") or "").strip().lower()
        base_candidates = [
            p
            for p in self.productos
            if (p.get("categoria", "").upper() == dep)
            and ((not gen) or (str(p.get("genero", "")).strip().lower() == gen))
            and (ALLOW_NO_STOCK or float(nz(p.get("cantidad_disponible"), 0.0)) > 0.0)
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
        base = next((p for p in base_candidates if str(p.get("id")) == cod_base), None)
        if not base:
            return

        botella = None
        if bool(pres.get("REQUIERE_BOTELLA", False)):
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
            if not bot_opts:
                QMessageBox.warning(
                    self,
                    "Sin botellas PC",
                    "No hay botellas PC compatibles para esta presentación.",
                )
                return
            botella = bot_opts[0]

        precio_pres = float(nz(pres.get("PRECIO_PRESENT"), 0.0))
        precio_bot = (
            float(nz(botella.get("precio_unitario"), 0.0)) if botella else 0.0
        )
        unit_price = precio_pres + precio_bot

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
        stock_ref = stock_base
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
            "_prod": {"precio_unitario": unit_price},
            "codigo": codigo_final,
            "producto": nombre_final,
            "categoria": "PRESENTACION",
            "cantidad": 1.0,
            "ml": str(ml) if ml else "",
            "precio": float(unit_price),
            "total": round(float(unit_price) * 1.0, 2),
            "fragancia": base.get("nombre", "")
            if dep in ("ESENCIA", "ESENCIAS")
            else "",
            "observacion": "",
            "stock_disponible": float(stock_ref),
            "precio_override": None,
            "precio_tier": None,
        }
        self.model.add_item(item)
