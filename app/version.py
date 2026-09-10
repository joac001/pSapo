"""Versión de pSapo.

Subir este número en `main` es lo que publica una release: el workflow lee el
valor, y si todavía no existe una release con ese tag, buildea el ejecutable y
la crea. El updater compara este número con el de la última release.
"""

VERSION = "1.2.1"
