from .db import SUPERS, get_conn

_CONVERSION = {("g", "g"): 1, ("ml", "ml"): 1, ("un", "un"): 1,
               ("kg", "g"): 1000, ("l", "ml"): 1000}

# Por qué un renglón no se puede costear. La UI usa el motivo para ofrecer la
# corrección correcta en vez de un "falta elegir producto" genérico.
SIN_PRODUCTO = "sin_producto"
SIN_TAMANO = "sin_tamano"
UNIDAD_INCOMPATIBLE = "unidad_incompatible"


def _convertir(cantidad, desde, hacia):
    if desde == hacia:
        return cantidad
    factor = _CONVERSION.get((desde, hacia))
    return cantidad * factor if factor else None


def costo_receta(receta_id, super_nombre, conn=None):
    """Calcula el costo de una receta usando productos de un super.

    Para cada ingrediente usa el override del renglón, si no la elección
    confirmada del ingrediente, si no el más relevante. Los tres niveles son
    por super. El precio de venta sale del precio de lista: los descuentos no
    se trasladan al cliente final.

    Si algún renglón no se puede costear, el super queda `completo: False` y
    sin `precio_venta`. Un total parcial es peor que ninguno: hace que el super
    al que le faltan ingredientes parezca el más barato.
    """
    propia = conn is None
    conn = conn or get_conn()
    try:
        receta = conn.execute("SELECT * FROM recetas WHERE id = ?", (receta_id,)).fetchone()
        if not receta:
            return None

        renglones = conn.execute("""
            SELECT ri.id, ri.cantidad, ri.unidad,
                   i.id AS ingrediente_id, i.nombre AS ingrediente
            FROM receta_ingredientes ri
            JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receta_id = ?
            ORDER BY i.nombre
        """, (receta_id,)).fetchall()

        detalle = []
        costo_ingredientes = 0.0
        problemas = []
        rancios = []

        for r in renglones:
            fila = {"renglon_id": r["id"], "ingrediente_id": r["ingrediente_id"],
                    "ingrediente": r["ingrediente"], "cantidad": r["cantidad"],
                    "unidad": r["unidad"], "producto": None, "origen": None,
                    "costo": None, "problema": None, "desactualizado": False}
            producto, origen = _producto_para(conn, r, super_nombre)

            if not producto:
                fila["problema"] = SIN_PRODUCTO
            else:
                fila["producto"] = dict(producto)
                fila["origen"] = origen
                # El automático nunca elige uno rancio; uno elegido a mano sí
                # puede quedar viejo, y ahí se avisa sin bloquear la comparación:
                # el precio existió, solo que es de otro día.
                fila["desactualizado"] = esta_rancio(
                    producto, ultima_del_super(conn, r["ingrediente_id"], super_nombre))
                if fila["desactualizado"]:
                    rancios.append({"ingrediente_id": r["ingrediente_id"],
                                    "ingrediente": r["ingrediente"],
                                    "fecha": producto["fecha_scrape"]})
                if not producto["tamano_efectivo"]:
                    fila["problema"] = SIN_TAMANO
                else:
                    cantidad = _convertir(r["cantidad"], r["unidad"], producto["unidad_efectiva"])
                    if cantidad is None:
                        fila["problema"] = UNIDAD_INCOMPATIBLE
                    else:
                        costo = producto["precio"] / producto["tamano_efectivo"] * cantidad
                        costo_ingredientes += costo
                        fila["costo"] = round(costo, 2)

            if fila["problema"]:
                problemas.append({"ingrediente_id": r["ingrediente_id"],
                                  "ingrediente": r["ingrediente"],
                                  "motivo": fila["problema"]})
            detalle.append(fila)

        packaging = conn.execute(
            "SELECT id, descripcion, costo FROM packaging WHERE receta_id = ?", (receta_id,)
        ).fetchall()
    finally:
        if propia:
            conn.close()

    costo_packaging = sum(p["costo"] for p in packaging)
    costo_total = costo_ingredientes + costo_packaging
    completo = not problemas
    ganancia = receta["ganancia_pct"] / 100

    return {
        "super": super_nombre,
        "detalle": detalle,
        "packaging": [dict(p) for p in packaging],
        "completo": completo,
        "problemas": problemas,
        "desactualizados": rancios,
        "costo_ingredientes": round(costo_ingredientes, 2) if completo else None,
        "costo_packaging": round(costo_packaging, 2),
        "costo_total": round(costo_total, 2) if completo else None,
        "precio_venta": round(costo_total * (1 + ganancia), 2) if completo else None,
    }


