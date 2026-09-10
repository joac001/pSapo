import json
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from .common import UA, Producto, parse_tamano, ruta_categoria

TIENDAS = {
    "carrefour": "https://www.carrefour.com.ar",
    "disco": "https://www.disco.com.ar",
}

SEARCH_PATH = "/_v/api/intelligent-search/product_search?query={query}&count={count}"
# Carrefour devuelve recordsFiltered:0 en intelligent-search para varios
# términos que su propia web sí encuentra ("azucar", "mascarpone"). El
# catálogo viejo los encuentra, así que se usa como fallback.
FALLBACK_PATH = "/api/catalog_system/pub/products/search?ft={query}&_from=0&_to={ultimo}"

# Estos sitios rechazan requests HTTP directos por fingerprint TLS
# ("Bad Request! Scripts are not allowed!"), incluso con headers de navegador,
# y también rechazan navegar la URL de API en forma directa. Lo único que
# funciona es cargar la home primero y hacer fetch desde el contexto de página.
_FETCH_JS = """async (url) => {
    const r = await fetch(url, {headers: {"Accept": "application/json"}});
    return await r.text();
}"""


class VtexScraper:
    """Mantiene un browser abierto para reusar la sesión entre búsquedas."""

    def __init__(self, headless=True):
        self._headless = headless
        self._pw = None
        self._browser = None
        self._paginas = {}

    def __enter__(self):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self._headless)
        return self

    def __exit__(self, *exc):
        self._browser.close()
        self._pw.stop()

    def _pagina(self, tienda):
        if tienda not in self._paginas:
            page = self._browser.new_page(user_agent=UA)
            page.goto(TIENDAS[tienda] + "/", timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            self._paginas[tienda] = page
        return self._paginas[tienda]

    def buscar(self, tienda, termino, limite=40):
        base = TIENDAS[tienda]
        page = self._pagina(tienda)
        q = quote(termino)

        url = base + SEARCH_PATH.format(query=q, count=limite)
        productos = json.loads(page.evaluate(_FETCH_JS, url)).get("products", [])

        if not productos:
            url = base + FALLBACK_PATH.format(query=q, ultimo=limite - 1)
            respuesta = json.loads(page.evaluate(_FETCH_JS, url))
            productos = respuesta if isinstance(respuesta, list) else []

        return [_a_producto(tienda, base, p) for p in productos]


def _a_producto(tienda, base, prod):
    item = (prod.get("items") or [{}])[0]
    seller = (item.get("sellers") or [{}])[0]
    offer = seller.get("commertialOffer") or {}

    # Sin stock los precios vienen basura: "Azúcar Ledesma Molida X 1 Kg" a
    # $7.89 y otros a $0. Además no se puede costear con algo que no se puede
    # comprar, así que se descartan.
    if seller.get("commertialOffer") and offer.get("AvailableQuantity") == 0:
        return None

    precio = offer.get("PriceWithoutDiscount") or offer.get("Price")
    if not precio:
        return None

    # ListPrice de Disco viene corrupto (247934 para un producto de $3000),
    # así que solo se considera descuento cuando Price es menor a PriceWithoutDiscount.
    precio_actual = offer.get("Price")
    tiene_descuento = precio_actual is not None and precio_actual < precio
    teasers = [t.get("name") for t in (offer.get("Teasers") or []) if t.get("name")]

    nombre = prod.get("productName") or ""
    tamano, unidad = parse_tamano(nombre)

    return Producto(
        super=tienda,
        id_externo=str(prod.get("productId")),
        marca=prod.get("brand"),
        nombre_producto=nombre,
        tamano=tamano,
        unidad=unidad,
        precio=float(precio),
        precio_descuento=float(precio_actual) if tiene_descuento else None,
        texto_descuento=", ".join(teasers) if teasers else None,
        link=f"{base}/{prod.get('linkText')}/p" if prod.get("linkText") else None,
        ean=item.get("ean"),
        imagen=(item.get("images") or [{}])[0].get("imageUrl"),
        categoria=_categoria(prod),
    )


def _categoria(prod):
    """VTEX manda las rutas de más profunda a más general, con barras.

    ["/Lácteos/Mantecas y Margarinas/Manteca/", "/Lácteos/Mantecas y Margarinas/",
     "/Lácteos/"] -> "Lácteos / Mantecas y Margarinas / Manteca"
    """
    rutas = prod.get("categories") or []
    if not rutas:
        return None
    mas_profunda = max(rutas, key=lambda r: r.count("/"))
    return ruta_categoria(mas_profunda.split("/"))
