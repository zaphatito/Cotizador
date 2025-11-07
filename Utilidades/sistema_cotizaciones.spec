# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# === Rutas base ===
# OJO: el release.ps1 hace Push-Location al root; por eso usamos cwd.
ROOT = Path.cwd()
MAIN = ROOT / "main.py"
ICON_PATH = ROOT / "templates" / "logo_sistema.ico"

# ===== DATA que deben viajar en el bundle =====
datas = []

# 1) Manifiesto del updater -> {app}\config
cfg = ROOT / "config" / "cotizador.json"
if cfg.exists():
    datas.append((str(cfg), "config"))

# 2) Templates completos (preserva subcarpetas)
#    (No usar Tree; usar par (src_dir, dest_dir) para PyInstaller 6.x)
datas.append((str(ROOT / "templates"), "templates"))

# 3) Archivos de PySide6 (plugins/platforms, etc.) sin .py
datas += collect_data_files("PySide6", include_py_files=False)

# ===== Hidden imports (Qt) =====
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
    excludes=['PySide6.scripts', 'project_lib'],  # <- sin punto final
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
