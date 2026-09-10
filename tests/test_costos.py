"""Costeo y comparación entre supers, sobre una base temporal."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402


def base_de_prueba():
    """Base nueva y vacía para cada test, con el esquema real."""
    db.DB_PATH = Path(tempfile.mkdtemp()) / "prueba.db"
    db.init_db()
    return db.get_conn()


def ingrediente(conn, nombre, unidad_base="g"):
    cur = conn.execute("INSERT INTO ingredientes (nombre, unidad_base) VALUES (?, ?)",
                       (nombre, unidad_base))
    return cur.lastrowid


def producto(conn, ingrediente_id, super_nombre, nombre, precio, tamano, unidad,
             orden=0, categoria=None, fecha="2026-01-01T00:00:00"):
    cur = conn.execute("""
        INSERT INTO productos (ingrediente_id, super, id_externo, nombre_producto,
                               tamano, unidad, precio, orden, categoria, fecha_scrape)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ingrediente_id, super_nombre, f"{super_nombre}-{nombre}", nombre,
          tamano, unidad, precio, orden, categoria, fecha))
    return cur.lastrowid


def receta(conn, nombre="Torta", ganancia=100):
    cur = conn.execute("INSERT INTO recetas (nombre, ganancia_pct) VALUES (?, ?)",
                       (nombre, ganancia))
    return cur.lastrowid


def renglon(conn, receta_id, ingrediente_id, cantidad, unidad):
    cur = conn.execute("""
        INSERT INTO receta_ingredientes (receta_id, ingrediente_id, cantidad, unidad)
        VALUES (?, ?, ?, ?)
    """, (receta_id, ingrediente_id, cantidad, unidad))
    return cur.lastrowid


def columna(resultado, super_nombre):
    return next(c for c in resultado["supers"] if c["super"] == super_nombre)


def escenario_completo(precios=(1000, 2000, 3000)):
    """Un ingrediente de 1 kg en los tres supers, receta que pide 500 g."""
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    for super_nombre, precio in zip(("carrefour", "disco", "coto"), precios):
        producto(conn, harina, super_nombre, f"Harina {super_nombre}", precio, 1000, "g")
    receta_id = receta(conn)
    renglon_id = renglon(conn, receta_id, harina, 500, "g")
    conn.commit()
    return conn, receta_id, renglon_id, harina


# --- lo básico ------------------------------------------------------------

def test_costo_se_prorratea_por_tamano():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.close()
    r = costos.comparar_supers(receta_id)
    # 500 g de un paquete de 1000 g que sale $1000
    assert columna(r, "carrefour")["costo_total"] == 500.0, columna(r, "carrefour")


def test_precio_de_venta_aplica_la_ganancia():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.close()
    r = costos.comparar_supers(receta_id)
    assert columna(r, "carrefour")["precio_venta"] == 1000.0  # 500 + 100%


def test_gana_el_mas_barato_de_los_completos():
    from app import costos
    conn, receta_id, _, _ = escenario_completo(precios=(3000, 1000, 2000))
    conn.close()
    r = costos.comparar_supers(receta_id)
    assert r["ganador"] == "disco", r["ganador"]
    assert columna(r, "disco")["diferencia"] == 0
    assert columna(r, "coto")["diferencia"] == 500.0


# --- el bug que motivó el rediseño ----------------------------------------

def test_un_super_sin_producto_queda_incompleto_y_sin_precio():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.execute("DELETE FROM productos WHERE super = 'coto'")
    conn.commit()
    conn.close()

    coto = columna(costos.comparar_supers(receta_id), "coto")
    assert coto["completo"] is False
    assert coto["costo_total"] is None, "un total parcial hace parecer barato al que no tiene todo"
    assert coto["precio_venta"] is None
    assert coto["problemas"] == [{"ingrediente_id": 1, "ingrediente": "Harina",
                                 "motivo": costos.SIN_PRODUCTO}]


