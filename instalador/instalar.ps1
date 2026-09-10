# Instalador de pSapo para Windows.
#
# Descargá este archivo, hacé clic derecho y elegí "Ejecutar con PowerShell".
# No necesita permisos de administrador: todo queda en tu usuario.
#
# Deja:
#   %LOCALAPPDATA%\pSapo\pSapo.exe        la app
#   %LOCALAPPDATA%\pSapo\psapo.db         tus recetas y precios
#   %LOCALAPPDATA%\pSapo\navegadores\     el navegador que busca los precios
# más un acceso directo en el menú inicio y una tarea que revisa si hay
# versión nueva cada vez que iniciás sesión.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo    = "joac001/pSapo"
$Carpeta = Join-Path $env:LOCALAPPDATA "pSapo"
$Tarea   = "pSapo - buscar actualizaciones"

function Paso($mensaje) { Write-Host "  $mensaje" -ForegroundColor Cyan }

try {
    Write-Host ""
    Write-Host "Instalando pSapo" -ForegroundColor Green
    Write-Host ""

    New-Item -ItemType Directory -Path $Carpeta -Force | Out-Null

    Paso "Buscando la última versión…"
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "pSapo-instalador" }
    $version = $release.tag_name -replace '^v', ''

    $asset = $release.assets | Where-Object { $_.name -eq "pSapo.exe" } | Select-Object -First 1
    if (-not $asset) { throw "La release $version no trae pSapo.exe" }

    if (Get-Process -Name "pSapo" -ErrorAction SilentlyContinue) {
        throw "pSapo está abierto. Cerralo y volvé a ejecutar este instalador."
    }

    Paso "Descargando pSapo $version…"
    $destino = Join-Path $Carpeta "pSapo.exe"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $destino `
        -Headers @{ "User-Agent" = "pSapo-instalador" } -UseBasicParsing
    Set-Content -Path (Join-Path $Carpeta "version.txt") -Value $version -Encoding UTF8

    Paso "Dejando el actualizador…"
    $assetScript = $release.assets | Where-Object { $_.name -eq "actualizador.ps1" } | Select-Object -First 1
    $actualizador = Join-Path $Carpeta "actualizador.ps1"
    Invoke-WebRequest -Uri $assetScript.browser_download_url -OutFile $actualizador `
        -Headers @{ "User-Agent" = "pSapo-instalador" } -UseBasicParsing

    Paso "Programando la revisión al iniciar sesión…"
    # -WindowStyle Hidden para que no aparezca una consola en cada arranque;
    # el actualizador deja lo que hizo en actualizador.log.
    $accion = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$actualizador`""
    $disparador = New-ScheduledTaskTrigger -AtLogOn
    # Un minuto de demora para no pelear con el resto del arranque de Windows.
    $disparador.Delay = "PT1M"
    $opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
    Register-ScheduledTask -TaskName $Tarea -Action $accion -Trigger $disparador `
        -Settings $opciones -Description "Revisa si hay una versión nueva de pSapo." `
        -Force | Out-Null

    Paso "Creando el acceso directo…"
    $shell = New-Object -ComObject WScript.Shell
    $menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\pSapo.lnk"
    foreach ($ruta in @($menu, (Join-Path ([Environment]::GetFolderPath("Desktop")) "pSapo.lnk"))) {
        $acceso = $shell.CreateShortcut($ruta)
        $acceso.TargetPath = $destino
        $acceso.WorkingDirectory = $Carpeta
        $acceso.Description = "Comparar precios de recetas"
        $acceso.Save()
    }

    Write-Host ""
    Write-Host "Listo. pSapo $version quedó instalado." -ForegroundColor Green
    Write-Host "Abrilo desde el menú inicio o desde el acceso directo del escritorio."
    Write-Host ""
    Write-Host "La primera vez que lo abras va a descargar el navegador que usa" -ForegroundColor Yellow
    Write-Host "para buscar precios. Tarda unos minutos y pasa una sola vez." -ForegroundColor Yellow
    Write-Host ""

    $abrir = Read-Host "¿Lo abro ahora? (s/n)"
    if ($abrir -eq "s") { Start-Process $destino }
}
catch {
    Write-Host ""
    Write-Host "No se pudo instalar: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Read-Host "Enter para cerrar"
    exit 1
}
