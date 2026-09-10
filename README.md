# pSapo

Calculadora de costos y precios de venta para repostería. Scrapea los precios
reales de tres supermercados argentinos (Carrefour, Disco, Coto), los asocia a
los ingredientes de cada receta y muestra cuánto sale producirla en cada uno y
a qué precio conviene venderla.

Reemplaza una planilla de Google Sheets que se actualizaba a mano.

## El trabajo que resuelve

> "Voy a vender una torta → miro en qué supermercado me sale menos hacerla →
> presupuesto con ese."

La comparación entre supermercados es el resultado principal, no una vista
secundaria. Todo lo demás existe para que esa comparación sea confiable.

De ahí salen dos reglas que atraviesan el código:

1. **Todo se elige por supermercado.** Los tres niveles de resolución de
   producto — override del renglón, elección del ingrediente, automático por
   relevancia — están indexados por `(algo, super)`. Un producto elegido para
   Carrefour no participa del costo de Coto.
2. **Un supermercado incompleto no compite.** Si no se puede costear algún
   renglón, esa columna queda sin total y sin precio de venta. Un total parcial
   es peor que ninguno: hace que el supermercado al que le faltan ingredientes
   parezca el más barato.

## Cómo funciona

```
ingrediente ──(términos)──▶ scrapers ──▶ productos (por super)
                                             │
                    ┌────────────────────────┴────────────────────────┐
                    │  override del renglón  →  elección del          │
                    │  (solo esta receta)       ingrediente  →  automático
                    │                           (todas las recetas)   │
                    └────────────────────────┬────────────────────────┘
                                             │  (los tres, por super)
receta ──(cantidad, unidad)──────────────────┴──▶ costo por super ──▶ precio de venta
```

## Pantallas

Tres, sin modos de edición. Cada funcionalidad tiene **un solo punto de
entrada**: si hace falta desde otro lado, se navega hasta el dueño, no se
duplica el botón.

| Pantalla | Es dueña de |
|---|---|
| **Receta** — una tabla comparativa, filas = ingredientes, columnas = los tres supermercados | Composición de la receta, cantidades (edición en la misma celda), packaging, margen, y el override de producto para un renglón |
| **Ingrediente** — tres columnas fijas, una por supermercado | El producto por defecto del ingrediente en cada supermercado, confirmar el automático, términos de búsqueda, tamaño cargado a mano |
| **Cabecera** | El único disparador del scraping, con alcance: todo / solo los vencidos / un ingrediente |

La pantalla de ingrediente no tiene filtro "todos los supermercados": la
columna *es* el supermercado. Mezclarlos en una sola grilla hacía leer tres
elecciones independientes como si fueran una sola.

Buscar precios de un ingrediente puntual ocurre además como efecto de crear el
ingrediente o corregir sus términos, porque un ingrediente sin productos no
sirve para nada. No es una acción aparte.

### Lo que la pantalla recuerda

Cada acción vuelve a pedir los datos y repinta. Para que eso no se sienta como
volver a empezar, sobreviven al repintado, en `estado`: la pestaña y el ítem
abierto (en la URL, con `replaceState`, para que recargar no devuelva a
Recetas), el texto del buscador de productos, el criterio de orden, qué listas
están desplegadas y la posición de la página. Cambiar de ingrediente sí limpia
el buscador y los desplegados: es un contexto nuevo.

## Arquitectura

```
app/
├── main.py            API FastAPI + servido de la SPA estática
├── db.py              Conexión SQLite, esquema y migraciones idempotentes
├── seed.py            Carga inicial: ingredientes, términos y recetas
├── costos.py          Costeo por super y comparación entre los tres
├── scraping.py        Orquestación del scraping y persistencia de productos
├── scrapers/
│   ├── common.py      Modelo Producto, parseo de tamaños, filtro de relevancia
│   ├── vtex.py        Carrefour y Disco (plataforma VTEX, vía Playwright)
│   └── coto.py        Coto (API de Constructor.io, vía requests)
└── static/            Frontend vanilla: index.html, app.js, estilo.css
tests/
├── test_parseo.py     Parseo de tamaños, relevancia y precios
└── test_costos.py     Costeo, resolución de producto, comparación y migración
psapo.db               Base SQLite (se crea sola al arrancar)
```

