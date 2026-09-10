import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import costos, scraping
from .db import SUPERS, get_conn, init_db

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="pSapo")

_scrape_estado = {"corriendo": False, "hecho": 0, "total": 0, "error": None, "alcance": None}


@app.on_event("startup")
def arranque():
    init_db()


# --- precios ---------------------------------------------------------------

@app.get("/api/estado")
def estado():
    return {
        "ultima_actualizacion": scraping.ultima_actualizacion(),
        "desactualizado": scraping.esta_desactualizado(),
        "vencidos": len(scraping.ingredientes_desactualizados()),
        "scrape": _scrape_estado,
    }


def _lanzar_scrape(ingredientes, alcance):
    if _scrape_estado["corriendo"]:
        return {"iniciado": False, "motivo": "Ya se están actualizando los precios"}

    def correr():
        _scrape_estado.update(corriendo=True, hecho=0, total=0, error=None, alcance=alcance)
        try:
            scraping.actualizar_todo(
                progreso=lambda hecho, total: _scrape_estado.update(hecho=hecho, total=total),
                ingredientes=ingredientes,
            )
        except Exception as e:
            _scrape_estado["error"] = str(e)
        finally:
            _scrape_estado["corriendo"] = False

    threading.Thread(target=correr, daemon=True).start()
    return {"iniciado": True}


class Alcance(BaseModel):
    # "todo" | "vencidos" | "ingrediente"
    alcance: str = "todo"
    ingrediente_id: int | None = None


@app.post("/api/actualizar")
def actualizar(body: Alcance):
    """Único disparador del scraping. El alcance decide qué se busca."""
    if body.alcance == "todo":
        return _lanzar_scrape(None, "todo")
    if body.alcance == "vencidos":
        vencidos = scraping.ingredientes_desactualizados()
        if not vencidos:
            return {"iniciado": False, "motivo": "No hay precios vencidos"}
        return _lanzar_scrape(vencidos, "vencidos")
    if body.alcance == "ingrediente":
        if body.ingrediente_id is None:
            raise HTTPException(400, "Falta el ingrediente")
        return _lanzar_scrape([body.ingrediente_id], "ingrediente")
    raise HTTPException(400, f"Alcance desconocido: {body.alcance}")


# --- ingredientes ----------------------------------------------------------

@app.get("/api/ingredientes")
def listar_ingredientes():
    conn = get_conn()
    filas = conn.execute("""
        SELECT i.*,
               COUNT(DISTINCT p.id) AS cantidad_productos,
               COUNT(DISTINCT e.super) AS supers_confirmados,
               COUNT(DISTINCT p.super) AS supers_con_productos
        FROM ingredientes i
        LEFT JOIN productos p ON p.ingrediente_id = i.id
        LEFT JOIN elecciones e ON e.ingrediente_id = i.id
        GROUP BY i.id ORDER BY i.nombre
    """).fetchall()
    conn.close()
    return [dict(f) for f in filas]


@app.get("/api/ingredientes/{ingrediente_id}")
def ver_ingrediente(ingrediente_id: int):
    """El ingrediente con una columna por super.

    Cada super trae el producto en uso y sus candidatos. La forma de la
    respuesta es la de la pantalla: no hay una lista mezclada de los tres
    supers, porque son tres elecciones independientes.
    """
    conn = get_conn()
    ingrediente = conn.execute("SELECT * FROM ingredientes WHERE id = ?",
                               (ingrediente_id,)).fetchone()
    if not ingrediente:
        conn.close()
        raise HTTPException(404, "Ingrediente no encontrado")

    terminos = [t["termino"] for t in conn.execute(
        "SELECT termino FROM terminos_busqueda WHERE ingrediente_id = ? ORDER BY termino",
        (ingrediente_id,)).fetchall()]

    productos = conn.execute("""
        SELECT p.*, (e.producto_id IS NOT NULL) AS confirmado
        FROM productos p
        LEFT JOIN elecciones e
               ON e.producto_id = p.id AND e.ingrediente_id = p.ingrediente_id
        WHERE p.ingrediente_id = ?
        ORDER BY p.orden
    """, (ingrediente_id,)).fetchall()

    elegidas = {f["super"]: f["categoria"] for f in conn.execute(
        "SELECT super, categoria FROM ingrediente_categorias WHERE ingrediente_id = ?",
        (ingrediente_id,)).fetchall()}
    conn.close()

    columnas = []
    for nombre in SUPERS:
        del_super = [dict(p) for p in productos if p["super"] == nombre]
        # Los que no volvieron en la última búsqueda quedaron con el precio de
        # ese día. Se muestran igual, marcados, para poder reemplazarlos.
        ultima = max((p["fecha_scrape"] for p in del_super), default=None)
        for p in del_super:
            p["desactualizado"] = bool(ultima and p["fecha_scrape"] < ultima)
        elegida = elegidas.get(nombre)
        candidatos = [p for p in del_super if _en_categoria(p["categoria"], elegida)]
        confirmado = next((p for p in candidatos if p["confirmado"]), None)
        automatico = next((p for p in candidatos if p["tamano_efectivo"]), None)
        en_uso = confirmado or automatico
        columnas.append({
            "super": nombre,
            "en_uso": en_uso,
            "origen": None if not en_uso else ("confirmado" if confirmado else "automatico"),
            "candidatos": candidatos,
            "categoria": elegida,
            # Todas las categorías que trajo la tienda para este ingrediente,
            # con cuántos productos cayeron en cada una. Es lo que se ofrece
            # para elegir: son las de la tienda, no una lista inventada.
            "categorias": _categorias_encontradas(del_super),
        })

    return {"ingrediente": dict(ingrediente), "terminos": terminos, "supers": columnas}


