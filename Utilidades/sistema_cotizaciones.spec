# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

ROOT = Path(os.getcwd())
MAIN = ROOT / "main.py"
ICON_PATH = ROOT / "templates" / "logo_sistema.ico"

def folder_files(src_dir: Path, dest_rel: str):
    if not src_dir.exists():
        return []
    items = []
    for p in src_dir.rglob("*"):
        if p.is_file():
            items.append((str(p), str(dest_rel)))
    return items

# === Datos propios que quieres dentro del bundle ===
datas = []
cfg = ROOT / "config" / "cotizador.json"
if cfg.exists():
    datas.append((str(cfg), "config"))

# (opcional) mete templates al bundle
# datas += folder_files(ROOT / "templates", "templates")

# === PySide6 & shiboken6: datos y submódulos (CLAVE) ===
datas += collect_data_files("PySide6")
datas += collect_data_files("shiboken6")
# Si usas reportlab/openpyxl con assets internos:
# datas += collect_data_files("reportlab")
# datas += collect_data_files("openpyxl")

hiddenimports = []
hiddenimports += collect_submodules("PySide6")  # <- imprescindible

a = Analysis(
    [str(MAIN)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    upx=True,         # pon False si no tienes UPX instalado
    console=False,    # pon True si quieres ver consola para depurar
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