No hay ORM, ni framework de frontend, ni build step. La base es un archivo
SQLite en la raíz del proyecto.

### Capas

**`db.py`** define el esquema como DDL con `IF NOT EXISTS` y lo ejecuta en cada
arranque, seguido de `_migrar()`, que lleva una base vieja al esquema actual
sin recrearla: los productos son re-scrapeables, pero las elecciones, recetas y
términos no. Las migraciones son idempotentes y se chequean con
`PRAGMA table_xinfo` (`table_info` no lista las columnas generadas).

**`scrapers/`** son adaptadores. Cada uno recibe un término de búsqueda y
devuelve una lista de `Producto` (dataclass en `common.py`) normalizada, sin
saber nada de la base ni de recetas.

- `vtex.py` cubre Carrefour y Disco. Ambos corren VTEX y rechazan requests HTTP
  directos por fingerprint TLS (`Bad Request! Scripts are not allowed!`), así
  que se levanta un Chromium con Playwright, se carga la home y se hace `fetch`
  desde el contexto de la página. `VtexScraper` es un context manager que
  mantiene el browser y una página por tienda abiertos para reusar sesión entre
  búsquedas. Si `intelligent-search` devuelve vacío (pasa con varios términos
  que la web sí encuentra), cae al endpoint viejo de `catalog_system`.
- `coto.py` usa la API pública de Constructor.io con la key extraída del bundle
  JS del sitio. No necesita browser.

**`scraping.py`** orquesta. Por ingrediente acumula los resultados de todos sus
términos en todos los supermercados, los deduplica por `id_externo` y recién
ahí los guarda. El orden de guardado es el criterio de relevancia y se aplica
sobre el conjunto completo: primero los que pasan `es_relevante`, después los
que están en la unidad base del ingrediente, después los que tienen tamaño
parseable. Ese índice se persiste en `productos.orden`.

**`scrapers/`** descartan lo que no se puede comprar: en VTEX,
`AvailableQuantity == 0` (sin stock los precios vienen basura — se vio
`Azúcar Ledesma Molida X 1 Kg` a $7.89 y otros a $0); en Coto, un
`store_availability` vacío, que significa "en ninguna sucursal".

**`costos.py`** no consulta la red. Lee productos ya guardados y calcula.
`comparar_supers` devuelve las tres columnas siempre en el mismo orden — para
que la tabla no se mueva entre recargas — con `completo`, `gana` y
`diferencia` ya resueltos.

**`main.py`** expone la API y sirve `static/`. El scraping corre en un thread
daemon con un dict de estado global (`_scrape_estado`) que el frontend poletea;
solo puede haber una corrida a la vez.

### Modelo de datos

| Tabla | Rol |
|---|---|
| `ingredientes` | Concepto de receta (`Harina`), con `unidad_base` (`g`/`ml`/`un`) |
| `terminos_busqueda` | Qué se escribe en el buscador de cada supermercado para encontrarlo |
| `productos` | Resultado concreto de un supermercado, con precio, tamaño y `orden` de relevancia |
| `elecciones` | `(ingrediente, super) → producto`. El producto por defecto, para todas las recetas |
| `recetas` | Nombre y `ganancia_pct` |
| `receta_ingredientes` | Renglón: cantidad + unidad |
| `renglon_elecciones` | `(renglón, super) → producto`. Override que pisa la elección del ingrediente solo en esa receta |
| `ingrediente_categorias` | `(ingrediente, super) → categoría`. En qué rama del catálogo de esa tienda vive el ingrediente |
| `packaging` | Costos fijos por receta (caja, cinta, etiqueta) |

`productos` guarda el tamaño en dos pares de columnas: `tamano`/`unidad` es lo
que pudo sacar el scraper del nombre, y `tamano_manual`/`unidad_manual` es la
corrección cargada a mano. Las columnas generadas `tamano_efectivo` y
`unidad_efectiva` son el `COALESCE` de ambas, y son las que usa el costeo. Ir
por columnas separadas hace que el próximo scrape no pise la corrección.

