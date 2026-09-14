# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path.cwd()
a = Analysis(
    [str(ROOT / 'pilot_main.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / 'templates'), 'templates')],
    hiddenimports=['PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'shiboken6'],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PySide6.scripts', 'project_lib', 'jinja2',
        'PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel',
        'PySide6.QtQml', 'PySide6.QtQuick', 'PySide6.QtQuickControls2', 'PySide6.QtQuickWidgets',
        'PySide6.Qt6Quick3D', 'PySide6.Qt3DCore', 'PySide6.Qt3DInput', 'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender', 'PySide6.QtDesigner', 'PySide6.QtPdf', 'PySide6.QtPdfWidgets',
        'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets', 'PIL._avif', 'PIL.AvifImagePlugin'],
    optimize=1, noarchive=False,
)
# Qt usa la API ICU de Windows. La ICU ajena hallada en PATH exporta símbolos
# con sufijo de versión y rompe QtCore al empaquetar; no debe ocultar la del SO.
a.binaries = [entry for entry in a.binaries
              if Path(entry[0]).name.lower() not in {'icuuc.dll', 'icudt78.dll'}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='CotizadorPiloto', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    icon=str(ROOT / 'templates/logo_sistema.ico'))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='CotizadorPiloto')
