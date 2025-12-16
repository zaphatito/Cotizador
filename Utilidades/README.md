# Activar entorno virtual
actv

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
# para hacerla obligatoria usar -mandatory
powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Bump patch

powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Bump patch -mandatory

powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch

powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch 
-mandatory
-PruneOpenGLSW



# Desactivar entorno virtual
deactivate






powershell -ExecutionPolicy Bypass -File tools\release.ps1 `
  -Bump patch `
  -RepoUser "zaphatito" `
  -RepoName "Cotizador" `
  -IssPath "Output\script inno.iss"
