# main.py
import sys


if __name__ == "__main__":
    check_flag = next((flag for flag in ("--release-self-check", "--precios-self-check")
                       if flag in sys.argv), None)
    if check_flag:
        # Verifica el ejecutable empaquetado sin iniciar la app ni abrir la DB.
        import json
        from pathlib import Path
        from src.build_profile import automatic_updates_allowed
        from src.lcdp_pricing import line_discount
        updates_allowed = automatic_updates_allowed()
        if check_flag == "--precios-self-check" and updates_allowed:
            raise RuntimeError("Las actualizaciones deben estar deshabilitadas")
        if check_flag == "--release-self-check" and not updates_allowed:
            raise RuntimeError("El release público debe permitir actualizaciones")
        for country in ("PE", "PY", "VE", "BO"):
            if line_discount(2, "amount", amount=1, country=country)[2] != 1:
                raise RuntimeError("El mínimo exacto no se conserva")
            try:
                line_discount(2, "amount", amount=1.01, country=country)
            except ValueError:
                pass
            else:
                raise AssertionError("El mínimo no se validó")
        from PySide6.QtWidgets import QApplication
        app = QApplication([])
        result = Path(sys.argv[sys.argv.index(check_flag) + 1])
        result.write_text(json.dumps({"automatic_updates": updates_allowed, "countries_verified": 4,
                                     "qt_loaded": True}), encoding="utf-8")
    else:
        from src.app import run_app
        run_app()
