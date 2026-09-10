# Actualizador de pSapo.
#
# Corre solo al iniciar sesión en Windows (lo programa instalar.ps1). Mira si
# hay una versión nueva publicada en GitHub y, si la hay, reemplaza el
# ejecutable. No necesita nada instalado: usa el PowerShell que ya trae Windows.
#
# Lo que NO hace es tocar la base: las migraciones las corre la propia app al
# abrirse, porque son idempotentes y viven junto al esquema que las necesita.
# Lo que sí hace es guardar una copia de la base antes de actualizar, porque una
# migración no se puede deshacer.

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo      = "joac001/pSapo"
$Carpeta   = Join-Path $env:LOCALAPPDATA "pSapo"
$Ejecutable = Join-Path $Carpeta "pSapo.exe"
$ArchivoVersion = Join-Path $Carpeta "version.txt"
$Registro  = Join-Path $Carpeta "actualizador.log"

function Anotar($mensaje) {
    $linea = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $mensaje
    Write-Host $linea
    Add-Content -Path $Registro -Value $linea -Encoding UTF8
}

function VersionInstalada {
    if (Test-Path $ArchivoVersion) {
        $texto = (Get-Content $ArchivoVersion -Raw).Trim()
        if ($texto) { return $texto }
    }
    return "0.0.0"
}

try {
    if (-not (Test-Path $Carpeta)) {
        Anotar "pSapo no está instalado en $Carpeta. No hay nada que actualizar."
        exit 0
    }

    $instalada = VersionInstalada
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
        -Headers @{ "User-Agent" = "pSapo-actualizador" }
    $publicada = $release.tag_name -replace '^v', ''

    if ([version]$publicada -le [version]$instalada) {
        Anotar "Ya estás en la última versión ($instalada)."
        exit 0
    }

    Anotar "Hay una versión nueva: $instalada -> $publicada."

    # En Windows no se puede reemplazar un ejecutable que está corriendo. Se
    # deja para el próximo inicio en vez de cerrarle la app al usuario.
    if (Get-Process -Name "pSapo" -ErrorAction SilentlyContinue) {
        Anotar "pSapo está abierto. Se actualiza la próxima vez que inicies la computadora."
        exit 0
    }

    $asset = $release.assets | Where-Object { $_.name -eq "pSapo.exe" } | Select-Object -First 1
    if (-not $asset) { throw "La release $publicada no trae pSapo.exe" }

    # La base sobrevive a la actualización, pero la versión nueva puede migrarla
    # y eso no tiene vuelta atrás. Una copia por versión alcanza para recuperarla.
    $base = Join-Path $Carpeta "psapo.db"
    if (Test-Path $base) {
        $copia = Join-Path $Carpeta "psapo.db.antes-de-$publicada"
        Copy-Item $base $copia -Force
        Anotar "Copia de tus datos en $copia"
    }

    $temporal = Join-Path $Carpeta "pSapo.exe.nuevo"
    Anotar "Descargando…"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $temporal `
        -Headers @{ "User-Agent" = "pSapo-actualizador" } -UseBasicParsing

    if ((Get-Item $temporal).Length -lt 1MB) { throw "La descarga vino incompleta" }

    if (Test-Path $Ejecutable) { Remove-Item $Ejecutable -Force }
    Move-Item $temporal $Ejecutable -Force
    Set-Content -Path $ArchivoVersion -Value $publicada -Encoding UTF8

    # También el actualizador, por si cambió cómo se actualiza.
    $assetScript = $release.assets | Where-Object { $_.name -eq "actualizador.ps1" } | Select-Object -First 1
    if ($assetScript) {
        Invoke-WebRequest -Uri $assetScript.browser_download_url `
            -OutFile (Join-Path $Carpeta "actualizador.ps1") `
            -Headers @{ "User-Agent" = "pSapo-actualizador" } -UseBasicParsing
    }

    Anotar "Listo. pSapo quedó en la versión $publicada."
}
catch {
    Anotar "No se pudo actualizar: $($_.Exception.Message)"
    # Sin código de error: que falle la actualización no puede romper el inicio
    # de sesión, y la app vieja sigue andando.
    exit 0
}
