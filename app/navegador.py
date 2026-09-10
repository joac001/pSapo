"""Instalación del Chromium que usa el scraper.

Carrefour y Disco rechazan requests HTTP directos, así que hacen falta un
navegador real. Pesa cientos de megas: no puede viajar dentro del ejecutable,
se baja la primera vez y se reusa entre actualizaciones.
"""

import os
import subprocess
import sys
from pathlib import Path

from .rutas import carpeta_de_navegadores, empaquetado


def preparar_entorno():
    """Apunta Playwright a la carpeta de datos del usuario, solo si hace falta.

    Empaquetado no hay caché del sistema que reusar, así que se usa la carpeta
    de datos: el updater reemplaza el ejecutable y el navegador sigue ahí.
    Desde el código fuente se deja la caché de siempre, para no bajar 600 MB de
    nuevo en cada checkout.
    """
    if empaquetado():
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(carpeta_de_navegadores())


def _donde_busca_playwright():
    ruta = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if ruta:
        return Path(ruta)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def esta_instalado():
    return any(_donde_busca_playwright().glob("chromium-*"))


def instalar(al_avisar=print):
    """Baja Chromium si falta. Devuelve True si quedó listo para usarse."""
    preparar_entorno()
    if esta_instalado():
        return True

    al_avisar("Preparando el navegador que busca los precios.")
    al_avisar("Es una descarga grande y pasa una sola vez. Puede tardar unos minutos…")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env

        comando = [*compute_driver_executable(), "install", "chromium"]
        resultado = subprocess.run(comando, env={**get_driver_env(), **os.environ})
    except Exception as e:
        al_avisar(f"No se pudo preparar el navegador: {e}")
        return False

    if resultado.returncode != 0 or not esta_instalado():
        al_avisar("La descarga del navegador falló. pSapo abre igual, pero solo")
        al_avisar("va a poder buscar precios en Coto hasta que se resuelva.")
        return False

    al_avisar("Navegador listo.")
    return True


if __name__ == "__main__":
    sys.exit(0 if instalar() else 1)