Un producto es la misma fila entre corridas por `(super, id_externo)`, el id que
le pone la tienda. El upsert actualiza precio, nombre, tamaño scrapeado y
categoría, y **no toca** `tamano_manual`/`unidad_manual`: lo que se carga a mano
sobrevive a todos los re-scrapes. Si la tienda cambiara el id de un producto,
entraría como fila nueva y esa corrección se perdería; también se guarda el
`ean`, que serviría para reconectarlas.

### Categorías de las tiendas

Cada producto guarda la ruta de categoría **tal como la nombra su tienda**, con
`" / "` entre niveles:

```
Carrefour   Lácteos y productos frescos / Mantecas, margarinas y levaduras
Disco       Lácteos / Mantecas y Margarinas / Manteca
Coto        Frescos / Lácteos / Mantecas y Margarinas / Mantecas
```

No hay taxonomía propia ni mapeo entre tiendas: las tres nombran distinto y
cualquier equivalencia que inventemos envejece mal. De dónde sale cada una:

- **VTEX (Carrefour, Disco)**: el campo `categories` del producto trae las
  rutas de más profunda a más general; se toma la más profunda.
- **Coto**: `data.groups` trae todos los niveles sueltos y sin orden; el más
  hondo es el que tiene el `path_list` más largo, y la ruta se arma con esos
  nombres más el propio.

Elegir una categoría para un ingrediente recorta qué productos cuentan como ese
ingrediente. Es el arreglo para el ruido de los buscadores: buscar `manteca` en
Carrefour devuelve *Medialunas de manteca* (`/Panadería/`), y buscar `aceite`
devuelve shampoo y limpiapisos. El filtro alcanza a la rama entera, así que
elegir `Lácteos` incluye sus subcategorías.

El filtro se aplica al leer, no al scrapear: los productos se guardan todos y
cambiar la categoría surte efecto sin volver a buscar.

### Precios que quedaron viejos

Un producto que deja de aparecer en los resultados no se borra ni se actualiza:
conserva el precio del día en que se lo vio por última vez. Se detecta
comparando su `fecha_scrape` con la más reciente de ese ingrediente en ese
supermercado.

- El elegido **automático** nunca usa uno viejo. Si no queda ninguno actual, el
  renglón informa que no hay producto, que es la verdad: no tenemos precio de hoy.
- Uno elegido **a mano** sí puede quedar viejo. Ahí se marca en la celda, en el
  producto en uso y en el veredicto (`con N precio(s) viejo(s)`), pero no se
  bloquea la comparación: el precio existió, solo que es de otro día.

### Decisiones que no son obvias

Están comentadas en el código; el resumen:

- **La relevancia se ordena por posición del buscador, nunca por precio.**
  Ordenar por precio por unidad hunde el producto correcto: buscar "Mascarpone"
  y quedarse con el más barato devuelve azúcar mascabo.
- **El automático prefiere los productos con tamaño, pero no descarta a los que
  no lo tienen.** Si ninguno tiene tamaño, el renglón falla con "no sabemos
  cuánto trae" y un botón para cargarlo, en vez de mentir con "no hay producto"
  y mandar a buscar donde no hay nada.
- **El tamaño se parsea del nombre del producto.** Casi ningún supermercado lo
  expone como campo. `parse_tamano` normaliza unidades (`kg`→`g`,
  `cc`/`cmq`→`ml`) y toma el primer match, porque en los packs el total va
  primero (`"357 g. Pack x3 de 119 g"`). Si el nombre no declara cantidad pero
  se vende por peso o volumen (`"Limón x Kg"`, `"granel"`), asume 1000 g /
  1000 ml.
- **La categoría manda sobre el resto al ordenar.** Si el ingrediente ya tiene
  una rama elegida, al scrapear los de esa rama van primero; recién después
  pesan `es_relevante`, la unidad y el tamaño. La lista `RUIDO` sigue siendo el
  criterio cuando todavía no hay categoría elegida.
- **`es_relevante` filtra por palabras, no por substring.** Exige que el nombre
  mencione todas las palabras significativas de algún término, y descarta una
  lista de categorías-ruido (`yogur`, `licor`, `moscada`, `light`…) salvo que
  esa palabra sea parte del término buscado. Sin el filtro, buscar "banana"
  devuelve yogur de banana.
