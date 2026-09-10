import uuid

import requests

from .common import UA, Producto, parse_tamano, ruta_categoria

# Key pública del cliente Constructor.io de Coto, extraída del bundle JS del
# sitio. Si Coto la rota, se vuelve a leer de cnstrc.com/js/cust/coto_*.js
API_KEY = "key_r6xzz4IAoTWcipni"
SEARCH_URL = "https://ac.cnstrc.com/search/{query}"
PRODUCT_URL = "https://www.coto.com.ar/productos/{path}"


def buscar(termino, limite=40):
    resp = requests.get(
        SEARCH_URL.format(query=requests.utils.quote(termino)),
        params={
            "c": "cio-ui-autocomplete-1.29.3",
            "key": API_KEY,
            "i": str(uuid.uuid4()),
            "s": "1",
            "num_results_per_page": limite,
        },
        headers={"User-Agent": UA, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    resultados = resp.json()["response"]["results"]
    return [_a_producto(r) for r in resultados]


def _a_producto(res):
    data = res["data"]

    # `store_availability` lista las sucursales que lo tienen. Vacía es "en
    # ninguna": no sirve para costear una compra. Si el campo no viene, no se
    # asume nada y el producto entra igual.
    sucursales = data.get("store_availability")
    if isinstance(sucursales, list) and not sucursales:
        return None

    nombre = data.get("sku_display_name") or res.get("value") or ""
    tamano, unidad = parse_tamano(nombre)

    descuentos = data.get("discounts") or []
    texto_descuento = None
    precio_descuento = None
    if descuentos:
        d = descuentos[0]
        texto_descuento = d.get("discountText")
        precio_descuento = _a_float(d.get("discountPrice"))

    url = data.get("url") or ""
    return Producto(
        super="coto",
        id_externo=str(data.get("id")),
        marca=data.get("product_brand"),
        nombre_producto=nombre,
        tamano=tamano,
        unidad=unidad,
        precio=float(data["product_list_price"]),
        precio_descuento=precio_descuento,
        texto_descuento=texto_descuento,
        link=PRODUCT_URL.format(path=url.lstrip("/")),
        ean=str(data["product_main_ean"]) if data.get("product_main_ean") else None,
        imagen=data.get("image_url"),
        categoria=_categoria(data),
    )


def _categoria(data):
    """Coto manda todos los niveles sueltos; el más hondo trae el `path_list` más largo.

    El grupo "Mantecas" llega con path_list [Categorias, Frescos, Lácteos,
    Mantecas y Margarinas], así que la ruta completa se arma con esos nombres
    más el propio. Se descarta el nivel raíz "Categorias", que no informa nada.
    """
    grupos = data.get("groups") or []
    if not grupos:
        return None
    mas_hondo = max(grupos, key=lambda g: len(g.get("path_list") or []))
    niveles = [n.get("display_name") for n in (mas_hondo.get("path_list") or [])[1:]]
    return ruta_categoria([*niveles, mas_hondo.get("display_name")])


def _a_float(texto):
    """Convierte '$2279.25' o '$1.234,56' a float.

    Coto mezcla formatos: si hay coma, es el separador decimal y los puntos
    son de miles; si no hay coma, el punto es el decimal.
    """
    if not texto:
        return None
    limpio = str(texto).replace("$", "").replace(" ", "").strip()
    if "," in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return None