def _en_categoria(categoria, elegida):
    """Un producto entra si está en la rama elegida, o si no se eligió ninguna."""
    if not elegida:
        return True
    return categoria == elegida or (categoria or "").startswith(elegida + " / ")


def _categorias_encontradas(productos):
    cuentas = {}
    for p in productos:
        if p["categoria"]:
            cuentas[p["categoria"]] = cuentas.get(p["categoria"], 0) + 1
    return [{"categoria": c, "productos": n}
            for c, n in sorted(cuentas.items(), key=lambda kv: (-kv[1], kv[0]))]


class NuevoIngrediente(BaseModel):
    nombre: str
    unidad_base: str
    terminos: list[str] = []


@app.post("/api/ingredientes")
def crear_ingrediente(body: NuevoIngrediente):
    """Crea el ingrediente y arranca su búsqueda de precios.

    Buscar es parte de crear, no una acción aparte: un ingrediente sin
    productos no sirve para nada y obligaba a un segundo botón.
    """
    conn = get_conn()
    if conn.execute("SELECT id FROM ingredientes WHERE nombre = ?",
                    (body.nombre.strip(),)).fetchone():
        conn.close()
        raise HTTPException(409, "Ya existe un ingrediente con ese nombre")

    cur = conn.execute("INSERT INTO ingredientes (nombre, unidad_base) VALUES (?, ?)",
                       (body.nombre.strip(), body.unidad_base))
    ingrediente_id = cur.lastrowid
    _guardar_terminos(conn, ingrediente_id, body.terminos or [body.nombre])
    conn.commit()
    conn.close()

    return {"id": ingrediente_id, **_lanzar_scrape([ingrediente_id], "ingrediente")}


def _guardar_terminos(conn, ingrediente_id, terminos):
    limpios = {t.strip().lower() for t in terminos if t.strip()}
    conn.execute("DELETE FROM terminos_busqueda WHERE ingrediente_id = ?", (ingrediente_id,))
    for termino in sorted(limpios):
        conn.execute(
            "INSERT OR IGNORE INTO terminos_busqueda (ingrediente_id, termino) VALUES (?, ?)",
            (ingrediente_id, termino))
    return sorted(limpios)


class Terminos(BaseModel):
    terminos: list[str]


@app.put("/api/ingredientes/{ingrediente_id}/terminos")
def editar_terminos(ingrediente_id: int, body: Terminos):
    """Corrige qué se escribe en el buscador de los supers y vuelve a buscar."""
    if not [t for t in body.terminos if t.strip()]:
        raise HTTPException(400, "Hace falta al menos un término de búsqueda")
    conn = get_conn()
    guardados = _guardar_terminos(conn, ingrediente_id, body.terminos)
    conn.commit()
    conn.close()
    return {"terminos": guardados, **_lanzar_scrape([ingrediente_id], "ingrediente")}


class Categorias(BaseModel):
    # {super: categoria}. Mandar null en un super lo deja sin filtro.
    categorias: dict[str, str | None]


