import importlib.util
import sys
import types
from pathlib import Path


def _load_build_completer_strings():
    module_name = "src.app_window_parts.completer"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached.build_completer_strings

    src_dir = Path(__file__).resolve().parents[1] / "src"
    app_parts_dir = src_dir / "app_window_parts"

    app_parts_pkg = sys.modules.get("src.app_window_parts")
    if app_parts_pkg is None:
        app_parts_pkg = types.ModuleType("src.app_window_parts")
        app_parts_pkg.__path__ = [str(app_parts_dir)]
        sys.modules["src.app_window_parts"] = app_parts_pkg

    spec = importlib.util.spec_from_file_location(module_name, app_parts_dir / "completer.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module.build_completer_strings


build_completer_strings = _load_build_completer_strings()


def test_build_completer_strings_uses_category_marker_as_wildcard():
    productos = [
        {
            "id": "ESENCIAS",
            "nombre": "ESENCIAS",
            "categoria": "ESENCIAS",
            "genero": "Otro",
            "cantidad_disponible": 0.0,
        },
        {
            "id": "BASE02",
            "nombre": "ALCOHOL PERFUMERIA",
            "categoria": "ESENCIAS",
            "genero": "dama",
            "cantidad_disponible": 100.0,
        },
        {
            "id": "FIJ001",
            "nombre": "FIJADOR",
            "categoria": "ESENCIAS",
            "genero": "dama",
            "cantidad_disponible": 50.0,
        },
        {
            "id": "ESENH001",
            "nombre": "ESENCIA HOMME",
            "categoria": "ESENCIAS",
            "genero": "dama",
            "cantidad_disponible": 10.0,
        },
    ]
    presentaciones = [
        {
            "CODIGO": "0003",
            "NOMBRE": "KIT 3 ML",
            "DEPARTAMENTO": "ESENCIAS",
            "GENERO": "dama",
            "STOCK_DISPONIBLE": 5.0,
            "CODIGOS_PRODUCTO": "BASE02,ESENCIAS,FIJ001",
            "P_MAX": 1.0,
        }
    ]

    sugs = build_completer_strings(productos, [], presentaciones)

    assert any(s.startswith("ESENH0010003 - ") for s in sugs)
    assert not any(s.startswith("BASE020003 - ") for s in sugs)
    assert not any(s.startswith("FIJ0010003 - ") for s in sugs)
    assert not any(s.startswith("ESENCIAS0003 - ") for s in sugs)
