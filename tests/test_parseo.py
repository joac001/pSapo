import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.scrapers.common import es_relevante, parse_tamano
from app.scrapers.coto import _a_float


def verificar(nombre, esperado):
    obtenido = parse_tamano(nombre)
    assert obtenido == esperado, f"{nombre!r}: esperaba {esperado}, obtuve {obtenido}"


def test_tamanos_reales():
    # Nombres tomados de resultados reales de los tres supers.
    verificar("Crema para Batir 200 Ml Las Tres Niñas", (200.0, "ml"))
    verificar("Crema De Leche ILOLAY 200 CC", (200.0, "ml"))
    verificar("Crema Leche MILKAUT 330cc", (330.0, "ml"))
    verificar("Crema De Leche Pasteurizada Tonadita 350cmq", (350.0, "ml"))
    verificar("Harina De Trigo PUREZA 0000 Paquete 1 Kg", (1000.0, "g"))
    verificar("Aceite Girasol CAÑUELAS Botella 1.5 L", (1500.0, "ml"))
    verificar("Huevo blanco grande maple CYCLAMEN 30u", (30.0, "un"))
    verificar("Huevo blanco El Mercado carton 30 uni", (30.0, "un"))
    verificar("Huevos blancos El Mercado 6 uni", (6.0, "un"))
    verificar("Vainillas Bimbo 36 uni", (36.0, "un"))
    verificar("Vainillas COTO 444g", (444.0, "g"))
    verificar("Azúcar Superior Real LEDESMA 1kg", (1000.0, "g"))


def test_pack_usa_el_total_no_el_desglose():
    verificar("Galletitas Pepitos con chips de chocolate 357 g. Pack x3 de 119 g.", (357.0, "g"))


def test_vendido_por_peso_vale_un_kilo():
    # Sin cantidad en el nombre, el precio es el del kilo.
    verificar("Banana selección x kg.", (1000.0, "g"))
    verificar("Limón x Kg", (1000.0, "g"))
    verificar("Nuez Pelada Mariposa X Kg", (1000.0, "g"))
    verificar("Chips De Banana Granel", (1000.0, "g"))


def test_peso_declarado_gana_sobre_por_kilo():
    # Acá el precio es el de la pieza, no el del kilo.
    verificar("Limón COSECHA CRIOLLA (Peso Aproximado 1.5 Kg)", (1500.0, "g"))


def test_gramos_como_los_escribe_coto():
    # Coto abrevia gramos como "Grm", que cortaba el match por el \b del final.
    verificar("Manteca La Paulina 500 Grm", (500.0, "g"))
    verificar("Manteca Salada CONAPROLE Paq 200 Grm", (200.0, "g"))
    verificar("Dulce de leche Ilolay 400 Grms", (400.0, "g"))


def test_abreviaturas_propias_de_coto():
    # Coto escribe litros como "Ltr", kilos como "Kgm" y centímetros cúbicos
    # como "cm3"; ninguna entraba y el producto quedaba sin tamaño.
    verificar("Leche Entera 3% CASANTO 1 Ltr", (1000.0, "ml"))
    verificar("Harina Trigo 000 FAVORITA Paq 1 Kgm", (1000.0, "g"))
    verificar("Crema Para Cocinar LA SERENISIMA 200cm3", (200.0, "ml"))


def test_los_metros_de_papel_no_son_un_tamano():
    # "Papel manteca x 5 mts" aparece buscando manteca: son metros de papel,
    # no una cantidad de ingrediente, así que tiene que quedar sin tamaño.
    verificar("Papel manteca Rolopac x 5 mts", (None, None))
    verificar("Rollo Papel Manteca Separata  5mts x 28 cm", (None, None))


def test_sin_tamano_declarado():
    verificar("Queso Cremoso Barraza", (None, None))
    verificar("Bondiola congelada", (None, None))


def test_precio_coto_con_punto_decimal():
    assert _a_float("$2279.25") == 2279.25
    assert _a_float("$4338.75") == 4338.75


def test_precio_coto_con_separador_de_miles():
    assert _a_float("$1.234,56") == 1234.56


def test_precio_invalido():
    assert _a_float(None) is None
    assert _a_float("") is None
    assert _a_float("consultar") is None


def test_acepta_el_ingrediente_buscado():
    assert es_relevante("Banana Cavendish X Kg", ["banana"])
    assert es_relevante("Limón x Kg", ["limon x kg"])
    assert es_relevante("Nuez Pelada Mariposa X Kg", ["nuez mariposa"])
    assert es_relevante("Manteca COTO 200 Gr", ["manteca"])
    assert es_relevante("Margarina en pan Margadan 200 g.", ["margarina"])
    assert es_relevante("Dulce de leche repostero La Serenísima 400 g.",
                        ["dulce de leche repostero"])
    assert es_relevante("Crema De Leche Clasica Para Cocinar 200 Ml", ["crema de leche"])


def test_descarta_otra_categoria_que_menciona_el_ingrediente():
    assert not es_relevante("Yogurísimo Griego Bebible Banana 190g", ["banana"])
    assert not es_relevante("Nestlé Bananita Dolca 30 grs", ["banana"])
    assert not es_relevante("Filet de Merluza Congelado Rebozado Limón", ["limon x kg"])
    assert not es_relevante("Gaseosa lima limón Sprite Zero", ["limon x kg"])
    assert not es_relevante("Nuez Moscada Molida La Parmesana", ["nuez mariposa"])
    assert not es_relevante("Huevo de chocolate sorpresa Kinder", ["huevos"])
    assert not es_relevante("Medialunas de manteca x 6 u.", ["manteca"])
    assert not es_relevante("Obleas de vainillas Bulnez 50 g.", ["vainillas"])
    assert not es_relevante("Licor Cusenier dulce de leche 700 cc",
                            ["dulce de leche repostero"])


def test_ruido_no_aplica_cuando_es_el_ingrediente_mismo():
    # "galletita" es ruido en general, pero acá es lo que se busca.
    assert es_relevante("Galletitas Chocolina de chocolate 250 g.",
                        ["galletitas chocolina"])
    assert es_relevante("Premezcla MAIZENA Brownies Sin Tacc 500 G",
                        ["premezcla sin tacc"])


def test_plural_del_termino_matchea_singular_del_producto():
    assert es_relevante("Huevo Blanco Grande 6u", ["huevos"])


if __name__ == "__main__":
    for nombre, funcion in sorted(globals().items()):
        if nombre.startswith("test_"):
            funcion()
            print("ok", nombre)
