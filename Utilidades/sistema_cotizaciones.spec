# -*- mode: python ; coding: utf-8 -*-

import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
BASE_DIR = os.path.abspath(".")
SRC_DIR  = os.path.join(BASE_DIR, "src")

pyside6_datas   = collect_data_files("PySide6")
reportlab_datas = collect_data_files("reportlab")
openpyxl_datas  = collect_data_files("openpyxl")

our_datas = [
    (os.path.join(BASE_DIR, "templates"), "templates"),
    (os.path.join(BASE_DIR, "Utilidades", "requirements.txt"), "Utilidades"),
]

all_datas = pyside6_datas + reportlab_datas + openpyxl_datas + our_datas

pyside6_hidden = collect_submodules("PySide6")

hidden = [
    "pandas",
    "openpyxl",
    "reportlab",
    "src",
    "src.pricing",
    "src.presentations",
    "src.logging_setup",
    "src.config",
    "src.version",      # NUEVO
    "src.updater",      # NUEVO
    *pyside6_hidden,
]

a = Analysis(
    ['main.py'],
    pathex=[BASE_DIR, SRC_DIR],
    binaries=[],
    datas=all_datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tests', 'pytest', 'unittest'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='SistemaCotizaciones',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(BASE_DIR, "templates", "logo_sistema.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='SistemaCotizaciones'
)
