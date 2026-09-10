"""Dónde viven los archivos según cómo se esté ejecutando pSapo.

Corriendo desde el código fuente, todo cuelga del proyecto. Empaquetado con
PyInstaller hay dos lugares distintos y no se pueden confundir:

* Lo que viene **dentro** del ejecutable (la interfaz) se descomprime en una
  carpeta temporal que Windows borra al cerrar la app.
* Lo que el usuario **genera** (su base de recetas, el navegador de Playwright)
  tiene que sobrevivir a la app, y sobre todo a que el updater la reemplace por
  una versión nueva.
"""

import os
import sys
from pathlib import Path

CARPETA_APP = "pSapo"


def empaquetado():
    """True si esto es el .exe de PyInstaller y no el código fuente."""
    return getattr(sys, "frozen", False)


def recurso(relativo):
    """Un archivo que viaja dentro del ejecutable, como la interfaz web."""
    if empaquetado():
        return Path(sys._MEIPASS) / relativo
    return Path(__file__).resolve().parent.parent / relativo


def carpeta_de_datos():
    """Dónde guardar lo que tiene que sobrevivir a una actualización.

    En Windows va a `%LOCALAPPDATA%\\pSapo`. Guardar la base al lado del
    ejecutable sería perderla cada vez que el updater lo reemplaza, y dentro del
    ejecutable sería perderla al cerrar la app.
    """
    if not empaquetado():
        return Path(__file__).resolve().parent.parent

    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    raiz = Path(base) if base else Path.home() / ".local" / "share"
    carpeta = raiz / CARPETA_APP
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def carpeta_de_navegadores():
    """Dónde descarga Playwright su Chromium.

    Pesa cientos de megas, así que no puede ir dentro del ejecutable: se baja
    una sola vez y se reusa entre versiones.
    """
    carpeta = carpeta_de_datos() / "navegadores"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta
