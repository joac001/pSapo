"""Carga inicial con los datos del Google Sheet que se usaba a mano."""

from .db import get_conn, init_db

# Los términos se eligieron probando contra los tres supers: sus buscadores
# son literales y sensibles al plural ("Bananas" devuelve yogur de banana,
# "banana" devuelve la fruta). Los que están acá son los que devolvieron el
# producto correcto como primer resultado en los tres.
INGREDIENTES = [
    ("Bananas", "g", ["banana"]),
    ("Harina", "g", ["harina 000", "harina 0000"]),
    ("Esencia de vainilla", "ml", ["esencia de vainilla"]),
    ("Coco rallado", "g", ["coco rallado"]),
    ("Azucar", "g", ["azucar comun 1kg", "azucar ledesma"]),
    ("Azucar impalpable", "g", ["azucar impalpable"]),
    ("Vino de postre", "ml", ["marsala", "vino marsala"]),
    ("Mascarpone", "g", ["queso mascarpone"]),
    ("Crema de leche", "ml", ["crema de leche"]),
    ("Vainillas", "g", ["vainillas"]),
    ("Cafe molido", "g", ["cafe molido"]),
    ("Huevos", "un", ["huevos"]),
    ("Manteca", "g", ["manteca"]),
    ("Aceite", "ml", ["aceite girasol"]),
    ("Cacao en polvo", "g", ["cacao amargo"]),
    ("Dulce de leche", "g", ["dulce de leche repostero"]),
    ("Leche condensada", "g", ["leche condensada"]),
    ("Limones", "g", ["limon x kg"]),
    ("Nueces", "g", ["nuez mariposa"]),
    ("Polvo de hornear", "g", ["polvo de hornear"]),
    ("Margarina", "g", ["margarina", "margarina reposteria"]),
    ("Leche", "ml", ["leche entera"]),
    ("Galletitas de chocolate", "g", ["galletitas chocolina"]),
    ("Queso crema", "g", ["queso crema casancrem"]),
    ("Premezcla sin tacc", "g", ["premezcla sin tacc"]),
]

# (nombre, ganancia_pct, [(ingrediente, cantidad, unidad)], [(packaging, costo)])
RECETAS = [
    # Frutas: se venden por kilo, así que la receta va en gramos aunque en el
    # Sheet estuvieran en unidades (2 bananas ≈ 240 g, 2 limones ≈ 200 g).
    ("Budin de banana", 100, [
        ("Bananas", 240, "g"),
        ("Harina", 200, "g"),
        ("Esencia de vainilla", 15, "ml"),
        ("Coco rallado", 50, "g"),
        ("Azucar", 100, "g"),
    ], [("Bandeja rectangular", 152)]),

    ("Tiramisu", 50, [
        ("Azucar", 150, "g"),
        ("Esencia de vainilla", 15, "ml"),
        ("Mascarpone", 400, "g"),
        ("Crema de leche", 300, "ml"),
        ("Vainillas", 222, "g"),
        ("Cafe molido", 15, "g"),
        ("Vino de postre", 30, "ml"),
    ], []),

    ("Chocotorta", 50, [
        ("Galletitas de chocolate", 750, "g"),
        ("Dulce de leche", 700, "g"),
        ("Queso crema", 600, "g"),
        ("Leche", 200, "ml"),
        ("Cafe molido", 10, "g"),
    ], []),

    ("Brownie con dulce de leche", 100, [
        ("Azucar", 160, "g"),
        ("Esencia de vainilla", 15, "ml"),
        ("Harina", 80, "g"),
        ("Huevos", 3, "un"),
        ("Cacao en polvo", 50, "g"),
        ("Manteca", 150, "g"),
        ("Dulce de leche", 200, "g"),
        ("Crema de leche", 200, "ml"),
    ], [("Bandeja rectangular con tapa", 152)]),

    ("Lemon pie", 100, [
        ("Huevos", 5, "un"),
        ("Azucar", 300, "g"),
        ("Leche condensada", 395, "g"),
        ("Harina", 250, "g"),
        ("Manteca", 50, "g"),
        ("Limones", 200, "g"),
        ("Esencia de vainilla", 15, "ml"),
    ], []),

    ("Alfajores de nuez", 100, [
        ("Huevos", 2, "un"),
        ("Azucar", 40, "g"),
        ("Aceite", 60, "ml"),
        ("Nueces", 160, "g"),
        ("Esencia de vainilla", 10, "ml"),
        ("Harina", 300, "g"),
        ("Polvo de hornear", 10, "g"),
        ("Dulce de leche", 300, "g"),
    ], [("Macaroon (alfajores)", 400)]),
]


def cargar():
    init_db()
    conn = get_conn()

    for nombre, unidad, terminos in INGREDIENTES:
        conn.execute(
            "INSERT OR IGNORE INTO ingredientes (nombre, unidad_base) VALUES (?, ?)",
            (nombre, unidad),
        )
        ing_id = conn.execute(
            "SELECT id FROM ingredientes WHERE nombre = ?", (nombre,)
        ).fetchone()["id"]
        for t in terminos:
            conn.execute(
                "INSERT OR IGNORE INTO terminos_busqueda (ingrediente_id, termino) VALUES (?, ?)",
                (ing_id, t),
            )

    for nombre, ganancia, ingredientes, packs in RECETAS:
        existe = conn.execute("SELECT id FROM recetas WHERE nombre = ?", (nombre,)).fetchone()
        if existe:
            continue
        cur = conn.execute(
            "INSERT INTO recetas (nombre, ganancia_pct) VALUES (?, ?)", (nombre, ganancia)
        )
        receta_id = cur.lastrowid
        for ing_nombre, cantidad, unidad in ingredientes:
            ing = conn.execute(
                "SELECT id FROM ingredientes WHERE nombre = ?", (ing_nombre,)
            ).fetchone()
            conn.execute("""
                INSERT INTO receta_ingredientes (receta_id, ingrediente_id, cantidad, unidad)
                VALUES (?, ?, ?, ?)
            """, (receta_id, ing["id"], cantidad, unidad))
        for desc, costo in packs:
            conn.execute(
                "INSERT INTO packaging (receta_id, descripcion, costo) VALUES (?, ?, ?)",
                (receta_id, desc, costo),
            )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    cargar()
    print("Datos iniciales cargados.")
