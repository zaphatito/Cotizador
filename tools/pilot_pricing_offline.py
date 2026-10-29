"""Piloto local de precios: modelo y diálogo reales, catálogo sintético, sin red."""
from pathlib import Path
import json
import os
import sqlite3
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    os.chdir(root)
    sandbox = tempfile.TemporaryDirectory(prefix="cotizador-precios-offline-", ignore_cleanup_errors=True)
    data = Path(sandbox.name).resolve()
    os.environ["LOG_DIR"] = str(data / "logs")
    os.environ["COTIZADOR_PROFILE"] = "pilot"
    checking = "--self-check" in sys.argv
    if checking:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto",
                     "subprocess.Popen", "os.system", "os.startfile", "os.startfile/2"}:
            raise RuntimeError("Piloto sin conexión: red y procesos externos bloqueados")
    sys.addaudithook(offline)
    original_connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        target = os.fspath(database)
        if target != ":memory:":
            path = Path(target.removeprefix("file:").split("?", 1)[0]).resolve()
            if not path.is_relative_to(data):
                raise sqlite3.OperationalError("Piloto: solo SQLite temporal")
        return original_connect(database, *args, **kwargs)
    sqlite3.connect = isolated_connect

    from PySide6.QtCore import Qt, QStandardPaths
    from PySide6.QtWidgets import QApplication, QMessageBox
    QStandardPaths.writableLocation = lambda *_: str(data)
    import src.app  # Orden requerido por las fachadas; no llama run_app.
    import pandas as pd
    from src.country_rules import country_profile
    from src.catalog_context import QuoteContext
    from src.app_window_parts import main as editor, currency
    from src.ui_theme import apply_modern_theme
    from src.paths import load_app_icon

    editor.resolve_db_path = lambda: str(data / "pilot.sqlite3")
    currency.resolve_db_path = editor.resolve_db_path
    editor.is_ai_enabled = lambda **_: False
    editor.is_recommendations_enabled = lambda **_: False

    def local_only(self):
        QMessageBox.information(self, "Piloto sin conexión",
            "Esta prueba permite editar y previsualizar cotizaciones. "
            "La emisión y el envío están deshabilitados en este piloto.")
    # Mantener la vista y los componentes reales, sin arrancar sincronizadores.
    editor.SistemaCotizaciones.generar_cotizacion = local_only
    app = QApplication([])
    apply_modern_theme(app)
    windows = []
    for code in ("PY", "VE", "BO", "PE"):
        profile = country_profile(code)
        products = pd.DataFrame([
            dict(id=sku, codigo=sku, nombre=name, categoria="BOTELLAS",
                 genero="UNISEX", p_max=value, p_min=value * 0.9,
                 p_oferta=value * 0.8, precio_venta=1, cantidad_disponible=999)
            for sku, name, value in (
                ("MINIMO", "Prueba del mínimo", 2.0),
                ("REDONDEO", "Prueba de redondeo", 5.99),
                ("IMPORTE", "Prueba de descuento por importe", 100.0),
                ("PEQUENO", "Producto inferior a una unidad", 0.5),
            )
        ])
        context = QuoteContext.from_values(country_code=code,
            company_type="LA CASA DEL PERFUME", username="PILOTO OFFLINE",
            id_cotizador="TEST", base_currency=profile.base_currency)
        window = editor.SistemaCotizaciones(products, pd.DataFrame(),
            load_app_icon(code), quote_context=context)
        for product in products.to_dict("records"):
            window.model.add_item(dict(codigo=product['id'], producto=product['nombre'],
                categoria=product['categoria'], precio=product['p_max'],
                cantidad=2 if product['id'] == 'REDONDEO' else 1,
                stock_disponible=999, _prod=product))
        window.setWindowTitle(f"Cotizador · {profile.name} · Piloto PRECIOS SIN CONEXIÓN")
        windows.append(window)
        window.show()
        app.processEvents()
        if checking:
            assert not window.model.setData(window.model.index(0, 2),
                dict(mode='amount', amount=1.01), Qt.EditRole)
            assert window.model.setData(window.model.index(0, 2),
                dict(mode='amount', amount=1), Qt.EditRole)
            assert window.items[0]['total'] == 1
    if checking:
        import socket
        try:
            socket.create_connection(('127.0.0.1', 9))
        except RuntimeError:
            pass
        else:
            raise AssertionError('La red debe estar bloqueada')
        print(json.dumps(dict(real_editor_windows=4, minimum_verified=True, network_blocked=True)))
        for window in windows:
            window.close()
        return 0
    windows[-1].raise_()
    windows[-1].activateWindow()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
