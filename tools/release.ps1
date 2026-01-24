param(
  [ValidateSet("major","minor","patch")]
  [string]$Bump = "patch",
  [string]$RepoUser = "zaphatito",
  [string]$RepoName = "Cotizador",
  [string]$ProjectRoot = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador",
  [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  [string]$SpecPath = "Utilidades\sistema_cotizaciones.spec",
  [string]$IssPath  = "Output\script inno.iss",
  [string]$VenvPath = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\.venv",
  [switch]$Mandatory,
  [switch]$PruneOpenGLSW
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Parse-Semver([string]$v) {
  if ($null -eq $v) { $v = "" }
  $v = $v.Trim()

  if ($v -notmatch '^\s*(\d+)\.(\d+)\.(\d+)\s*$') {
    throw "Versión inválida '$v'. Debe ser SEMVER: M.m.p (ej: 1.1.13)"
  }

  [pscustomobject]@{
    Major = [int]$Matches[1]
    Minor = [int]$Matches[2]
    Patch = [int]$Matches[3]
  }
}

function Bump-Version([string]$v, [string]$kind) {
  $pv = Parse-Semver $v
  if ($null -eq $kind) { $kind = "" }
  $kind = $kind.ToLowerInvariant()

  switch ($kind) {
    "major" { return "{0}.{1}.{2}" -f ($pv.Major + 1), 0, 0 }
    "minor" { return "{0}.{1}.{2}" -f $pv.Major, ($pv.Minor + 1), 0 }
    "patch" { return "{0}.{1}.{2}" -f $pv.Major, $pv.Minor, ($pv.Patch + 1) }
    default { throw "Bump inválido '$kind' (use major/minor/patch)" }
  }
}

function Set-ContentUtf8NoBOM([string]$Path, [string]$Text) {
  $enc = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Remove-Dir-Robust([string]$path) {
  if (!(Test-Path $path)) { return }
  try { attrib -r -s -h "$path" /s /d 2>$null } catch {}
  for ($i=0; $i -lt 6; $i++) {
    try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop; return }
    catch { Start-Sleep -Milliseconds 600 }
  }
  try {
    $tmp = "$path._old_" + (Get-Random)
    Rename-Item -LiteralPath $path -NewName (Split-Path $tmp -Leaf) -ErrorAction SilentlyContinue
    Start-Sleep 2
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
  } catch {}
}

function Update-ChangelogHeader([string]$Path, [string]$Version) {
  $date = (Get-Date).ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)

  if (!(Test-Path $Path)) {
    # si no existe, lo creamos mínimo (pero en tu caso ya existe)
    $tpl = @"
SISTEMA DE COTIZACIONES — REGISTRO DE CAMBIOS

Versión: $Version
Fecha: $date

Resumen
- Se aplicaron mejoras generales de estabilidad, rendimiento y usabilidad.

Cambios y mejoras
- [Mejora] ________________________________________________
- [Mejora] ________________________________________________
- [Mejora] ________________________________________________

Correcciones
- [Fix] _________________________________________________
- [Fix] _________________________________________________
- [Fix] _________________________________________________

Notas importantes
- _________________________________________________
- _________________________________________________

Compatibilidad
- No se requieren acciones adicionales para continuar usando el sistema.
- La base de datos local y la configuración del usuario se conservan.

Soporte
- Si notas algún comportamiento inesperado después de actualizar, reinicia la aplicación y vuelve a intentar.
- Si el problema persiste, llama al soporte técnico.
"@
    Set-ContentUtf8NoBOM -Path $Path -Text $tpl
    return
  }

  $txt = Get-Content -Raw -LiteralPath $Path

  # soporta "Versión" con o sin tilde por si acaso
  $txt2 = [regex]::Replace($txt, '(?m)^\s*Versi[oó]n\s*:\s*.*$', "Versión: $Version")
  $txt2 = [regex]::Replace($txt2, '(?m)^\s*Fecha\s*:\s*.*$', "Fecha: $date")

  if ($txt2 -ne $txt) {
    Set-ContentUtf8NoBOM -Path $Path -Text $txt2
  }
}

# --- venv ---
if (-not $VenvPath -or -not (Test-Path $VenvPath)) {
  $cand1 = Join-Path $ProjectRoot ".venv"
  $cand2 = Join-Path $ProjectRoot "venv"
  if (Test-Path $cand1) { $VenvPath = $cand1 } elseif (Test-Path $cand2) { $VenvPath = $cand2 }
}
$py = $null
if ($VenvPath -and (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) { $py = Join-Path $VenvPath "Scripts\python.exe" }
else { throw "No se encontró el venv. Pasa -VenvPath o crea .venv/venv." }

Write-Host "Usando Python del venv: $py"

& $py -m pip install --upgrade pip
$reqFile = Join-Path $ProjectRoot "Utilidades\requirements.txt"
if (Test-Path $reqFile) { & $py -m pip install -r $reqFile }

# 1) bump version
$verFile = Join-Path $ProjectRoot "src\version.py"
$verTxt  = Get-Content -Raw $verFile
if ($verTxt -notmatch '__version__\s*=\s*"([^"]+)"') { throw "No se encontró __version__ en src\version.py" }
$cur = $Matches[1]
$prev = $cur
$next = Bump-Version $cur $Bump
Write-Host "Versión actual: $cur  ->  Nueva versión: $next"
$curV  = Parse-Semver $cur
$nextV = Parse-Semver $next

switch ($Bump.ToLowerInvariant()) {
  "minor" {
    if ($nextV.Major -ne $curV.Major) { throw "BUG: minor cambió major ($cur -> $next). Abortando." }
    if ($nextV.Minor -ne ($curV.Minor + 1)) { throw "BUG: minor no incrementó correctamente ($cur -> $next). Abortando." }
    if ($nextV.Patch -ne 0) { throw "BUG: minor debe resetear patch a 0 ($cur -> $next). Abortando." }
  }
  "patch" {
    if ($nextV.Major -ne $curV.Major -or $nextV.Minor -ne $curV.Minor) { throw "BUG: patch cambió major/minor ($cur -> $next). Abortando." }
    if ($nextV.Patch -ne ($curV.Patch + 1)) { throw "BUG: patch no incrementó correctamente ($cur -> $next). Abortando." }
  }
  "major" {
    if ($nextV.Major -ne ($curV.Major + 1) -or $nextV.Minor -ne 0 -or $nextV.Patch -ne 0) {
      throw "BUG: major inválido ($cur -> $next). Abortando."
    }
  }
}

Write-Host "Bump='$Bump' OK. ($cur -> $next)"

$verTxt = $verTxt -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$next`""
Set-Content -Path $verFile -Value $verTxt -Encoding UTF8

$changelogPath = Join-Path $ProjectRoot "changelog.txt"
Update-ChangelogHeader -Path $changelogPath -Version $next
Write-Host "OK: changelog actualizado (Versión/Fecha) -> $changelogPath"

# 2) patch .iss
$issFull = Join-Path $ProjectRoot $IssPath
$issTxt  = Get-Content -Raw $issFull
$issTxt  = $issTxt -replace '#define\s+MyAppVersion\s+"[^"]+"', "#define MyAppVersion `"$next`""
$manifestUrl = "https://raw.githubusercontent.com/$RepoUser/$RepoName/main/config/cotizador.json"
if ($issTxt -match '#define\s+UpdateManifestUrl\s+"[^"]+"') {
  $issTxt = $issTxt -replace '#define\s+UpdateManifestUrl\s+"[^"]+"', "#define UpdateManifestUrl `"$manifestUrl`""
}
Set-Content -Path $issFull -Value $issTxt -Encoding UTF8

# 3) PyInstaller build main app
$distRoot = Join-Path $ProjectRoot "dist"
$distDir  = Join-Path $distRoot "SistemaCotizaciones"

Get-Process SistemaCotizaciones -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Remove-Dir-Robust $distDir

$workRoot = Join-Path $env:LOCALAPPDATA "Cotizador\pyi_work"
Remove-Dir-Robust $workRoot

Push-Location $ProjectRoot
& $py -m PyInstaller -y --workpath $workRoot --distpath $distRoot $SpecPath
$pyiExit = $LASTEXITCODE
Pop-Location
if ($pyiExit -ne 0) { throw "PyInstaller falló con código $pyiExit." }

$builtExe = Join-Path $distDir "SistemaCotizaciones.exe"
if (!(Test-Path $builtExe)) { throw "No se encontró: $builtExe" }

# 3.1 prune opcional
$internal = Join-Path $distDir "_internal"
$pruneFiles = @(
  "PIL\_avif.cp313-win_amd64.pyd",
  "PySide6\Qt6Qml.dll",
  "PySide6\Qt6Quick.dll",
  "PySide6\Qt6Pdf.dll",
  "PySide6\qmlls.exe"
)
foreach ($rel in $pruneFiles) {
  $p = Join-Path $internal $rel
  if (Test-Path $p) { Remove-Item $p -Force; Write-Host "Removed: $p" }
}
if ($PruneOpenGLSW) {
  $opengl = Join-Path $internal "PySide6\opengl32sw.dll"
  if (Test-Path $opengl) { Remove-Item $opengl -Force; Write-Host "Removed: $opengl" }
}

# 3.2 (IMPORTANTE) NO inyectar seed DB: la app la crea en primera ejecución
# Limpieza defensiva por si alguien ejecutó el EXE dentro de dist y se creó la DB
$maybeDb = Join-Path $distDir "sqlModels\app.sqlite3"
if (Test-Path $maybeDb) {
  Remove-Item -Force $maybeDb
  Write-Host "OK: removido app.sqlite3 accidental de dist (la app la creará en runtime)"
}
$maybeWal = Join-Path $distDir "sqlModels\app.sqlite3-wal"
if (Test-Path $maybeWal) { Remove-Item -Force $maybeWal }
$maybeShm = Join-Path $distDir "sqlModels\app.sqlite3-shm"
if (Test-Path $maybeShm) { Remove-Item -Force $maybeShm }

Set-ContentUtf8NoBOM -Path (Join-Path $distDir "version.txt") -Text $next

# 4) build apply_update.exe
$applyScript = Join-Path $ProjectRoot "tools\apply_update.py"
if (!(Test-Path $applyScript)) { throw "No existe tools\apply_update.py" }

$applyBuildRoot = Join-Path $env:LOCALAPPDATA "Cotizador\apply_update_build"
Remove-Dir-Robust $applyBuildRoot
New-Item -ItemType Directory -Path $applyBuildRoot | Out-Null

$applyWork = Join-Path $applyBuildRoot "work"
$applyDist = Join-Path $applyBuildRoot "dist"
New-Item -ItemType Directory -Path $applyWork | Out-Null
New-Item -ItemType Directory -Path $applyDist | Out-Null

Push-Location $ProjectRoot
& $py -m PyInstaller -y --noconsole --onefile --name apply_update --workpath $applyWork --distpath $applyDist $applyScript
$applyExit = $LASTEXITCODE
Pop-Location
if ($applyExit -ne 0) { throw "PyInstaller (apply_update) falló con código $applyExit" }

$applyExe = Join-Path $applyDist "apply_update.exe"
if (!(Test-Path $applyExe)) { throw "No se generó apply_update.exe" }

$applyTargetDir = Join-Path $distDir "updater"
New-Item -ItemType Directory -Force -Path $applyTargetDir | Out-Null
Copy-Item -Force $applyExe (Join-Path $applyTargetDir "apply_update.exe")
Write-Host "OK: apply_update.exe -> dist\SistemaCotizaciones\updater\apply_update.exe"

# 5) build installer ALWAYS
& "$ISCC" "$issFull"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup falló con código $LASTEXITCODE." }

$setupName = "Setup_SistemaCotizaciones_{0}.exe" -f $next
$setupLocal = Join-Path $ProjectRoot "Output\$setupName"
if (!(Test-Path $setupLocal)) { throw "No se encontró $setupLocal" }
$setupSha = (Get-FileHash $setupLocal -Algorithm SHA256).Hash.ToUpper()

$exeUrl = "https://media.githubusercontent.com/media/$RepoUser/$RepoName/main/Output/$setupName"

# 6) build updates/<version> (FILES)
$updatesRoot = Join-Path $ProjectRoot "Output\updates"
$updateVerDir = Join-Path $updatesRoot $next
Remove-Dir-Robust $updateVerDir
New-Item -ItemType Directory -Force -Path $updateVerDir | Out-Null

Copy-Item -Path (Join-Path $distDir "*") -Destination $updateVerDir -Recurse -Force

# IMPORTANT: NO versionar DB dentro de updates/<ver> (por si apareciera)
$updateDb = Join-Path $updateVerDir "sqlModels\app.sqlite3"
if (Test-Path $updateDb) { Remove-Item -Force $updateDb; Write-Host "OK: removido app.sqlite3 de updates/<ver>" }
$updateWal = Join-Path $updateVerDir "sqlModels\app.sqlite3-wal"
if (Test-Path $updateWal) { Remove-Item -Force $updateWal }
$updateShm = Join-Path $updateVerDir "sqlModels\app.sqlite3-shm"
if (Test-Path $updateShm) { Remove-Item -Force $updateShm }

Write-Host "OK: update package -> $updateVerDir"

# 6.1 files[] sha (exclude DB)
$filesList = @()
Get-ChildItem -Path $updateVerDir -Recurse -File | ForEach-Object {
  $full = $_.FullName
  $rel  = $full.Substring($updateVerDir.Length + 1).Replace("\","/")
  $relLower = $rel.ToLower()
  if ($relLower -eq "sqlmodels/app.sqlite3") { return }
  if ($relLower -eq "sqlmodels/app.sqlite3-wal") { return }
  if ($relLower -eq "sqlmodels/app.sqlite3-shm") { return }
  $sha  = (Get-FileHash $full -Algorithm SHA256).Hash.ToLower()
  $filesList += [ordered]@{ path = $rel; sha256 = $sha }
}

# 6.2 delete[] (diff contra updates/<prev>) (exclude DB)
$deleteList = @()
$prevUpdateDir = Join-Path $updatesRoot $prev
if (Test-Path $prevUpdateDir) {
  $prevFiles = Get-ChildItem -Path $prevUpdateDir -Recurse -File | ForEach-Object {
    $_.FullName.Substring($prevUpdateDir.Length + 1).Replace("\","/").ToLower()
  }
  $newFiles = Get-ChildItem -Path $updateVerDir -Recurse -File | ForEach-Object {
    $_.FullName.Substring($updateVerDir.Length + 1).Replace("\","/").ToLower()
  }

  $prevSet = New-Object "System.Collections.Generic.HashSet[string]"
  $newSet  = New-Object "System.Collections.Generic.HashSet[string]"
  foreach($p in $prevFiles){ [void]$prevSet.Add($p) }
  foreach($n in $newFiles){ [void]$newSet.Add($n) }

  foreach($p in $prevSet){
    if (-not $newSet.Contains($p)) {
      if ($p -eq "sqlmodels/app.sqlite3") { continue }
      if ($p -eq "sqlmodels/app.sqlite3-wal") { continue }
      if ($p -eq "sqlmodels/app.sqlite3-shm") { continue }
      $deleteList += $p
    }
  }
}

# 6.3 manifest cotizador.json
$manifestPath = Join-Path $ProjectRoot "config\cotizador.json"
$baseUrl = "https://media.githubusercontent.com/media/$RepoUser/$RepoName/main/Output/updates/$next/"

$manifestObj = [ordered]@{
  version   = $next
  type      = "files"
  base_url  = $baseUrl
  files     = $filesList
  delete    = $deleteList
  mandatory = [bool]$Mandatory
  notes     = "Release $next"

  # fallback installer (para versiones viejas / si FILES falla)
  url       = $exeUrl
  sha256    = $setupSha
}
Set-ContentUtf8NoBOM -Path $manifestPath -Text ($manifestObj | ConvertTo-Json -Depth 10)

Write-Host "Manifest actualizado: $manifestPath"
Write-Host "  files  = $($filesList.Count)"
Write-Host "  delete = $($deleteList.Count)"

# 7) git lfs + commit
try { git lfs env | Out-Null } catch { git lfs install | Out-Null }

$attrPath = Join-Path $ProjectRoot ".gitattributes"
if (!(Test-Path $attrPath)) { New-Item -ItemType File -Path $attrPath | Out-Null }
$attrTxt = Get-Content -Raw $attrPath

if ($attrTxt -notmatch 'Output/\*\.exe\s+filter=lfs') {
  'Output/*.exe filter=lfs diff=lfs merge=lfs -text' | Add-Content $attrPath
}
if ($attrTxt -notmatch 'Output/updates/\*\*\s+filter=lfs') {
  'Output/updates/** filter=lfs diff=lfs merge=lfs -text' | Add-Content $attrPath
}
git -C $ProjectRoot add .gitattributes

$filesToAdd = @(
  "src/version.py",
  "src/app.py",
  "src/updater.py",
  "tools/apply_update.py",
  $IssPath,
  "changelog.txt",                 # ✅ se commitea con versión/fecha actualizadas
  ("Output\" + $setupName),
  ("Output/updates/" + $next),
  "config/cotizador.json"
)

git -C $ProjectRoot add -- $filesToAdd
git -C $ProjectRoot commit -m ("Release {0}: silent files-updater" -f $next)

Write-Host ("Listo. Haz: git -C `"{0}`" push origin main" -f $ProjectRoot)