@app.put("/api/ingredientes/{ingrediente_id}/categorias")
def editar_categorias(ingrediente_id: int, body: Categorias):
    """Fija en qué rama del catálogo de cada tienda vive este ingrediente."""
    desconocidos = set(body.categorias) - set(SUPERS)
    if desconocidos:
        raise HTTPException(400, f"Super desconocido: {', '.join(sorted(desconocidos))}")
    conn = get_conn()
    for nombre, categoria in body.categorias.items():
        if categoria:
            conn.execute("""
                INSERT INTO ingrediente_categorias (ingrediente_id, super, categoria)
                VALUES (?, ?, ?)
                ON CONFLICT (ingrediente_id, super) DO UPDATE SET categoria = excluded.categoria
            """, (ingrediente_id, nombre, categoria))
        else:
            conn.execute(
                "DELETE FROM ingrediente_categorias WHERE ingrediente_id = ? AND super = ?",
                (ingrediente_id, nombre))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/ingredientes/{ingrediente_id}")
def borrar_ingrediente(ingrediente_id: int):
    conn = get_conn()
    en_uso = conn.execute("SELECT COUNT(*) AS n FROM receta_ingredientes WHERE ingrediente_id = ?",
                          (ingrediente_id,)).fetchone()["n"]
    if en_uso:
        conn.close()
        raise HTTPException(409, f"Lo usan {en_uso} receta(s)")
    conn.execute("DELETE FROM ingredientes WHERE id = ?", (ingrediente_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class EleccionSuper(BaseModel):
    super: str
    producto_id: int


def _validar_producto(conn, producto_id, super_nombre, ingrediente_id=None):
    """El producto tiene que existir y ser del super que se está eligiendo.

    Un producto de otro super se guardaría igual y después se ignoraría al
    costear, que es exactamente el bug que este rediseño saca de la base.
    """
    producto = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if not producto:
        conn.close()
        raise HTTPException(404, "Producto no encontrado")
    if producto["super"] != super_nombre:
        conn.close()
        raise HTTPException(400, f"Ese producto es de {producto['super']}, no de {super_nombre}")
    if ingrediente_id is not None and producto["ingrediente_id"] != ingrediente_id:
        conn.close()
        raise HTTPException(400, "Ese producto es de otro ingrediente")
    return producto


@app.put("/api/ingredientes/{ingrediente_id}/eleccion")
def elegir_para_ingrediente(ingrediente_id: int, body: EleccionSuper):
    """Fija el producto por defecto del ingrediente en un super."""
    conn = get_conn()
    _validar_producto(conn, body.producto_id, body.super, ingrediente_id)
    conn.execute("""
        INSERT INTO elecciones (ingrediente_id, super, producto_id) VALUES (?, ?, ?)
        ON CONFLICT (ingrediente_id, super) DO UPDATE SET producto_id = excluded.producto_id
    """, (ingrediente_id, body.super, body.producto_id))
    conn.commit()
    conn.close()
    return {"ok": True}


class TamanoManual(BaseModel):
    tamano: float | None
    unidad: str | None


@app.put("/api/productos/{producto_id}/tamano")
def corregir_tamano(producto_id: int, body: TamanoManual):
    """Carga el tamaño a mano cuando no se puede sacar del nombre.

    Va a columnas aparte para que el próximo scrape no lo pise. Mandar
    tamano en null vuelve a lo que diga el scraper.
    """
    if body.tamano is not None and body.tamano <= 0:
        raise HTTPException(400, "El tamaño tiene que ser mayor a cero")
    if body.tamano is not None and body.unidad not in ("g", "ml", "un"):
        raise HTTPException(400, "Unidad inválida")
    conn = get_conn()
    conn.execute("UPDATE productos SET tamano_manual = ?, unidad_manual = ? WHERE id = ?",
                 (body.tamano, body.unidad if body.tamano is not None else None, producto_id))
    conn.commit()
    conn.close()
    return {"ok": True}


# --- recetas ---------------------------------------------------------------

@app.get("/api/recetas")
def listar_recetas():
    conn = get_conn()
    recetas = conn.execute("""
        SELECT r.*, COUNT(ri.id) AS cantidad_ingredientes
        FROM recetas r
        LEFT JOIN receta_ingredientes ri ON ri.receta_id = r.id
        GROUP BY r.id ORDER BY r.nombre
    """).fetchall()
    conn.close()
    return [dict(r) for r in recetas]


@app.get("/api/recetas/{receta_id}")
def ver_receta(receta_id: int):
    """La receta costeada en los tres supers. Es la pantalla entera."""
    resultado = costos.comparar_supers(receta_id)
    if not resultado:
        raise HTTPException(404, "Receta no encontrada")
    return resultado


class DatosReceta(BaseModel):
    nombre: str
    ganancia_pct: float = 100


@app.post("/api/recetas")
def crear_receta(body: DatosReceta):
    conn = get_conn()
    cur = conn.execute("INSERT INTO recetas (nombre, ganancia_pct) VALUES (?, ?)",
                       (body.nombre.strip(), body.ganancia_pct))
    conn.commit()
    receta_id = cur.lastrowid
    conn.close()
    return {"id": receta_id}


@app.put("/api/recetas/{receta_id}")
def editar_receta(receta_id: int, body: DatosReceta):
    conn = get_conn()
    conn.execute("UPDATE recetas SET nombre = ?, ganancia_pct = ? WHERE id = ?",
                 (body.nombre.strip(), body.ganancia_pct, receta_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/recetas/{receta_id}")
def borrar_receta(receta_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM recetas WHERE id = ?", (receta_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class Renglon(BaseModel):
    ingrediente_id: int
    cantidad: float
    unidad: str


@app.post("/api/recetas/{receta_id}/ingredientes")
def agregar_renglon(receta_id: int, body: Renglon):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO receta_ingredientes (receta_id, ingrediente_id, cantidad, unidad)
        VALUES (?, ?, ?, ?)
    """, (receta_id, body.ingrediente_id, body.cantidad, body.unidad))
    conn.commit()
    renglon_id = cur.lastrowid
    conn.close()
    return {"id": renglon_id}


class Cantidad(BaseModel):
    cantidad: float
    unidad: str


@app.put("/api/renglones/{renglon_id}")
def editar_renglon(renglon_id: int, body: Cantidad):
    conn = get_conn()
    conn.execute("UPDATE receta_ingredientes SET cantidad = ?, unidad = ? WHERE id = ?",
                 (body.cantidad, body.unidad, renglon_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/renglones/{renglon_id}")
def borrar_renglon(renglon_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM receta_ingredientes WHERE id = ?", (renglon_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class OverrideRenglon(BaseModel):
    # {super: producto_id}. Mandar los tres de una vez mantiene la comparación
    # pareja: un override en un solo super compara productos distintos.
    productos: dict[str, int]


@app.put("/api/renglones/{renglon_id}/eleccion")
def override_de_renglon(renglon_id: int, body: OverrideRenglon):
    """Pisa el producto por defecto solo para este renglón, por super."""
    desconocidos = set(body.productos) - set(SUPERS)
    if desconocidos:
        raise HTTPException(400, f"Super desconocido: {', '.join(sorted(desconocidos))}")
    conn = get_conn()
    renglon = conn.execute("SELECT ingrediente_id FROM receta_ingredientes WHERE id = ?",
                           (renglon_id,)).fetchone()
    if not renglon:
        conn.close()
        raise HTTPException(404, "Renglón no encontrado")
    for nombre, producto_id in body.productos.items():
        _validar_producto(conn, producto_id, nombre, renglon["ingrediente_id"])
    for nombre, producto_id in body.productos.items():
        conn.execute("""
            INSERT INTO renglon_elecciones (renglon_id, super, producto_id) VALUES (?, ?, ?)
            ON CONFLICT (renglon_id, super) DO UPDATE SET producto_id = excluded.producto_id
        """, (renglon_id, nombre, producto_id))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/renglones/{renglon_id}/eleccion")
def quitar_override_de_renglon(renglon_id: int):
    """Vuelve el renglón al producto por defecto del ingrediente."""
    conn = get_conn()
    conn.execute("DELETE FROM renglon_elecciones WHERE renglon_id = ?", (renglon_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


class Packaging(BaseModel):
    descripcion: str
    costo: float


@app.post("/api/recetas/{receta_id}/packaging")
def agregar_packaging(receta_id: int, body: Packaging):
    conn = get_conn()
    cur = conn.execute("INSERT INTO packaging (receta_id, descripcion, costo) VALUES (?, ?, ?)",
                       (receta_id, body.descripcion.strip(), body.costo))
    conn.commit()
    pack_id = cur.lastrowid
    conn.close()
    return {"id": pack_id}


@app.delete("/api/packaging/{packaging_id}")
def borrar_packaging(packaging_id: int):
    conn = get_conn()
    conn.execute("DELETE FROM packaging WHERE id = ?", (packaging_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