def test_un_super_incompleto_no_gana_aunque_su_parcial_sea_menor():
    from app import costos
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    sal = ingrediente(conn, "Sal")
    receta_id = receta(conn)
    for ing, cantidad in ((harina, 500), (sal, 100)):
        renglon(conn, receta_id, ing, cantidad, "g")
    # Coto tiene la harina baratísima pero no tiene sal: su parcial es el menor.
    for super_nombre, precio in (("carrefour", 1000), ("disco", 1200), ("coto", 100)):
        producto(conn, harina, super_nombre, "Harina", precio, 1000, "g")
    for super_nombre in ("carrefour", "disco"):
        producto(conn, sal, super_nombre, "Sal", 500, 1000, "g")
    conn.commit()
    conn.close()

    r = costos.comparar_supers(receta_id)
    assert r["ganador"] == "carrefour", r["ganador"]
    assert columna(r, "coto")["completo"] is False


def test_sin_ningun_super_completo_no_hay_ganador():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.execute("DELETE FROM productos")
    conn.commit()
    conn.close()

    r = costos.comparar_supers(receta_id)
    assert r["ganador"] is None
    assert all(c["completo"] is False for c in r["supers"])


# --- resolución del producto ----------------------------------------------

def test_la_eleccion_del_ingrediente_gana_al_automatico():
    from app import costos
    conn, receta_id, _, harina = escenario_completo()
    caro = producto(conn, harina, "carrefour", "Harina cara", 9000, 1000, "g", orden=5)
    conn.execute("INSERT INTO elecciones (ingrediente_id, super, producto_id) VALUES (?, ?, ?)",
                 (harina, "carrefour", caro))
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["origen"] == "confirmado"
    assert fila["costo"] == 4500.0


def test_el_override_del_renglon_gana_a_la_eleccion_del_ingrediente():
    from app import costos
    conn, receta_id, renglon_id, harina = escenario_completo()
    confirmado = producto(conn, harina, "carrefour", "Harina confirmada", 9000, 1000, "g", orden=5)
    especial = producto(conn, harina, "carrefour", "Harina especial", 4000, 1000, "g", orden=9)
    conn.execute("INSERT INTO elecciones (ingrediente_id, super, producto_id) VALUES (?, ?, ?)",
                 (harina, "carrefour", confirmado))
    conn.execute("INSERT INTO renglon_elecciones (renglon_id, super, producto_id) VALUES (?, ?, ?)",
                 (renglon_id, "carrefour", especial))
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["origen"] == "receta"
    assert fila["costo"] == 2000.0


def test_el_override_de_un_super_no_afecta_a_los_otros():
    """El bug viejo: producto_elegido_id guardaba un solo producto.

    Valía para su super y se ignoraba en los otros dos, comparando cosas
    distintas sin avisar.
    """
    from app import costos
    conn, receta_id, renglon_id, harina = escenario_completo(precios=(1000, 1000, 1000))
    especial = producto(conn, harina, "carrefour", "Harina especial", 6000, 1000, "g", orden=9)
    conn.execute("INSERT INTO renglon_elecciones (renglon_id, super, producto_id) VALUES (?, ?, ?)",
                 (renglon_id, "carrefour", especial))
    conn.commit()
    conn.close()

    r = costos.comparar_supers(receta_id)
    assert columna(r, "carrefour")["detalle"][0]["origen"] == "receta"
    assert columna(r, "disco")["detalle"][0]["origen"] == "automatico"
    assert columna(r, "disco")["costo_total"] == 500.0


def test_el_automatico_prefiere_los_que_tienen_tamano():
    from app import costos
    conn, receta_id, _, harina = escenario_completo()
    conn.execute("DELETE FROM productos WHERE super = 'carrefour'")
    producto(conn, harina, "carrefour", "Harina sin tamaño", 800, None, None, orden=0)
    producto(conn, harina, "carrefour", "Harina con tamaño", 1000, 1000, "g", orden=3)
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Harina con tamaño"


def test_si_ninguno_tiene_tamano_avisa_que_falta_el_tamano():
    """Antes decía "no hay producto", que manda a buscar donde no hay nada."""
    from app import costos
    conn, receta_id, _, harina = escenario_completo()
    conn.execute("DELETE FROM productos WHERE super = 'carrefour'")
    producto(conn, harina, "carrefour", "Harina sin tamaño", 800, None, None)
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["problema"] == costos.SIN_TAMANO
    assert fila["producto"]["nombre_producto"] == "Harina sin tamaño"


