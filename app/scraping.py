from datetime import datetime, timedelta

from .db import get_conn
from .scrapers import coto
from .scrapers.common import es_relevante
from .scrapers.vtex import VtexScraper

DIAS_FRESCURA = 2


def ultima_actualizacion():
    conn = get_conn()
    fila = conn.execute("SELECT MAX(fecha_scrape) AS f FROM productos").fetchone()
    conn.close()
    return fila["f"]


def esta_desactualizado():
    ultima = ultima_actualizacion()
    if not ultima:
        return True
    return datetime.fromisoformat(ultima) < datetime.now() - timedelta(days=DIAS_FRESCURA)


def _limite_frescura():
    return (datetime.now() - timedelta(days=DIAS_FRESCURA)).isoformat(timespec="seconds")


def ingredientes_desactualizados():
    """Ids de ingredientes sin productos o con precios vencidos."""
    conn = get_conn()
    filas = conn.execute("""
        SELECT i.id FROM ingredientes i
        LEFT JOIN productos p ON p.ingrediente_id = i.id
        GROUP BY i.id
        HAVING MAX(p.fecha_scrape) IS NULL OR MAX(p.fecha_scrape) < ?
    """, (_limite_frescura(),)).fetchall()
    conn.close()
    return [f["id"] for f in filas]


def terminos_por_ingrediente():
    conn = get_conn()
    filas = conn.execute("""
        SELECT i.id, i.unidad_base, COALESCE(t.termino, i.nombre) AS termino
        FROM ingredientes i
        LEFT JOIN terminos_busqueda t ON t.ingrediente_id = i.id
    """).fetchall()
    conn.close()
    agrupado = {}
    for f in filas:
        entrada = agrupado.setdefault(f["id"], {"unidad": f["unidad_base"], "terminos": set()})
        entrada["terminos"].add(f["termino"])
    return agrupado


def categorias_elegidas():
    """{(ingrediente_id, super): categoria} de lo que el usuario ya marcó."""
    conn = get_conn()
    filas = conn.execute("SELECT ingrediente_id, super, categoria FROM ingrediente_categorias").fetchall()
    conn.close()
    return {(f["ingrediente_id"], f["super"]): f["categoria"] for f in filas}


def en_categoria(categoria, elegida):
    if not elegida:
        return True
    return categoria == elegida or (categoria or "").startswith(elegida + " / ")


def _buscadores(vtex):
    return [
        ("coto", coto.buscar),
        ("carrefour", lambda t: vtex.buscar("carrefour", t)),
        ("disco", lambda t: vtex.buscar("disco", t)),
    ]


def actualizar_todo(progreso=None, ingredientes=None):
    """Scrapea los tres supers para cada ingrediente y guarda los productos.

    `ingredientes` limita la corrida a esos ids; None scrapea todos.
    """
    terminos = terminos_por_ingrediente()
    categorias = categorias_elegidas()
    if ingredientes is not None:
        permitidos = set(ingredientes)
        terminos = {k: v for k, v in terminos.items() if k in permitidos}
    total = len(terminos)
    guardados = 0

    with VtexScraper() as vtex:
        for i, (ingrediente_id, datos) in enumerate(terminos.items(), start=1):
            terms, unidad_base = datos["terminos"], datos["unidad"]
            # Los resultados se acumulan por super a lo largo de todos los
            # términos y recién después se guardan, para que `orden` refleje
            # la relevancia combinada. Guardar término por término hacía que
            # el último pisara el orden de los anteriores.
            por_super = {}
            for termino in sorted(terms):
                for tienda, buscar in _buscadores(vtex):
                    try:
                        encontrados = [p for p in buscar(termino) if p]
                    except Exception as e:
                        print(f"[{tienda}] error en '{termino}': {e}")
                        continue
                    vistos = por_super.setdefault(tienda, {})
                    for p in encontrados:
                        vistos.setdefault(p.id_externo, p)

            for tienda, productos in por_super.items():
                # Si el ingrediente ya tiene categoría, esa es la señal más
                # fuerte que hay y manda sobre el resto. Después los relevantes
                # por nombre, para que el auto-elegido (orden 0) sea uno de
                # ellos, y entre esos primero los que se pueden convertir a la
                # unidad de la receta: unas vainillas medidas en unidades no
                # sirven para una receta que pide gramos. El resto se guarda
                # igual, para poder elegir a mano si el filtro se pasa de
                # estricto.
                elegida = categorias.get((ingrediente_id, tienda))
                ordenados = sorted(
                    productos.values(),
                    key=lambda p: (
                        not en_categoria(p.categoria, elegida),
                        not es_relevante(p.nombre_producto, terms),
                        p.unidad != unidad_base,
                        p.tamano is None,
                    ),
                )
                guardados += guardar_productos(ingrediente_id, ordenados)

            if progreso:
                progreso(i, total)

    return guardados


def guardar_productos(ingrediente_id, productos, desde_orden=0):
    ahora = datetime.now().isoformat(timespec="seconds")
    conn = get_conn()
    for i, p in enumerate(productos, start=desde_orden):
        conn.execute("""
            INSERT INTO productos (ingrediente_id, super, id_externo, marca, nombre_producto,
                                   tamano, unidad, precio, precio_descuento, texto_descuento,
                                   link, ean, imagen, categoria, orden, fecha_scrape)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (super, id_externo) DO UPDATE SET
                ingrediente_id = excluded.ingrediente_id,
                marca = excluded.marca,
                nombre_producto = excluded.nombre_producto,
                tamano = excluded.tamano,
                unidad = excluded.unidad,
                precio = excluded.precio,
                precio_descuento = excluded.precio_descuento,
                texto_descuento = excluded.texto_descuento,
                link = excluded.link,
                ean = excluded.ean,
                imagen = excluded.imagen,
                categoria = excluded.categoria,
                orden = excluded.orden,
                fecha_scrape = excluded.fecha_scrape
        """, (ingrediente_id, p.super, p.id_externo, p.marca, p.nombre_producto,
              p.tamano, p.unidad, p.precio, p.precio_descuento, p.texto_descuento,
              p.link, p.ean, p.imagen, p.categoria, i, ahora))
    conn.commit()
    conn.close()
    return len(productos)