def _producto_para(conn, renglon, super_nombre):
    """Override del renglón, si no la elección del ingrediente, si no el más relevante.

    Nunca ordena por precio: el buscador de cada super devuelve el producto
    correcto primero, y ordenar por precio/unidad lo hunde (buscar
    "Mascarpone" y quedarse con el más barato devuelve azúcar mascabo).
    """
    override = conn.execute("""
        SELECT p.* FROM renglon_elecciones re
        JOIN productos p ON p.id = re.producto_id
        WHERE re.renglon_id = ? AND re.super = ?
    """, (renglon["id"], super_nombre)).fetchone()
    if override:
        return override, "receta"

    elegido = conn.execute("""
        SELECT p.* FROM elecciones e
        JOIN productos p ON p.id = e.producto_id
        WHERE e.ingrediente_id = ? AND e.super = ?
    """, (renglon["ingrediente_id"], super_nombre)).fetchone()
    if elegido:
        return elegido, "confirmado"

    # Los que tienen tamaño van primero, pero si no hay ninguno se devuelve el
    # más relevante igual: así el renglón falla con "no sabemos cuánto trae" y
    # un botón para cargarlo, en vez de mentir con "no hay producto".
    condicion, parametros = filtro_categoria(conn, renglon["ingrediente_id"], super_nombre)
    automatico = conn.execute(f"""
        SELECT * FROM productos p
        WHERE ingrediente_id = ? AND super = ? {condicion}
              AND fecha_scrape = {SQL_ULTIMA_DEL_SUPER}
        ORDER BY (tamano_efectivo IS NULL OR tamano_efectivo <= 0), orden ASC
        LIMIT 1
    """, (renglon["ingrediente_id"], super_nombre, *parametros)).fetchone()
    return automatico, "automatico"


# Un producto que dejó de aparecer en los resultados no se vuelve a tocar: se
# queda con el precio del día que lo vimos por última vez. Elegirlo solo
# compara mal, así que el automático se limita a los que volvieron en la última
# búsqueda de ese ingrediente en ese supermercado.
SQL_ULTIMA_DEL_SUPER = """(
    SELECT MAX(fecha_scrape) FROM productos u
    WHERE u.ingrediente_id = p.ingrediente_id AND u.super = p.super
)"""


def ultima_del_super(conn, ingrediente_id, super_nombre):
    """Fecha del último scrape que trajo productos de ese ingrediente y super."""
    fila = conn.execute(
        "SELECT MAX(fecha_scrape) AS f FROM productos WHERE ingrediente_id = ? AND super = ?",
        (ingrediente_id, super_nombre)).fetchone()
    return fila["f"]


def esta_rancio(producto, ultima):
    """El producto no volvió en la última búsqueda: su precio quedó viejo."""
    return bool(ultima and producto["fecha_scrape"] < ultima)


def filtro_categoria(conn, ingrediente_id, super_nombre):
    """Condición SQL que deja solo los productos de la categoría elegida.

    Sin esto el elegido automático puede ser cualquier cosa que mencione el
    ingrediente: buscar "aceite" en Carrefour trae shampoo y limpiapisos, y
    "manteca" trae medialunas. La categoría la pone la tienda, así que alcanza
    con quedarse con la rama que el usuario marcó. Coincide con la rama entera:
    elegir "Almacén / Aceites y vinagres" incluye sus subcategorías.
    """
    fila = conn.execute(
        "SELECT categoria FROM ingrediente_categorias WHERE ingrediente_id = ? AND super = ?",
        (ingrediente_id, super_nombre)).fetchone()
    if not fila:
        return "", ()
    return "AND (categoria = ? OR categoria LIKE ?)", (fila["categoria"], fila["categoria"] + " / %")


def comparar_supers(receta_id):
    """Costea la receta en los tres supers y marca cuál gana.

    Devuelve los supers siempre en el mismo orden, para que las columnas de la
    tabla no se muevan entre recargas. Solo compite el que puede costear todos
    los renglones.
    """
    conn = get_conn()
    try:
        receta = conn.execute("SELECT * FROM recetas WHERE id = ?", (receta_id,)).fetchone()
        if not receta:
            return None
        columnas = [costo_receta(receta_id, s, conn) for s in SUPERS]
    finally:
        conn.close()

    # Una receta sin renglones cuesta cero en los tres: declarar ganador ahí no
    # dice nada. Recién hay algo que comparar cuando hay ingredientes.
    completos = [c for c in columnas if c["completo"]] if columnas[0]["detalle"] else []
    mas_barato = min((c["costo_total"] for c in completos), default=None)
    ganador = next((c["super"] for c in completos if c["costo_total"] == mas_barato), None)

    for c in columnas:
        c["gana"] = c["super"] == ganador
        c["diferencia"] = (round(c["costo_total"] - mas_barato, 2)
                           if c["completo"] and mas_barato is not None else None)

    return {"receta": dict(receta), "supers": columnas, "ganador": ganador}
