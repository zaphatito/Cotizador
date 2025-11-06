# Activar entorno virtual
source .venv/Scripts/activate

# TODO CON ENTORNO VIRTUAL ACTIVO

# Diseñador de QT
pyside6-designer

# Migrar librerias(entorno virtual activo)
pip freeze > Utilidades/requirements.txt

# iniciar
python main.py

# iniciar Test
pytest -q tests/test_presentations.py


# ejecutable

powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Bump patch -RepoUser "zaphatito" -RepoName "Cotizador" -IssPath "Output\script inno.iss" -VenvPath "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\.venv"





# Desactivar entorno virtual
deactivate


# Estructura
Cotizador/
├─ main.py
└─ tests/
   ├─ conftest.py
   ├─ test_presentations.py
   ├─ test_pricing.py
└─ src/
   ├─ __init__.py           # vacío intencionalmente
   ├─ paths.py              # Rutas, resource_path, carpetas de usuario, ícono, AppUserModelID, templates
   ├─ config.py             # País/moneda, tipo de listado, constantes de app
   ├─ utils.py              # Utilidades numéricas y formateo de moneda
   ├─ pricing.py            # CATS, cantidad_para_mostrar, reglas y precios
   ├─ dataio.py             # Lectura de inventarios (Hoja 1)
   ├─ presentations.py      # Lectura Hoja 2, normalización de códigos, helpers
   ├─ pdfgen.py             # generar_pdf (usa ReportLab)
   ├─ logging_setup.py      # Generar Logger
   ├─ widgets.py            # SelectorTablaSimple y ListadoProductosDialog
   ├─ models.py             # ItemsModel (QAbstractTableModel)
   ├─ app_window.py         # QMainWindow SistemaCotizaciones (controla todo el flujo)
   └─ app.py                # run_app(): arranca QApplication, carga datos, muestra la ventana




powershell -ExecutionPolicy Bypass -File tools\release.ps1 `
  -Bump patch `
  -RepoUser "zaphatito" `
  -RepoName "Cotizador" `
  -IssPath "Output\script inno.iss"
