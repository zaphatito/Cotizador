param(
  [ValidateSet("major","minor","patch")]
  [string]$Bump = "patch",
  [string]$RepoUser = "zaphatito",
  [string]$RepoName = "Cotizador",
  [string]$ProjectRoot = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador",
  [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  [string]$SpecPath = "Utilidades\sistema_cotizaciones.spec",   # ajusta si tu .spec tiene otro nombre
  [string]$IssPath  = "Output\script inno.iss"     # ajusta a la ruta real de tu .iss
)

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

# 1) Leer versión actual de src/version.py
$verFile = Join-Path $ProjectRoot "src\version.py"
$verTxt  = Get-Content -Raw $verFile
if ($verTxt -notmatch '__version__\s*=\s*"([^"]+)"') { throw "No se encontró __version__ en src\version.py" }
$cur = $Matches[1]
$next = Bump-Version $cur $Bump
Write-Host "Versión actual: $cur  ->  Nueva versión: $next"

# 2) Escribir nueva versión en src/version.py
$verTxt = $verTxt -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$next`""
Set-Content -Path $verFile -Value $verTxt -Encoding UTF8

# 3) Actualizar MyAppVersion y UpdateManifestUrl en el .iss
$issFull = Join-Path $ProjectRoot $IssPath
$issTxt  = Get-Content -Raw $issFull
$issTxt  = $issTxt -replace '#define\s+MyAppVersion\s+"[^"]+"', "#define MyAppVersion `"$next`""
$manifestUrl = "https://raw.githubusercontent.com/$RepoUser/$RepoName/main/config/cotizador.json"
if ($issTxt -match '#define\s+UpdateManifestUrl\s+"[^"]+"') {
  $issTxt = $issTxt -replace '#define\s+UpdateManifestUrl\s+"[^"]+"', "#define UpdateManifestUrl `"$manifestUrl`""
}
Set-Content -Path $issFull -Value $issTxt -Encoding UTF8

# 4) Compilar app (PyInstaller)
# --- Limpieza robusta antes de PyInstaller ---
$distRoot = Join-Path $ProjectRoot "dist"
$distDir  = Join-Path $distRoot "SistemaCotizaciones"

# Cierra el EXE si está vivo
Get-Process SistemaCotizaciones -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# Intenta quitar atributos y borrar con reintentos
function Remove-Dir-Robust([string]$path) {
  if (!(Test-Path $path)) { return }
  try {
    attrib -r -s -h "$path" /s /d 2>$null
  } catch {}
  for ($i=0; $i -lt 5; $i++) {
    try {
      Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop
      return
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  # último recurso: renombrar y borrar en background
  try {
    $tmp = "$path._old_" + (Get-Random)
    Rename-Item -LiteralPath $path -NewName (Split-Path $tmp -Leaf) -ErrorAction SilentlyContinue
    Start-Job { param($p) Start-Sleep 2; Remove-Item -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue } -ArgumentList $tmp | Out-Null
  } catch {}
}

Remove-Dir-Robust $distDir

Push-Location $ProjectRoot
pyinstaller -y $SpecPath
Pop-Location

# 5) Compilar instalador (ISCC)
& "$ISCC" "$issFull" | Write-Host

# 6) Calcular SHA256 del instalador generado y actualizar config/cotizador.json
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

