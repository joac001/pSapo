import re
from dataclasses import dataclass, asdict

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# Orden importa: kg/l antes que g/ml para que la alternancia no corte corto.
_SIZE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kilogramos|kilogramo|kilos|kilo|kgms|kgm|kgs|kg|"
    r"litros|litro|ltrs|ltr|lts|lt|l|"
    r"gramos|gramo|grms|grm|grs|gr|g|mililitros|centimetros cubicos|cm3|cmq|ccs|cc|mls|ml|"
    r"unidades|unidad|unids|unid|unis|uni|uds|ud|un|u)\b",
    re.IGNORECASE,
)

_UNIT_MAP = {
    "kilogramos": ("g", 1000), "kilogramo": ("g", 1000), "kilos": ("g", 1000),
    "kilo": ("g", 1000), "kgms": ("g", 1000), "kgm": ("g", 1000),
    "kgs": ("g", 1000), "kg": ("g", 1000),
    "gramos": ("g", 1), "gramo": ("g", 1), "grms": ("g", 1), "grm": ("g", 1),
    "grs": ("g", 1), "gr": ("g", 1), "g": ("g", 1),
    "litros": ("ml", 1000), "litro": ("ml", 1000), "ltrs": ("ml", 1000),
    "ltr": ("ml", 1000), "lts": ("ml", 1000), "lt": ("ml", 1000), "l": ("ml", 1000),
    "mililitros": ("ml", 1), "mls": ("ml", 1), "ml": ("ml", 1),
    "centimetros cubicos": ("ml", 1), "cm3": ("ml", 1), "cmq": ("ml", 1),
    "ccs": ("ml", 1), "cc": ("ml", 1),
    "unidades": ("un", 1), "unidad": ("un", 1), "unids": ("un", 1),
    "unid": ("un", 1), "unis": ("un", 1), "uni": ("un", 1),
    "uds": ("un", 1), "ud": ("un", 1), "un": ("un", 1), "u": ("un", 1),
}


# Productos pesables: el nombre no trae cantidad, el precio es por kilo o por
# litro ("Banana selección x kg.", "Limón x Kg", "Nuez Pelada Mariposa X Kg").
_POR_PESO_RE = re.compile(
    r"\b(?:x|por)\s*(kilogramo|kilos?|kgs?|k)\b|\bgranel\b", re.IGNORECASE)
_POR_VOLUMEN_RE = re.compile(r"\b(?:x|por)\s*(litros?|lts?|l)\b", re.IGNORECASE)


def parse_tamano(nombre):
    """Extrae (cantidad, unidad_normalizada) del nombre del producto.

    El tamaño casi nunca viene en un campo propio: está embebido en el nombre
    ("Crema para Batir 200 Ml Las Tres Niñas"). Devuelve (None, None) si no
    se puede determinar, y en ese caso la UI pide cargarlo a mano.
    """
    nombre = nombre or ""
    matches = _SIZE_RE.findall(nombre)
    if not matches:
        # Sin cantidad declarada, pero vendido por peso o volumen: el precio
        # corresponde a 1 kg o 1 litro.
        if _POR_PESO_RE.search(nombre):
            return 1000.0, "g"
        if _POR_VOLUMEN_RE.search(nombre):
            return 1000.0, "ml"
        return None, None
    # Primer match: los packs vienen como "357 g. Pack x3 de 119 g", donde el
    # total comprado (357) va primero y el desglose por unidad después.
    valor, unidad_raw = matches[0]
    unidad, factor = _UNIT_MAP[unidad_raw.lower()]
    return float(valor.replace(",", ".")) * factor, unidad


_ACENTOS = str.maketrans("áéíóúüñ", "aeiouun")