# --- tamaño y unidades ----------------------------------------------------

def test_el_tamano_manual_pisa_al_scrapeado():
    from app import costos
    conn, receta_id, _, harina = escenario_completo()
    conn.execute("""UPDATE productos SET tamano_manual = 500, unidad_manual = 'g'
                    WHERE super = 'carrefour'""")
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["tamano_efectivo"] == 500
    assert fila["costo"] == 1000.0  # 500 g de un paquete de 500 g que sale $1000


def test_convierte_kilos_de_la_receta_a_gramos_del_producto():
    from app import costos
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    for super_nombre in ("carrefour", "disco", "coto"):
        producto(conn, harina, super_nombre, "Harina", 1000, 1000, "g")
    receta_id = receta(conn)
    renglon(conn, receta_id, harina, 2, "kg")
    conn.commit()
    conn.close()

    assert columna(costos.comparar_supers(receta_id), "carrefour")["costo_total"] == 2000.0


def test_unidad_que_no_se_puede_convertir_deja_el_super_incompleto():
    from app import costos
    conn = base_de_prueba()
    leche = ingrediente(conn, "Leche", "ml")
    for super_nombre in ("carrefour", "disco", "coto"):
        producto(conn, leche, super_nombre, "Leche en polvo", 1000, 1000, "g")
    receta_id = receta(conn)
    renglon(conn, receta_id, leche, 500, "ml")
    conn.commit()
    conn.close()

    carrefour = columna(costos.comparar_supers(receta_id), "carrefour")
    assert carrefour["completo"] is False
    assert carrefour["detalle"][0]["problema"] == costos.UNIDAD_INCOMPATIBLE


# --- categoría de la tienda -----------------------------------------------

def escenario_manteca():
    """Lo que devuelve buscar "manteca": manteca de verdad y ruido de otra góndola."""
    conn = base_de_prueba()
    manteca = ingrediente(conn, "Manteca")
    # El ruido sale primero en el buscador, así que sin filtro gana el automático.
    producto(conn, manteca, "carrefour", "Medialunas de manteca x 6", 4000, 300, "g",
             orden=0, categoria="Panadería / Elaboración Carrefour / Especialidades dulces")
    producto(conn, manteca, "carrefour", "Manteca Primer Premio 200 g", 2000, 200, "g",
             orden=1, categoria="Lácteos y productos frescos / Mantecas, margarinas y levaduras")
    receta_id = receta(conn)
    renglon(conn, receta_id, manteca, 100, "g")
    conn.commit()
    return conn, receta_id, manteca


def elegir_categoria(conn, ingrediente_id, super_nombre, categoria):
    conn.execute("""
        INSERT INTO ingrediente_categorias (ingrediente_id, super, categoria) VALUES (?, ?, ?)
    """, (ingrediente_id, super_nombre, categoria))
    conn.commit()


def test_sin_categoria_el_automatico_puede_agarrar_ruido():
    from app import costos
    conn, receta_id, _ = escenario_manteca()
    conn.close()
    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Medialunas de manteca x 6"


def test_la_categoria_de_la_tienda_descarta_el_ruido():
    from app import costos
    conn, receta_id, manteca = escenario_manteca()
    elegir_categoria(conn, manteca, "carrefour",
                     "Lácteos y productos frescos / Mantecas, margarinas y levaduras")
    conn.close()
    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Manteca Primer Premio 200 g"
    assert fila["costo"] == 1000.0


def test_la_categoria_incluye_sus_subcategorias():
    """Elegir una rama ancha tiene que alcanzar a lo que cuelga de ella."""
    from app import costos
    conn, receta_id, manteca = escenario_manteca()
    elegir_categoria(conn, manteca, "carrefour", "Lácteos y productos frescos")
    conn.close()
    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Manteca Primer Premio 200 g"


def test_la_categoria_no_se_mezcla_entre_tiendas():
    """Cada tienda nombra distinto; la de una no puede filtrar a la otra."""
    from app import costos
    conn, receta_id, manteca = escenario_manteca()
    producto(conn, manteca, "disco", "Manteca La Paulina 200 g", 2200, 200, "g",
             orden=0, categoria="Lácteos / Mantecas y Margarinas / Manteca")
    elegir_categoria(conn, manteca, "carrefour", "Lácteos y productos frescos")
    conn.commit()
    conn.close()
    r = costos.comparar_supers(receta_id)
    assert columna(r, "disco")["detalle"][0]["producto"]["nombre_producto"] == "Manteca La Paulina 200 g"


