# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os

# Para recoger datos y submódulos de PySide6/shiboken6 de forma robusta
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# === Rutas base ===
ROOT = Path(os.getcwd())            # ejecuta pyinstaller desde la RAÍZ del repo
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

# ===== DATA que deben viajar en el bundle =====
datas = []

# 1) Manifiesto del updater
cfg = ROOT / "config" / "cotizador.json"
if cfg.exists():
    datas.append((str(cfg), "config"))

# 2) Templates (¡IMPORTANTE! aquí vive el manual y el logo)
datas += folder_files(ROOT / "templates", "templates")

# 3) Archivos de PySide6 (plugins/platforms, etc.)
datas += collect_data_files("PySide6", include_py_files=False)

# ===== Ocultamos dependencias dinámicas =====
hiddenimports = []
hiddenimports += collect_submodules("PySide6")
hiddenimports += collect_submodules("shiboken6")

a = Analysis(
    [str(MAIN)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Excluimos solo herramientas de deploy que no hacen falta
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
    console=False,
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
