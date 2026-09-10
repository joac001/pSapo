"""Punto de entrada de pSapo como aplicación de escritorio.

Levanta el server local y abre la app en el navegador que el usuario ya usa.
Es también el entry point que empaqueta PyInstaller.
"""

import socket
import sys
import threading
import time
import webbrowser


def puerto_libre(preferido=8000):
    """El puerto de siempre si está libre; si no, cualquiera que el sistema dé.

    Sin esto, tener otra cosa escuchando en el 8000 hace que la app no arranque
    y el usuario no tiene forma de saber por qué.
    """
    for puerto in (preferido, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", puerto))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferido


def esperar_a_que_responda(puerto, segundos=30):
    limite = time.time() + segundos
    while time.time() < limite:
        with socket.socket() as s:
            s.settimeout(0.4)
            if s.connect_ex(("127.0.0.1", puerto)) == 0:
                return True
        time.sleep(0.2)
    return False


def main():
    from app import navegador
    from app.rutas import carpeta_de_datos
    from app.version import VERSION

    print(f"pSapo {VERSION}")
    print(f"Tus recetas y precios se guardan en: {carpeta_de_datos()}")
    print()

    # Antes de importar nada que abra un navegador, para que Playwright use la
    # carpeta que sobrevive a las actualizaciones.
    navegador.preparar_entorno()
    navegador.instalar()

    import uvicorn

    from app.main import app

    puerto = puerto_libre()
    url = f"http://127.0.0.1:{puerto}"

    servidor = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=puerto, log_level="warning"))
    threading.Thread(target=servidor.run, daemon=True).start()

    if esperar_a_que_responda(puerto):
        print(f"pSapo está andando en {url}")
        print("Cerrá esta ventana para apagarlo.")
        webbrowser.open(url)
    else:
        print("El server no llegó a levantar. Probá cerrar y volver a abrir pSapo.")
        return 1

    try:
        while not servidor.should_exit:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
