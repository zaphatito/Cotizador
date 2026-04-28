param(
  [ValidateSet("major","mayor","minor","patch")]
  [string]$Bump = "patch",
  [string]$RepoUser = "zaphatito",
  [string]$RepoName = "CotizadorReleases",
  [string]$ReleaseTagPrefix = "v",
  [string]$ProjectRoot = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador",
  [string]$ISCC = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
  [string]$SpecPath = "Utilidades\sistema_cotizaciones.spec",
  [string]$IssPath  = "Output\script inno.iss",
  [string]$VenvPath = "C:\Users\Samuel\OneDrive\Escritorio\Cotizador\.venv",
  [string]$ReleaseManifestPath = "Output\cotizador.json",
  [switch]$Publish,
  [switch]$NoDraft,
  [switch]$Mandatory,
  [switch]$PruneOpenGLSW
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

if ($Publish -and [string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
  throw "Falta GH_TOKEN. Configuralo con permiso Contents: Read/Write sobre $RepoUser/$RepoName."
}

function Get-CurrentGitBranch() {
  $branch = (& git -C $ProjectRoot branch --show-current 2>$null)
  if ($LASTEXITCODE -ne 0) {
    throw "No se pudo validar la rama git actual."
  }

  $branch = ([string]$branch).Trim()
  if ([string]::IsNullOrWhiteSpace($branch)) {
    $branch = (& git -C $ProjectRoot rev-parse --abbrev-ref HEAD 2>$null)
    $branch = ([string]$branch).Trim()
  }

  return $branch
}

function Assert-PublishBranch() {
  if (-not $Publish) { return }

  $branch = Get-CurrentGitBranch
  if ($branch -ne "main") {
    throw "Estas en la rama '$branch'. Para publicar instaladores debes hacer merge a prod (main) y ejecutar el release desde main."
  }
}

Assert-PublishBranch

$publishAsDraft = -not [bool]$NoDraft

function Normalize-BumpKind([string]$kind) {
  if ($null -eq $kind) { $kind = "" }

  switch ($kind.Trim().ToLowerInvariant()) {
    "major" { return "major" }
    "mayor" { return "major" }
    "minor" { return "minor" }
    "patch" { return "patch" }
    default { throw "Bump invalido '$kind' (use major/minor/patch)" }
  }
}

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
  $kind = Normalize-BumpKind $kind

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

function Read-TextAuto([string]$Path) {
  $utf8 = New-Object System.Text.UTF8Encoding($false)
  $sr = New-Object System.IO.StreamReader($Path, $utf8, $true)
  try { return $sr.ReadToEnd() } finally { $sr.Dispose() }
}

function Set-ContentUtf8BOM([string]$Path, [string]$Text) {
  $enc = New-Object System.Text.UTF8Encoding($true)
  [System.IO.File]::WriteAllText($Path, $Text, $enc)
}

function Fix-MojibakeIfNeeded([string]$Text) {
  $pat = '(\u00C3|\u00C2|\u00E2)'   # Ã, Â, â

  if ($Text -notmatch $pat) { return $Text }

  try {
    $win1252 = [System.Text.Encoding]::GetEncoding(1252)
    $bytes   = $win1252.GetBytes($Text)
    $fixed   = [System.Text.Encoding]::UTF8.GetString($bytes)

    $before = ([regex]::Matches($Text,  $pat).Count)
    $after  = ([regex]::Matches($fixed, $pat).Count)

    if ($after -lt $before) { return $fixed }
  } catch {}

  return $Text
}

function Update-ChangelogHeader([string]$Path, [string]$Version) {
  $date = (Get-Date).ToString("yyyy-MM-dd", [System.Globalization.CultureInfo]::InvariantCulture)
  $verLine  = "Versi$([char]0x00F3)n: $Version"  # ó
  $dateLine = "Fecha: $date"

  if (!(Test-Path $Path)) {
    $tpl = @"
SISTEMA DE COTIZACIONES - REGISTRO DE CAMBIOS

$verLine
$dateLine
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

  $txt = Read-TextAuto -Path $Path
  $txt = Fix-MojibakeIfNeeded -Text $txt

  # Regex: soporta Version / Versión (con ó via \u00F3)
  $verPattern  = '(?mi)^\s*Versi(?:o|\u00F3)n\s*:\s*.*$|^\s*Version\s*:\s*.*$'
  $datePattern = '(?mi)^\s*Fecha\s*:\s*.*$'

  $txt2 = [regex]::Replace($txt,  $verPattern,  $verLine)
  $txt2 = [regex]::Replace($txt2, $datePattern, $dateLine)

  # Si no encontró líneas, las inserta debajo del título (primera línea)
  if ($txt2 -eq $txt) {
    if ($txt2 -notmatch '(?mi)^\s*Versi(?:o|\u00F3)n\s*:|(?mi)^\s*Version\s*:') {
      $txt2 = $txt2 -replace '(?m)^(.*\r?\n)', "`$1`r`n$verLine`r`n"
    }
    if ($txt2 -notmatch '(?mi)^\s*Fecha\s*:') {
      $txt2 = $txt2 -replace '(?m)^(.*\r?\n)', "`$1`r`n$dateLine`r`n"
    }
  }

  if ($txt2 -ne $txt) {
    Set-ContentUtf8BOM -Path $Path -Text $txt2
  }
}

function Get-ReleaseTag([string]$Version) {
  return "$ReleaseTagPrefix$Version"
}

function Get-GitHubHeaders() {
  if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
    throw "Falta GH_TOKEN. Configuralo con permiso Contents: Read/Write sobre $RepoUser/$RepoName."
  }

  return @{
    Authorization = "Bearer $($env:GH_TOKEN)"
    Accept = "application/vnd.github+json"
    "X-GitHub-Api-Version" = "2022-11-28"
    "User-Agent" = "Cotizador-Release-Script"
  }
}

function Get-HttpStatusCode($Err) {
  try { return [int]$Err.Exception.Response.StatusCode } catch { return 0 }
}

function Invoke-GitHubJson([string]$Method, [string]$Uri, $Body = $null) {
  $headers = Get-GitHubHeaders
  if ($null -eq $Body) {
    return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers
  }

  $json = $Body | ConvertTo-Json -Depth 20
  return Invoke-RestMethod -Method $Method -Uri $Uri -Headers $headers -Body $json -ContentType "application/json"
}

function Get-GitHubReleaseByTag([string]$Owner, [string]$Repo, [string]$Tag) {
  $tagEsc = [System.Uri]::EscapeDataString($Tag)
  $uri = "https://api.github.com/repos/$Owner/$Repo/releases/tags/$tagEsc"
  try {
    return Invoke-GitHubJson -Method "Get" -Uri $uri
  } catch {
    if ((Get-HttpStatusCode $_) -eq 404) { return $null }
    throw
  }
}

function Ensure-GitHubRelease([string]$Owner, [string]$Repo, [string]$Tag, [string]$Name, [bool]$IsDraft) {
  $release = Get-GitHubReleaseByTag -Owner $Owner -Repo $Repo -Tag $Tag
  if ($null -ne $release) { return $release }

  $body = @{
    tag_name = $Tag
    name = $Name
    draft = $IsDraft
    prerelease = $false
    generate_release_notes = $false
  }

  return Invoke-GitHubJson -Method "Post" -Uri "https://api.github.com/repos/$Owner/$Repo/releases" -Body $body
}

function Remove-GitHubAssetIfExists($Release, [string]$AssetName) {
  $assetsUrl = "https://api.github.com/repos/$RepoUser/$RepoName/releases/$($Release.id)/assets?per_page=100"
  $assets = Invoke-GitHubJson -Method "Get" -Uri $assetsUrl
  foreach ($asset in @($assets)) {
    if ([string]$asset.name -eq $AssetName) {
      Write-Host "Reemplazando asset existente: $AssetName"
      Invoke-GitHubJson -Method "Delete" -Uri "https://api.github.com/repos/$RepoUser/$RepoName/releases/assets/$($asset.id)" | Out-Null
    }
  }
}

function Upload-GitHubAsset($Release, [string]$Path) {
  if (!(Test-Path $Path)) { throw "No existe asset para subir: $Path" }

  $assetName = Split-Path $Path -Leaf
  Remove-GitHubAssetIfExists -Release $Release -AssetName $assetName

  $uploadBase = ([string]$Release.upload_url) -replace '\{\?name,label\}.*$', ''
  $nameEsc = [System.Uri]::EscapeDataString($assetName)
  $uploadUri = '{0}?name={1}' -f $uploadBase, $nameEsc

  Write-Host "Subiendo asset: $assetName"
  Invoke-RestMethod `
    -Method Post `
    -Uri $uploadUri `
    -Headers (Get-GitHubHeaders) `
    -ContentType "application/octet-stream" `
    -InFile $Path | Out-Null
}

function Publish-GitHubReleaseAssets([string]$Owner, [string]$Repo, [string]$Tag, [string]$Name, [bool]$IsDraft, [string[]]$Paths) {
  $release = Ensure-GitHubRelease -Owner $Owner -Repo $Repo -Tag $Tag -Name $Name -IsDraft $IsDraft

  foreach ($path in $Paths) {
    Upload-GitHubAsset -Release $release -Path $path
  }

  return $release
}

function New-UpdateArchive([string]$SourceRoot, [array]$FilesList, [string]$ArchivePath) {
  if (Test-Path $ArchivePath) { Remove-Item -LiteralPath $ArchivePath -Force }

  $stage = Join-Path $env:LOCALAPPDATA ("Cotizador\release_archive\" + [System.IO.Path]::GetFileNameWithoutExtension($ArchivePath))
  Remove-Dir-Robust $stage
  New-Item -ItemType Directory -Force -Path $stage | Out-Null

  foreach ($it in @($FilesList)) {
    $rel = [string]$it.path
    if ([string]::IsNullOrWhiteSpace($rel)) { continue }

    $src = Join-Path $SourceRoot ($rel.Replace("/", "\"))
    if (!(Test-Path $src)) { throw "No existe archivo para zip de update: $src" }

    $dst = Join-Path $stage ($rel.Replace("/", "\"))
    $dstDir = Split-Path $dst -Parent
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    Copy-Item -Force $src $dst
  }

  if ((Get-ChildItem -Path $stage -Recurse -File | Measure-Object).Count -eq 0) {
    Set-ContentUtf8NoBOM -Path (Join-Path $stage "__empty_update.txt") -Text "empty"
  }

  Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $ArchivePath -Force
  Remove-Dir-Robust $stage

  return (Get-FileHash $ArchivePath -Algorithm SHA256).Hash.ToUpper()
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
$normalizedBump = Normalize-BumpKind $Bump
$next = Bump-Version $cur $normalizedBump
Write-Host "Versión actual: $cur  ->  Nueva versión: $next"
$curV  = Parse-Semver $cur
$nextV = Parse-Semver $next

switch ($normalizedBump) {
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

Write-Host "Bump='$Bump' -> '$normalizedBump' OK. ($cur -> $next)"

$verTxt = $verTxt -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$next`""
Set-Content -Path $verFile -Value $verTxt -Encoding UTF8

$changelogPath = Join-Path $ProjectRoot "changelog.txt"
Update-ChangelogHeader -Path $changelogPath -Version $next
Write-Host "OK: changelog actualizado (Versión/Fecha) -> $changelogPath"

# 2) patch .iss
$issFull = Join-Path $ProjectRoot $IssPath
$issTxt  = Get-Content -Raw $issFull
$issTxt  = $issTxt -replace '#define\s+MyAppVersion\s+"[^"]+"', "#define MyAppVersion `"$next`""
$manifestUrl = "https://github.com/$RepoUser/$RepoName/releases/latest/download/cotizador.json"
if ($issTxt -match '#define\s+UpdateManifestUrl\s+"[^"]+"') {
  $issTxt = $issTxt -replace '#define\s+UpdateManifestUrl\s+"[^"]+"', "#define UpdateManifestUrl `"$manifestUrl`""
}
Set-Content -Path $issFull -Value $issTxt -Encoding UTF8

$appConfigPath = Join-Path $ProjectRoot "config\config.json"
if (Test-Path $appConfigPath) {
  $appConfigTxt = Get-Content -Raw $appConfigPath
  $appConfigTxt = $appConfigTxt -replace '"update_manifest_url"\s*:\s*"[^"]+"', "`"update_manifest_url`": `"$manifestUrl`""
  Set-ContentUtf8NoBOM -Path $appConfigPath -Text $appConfigTxt
}

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

$changelogPath = Join-Path $ProjectRoot "changelog.txt"
if (Test-Path $changelogPath) {
  Copy-Item -Force $changelogPath (Join-Path $distDir "changelog.txt")
  Write-Host "OK: changelog.txt -> dist\SistemaCotizaciones\changelog.txt"
} else {
  Write-Host "WARN: no existe changelog.txt en la raíz: $changelogPath"
}

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

$releaseTag = Get-ReleaseTag $next
$releaseBaseUrl = "https://github.com/$RepoUser/$RepoName/releases/download/$releaseTag"
$exeUrl = "$releaseBaseUrl/$setupName"

# 6) build updates/<version> (FILES)
$updatesRoot = Join-Path $ProjectRoot "Output\updates"
$updateVerDir = Join-Path $updatesRoot $next
$prevUpdateDir = Join-Path $updatesRoot $prev
$hasPrevUpdate = Test-Path $prevUpdateDir
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

# 6.1 snapshot new/prev + sha (exclude DB)
$newEntries = @()
Get-ChildItem -Path $updateVerDir -Recurse -File | Sort-Object FullName | ForEach-Object {
  $full = $_.FullName
  $rel  = $full.Substring($updateVerDir.Length + 1).Replace("\","/")
  $relLower = $rel.ToLower()
  if ($relLower -eq "sqlmodels/app.sqlite3") { return }
  if ($relLower -eq "sqlmodels/app.sqlite3-wal") { return }
  if ($relLower -eq "sqlmodels/app.sqlite3-shm") { return }

  $newEntries += [pscustomobject]@{
    rel      = $rel
    relLower = $relLower
    sha      = (Get-FileHash $full -Algorithm SHA256).Hash.ToLower()
  }
}

$prevEntries = @()
if ($hasPrevUpdate) {
  Get-ChildItem -Path $prevUpdateDir -Recurse -File | Sort-Object FullName | ForEach-Object {
    $full = $_.FullName
    $rel  = $full.Substring($prevUpdateDir.Length + 1).Replace("\","/")
    $relLower = $rel.ToLower()
    if ($relLower -eq "sqlmodels/app.sqlite3") { return }
    if ($relLower -eq "sqlmodels/app.sqlite3-wal") { return }
    if ($relLower -eq "sqlmodels/app.sqlite3-shm") { return }

    $prevEntries += [pscustomobject]@{
      rel      = $relLower
      relLower = $relLower
      sha      = (Get-FileHash $full -Algorithm SHA256).Hash.ToLower()
    }
  }
}

$prevShaByRel = @{}
$newByRel = @{}
foreach ($it in $prevEntries) { $prevShaByRel[$it.relLower] = $it.sha }
foreach ($it in $newEntries) { $newByRel[$it.relLower] = $true }

# 6.2 files[] incremental (new + changed)
$filesList = @()
foreach ($it in $newEntries) {
  $prevSha = $null
  if ($prevShaByRel.ContainsKey($it.relLower)) { $prevSha = [string]$prevShaByRel[$it.relLower] }
  if ($prevSha -eq $it.sha) { continue }
  $filesList += [ordered]@{ path = $it.rel; sha256 = $it.sha }
}

# 6.3 delete[] (rutas que ya no existen)
$deleteList = @()
foreach ($it in $prevEntries) {
  if (-not $newByRel.ContainsKey($it.relLower)) {
    $deleteList += $it.relLower
  }
}
$deleteList = @($deleteList | Sort-Object -Unique)

$isMajorRelease = $normalizedBump -eq "major"
$isDeltaManifest = $hasPrevUpdate -and -not $isMajorRelease

# 6.4 manifest cotizador.json para GitHub Releases.
# No escribir config\cotizador.json: ese archivo queda como puente legacy 2.0.10
# para clientes viejos que aun consultan raw.githubusercontent.com.
$manifestPath = Join-Path $ProjectRoot $ReleaseManifestPath
$manifestDir = Split-Path $manifestPath -Parent
if ($manifestDir -and !(Test-Path $manifestDir)) {
  New-Item -ItemType Directory -Force -Path $manifestDir | Out-Null
}
$archiveName = "SistemaCotizaciones_Update_{0}.zip" -f $next
$archiveLocal = Join-Path $ProjectRoot "Output\$archiveName"
$archiveSha = ""
$archiveUrl = "$releaseBaseUrl/$archiveName"
$manifestType = if ($isMajorRelease) { "installer" } else { "archive" }

if ($manifestType -eq "archive") {
  $archiveSha = New-UpdateArchive -SourceRoot $updateVerDir -FilesList $filesList -ArchivePath $archiveLocal
  Write-Host "OK: update archive -> $archiveLocal"
}

$manifestObj = [ordered]@{}
$manifestObj["version"] = $next
$manifestObj["type"] = $manifestType
if ($manifestType -eq "archive") {
  $manifestObj["archive_url"] = $archiveUrl
  $manifestObj["archive_sha256"] = $archiveSha
  if ($isDeltaManifest) { $manifestObj["from_version"] = $prev }
  $manifestObj["files"] = $filesList
  $manifestObj["delete"] = $deleteList
}
$manifestObj["mandatory"] = [bool]$Mandatory
$manifestObj["notes"] = "Release $next"
$manifestObj["url"] = $exeUrl
$manifestObj["sha256"] = $setupSha
Set-ContentUtf8NoBOM -Path $manifestPath -Text ($manifestObj | ConvertTo-Json -Depth 10)

Write-Host "Manifest de release actualizado: $manifestPath"
Write-Host "Manifest legacy conservado: config\cotizador.json"
Write-Host "  type   = $manifestType"
if ($manifestType -eq "archive") {
  Write-Host "  files  = $($filesList.Count) (cambiados de $($newEntries.Count))"
  Write-Host "  delete = $($deleteList.Count)"
  Write-Host "  zip    = $archiveName"
  if ($isDeltaManifest) {
    Write-Host "  from_version = $prev"
  } else {
    Write-Host "  from_version = (sin base, se publica paquete completo)"
  }
} else {
  Write-Host "  compat  = instalador completo (release mayor)"
}

# 7) commit source metadata only. Release binaries go to GitHub Releases.
# El manifest de release se genera en Output\cotizador.json y se sube como asset.
# No se commitea config\cotizador.json para no mover el puente legacy de 2.0.10.

$filesToAdd = @(
  "src/version.py",
  "src/app.py",
  "src/updater.py",
  "tools/apply_update.py",
  $IssPath,
  "changelog.txt",                 # ✅ se commitea con versión/fecha actualizadas
  "config/config.json"
)

git -C $ProjectRoot add -- $filesToAdd
$commitMode = if ($manifestType -eq "installer") { "full-installer-updater" } else { "silent files-updater" }
git -C $ProjectRoot commit -m ("Release {0}: {1}" -f $next, $commitMode)

# 8) publish GitHub Release assets when requested.
if ($Publish) {
  $assetPaths = @($manifestPath, $setupLocal)
  if ($manifestType -eq "archive") { $assetPaths += $archiveLocal }

  $releaseName = "Sistema de Cotizaciones $next"
  Publish-GitHubReleaseAssets `
    -Owner $RepoUser `
    -Repo $RepoName `
    -Tag $releaseTag `
    -Name $releaseName `
    -IsDraft $publishAsDraft `
    -Paths $assetPaths | Out-Null

  if ($publishAsDraft) {
    Write-Host "GitHub recibio un draft release en $RepoUser/$RepoName. Publicalo cuando lo valides."
  } else {
    Write-Host "GitHub release publicado en $RepoUser/$RepoName."
  }
}

Write-Host ("Listo. Haz: git -C `"{0}`" push origin main" -f $ProjectRoot)
