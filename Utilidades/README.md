# Activar entorno virtual
actv

# TODO CON ENTORNO VIRTUAL ACTIVO

# Diseñador de QT
pyside6-designer

# Migrar librerias(entorno virtual activo)
pip freeze > Utilidades/requirements.txt

# iniciar
python main.py

# abrir app en modo dev
# ejecutar desde la raiz del proyecto, con el entorno virtual activo
# en modo dev la app usa el endpoint local: http://localhost:3000/service
python main.py


# ejecutable
powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Bump patch

# bash
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch

# release -Bump major
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump major


# Desactivar entorno virtual
deactivate






powershell -ExecutionPolicy Bypass -File tools\release.ps1 `
  -Bump patch `
  -RepoUser "zaphatito" `
  -RepoName "Cotizador" `
  -IssPath "Output\script inno.iss"
