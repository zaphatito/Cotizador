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


# ejecutable local, sin publicar assets
powershell -ExecutionPolicy Bypass -File tools\release.ps1 -Bump patch

# bash
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch

# draft release en GitHub Releases publico
$env:GH_TOKEN='github_pat_con_permiso_contents_write_del_repo_de_releases'
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch -Publish

# publicar directamente, sin draft
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump patch -Publish -Draft:$false

# El codigo puede estar privado en zaphatito/Cotizador.
# El updater de clientes lee el manifiesto publico desde:
# https://github.com/zaphatito/CotizadorReleases/releases/latest/download/cotizador.json

# release -Bump major
powershell.exe -ExecutionPolicy Bypass -File "tools/release.ps1" -Bump major -Publish


# Desactivar entorno virtual
deactivate






powershell -ExecutionPolicy Bypass -File tools\release.ps1 `
  -Bump patch `
  -RepoUser "zaphatito" `
  -RepoName "CotizadorReleases" `
  -Publish `
  -IssPath "Output\script inno.iss"