- **Los descuentos se guardan pero no se usan para costear.** El precio de
  venta sale siempre del precio de lista: la promo puede no estar el día que se
  compra.
- **`ListPrice` de Disco viene corrupto**, así que solo se considera descuento
  cuando `Price < PriceWithoutDiscount`.

## Cómo se publica

`dev` es la rama de trabajo y es la rama por defecto del repo. `main` es solo lo
que está en producción: cada push a `main` dispara el workflow que buildea el
ejecutable de Windows y publica la release.

**Lo que dispara una release es subir el número en `app/version.py`.** El
workflow lee ese valor y, si ya existe una release con ese tag, no hace nada.
Así un merge a `main` sin cambio de versión no genera una release repetida.

```bash
# trabajar
git checkout dev
...
git push

# salir a producción
# 1. subir VERSION en app/version.py y commitear en dev
git checkout main && git merge dev && git push
```

El workflow corre los tres archivos de tests antes de buildear: si algo falla,
no se publica nada.

## Cómo llega al cliente

Es un único `.exe` de Windows, sin dependencias: no hace falta Python ni nada
instalado. Lo único que no viaja adentro es el Chromium de Playwright, que pesa
cientos de megas y se baja solo la primera vez que se abre la app.

| Archivo | Para qué |
|---|---|
| `pSapo.exe` | La app |
| `instalador/instalar.ps1` | Instalación nueva: descarga la última versión, deja los accesos directos y programa el actualizador |
| `instalador/actualizador.ps1` | Corre al iniciar sesión, mira si hay versión nueva y reemplaza el ejecutable |

Todo queda en `%LOCALAPPDATA%\pSapo`: el ejecutable, `psapo.db`, la versión
instalada, el navegador y el log del actualizador. No pide permisos de
administrador.

### Qué pasa con los datos al actualizar

La base vive en la carpeta de datos, no al lado del ejecutable, así que
reemplazar el `.exe` no la toca. Las migraciones **no** las corre el
actualizador: las corre la app al abrirse, porque `init_db()` es idempotente y
las migraciones viven junto al esquema que las necesita. Como una migración no
se puede deshacer, el actualizador guarda una copia
(`psapo.db.antes-de-<versión>`) antes de reemplazar nada.

Si la app está abierta, el actualizador no hace nada y lo reintenta en el
próximo inicio: Windows no deja reemplazar un ejecutable en uso, y cerrarle la
app al usuario sin avisar sería peor.

## Correr el proyecto

Ya hay un venv armado en `venv/` con las dependencias instaladas.

```bash
venv/bin/uvicorn app.main:app --reload --port 8000
```

Abrir http://localhost:8000. La base se crea y se migra sola en el arranque.

Desde cero:

```bash
python -m venv venv
venv/bin/pip install fastapi uvicorn pydantic requests playwright
venv/bin/playwright install chromium
venv/bin/python -m app.seed      # carga ingredientes y recetas iniciales
```

`playwright install chromium` es obligatorio: sin el browser, Carrefour y Disco
no se pueden scrapear (Coto sí, usa `requests`).

### Actualizar precios

Desde la UI, botón **Actualizar precios**, o:

```bash
curl -X POST localhost:8000/api/actualizar \
     -H 'content-type: application/json' -d '{"alcance": "todo"}'
curl localhost:8000/api/estado          # seguimiento del progreso
```

Todo el catálogo tarda varios minutos: son 25 ingredientes × sus términos × 3
supermercados, con un browser real de por medio. La UI marca los precios como
vencidos después de 2 días (`scraping.DIAS_FRESCURA`).

### Tests

```bash
venv/bin/python tests/test_parseo.py
venv/bin/python tests/test_costos.py
venv/bin/python tests/test_api.py
```

`test_costos.py` y `test_api.py` levantan una base temporal con el esquema real
por cada test. `test_api.py` llama a las funciones de los endpoints en vez de
levantar un cliente HTTP (no hay `httpx` en el venv) y reemplaza
`_lanzar_scrape`, para que ningún test dependa de la red. No hay tests de los
scrapers en vivo.

