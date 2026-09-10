import sqlite3

from .rutas import carpeta_de_datos

# Se resuelve en cada uso y no al importar: los tests la reemplazan por una
# base temporal, y empaquetado apunta a la carpeta de datos del usuario.
DB_PATH = carpeta_de_datos() / "psapo.db"

SUPERS = ("carrefour", "disco", "coto")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingredientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE,
    unidad_base TEXT NOT NULL CHECK (unidad_base IN ('g', 'ml', 'un'))
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id) ON DELETE CASCADE,
    super TEXT NOT NULL CHECK (super IN ('carrefour', 'disco', 'coto')),
    id_externo TEXT NOT NULL,
    marca TEXT,
    nombre_producto TEXT NOT NULL,
    -- Lo que el scraper pudo sacar del nombre del producto.
    tamano REAL,
    unidad TEXT,
    -- Corrección a mano, para cuando el nombre no declara el tamaño. Va en
    -- columnas aparte para que un re-scrape no la pise.
    tamano_manual REAL,
    unidad_manual TEXT,
    tamano_efectivo REAL GENERATED ALWAYS AS (COALESCE(tamano_manual, tamano)) VIRTUAL,
    unidad_efectiva TEXT GENERATED ALWAYS AS (COALESCE(unidad_manual, unidad)) VIRTUAL,
    precio REAL NOT NULL,
    precio_descuento REAL,
    texto_descuento TEXT,
    link TEXT,
    ean TEXT,
    imagen TEXT,
    -- Ruta de categoría tal como la nombra la tienda, con " / " entre niveles
    -- ("Lácteos / Mantecas y Margarinas / Manteca"). No se traduce ni se
    -- unifica entre tiendas: cada una tiene su taxonomía y sirve tal cual.
    categoria TEXT,
    -- Posición en los resultados del buscador del super. Es la mejor señal de
    -- relevancia disponible: ordenar por precio hunde el producto correcto.
    orden INTEGER NOT NULL DEFAULT 999,
    fecha_scrape TEXT NOT NULL,
    UNIQUE (super, id_externo)
);

-- Producto confirmado a mano para un ingrediente en un super. Vale para todas
-- las recetas; un renglón de receta puede pisarlo con renglon_elecciones.
CREATE TABLE IF NOT EXISTS elecciones (
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id) ON DELETE CASCADE,
    super TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    PRIMARY KEY (ingrediente_id, super)
);

CREATE TABLE IF NOT EXISTS recetas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    ganancia_pct REAL NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS receta_ingredientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id),
    cantidad REAL NOT NULL,
    unidad TEXT NOT NULL
);

-- Override de producto para un renglón puntual. Va por super igual que
-- `elecciones`: guardar un solo producto haría que el override valiera para su
-- super y se ignorara en los otros dos, comparando cosas distintas.
CREATE TABLE IF NOT EXISTS renglon_elecciones (
    renglon_id INTEGER NOT NULL REFERENCES receta_ingredientes(id) ON DELETE CASCADE,
    super TEXT NOT NULL,
    producto_id INTEGER NOT NULL REFERENCES productos(id) ON DELETE CASCADE,
    PRIMARY KEY (renglon_id, super)
);

-- Categoría elegida para un ingrediente en un super. Recorta qué productos
-- cuentan como ese ingrediente: sin esto, buscar "manteca" en Carrefour trae
-- "Medialunas de manteca", que está en /Panadería/ y no en /Lácteos/.
CREATE TABLE IF NOT EXISTS ingrediente_categorias (
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id) ON DELETE CASCADE,
    super TEXT NOT NULL,
    categoria TEXT NOT NULL,
    PRIMARY KEY (ingrediente_id, super)
);

CREATE TABLE IF NOT EXISTS packaging (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    receta_id INTEGER NOT NULL REFERENCES recetas(id) ON DELETE CASCADE,
    descripcion TEXT NOT NULL,
    costo REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS terminos_busqueda (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ingrediente_id INTEGER NOT NULL REFERENCES ingredientes(id) ON DELETE CASCADE,
    termino TEXT NOT NULL,
    UNIQUE (ingrediente_id, termino)
);

CREATE INDEX IF NOT EXISTS idx_productos_ingrediente ON productos(ingrediente_id);
CREATE INDEX IF NOT EXISTS idx_receta_ing_receta ON receta_ingredientes(receta_id);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _columnas(conn, tabla):
    return {f["name"] for f in conn.execute(f"PRAGMA table_xinfo({tabla})")}


def _migrar(conn):
    """Lleva una base vieja al esquema actual sin perder lo cargado a mano.

    `SCHEMA` usa CREATE TABLE IF NOT EXISTS, así que no alcanza para bases que
    ya existen. Los productos son re-scrapeables, pero las elecciones, recetas
    y términos no, así que se migran en vez de recrear la base.
    """
    productos = _columnas(conn, "productos")
    if "tamano_manual" not in productos:
        conn.execute("ALTER TABLE productos ADD COLUMN tamano_manual REAL")
    if "unidad_manual" not in productos:
        conn.execute("ALTER TABLE productos ADD COLUMN unidad_manual TEXT")
    if "tamano_efectivo" not in productos:
        conn.execute("ALTER TABLE productos ADD COLUMN tamano_efectivo REAL "
                     "GENERATED ALWAYS AS (COALESCE(tamano_manual, tamano)) VIRTUAL")
    if "unidad_efectiva" not in productos:
        conn.execute("ALTER TABLE productos ADD COLUMN unidad_efectiva TEXT "
                     "GENERATED ALWAYS AS (COALESCE(unidad_manual, unidad)) VIRTUAL")

    if "categoria" not in productos:
        conn.execute("ALTER TABLE productos ADD COLUMN categoria TEXT")

    if "producto_elegido_id" in _columnas(conn, "receta_ingredientes"):
        # El override viejo guardaba un producto suelto: se convierte en el
        # override del super al que ese producto pertenece.
        conn.execute("""
            INSERT OR IGNORE INTO renglon_elecciones (renglon_id, super, producto_id)
            SELECT ri.id, p.super, p.id
            FROM receta_ingredientes ri
            JOIN productos p ON p.id = ri.producto_elegido_id
        """)
        conn.execute("ALTER TABLE receta_ingredientes DROP COLUMN producto_elegido_id")


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrar(conn)
    conn.commit()
    conn.close()
