"""Endpoints de la API, sobre una base temporal.

Se llaman las funciones directamente en vez de levantar un cliente HTTP: no hay
`httpx` en el venv y lo que interesa validar es la lógica —qué se rechaza, con
qué código y qué forma tiene la respuesta—, no el ruteo de FastAPI.

`_lanzar_scrape` se reemplaza en cada test: crear un ingrediente o corregir sus
términos dispara una búsqueda real contra los tres supermercados, y un test no
puede depender de la red.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import HTTPException  # noqa: E402

from app import db, main  # noqa: E402

_scrapes = []


def base_de_prueba():
    db.DB_PATH = Path(tempfile.mkdtemp()) / "prueba.db"
    db.init_db()
    _scrapes.clear()
    main._lanzar_scrape = lambda ingredientes, alcance: (
        _scrapes.append((ingredientes, alcance)) or {"iniciado": True})
    main._scrape_estado.update(corriendo=False, hecho=0, total=0, error=None, alcance=None)
    return db.get_conn()


def rechaza(codigo, funcion, *args, **kwargs):
    """Corre la función y devuelve el detalle del HTTPException esperado."""
    try:
        funcion(*args, **kwargs)
    except HTTPException as e:
        assert e.status_code == codigo, f"esperaba {codigo}, vino {e.status_code}: {e.detail}"
        return e.detail
    raise AssertionError(f"esperaba HTTPException {codigo} y no hubo error")


def producto(conn, ingrediente_id, super_nombre, nombre, precio=1000, tamano=1000,
             unidad="g", orden=0, categoria=None, fecha="2026-01-01T00:00:00"):
    cur = conn.execute("""
        INSERT INTO productos (ingrediente_id, super, id_externo, nombre_producto,
                               tamano, unidad, precio, orden, categoria, fecha_scrape)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ingrediente_id, super_nombre, f"{super_nombre}-{nombre}", nombre,
          tamano, unidad, precio, orden, categoria, fecha))
    return cur.lastrowid


def crear_ingrediente(nombre="Harina", unidad="g", terminos=("harina 000",)):
    return main.crear_ingrediente(main.NuevoIngrediente(
        nombre=nombre, unidad_base=unidad, terminos=list(terminos)))["id"]


# --- ingredientes ---------------------------------------------------------

def test_crear_ingrediente_guarda_terminos_y_dispara_la_busqueda():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    assert _scrapes == [([ingrediente_id], "ingrediente")], "crear tiene que buscar precios"
    assert main.ver_ingrediente(ingrediente_id)["terminos"] == ["harina 000"]


def test_sin_terminos_se_usa_el_nombre():
    base_de_prueba()
    ingrediente_id = crear_ingrediente(terminos=())
    assert main.ver_ingrediente(ingrediente_id)["terminos"] == ["harina"]


def test_no_se_puede_repetir_el_nombre():
    base_de_prueba()
    crear_ingrediente()
    assert "Ya existe" in rechaza(409, crear_ingrediente)


def test_ver_un_ingrediente_que_no_existe():
    base_de_prueba()
    rechaza(404, main.ver_ingrediente, 999)


def test_no_se_borra_un_ingrediente_que_usa_una_receta():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    receta_id = main.crear_receta(main.DatosReceta(nombre="Torta"))["id"]
    main.agregar_renglon(receta_id, main.Renglon(
        ingrediente_id=ingrediente_id, cantidad=100, unidad="g"))
    conn.close()
    assert "1 receta" in rechaza(409, main.borrar_ingrediente, ingrediente_id)


def test_editar_terminos_vuelve_a_buscar():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    _scrapes.clear()
    r = main.editar_terminos(ingrediente_id, main.Terminos(terminos=["harina 0000", " HARINA 000 "]))
    assert r["terminos"] == ["harina 000", "harina 0000"], "se normalizan y ordenan"
    assert _scrapes == [([ingrediente_id], "ingrediente")]


def test_no_se_puede_dejar_un_ingrediente_sin_terminos():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    rechaza(400, main.editar_terminos, ingrediente_id, main.Terminos(terminos=["  "]))


# --- elección de producto -------------------------------------------------

def test_no_se_puede_elegir_un_producto_de_otro_supermercado():
    """Guardarlo reintroduce el bug de un override que se ignora al costear."""
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    de_coto = producto(conn, ingrediente_id, "coto", "Harina Coto")
    conn.commit()
    conn.close()
    detalle = rechaza(400, main.elegir_para_ingrediente, ingrediente_id,
                      main.EleccionSuper(**{"super": "carrefour", "producto_id": de_coto}))
    assert "de coto, no de carrefour" in detalle


