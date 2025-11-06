# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

block_cipher = None

# === Rutas base (sin __file__) ===
# Ejecuta el comando de PyInstaller desde la RAÍZ del repo (donde está main.py)
ROOT = Path(os.getcwd())
MAIN = ROOT / "main.py"
ICON_PATH = ROOT / "templates" / "logo_sistema.ico"

# --- Util: incluir archivos de una carpeta (recursivo) ---
def folder_files(src_dir: Path, dest_rel: str):
    if not src_dir.exists():
        return []
    items = []
    for p in src_dir.rglob("*"):
        if p.is_file():
            items.append((str(p), str(dest_rel)))
    return items

# DATA que quieres llevar al bundle:
datas = []
# 1) config/cotizador.json  -> dist/.../config
cfg = ROOT / "config" / "cotizador.json"
if cfg.exists():
    datas.append((str(cfg), "config"))

# 2) Copias opcionales de carpetas completas (descomenta si las necesitas)
# datas += folder_files(ROOT / "assets", "assets")
# datas += folder_files(ROOT / "templates", "templates")

a = Analysis(
    [str(MAIN)],
    pathex=[str(ROOT)],             # agrega la raíz al sys.path del build
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Silencia el warning de PySide6 deploy_lib
    excludes=['PySide6.scripts.deploy', 'PySide6.scripts.deploy_lib', 'project_lib'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SistemaCotizaciones",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # pon True si quieres consola
    icon=str(ICON_PATH) if ICON_PATH.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="SistemaCotizaciones",
)