def test_categoria_sin_productos_deja_el_super_incompleto():
    """Si la rama elegida quedó vacía, se avisa; no se cae de vuelta al ruido."""
    from app import costos
    conn, receta_id, manteca = escenario_manteca()
    elegir_categoria(conn, manteca, "carrefour", "Limpieza / Papeles de cocina")
    conn.close()
    carrefour = columna(costos.comparar_supers(receta_id), "carrefour")
    assert carrefour["completo"] is False
    assert carrefour["detalle"][0]["problema"] == costos.SIN_PRODUCTO


# --- packaging ------------------------------------------------------------

def test_el_packaging_suma_igual_en_los_tres_supers():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.execute("INSERT INTO packaging (receta_id, descripcion, costo) VALUES (?, 'Caja', 300)",
                 (receta_id,))
    conn.commit()
    conn.close()

    r = costos.comparar_supers(receta_id)
    for c in r["supers"]:
        assert c["costo_packaging"] == 300
    assert columna(r, "carrefour")["costo_total"] == 800.0


# --- productos que dejaron de aparecer ------------------------------------

def escenario_rancio():
    """Un producto viejo primero en relevancia y uno actual detrás.

    El viejo no volvió en la última búsqueda: su precio es del día que lo vimos.
    """
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    viejo = producto(conn, harina, "carrefour", "Harina de oferta vieja", 200, 1000, "g",
                     orden=0, fecha="2026-01-01T00:00:00")
    producto(conn, harina, "carrefour", "Harina de ahora", 1000, 1000, "g",
             orden=1, fecha="2026-03-01T00:00:00")
    receta_id = receta(conn)
    renglon_id = renglon(conn, receta_id, harina, 500, "g")
    conn.commit()
    return conn, receta_id, renglon_id, harina, viejo


def test_el_automatico_ignora_el_que_no_volvio_en_la_ultima_busqueda():
    """Si no, una oferta de hace meses gana la comparación para siempre."""
    from app import costos
    conn, receta_id, _, _, _ = escenario_rancio()
    conn.close()
    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Harina de ahora"
    assert fila["costo"] == 500.0


def test_un_elegido_a_mano_que_quedo_viejo_se_avisa_pero_no_bloquea():
    from app import costos
    conn, receta_id, _, harina, viejo = escenario_rancio()
    conn.execute("INSERT INTO elecciones (ingrediente_id, super, producto_id) VALUES (?, ?, ?)",
                 (harina, "carrefour", viejo))
    conn.commit()
    conn.close()

    carrefour = columna(costos.comparar_supers(receta_id), "carrefour")
    assert carrefour["completo"] is True, "el precio existió; no se bloquea la comparación"
    assert carrefour["detalle"][0]["desactualizado"] is True
    assert [d["ingrediente"] for d in carrefour["desactualizados"]] == ["Harina"]


def test_si_todos_son_de_la_misma_fecha_ninguno_esta_viejo():
    from app import costos
    conn, receta_id, _, _ = escenario_completo()
    conn.close()
    for c in costos.comparar_supers(receta_id)["supers"]:
        assert c["desactualizados"] == []
        assert all(d["desactualizado"] is False for d in c["detalle"])


def test_si_el_unico_producto_quedo_viejo_el_super_avisa_que_no_tiene():
    """Mejor decir que no hay precio actual que costear con uno de otro mes."""
    from app import costos
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    producto(conn, harina, "carrefour", "Harina vieja", 200, 1000, "g", fecha="2026-01-01T00:00:00")
    producto(conn, harina, "carrefour", "Otra cosa", 300, 1000, "g", orden=1,
             fecha="2026-03-01T00:00:00")
    conn.execute("DELETE FROM productos WHERE nombre_producto = 'Otra cosa'")
    producto(conn, harina, "carrefour", "Marcador nuevo", 900, 1000, "g", orden=9,
             fecha="2026-03-01T00:00:00")
    receta_id = receta(conn)
    renglon(conn, receta_id, harina, 500, "g")
    conn.commit()
    conn.close()

    fila = columna(costos.comparar_supers(receta_id), "carrefour")["detalle"][0]
    assert fila["producto"]["nombre_producto"] == "Marcador nuevo"