def test_no_se_puede_elegir_un_producto_inexistente():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    rechaza(404, main.elegir_para_ingrediente, ingrediente_id,
            main.EleccionSuper(**{"super": "carrefour", "producto_id": 999}))


def test_no_se_puede_elegir_el_producto_de_otro_ingrediente():
    conn = base_de_prueba()
    harina = crear_ingrediente()
    azucar = crear_ingrediente("Azucar")
    ajeno = producto(conn, azucar, "carrefour", "Azúcar")
    conn.commit()
    conn.close()
    assert "otro ingrediente" in rechaza(
        400, main.elegir_para_ingrediente, harina,
        main.EleccionSuper(**{"super": "carrefour", "producto_id": ajeno}))


def test_el_override_de_un_renglon_valida_el_supermercado():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    de_coto = producto(conn, ingrediente_id, "coto", "Harina Coto")
    conn.commit()
    receta_id = main.crear_receta(main.DatosReceta(nombre="Torta"))["id"]
    renglon_id = main.agregar_renglon(receta_id, main.Renglon(
        ingrediente_id=ingrediente_id, cantidad=100, unidad="g"))["id"]
    conn.close()

    rechaza(400, main.override_de_renglon, renglon_id,
            main.OverrideRenglon(productos={"jumbo": de_coto}))
    rechaza(400, main.override_de_renglon, renglon_id,
            main.OverrideRenglon(productos={"carrefour": de_coto}))
    main.override_de_renglon(renglon_id, main.OverrideRenglon(productos={"coto": de_coto}))
    assert main.ver_receta(receta_id)["supers"][2]["detalle"][0]["origen"] == "receta"


def test_el_override_de_un_renglon_inexistente():
    base_de_prueba()
    rechaza(404, main.override_de_renglon, 999, main.OverrideRenglon(productos={"coto": 1}))


# --- tamaño a mano --------------------------------------------------------

def test_el_tamano_a_mano_valida_lo_que_recibe():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    p = producto(conn, ingrediente_id, "coto", "Harina suelta", tamano=None, unidad=None)
    conn.commit()
    conn.close()

    rechaza(400, main.corregir_tamano, p, main.TamanoManual(tamano=-5, unidad="g"))
    rechaza(400, main.corregir_tamano, p, main.TamanoManual(tamano=500, unidad="litros"))
    main.corregir_tamano(p, main.TamanoManual(tamano=500, unidad="g"))

    conn = db.get_conn()
    fila = conn.execute("SELECT * FROM productos WHERE id = ?", (p,)).fetchone()
    conn.close()
    assert (fila["tamano_efectivo"], fila["unidad_efectiva"]) == (500, "g")


def test_mandar_el_tamano_en_null_vuelve_a_lo_que_diga_el_scraper():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    p = producto(conn, ingrediente_id, "coto", "Harina", tamano=1000, unidad="g")
    conn.commit()
    conn.close()

    main.corregir_tamano(p, main.TamanoManual(tamano=250, unidad="g"))
    main.corregir_tamano(p, main.TamanoManual(tamano=None, unidad=None))

    conn = db.get_conn()
    fila = conn.execute("SELECT * FROM productos WHERE id = ?", (p,)).fetchone()
    conn.close()
    assert fila["tamano_efectivo"] == 1000


# --- categorías -----------------------------------------------------------

def test_la_categoria_recorta_los_candidatos_y_ofrece_las_de_la_tienda():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente("Manteca")
    producto(conn, ingrediente_id, "carrefour", "Medialunas de manteca", orden=0,
             categoria="Panadería / Especialidades dulces")
    producto(conn, ingrediente_id, "carrefour", "Manteca Primer Premio", orden=1,
             categoria="Lácteos y productos frescos / Mantecas")
    conn.commit()
    conn.close()

    carrefour = main.ver_ingrediente(ingrediente_id)["supers"][0]
    assert len(carrefour["candidatos"]) == 2
    assert [c["categoria"] for c in carrefour["categorias"]] == [
        "Lácteos y productos frescos / Mantecas", "Panadería / Especialidades dulces"]

    main.editar_categorias(ingrediente_id, main.Categorias(
        categorias={"carrefour": "Lácteos y productos frescos"}))
    carrefour = main.ver_ingrediente(ingrediente_id)["supers"][0]
    assert [c["nombre_producto"] for c in carrefour["candidatos"]] == ["Manteca Primer Premio"]
    assert carrefour["en_uso"]["nombre_producto"] == "Manteca Primer Premio"
    # Se siguen ofreciendo todas, para poder cambiar de rama.
    assert len(carrefour["categorias"]) == 2


