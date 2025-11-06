# src/paths.py
import os, sys, shutil
from PySide6.QtGui import QIcon
from PySide6.QtCore import QStandardPaths

BASE_APP_TITLE = "Cotizador"

def resource_path(relative_path: str) -> str:
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)

        cand_root = os.path.join(base_dir, relative_path)
        if os.path.exists(cand_root):
            return cand_root

        cand_internal = os.path.join(base_dir, "_internal", relative_path)
        if os.path.exists(cand_internal):
            return cand_internal

        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            cand_meipass = os.path.join(meipass, relative_path)
            if os.path.exists(cand_meipass):
                return cand_meipass

        return cand_root
    else:
        return os.path.join(os.path.abspath("."), relative_path)

def user_docs_root() -> str:
    try:
        base = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation) or os.path.join(os.path.expanduser("~"), "Documents")
    except Exception:
        base = os.path.join(os.path.expanduser("~"), "Documents")
    root = os.path.join(base, "Cotizaciones")
    os.makedirs(root, exist_ok=True)
    return root

def user_docs_dir(subfolder: str) -> str:
    d = os.path.join(user_docs_root(), subfolder)
    os.makedirs(d, exist_ok=True)
    return d

# Rutas principales
APP_DATA_DIR   = os.path.abspath(resource_path("data"))         # solo lectura / fallback
DATA_DIR       = user_docs_dir("data")                          # escribible
COTIZACIONES_DIR = user_docs_dir("cotizaciones")                # escribible
TEMPLATES_DIR  = resource_path("templates")                     # solo lectura
CONFIG_DIR     = resource_path("config")                        # solo lectura (installer)

def resolve_country_asset(filename: str, country_code: str | None = None) -> str | None:
    """
    Devuelve primero templates/<PAIS>/<filename> y si no existe,
    hace fallback a templates/<filename>.
    """
    tries = []
    if country_code:
        tries.append(os.path.join(TEMPLATES_DIR, country_code, filename))
    tries.append(os.path.join(TEMPLATES_DIR, filename))
    for p in tries:
        if os.path.exists(p):
            return p
    return None


def resolve_template_path(country_code: str | None) -> str | None:
    # Busca template.{jpg/png} en templates/<PAIS>/ luego en templates/
    tries = []
    if country_code:
        for ext in ("jpg", "jpeg", "png"):
            tries.append(os.path.join(TEMPLATES_DIR, country_code, f"template.{ext}"))
    for ext in ("jpg", "jpeg", "png"):
        tries.append(os.path.join(TEMPLATES_DIR, f"template.{ext}"))

    for p in tries:
        if os.path.exists(p): return p
    return None

def load_app_icon(country_code: str | None) -> QIcon:
    if country_code:
        cand = os.path.join(TEMPLATES_DIR, country_code, "logo_sistema.ico")
        if os.path.exists(cand): return QIcon(cand)
    p2 = resource_path("logo_sistema.ico")
    if os.path.exists(p2):
        return QIcon(p2)
    return QIcon()

def set_win_app_id():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u"Cotizador.1")
        except Exception:
            pass

def ensure_data_seed_if_empty():
    try:
        if not os.path.isdir(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)
        is_empty = (len(os.listdir(DATA_DIR)) == 0)
        if is_empty and os.path.isdir(APP_DATA_DIR) and len(os.listdir(APP_DATA_DIR)) > 0:
            for name in os.listdir(APP_DATA_DIR):
                src = os.path.join(APP_DATA_DIR, name)
                dst = os.path.join(DATA_DIR, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
    except Exception:
        pass
