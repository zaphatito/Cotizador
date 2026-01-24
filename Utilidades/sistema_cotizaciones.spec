# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

block_cipher = None

ROOT = Path.cwd()
MAIN = ROOT / "main.py"
ICON_PATH = ROOT / "templates" / "logo_sistema.ico"

datas = []

cfg = ROOT / "config" / "cotizador.json"
if cfg.exists():
    datas.append((str(cfg), "config"))

datas.append((str(ROOT / "templates"), "templates"))

changelog = ROOT / "changelog.txt"
if changelog.exists():
    datas.append((str(changelog), "."))

# Solo lo que tú usas (Widgets/Gui/Core)
hiddenimports = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "shiboken6",
]

excludes = [
    "PySide6.scripts",
    "project_lib",
    "jinja2",

    # WebEngine (EL GIGANTE)
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",

    # QML / Quick
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",

    # 3D / tools
    "PySide6.Qt6Quick3D",  # por si acaso (algunas instalaciones)
    "PySide6.Qt3DCore",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtDesigner",

    # Pdf (solo exclúyelo si NO usas QtPdf)
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",

    # Multimedia (si no lo usas)
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",

    # Pillow: no uso AVIF
    "PIL._avif",
    "PIL.AvifImagePlugin",
]

a = Analysis(
    [str(MAIN)],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    optimize=1,
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