def test_mandar_la_categoria_en_null_saca_el_filtro():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente("Manteca")
    producto(conn, ingrediente_id, "coto", "Papel manteca", categoria="Limpieza / Papeles")
    conn.commit()
    conn.close()

    main.editar_categorias(ingrediente_id, main.Categorias(categorias={"coto": "Frescos"}))
    assert main.ver_ingrediente(ingrediente_id)["supers"][2]["candidatos"] == []
    main.editar_categorias(ingrediente_id, main.Categorias(categorias={"coto": None}))
    assert len(main.ver_ingrediente(ingrediente_id)["supers"][2]["candidatos"]) == 1


def test_la_categoria_valida_el_supermercado():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    rechaza(400, main.editar_categorias, ingrediente_id,
            main.Categorias(categorias={"jumbo": "Almacén"}))


# --- productos que dejaron de aparecer ------------------------------------

def test_los_candidatos_viejos_vienen_marcados():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    producto(conn, ingrediente_id, "coto", "Harina de antes", fecha="2026-01-01T00:00:00")
    producto(conn, ingrediente_id, "coto", "Harina de ahora", orden=1, fecha="2026-03-01T00:00:00")
    conn.commit()
    conn.close()

    coto = main.ver_ingrediente(ingrediente_id)["supers"][2]
    marcas = {c["nombre_producto"]: c["desactualizado"] for c in coto["candidatos"]}
    assert marcas == {"Harina de antes": True, "Harina de ahora": False}


# --- recetas --------------------------------------------------------------

def test_una_receta_sin_ingredientes_no_tiene_ganador():
    base_de_prueba()
    receta_id = main.crear_receta(main.DatosReceta(nombre="Torta", ganancia_pct=50))["id"]
    r = main.ver_receta(receta_id)
    assert r["ganador"] is None
    assert [c["super"] for c in r["supers"]] == ["carrefour", "disco", "coto"]


def test_ver_una_receta_que_no_existe():
    base_de_prueba()
    rechaza(404, main.ver_receta, 999)


def test_borrar_una_receta_se_lleva_sus_renglones():
    conn = base_de_prueba()
    ingrediente_id = crear_ingrediente()
    receta_id = main.crear_receta(main.DatosReceta(nombre="Torta"))["id"]
    main.agregar_renglon(receta_id, main.Renglon(
        ingrediente_id=ingrediente_id, cantidad=100, unidad="g"))
    main.borrar_receta(receta_id)
    conn = db.get_conn()
    assert conn.execute("SELECT COUNT(*) AS n FROM receta_ingredientes").fetchone()["n"] == 0
    conn.close()


def test_la_lista_de_recetas_cuenta_sus_ingredientes():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    receta_id = main.crear_receta(main.DatosReceta(nombre="Torta"))["id"]
    for cantidad in (100, 200):
        main.agregar_renglon(receta_id, main.Renglon(
            ingrediente_id=ingrediente_id, cantidad=cantidad, unidad="g"))
    assert main.listar_recetas()[0]["cantidad_ingredientes"] == 2


# --- alcance del scraping -------------------------------------------------

def test_el_alcance_del_scraping_se_valida():
    base_de_prueba()
    rechaza(400, main.actualizar, main.Alcance(alcance="lo que sea"))
    rechaza(400, main.actualizar, main.Alcance(alcance="ingrediente"))


def test_sin_vencidos_no_se_dispara_nada():
    base_de_prueba()
    r = main.actualizar(main.Alcance(alcance="vencidos"))
    assert r == {"iniciado": False, "motivo": "No hay precios vencidos"}


def test_un_ingrediente_sin_productos_cuenta_como_vencido():
    base_de_prueba()
    ingrediente_id = crear_ingrediente()
    _scrapes.clear()
    main.actualizar(main.Alcance(alcance="vencidos"))
    assert _scrapes == [([ingrediente_id], "vencidos")]


if __name__ == "__main__":
    fallos = 0
    for nombre, funcion in sorted(globals().items()):
        if not nombre.startswith("test_"):
            continue
        try:
            funcion()
            print("ok", nombre)
        except AssertionError as e:
            fallos += 1
            print("FALLA", nombre, "->", e)
    sys.exit(1 if fallos else 0)