# --- el tamaño a mano frente a un re-scrape -------------------------------

def _producto_scrapeado(nombre, precio, tamano, unidad, id_externo="carrefour-x"):
    from app.scrapers.common import Producto
    return Producto(super="carrefour", id_externo=id_externo, marca="Marca",
                    nombre_producto=nombre, tamano=tamano, unidad=unidad, precio=precio,
                    precio_descuento=None, texto_descuento=None, link=None, ean="779",
                    imagen=None, categoria="Almacén / Harinas")


def test_el_tamano_cargado_a_mano_sobrevive_al_re_scrape():
    """El precio se actualiza; la corrección a mano no se pisa.

    Por eso `tamano_manual` va en columnas aparte y el upsert no las toca: el
    scraper no puede deducir lo que el usuario tuvo que cargar mirando el envase.
    """
    from app import scraping
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    conn.commit()
    conn.close()

    scraping.guardar_productos(harina, [_producto_scrapeado("Harina suelta", 1000, None, None)])

    conn = db.get_conn()
    conn.execute("UPDATE productos SET tamano_manual = 750, unidad_manual = 'g'")
    conn.commit()
    conn.close()

    # Segunda corrida: mismo producto (mismo id_externo), precio nuevo.
    scraping.guardar_productos(harina, [_producto_scrapeado("Harina suelta", 1800, None, None)])

    conn = db.get_conn()
    p = conn.execute("SELECT * FROM productos").fetchone()
    conn.close()
    assert p["precio"] == 1800, "el precio tiene que actualizarse"
    assert p["tamano_manual"] == 750, "el tamaño a mano no se puede perder"
    assert p["tamano_efectivo"] == 750
    assert p["unidad_efectiva"] == "g"


def test_el_producto_se_identifica_por_el_id_de_la_tienda():
    """Dos corridas del mismo producto son una sola fila, no dos."""
    from app import scraping
    conn = base_de_prueba()
    harina = ingrediente(conn, "Harina")
    conn.commit()
    conn.close()

    scraping.guardar_productos(harina, [_producto_scrapeado("Harina 000", 1000, 1000, "g")])
    scraping.guardar_productos(harina, [_producto_scrapeado("Harina 000 nuevo nombre", 1200, 1000, "g")])

    conn = db.get_conn()
    filas = conn.execute("SELECT * FROM productos").fetchall()
    conn.close()
    assert len(filas) == 1
    assert filas[0]["nombre_producto"] == "Harina 000 nuevo nombre"


# --- migración ------------------------------------------------------------

def test_la_migracion_conserva_el_override_viejo():
    """La columna vieja guardaba un producto suelto; pasa a ser el de su super."""
    conn = base_de_prueba()
    conn.execute("ALTER TABLE receta_ingredientes ADD COLUMN producto_elegido_id INTEGER")
    harina = ingrediente(conn, "Harina")
    elegido = producto(conn, harina, "disco", "Harina elegida a mano", 1000, 1000, "g")
    receta_id = receta(conn)
    renglon_id = renglon(conn, receta_id, harina, 500, "g")
    conn.execute("UPDATE receta_ingredientes SET producto_elegido_id = ? WHERE id = ?",
                 (elegido, renglon_id))
    conn.commit()
    conn.close()

    db.init_db()
    conn = db.get_conn()
    filas = conn.execute("SELECT * FROM renglon_elecciones").fetchall()
    assert len(filas) == 1
    assert (filas[0]["renglon_id"], filas[0]["super"], filas[0]["producto_id"]) \
        == (renglon_id, "disco", elegido)
    assert "producto_elegido_id" not in {f["name"] for f in
                                        conn.execute("PRAGMA table_xinfo(receta_ingredientes)")}
    conn.close()


def test_init_db_se_puede_correr_dos_veces():
    base_de_prueba()
    db.init_db()
    db.init_db()


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
