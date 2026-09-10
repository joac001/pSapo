# Receta de PyInstaller para el ejecutable de Windows.
#
# Sale un único .exe sin dependencias: el cliente no necesita Python ni nada
# instalado. Lo único que no viaja adentro es el Chromium de Playwright, que
# pesa cientos de megas y se baja solo la primera vez que se abre la app.

from PyInstaller.utils.hooks import collect_all, collect_submodules

# El paquete de Playwright trae adentro su driver de Node, que no es un import
# de Python y PyInstaller no puede deducir.
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

# uvicorn resuelve sus loops y protocolos por nombre en tiempo de ejecución.
uvicorn_hidden = collect_submodules("uvicorn")

a = Analysis(
    ["psapo.py"],
    pathex=[],
    binaries=playwright_binaries,
    # La interfaz se sirve desde adentro del ejecutable; `app.rutas.recurso`
    # la resuelve tanto acá como corriendo desde el código.
    datas=[("app/static", "app/static"), *playwright_datas],
    hiddenimports=[*playwright_hidden, *uvicorn_hidden],
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc_data"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="pSapo",
    debug=False,
    strip=False,
    upx=False,
    # Con consola a propósito: la primera vez baja el navegador y tarda varios
    # minutos. Sin una ventana que lo diga, parece que la app no arrancó.
    console=True,
    disable_windowed_traceback=False,
    icon=None,
)
