# src/paths.py
import os, sys, shutil, re
from PySide6.QtGui import QIcon
from PySide6.QtCore import QStandardPaths

BASE_APP_TITLE = "Cotizador"

def resource_path(relative_path: str) -> str:
    """
    Devuelve una ruta válida tanto en modo desarrollo como 'frozen' (PyInstaller).
    """
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

        # último intento (aunque no exista todavía)
        return cand_root
    else:
        return os.path.join(os.path.abspath("."), relative_path)


def user_docs_root() -> str:
    try:
        base = (
            QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
            or os.path.join(os.path.expanduser("~"), "Documents")
        )
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
APP_DATA_DIR      = os.path.abspath(resource_path("data"))   # solo lectura / fallback
DATA_DIR          = user_docs_dir("data")                    # escribible
COTIZACIONES_DIR  = user_docs_dir("cotizaciones")            # escribible
TEMPLATES_DIR     = resource_path("templates")               # solo lectura
CONFIG_DIR        = resource_path("config")                  # solo lectura (installer)
FONTS_DIR         = os.path.join(TEMPLATES_DIR, "fonts")     # solo lectura


# -------- helpers internos --------
_CC_RE = re.compile(r'(?i)\btemplate[_-]?([a-z]{2})(?:[_-]\d+)?\.(jpg|jpeg|png)$')

def _infer_cc_from_filename(filename: str) -> str | None:
    """
    Si el filename es del estilo TEMPLATE_PE.jpg / TEMPLATE_py_2.png, devuelve 'PE'/'PY'.
    """
    base = os.path.basename(filename)
    m = _CC_RE.search(base)
    if m:
        return m.group(1).upper()
    return None


def resolve_country_asset(filename: str, country_code: str | None = None) -> str | None:
    """
    Busca un asset priorizando carpeta por país.
    Soporta dos formas:
      - Pasando country_code y filename (p.ej. 'TEMPLATE_PE.jpg'): templates/PE/TEMPLATE_PE.jpg
      - Sin country_code pero con sufijo en filename (TEMPLATE_PE.jpg): infiere 'PE' y busca igual.

    Fallback: templates/<filename> en la raíz de templates.
    """
    base_name = os.path.basename(filename)
    tries: list[str] = []

    cc = (country_code or "").strip().upper()
    if not cc:
        cc = _infer_cc_from_filename(base_name) or ""

    if cc:
        tries.append(os.path.join(TEMPLATES_DIR, cc, base_name))

    # raíz de templates/
    tries.append(os.path.join(TEMPLATES_DIR, base_name))

    for p in tries:
        if os.path.exists(p):
            return p
    return None


def resolve_template_path(country_code: str | None) -> str | None:
    """
    Busca la plantilla siguiendo tu convención:
      templates/<PAIS>/TEMPLATE_<PAIS>.(jpg|jpeg|png)
    con fallbacks razonables.
    """
    cc = (country_code or "").strip().upper()
    exts = ("jpg", "jpeg", "png")
    tries: list[str] = []

    if cc:
        # 1) TEMPLATE_<PAIS>.* dentro del país
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, cc, f"TEMPLATE_{cc}.{ext}"))
        # 2) Variante _2
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, cc, f"TEMPLATE_{cc}_2.{ext}"))
        # 3) TEMPLATE.* dentro del país
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, cc, f"TEMPLATE.{ext}"))
        # 4) En raíz con sufijo de país
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, f"TEMPLATE_{cc}.{ext}"))

    # 5) En la raíz, genérico
    for ext in exts:
        tries.append(os.path.join(TEMPLATES_DIR, f"TEMPLATE.{ext}"))

    # 6) Fallbacks minúscula/compat
    if cc:
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, cc, f"template_{cc}.{ext}"))
        for ext in exts:
            tries.append(os.path.join(TEMPLATES_DIR, cc, f"template.{ext}"))
    for ext in exts:
        tries.append(os.path.join(TEMPLATES_DIR, f"template.{ext}"))

    for p in tries:
        if os.path.exists(p):
            return p
    return None


def load_app_icon(country_code: str | None) -> QIcon:
    """
    Carga el ícono, priorizando por país.
    """
    cc = (country_code or "").strip().upper()
    candidates: list[str] = []

    if cc:
        candidates.append(os.path.join(TEMPLATES_DIR, cc, "logo_sistema.ico"))
    candidates.append(os.path.join(TEMPLATES_DIR, "logo_sistema.ico"))
    candidates.append(resource_path("logo_sistema.ico"))

    # Fallback PNG (QIcon soporta .png)
    if cc:
        candidates.append(os.path.join(TEMPLATES_DIR, cc, "logo.png"))
    candidates.append(os.path.join(TEMPLATES_DIR, "logo.png"))

    for p in candidates:
        if p and os.path.exists(p):
            return QIcon(p)

    return QIcon()


def set_win_app_id():
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(u"Cotizador.1")
        except Exception:
            pass


def ensure_data_seed_if_empty():
    """
    Deja 'data' vacío salvo que empaquetes datos en APP_DATA_DIR.
    """
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

def resolve_pdf_path_portable(stored: str) -> str:
    p = (stored or "").strip()
    if not p:
        return ""
    # Si ya existe tal cual (misma PC/ruta), úsalo
    if os.path.isabs(p) and os.path.exists(p):
        return p
    # Si no existe (otra PC/usuario), usa la carpeta local y solo el filename
    return os.path.join(COTIZACIONES_DIR, os.path.basename(p))



def resolve_font_asset(font_family: str, base_name: str, exts: tuple[str, ...] = ("otf", "ttf")) -> str | None:
    """
    Busca una fuente en:
      templates/fonts/<font_family>/<base_name>.<ext>
      templates/fonts/<base_name>.<ext>
    Devuelve la primera coincidencia existente, si no encuentra devuelve None.
    """
    candidates: list[str] = []
    for ext in exts:
        candidates.append(os.path.join(FONTS_DIR, font_family, f"{base_name}.{ext}"))
        candidates.append(os.path.join(FONTS_DIR, f"{base_name}.{ext}"))

    # Fallback explícito vía resource_path (por si PyInstaller reubica)
    for ext in exts:
        candidates.append(resource_path(os.path.join("templates", "fonts", font_family, f"{base_name}.{ext}")))
        candidates.append(resource_path(os.path.join("templates", "fonts", f"{base_name}.{ext}")))

    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None
