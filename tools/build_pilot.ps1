param(
    [Parameter(Mandatory=$true)][string]$Python,
    [string]$ISCC = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
    [switch]$InstallerOnly
)
$ErrorActionPreference = 'Stop'
$pilotRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $pilotRoot
try {
    foreach ($relative in @('dist/piloto-historico', 'build/piloto-historico', 'Output/piloto-historico')) {
        $resolvedTarget = [IO.Path]::GetFullPath((Join-Path $pilotRoot $relative))
        $allowedRoot = [IO.Path]::GetFullPath($pilotRoot).TrimEnd('\') + '\'
        if (-not $resolvedTarget.StartsWith($allowedRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Destino fuera del workspace piloto.'
        }
        if ((Test-Path -LiteralPath $resolvedTarget) -and (Get-Item -LiteralPath $resolvedTarget).LinkType) {
            throw 'El destino de compilación no puede ser un enlace.'
        }
    }
    if (-not $InstallerOnly) {
        & $Python -m PyInstaller --noconfirm --distpath 'dist/piloto-historico' --workpath 'build/piloto-historico' 'Utilidades/piloto_historico.spec'
        if ($LASTEXITCODE -ne 0) { throw 'Falló la compilación del piloto.' }
    }
    $bundle = Join-Path $pilotRoot 'dist/piloto-historico/CotizadorPiloto'
    $forbidden = Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object {
        $_.Name -match '\.(sqlite3|db)(-wal|-shm)?$' -or
        $_.Name -in @('cotizador.json', 'apply_update.exe', 'config.json')
    }
    if ($forbidden) { throw 'El bundle contiene datos o componentes de actualización no permitidos.' }
    & $ISCC "/DProjectRoot=$pilotRoot" 'tools/piloto_historico.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Falló el instalador piloto.' }
} finally { Pop-Location }