# Palabras que delatan otra categoría de producto. Los buscadores de los supers
# devuelven cualquier cosa que mencione el ingrediente ("Yogur bebible banana",
# "Licor dulce de leche", "Nuez moscada"), y además no son estables entre
# corridas, así que no alcanza con quedarse con el primer resultado.
RUIDO = {
    "yogur", "yogurisimo", "bebible", "jugo", "gaseosa", "cerveza", "vino",
    "licor", "aperitivo", "barra", "barrita", "cereal", "granola", "snack",
    "helado", "postre", "flan", "budin", "bizcochuelo", "magdalena", "muffin",
    "alfajor", "oblea", "galletita", "medialuna", "factura", "pizza",
    "shampoo", "acondicionador", "jabon", "perfume", "fragancia", "crema corporal",
    "desodorante", "pañal", "papel", "rollo", "servilleta", "lavandina",
    "moscada", "saborizado", "chip", "relleno", "bombon", "chocolatada",
    "infusion", "caramelo", "gomita", "turron", "mermelada", "pizzeta",
    "empanada", "fideo", "tallarin", "filet", "merluza", "milanesa", "rebozado",
    "hamburguesa", "salchicha", "pate", "congelado", "bananita", "sorpresa",
    "kinder", "palmerita", "grisin", "tostada", "figacita", "figazza",
    "criollita", "scon", "bizcocho", "pepa", "churro", "masita",
    # Versiones dietéticas: son el ingrediente, pero no rinden igual en
    # repostería, así que no sirven como elección por defecto.
    "light", "dietetico", "edulcorante", "stevia", "zero",
}

_IGNORAR = {"x", "de", "la", "el", "los", "las", "por", "kg", "kilo", "gr",
            "g", "ml", "cc", "un", "para", "y", "con", "sin", "tipo", "en"}


def normalizar(texto):
    return (texto or "").lower().translate(_ACENTOS)


def _lema(palabra):
    """Quita el plural simple para que 'chocolinas' matchee 'Chocolina'."""
    for sufijo in ("es", "s"):
        if len(palabra) > 4 and palabra.endswith(sufijo):
            return palabra[: -len(sufijo)]
    return palabra


def _palabras(texto):
    return {_lema(t) for t in re.split(r"[^a-z0-9]+", normalizar(texto))
            if t and t not in _IGNORAR and not t.isdigit() and len(t) > 2}


def tokens_significativos(termino):
    """Palabras del término que el producto tiene que mencionar."""
    return _palabras(termino)


def es_relevante(nombre_producto, terminos):
    """True si el producto corresponde a alguno de los términos buscados.

    Pide que el nombre mencione todas las palabras significativas de algún
    término, y descarta las categorías de RUIDO — salvo que esa palabra sea
    parte del término, para que buscar "galletitas chocolina" siga
    encontrando galletitas.
    """
    del_producto = _palabras(nombre_producto)
    if not del_producto:
        return False

    buscadas = set()
    coincide = False
    for termino in terminos:
        tokens = tokens_significativos(termino)
        buscadas |= tokens
        if tokens and tokens <= del_producto:
            coincide = True

    if not coincide:
        return False

    ruido = {_lema(r) for r in RUIDO} - buscadas
    return not (del_producto & ruido)


SEPARADOR_CATEGORIA = " / "


def ruta_categoria(partes):
    """Arma la ruta de categoría a partir de los nombres de cada nivel.

    Se guarda la ruta completa y tal cual la nombra la tienda, sin traducir ni
    unificar: las tres usan taxonomías distintas y cualquier mapeo propio
    envejece mal. Guardar la ruta entera permite elegir un nivel ancho
    ("Lácteos") o angosto ("Lácteos / Mantecas y Margarinas / Manteca").
    """
    limpias = [str(p).strip() for p in partes if p and str(p).strip()]
    return SEPARADOR_CATEGORIA.join(limpias) or None


@dataclass
class Producto:
    super: str
    id_externo: str
    marca: str | None
    nombre_producto: str
    tamano: float | None
    unidad: str | None
    precio: float
    precio_descuento: float | None
    texto_descuento: str | None
    link: str | None
    ean: str | None
    imagen: str | None
    categoria: str | None = None

    def as_dict(self):
        return asdict(self)
