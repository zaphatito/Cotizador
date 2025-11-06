param(
  [ValidateSet("major","minor","patch")]
  [string]$Bump = "patch",
  [string]$RepoUser = "zaphatito",
  [string]$RepoName = "Cotizador",
  [string]$ProjectRoot = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador",
  [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  [string]$SpecPath = "Utilidades\sistema_cotizaciones.spec",
  [string]$IssPath  = "Output\script inno.iss",
  # <-- NUEVO: ruta del venv. Si no la pasas, intenta .venv o venv dentro del proyecto.
  [string]$VenvPath = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\.venv"
)

$ErrorActionPreference = "Stop"

function Get-VersionTuple($v) {
  $parts = $v -split '\.'; while($parts.Count -lt 3){ $parts += '0' }
  return ,([int]$parts[0]),([int]$parts[1]),([int]$parts[2])
}
function Bump-Version($v, $kind) {
  $M,$m,$p = Get-VersionTuple $v
  switch ($kind) {
    "major" { $M++; $m=0; $p=0 }
    "minor" { $m++; $p=0 }
    default { $p++ }
  }
  return "$M.$m.$p"
}

# --- Detectar venv ---
if (-not $VenvPath -or -not (Test-Path $VenvPath)) {
  $cand1 = Join-Path $ProjectRoot ".venv"
  $cand2 = Join-Path $ProjectRoot "venv"
  if (Test-Path $cand1) { $VenvPath = $cand1 }
  elseif (Test-Path $cand2) { $VenvPath = $cand2 }
}
$py = $null
if ($VenvPath -and (Test-Path (Join-Path $VenvPath "Scripts\python.exe"))) {
  $py = Join-Path $VenvPath "Scripts\python.exe"
} else {
  throw "No se encontró el venv. Pasa -VenvPath 'C:\ruta\al\venv' o crea .venv/venv en el proyecto."
}

Write-Host "Usando Python del venv: $py"

# 0) Asegurar deps en venv
& $py -m pip install --upgrade pip
# OJO: tu archivo se llama 'requirements.txt' (sin s)
$reqFile = Join-Path $ProjectRoot "Utilidades\requirements.txt"
if (Test-Path $reqFile) {
  & $py -m pip install -r $reqFile
} else {
  Write-Warning "No se halló Utilidades\requirements.txt; se asume venv ya tiene dependencias."
}

# 1) Leer y bump version
$verFile = Join-Path $ProjectRoot "src\version.py"
$verTxt  = Get-Content -Raw $verFile
if ($verTxt -notmatch '__version__\s*=\s*"([^"]+)"') { throw "No se encontró __version__ en src\version.py" }
$cur = $Matches[1]
$next = Bump-Version $cur $Bump
Write-Host "Versión actual: $cur  ->  Nueva versión: $next"

# 2) Escribir nueva versión
$verTxt = $verTxt -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$next`""
Set-Content -Path $verFile -Value $verTxt -Encoding UTF8

# 3) Actualizar .iss (MyAppVersion y UpdateManifestUrl)
$issFull = Join-Path $ProjectRoot $IssPath
$issTxt  = Get-Content -Raw $issFull
$issTxt  = $issTxt -replace '#define\s+MyAppVersion\s+"[^"]+"', "#define MyAppVersion `"$next`""
$manifestUrl = "https://raw.githubusercontent.com/$RepoUser/$RepoName/main/config/cotizador.json"
if ($issTxt -match '#define\s+UpdateManifestUrl\s+"[^"]+"') {
  $issTxt = $issTxt -replace '#define\s+UpdateManifestUrl\s+"[^"]+"', "#define UpdateManifestUrl `"$manifestUrl`""
}
Set-Content -Path $issFull -Value $issTxt -Encoding UTF8

# 4) Compilar app (PyInstaller) usando SIEMPRE el venv
# Requiere que el venv esté activo o que exista .venv en la raíz

# 4.1 Determinar intérprete Python del venv
$py = $null
if ($env:VIRTUAL_ENV -and (Test-Path (Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"))) {
  $py = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
} elseif (Test-Path (Join-Path $ProjectRoot ".venv\Scripts\python.exe")) {
  $py = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
} else {
  throw "No encuentro el Python del venv. Activa el venv o crea .venv en la raíz."
}

# 4.2 Limpieza robusta
$distRoot = Join-Path $ProjectRoot "dist"
$distDir  = Join-Path $distRoot "SistemaCotizaciones"
Get-Process SistemaCotizaciones -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
function Remove-Dir-Robust([string]$path) {
  if (!(Test-Path $path)) { return }
  try { attrib -r -s -h "$path" /s /d 2>$null } catch {}
  for ($i=0; $i -lt 5; $i++) {
    try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop; return } catch { Start-Sleep -Milliseconds 500 }
  }
  try {
    $tmp = "$path._old_" + (Get-Random)
    Rename-Item -LiteralPath $path -NewName (Split-Path $tmp -Leaf) -ErrorAction SilentlyContinue
    Start-Job { param($p) Start-Sleep 2; Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue } -ArgumentList $tmp | Out-Null
  } catch {}
}
Remove-Dir-Robust $distDir

# 4.3 Ejecutar PyInstaller DESDE el venv
Push-Location $ProjectRoot
& $py -m PyInstaller -y $SpecPath
Pop-Location


# 5) Compilar instalador (ISCC)
& "$ISCC" "$issFull" | Write-Host

# 6) SHA256 + actualizar manifest
$setupName = "Setup_SistemaCotizaciones_{0}.exe" -f $next
$setupLocal = Join-Path $ProjectRoot "output\$setupName"
if (!(Test-Path $setupLocal)) { throw "No se encontró $setupLocal" }
$sha = (Get-FileHash $setupLocal -Algorithm SHA256).Hash

$manifestPath = Join-Path $ProjectRoot "config\cotizador.json"
$rawExeUrl = "https://raw.githubusercontent.com/$RepoUser/$RepoName/main/output/$setupName"

$manifestObj = [ordered]@{
  version   = $next
  url       = $rawExeUrl
  sha256    = $sha
  mandatory = $true
  notes     = "Release $next"
}
($manifestObj | ConvertTo-Json -Depth 3) | Set-Content -Path $manifestPath -Encoding UTF8

Write-Host "Actualizado manifest: $manifestPath"
Write-Host "  version = $next"
Write-Host "  url     = $rawExeUrl"
Write-Host "  sha256  = $sha"

# 7) Git (LFS + commit)
$attrPath = Join-Path $ProjectRoot ".gitattributes"
if (!(Test-Path $attrPath) -or -not ((Get-Content $attrPath) -match '^output/\*\.exe')) {
  'output/*.exe filter=lfs diff=lfs merge=lfs -text' | Add-Content $attrPath
  git -C $ProjectRoot add .gitattributes
}

$files = @(
  "src/version.py",
  $IssPath,
  ("output\" + $setupName),
  "config\cotizador.json"
)

git -C $ProjectRoot add -- $files
git -C $ProjectRoot commit -m ("Release {0}: bump version, build installer and manifest" -f $next)
Write-Host ("Listo. Revisa y haz: git -C `"{0}`" push origin main" -f $ProjectRoot)