## API

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/api/estado` | Última actualización, si está vencido, cuántos ingredientes vencidos, progreso del scrape |
| `POST` | `/api/actualizar` | Único disparador del scraping. Body: `{alcance: "todo" \| "vencidos" \| "ingrediente", ingrediente_id?}` |
| `GET` | `/api/ingredientes` | Lista con cantidad de productos y en cuántos supermercados hay elección confirmada |
| `GET` | `/api/ingredientes/{id}` | El ingrediente con sus términos y **una columna por supermercado**: producto en uso, origen y candidatos |
| `POST` | `/api/ingredientes` | Crea el ingrediente con sus términos y arranca su búsqueda |
| `PUT` | `/api/ingredientes/{id}/terminos` | Corrige los términos y vuelve a buscar |
| `PUT` | `/api/ingredientes/{id}/categorias` | Fija la rama del catálogo por tienda. Body: `{categorias: {super: categoria \| null}}` |
| `PUT` | `/api/ingredientes/{id}/eleccion` | Fija el producto por defecto en un supermercado |
| `DELETE` | `/api/ingredientes/{id}` | Borra (409 si alguna receta lo usa) |
| `PUT` | `/api/productos/{id}/tamano` | Carga el tamaño a mano; `tamano: null` vuelve a lo que diga el scraper |
| `GET` | `/api/recetas` | Lista de recetas con su cantidad de ingredientes |
| `GET` | `/api/recetas/{id}` | **La receta costeada en los tres supermercados.** Es la pantalla entera |
| `POST` `PUT` `DELETE` | `/api/recetas[/{id}]` | ABM de recetas (nombre y margen) |
| `POST` | `/api/recetas/{id}/ingredientes` | Agrega un renglón |
| `PUT` `DELETE` | `/api/renglones/{id}` | Edita cantidad y unidad, o borra el renglón |
| `PUT` | `/api/renglones/{id}/eleccion` | Override de producto para ese renglón. Body: `{productos: {super: producto_id}}` |
| `DELETE` | `/api/renglones/{id}/eleccion` | Vuelve el renglón al producto por defecto |
| `POST` | `/api/recetas/{id}/packaging` · `DELETE /api/packaging/{id}` | Costos fijos de la receta |

`GET /api/recetas/{id}` devuelve `{receta, supers: [...], ganador}`. Cada
columna trae el detalle renglón por renglón (producto usado, costo, `origen` en
`receta` / `confirmado` / `automatico`, y `problema` si no se pudo costear),
más `completo`, `problemas`, subtotales, `costo_total`, `precio_venta`, `gana`
y `diferencia` contra el ganador. Cuando `completo` es `false`, `costo_total` y
`precio_venta` vienen en `null` a propósito.

Los `problema` posibles son `sin_producto`, `sin_tamano` y
`unidad_incompatible`. La UI usa el motivo para ofrecer la corrección que
corresponde en vez de un "falta elegir producto" genérico.

Las elecciones se validan contra el supermercado: mandar un producto de Coto
como elección de Carrefour devuelve 400. Guardarlo sería reintroducir el bug de
un override que se ignora en silencio al costear.

## Limitaciones conocidas

- El progreso de una corrida vive en memoria del proceso: reiniciar el server
  durante un scrape pierde la barra de progreso. Lo ya guardado no se pierde,
  porque se commitea por ingrediente.
- Las categorías solo existen para productos scrapeados después de que se
  agregó la columna. Una base vieja necesita una corrida de **Actualizar
  precios → Todo el catálogo** para poder filtrar.
- El scraping depende de APIs no documentadas. La key de Constructor.io de Coto
  puede rotar (se relee de `cnstrc.com/js/cust/coto_*.js`), y los endpoints de
  VTEX pueden cambiar de forma.
- La conversión de unidades solo cubre `g`/`kg` y `ml`/`l`. No hay densidades,
  así que una receta en `ml` no puede usar un producto vendido en `g`.
- Los tres supermercados están hardcodeados en un `CHECK` de `productos.super`
  y en `db.SUPERS`. Agregar uno cuarto toca esquema y scrapers.

## Pendiente

Acordado pero fuera del alcance actual:

- Congelar el presupuesto: guardar supermercado, fecha, costo y precio de una
  cotización para no recalcularla.
- Poner el precio de venta a mano y ver el margen real que queda.
- Duplicar una receta como base de otra.
