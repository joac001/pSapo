// pSapo — una pantalla por cosa, una sola forma de hacer cada cosa.
//
// Reglas de diseño que sostiene este archivo:
//   · El producto por defecto de un ingrediente se elige SOLO en Ingredientes.
//   · El override de un renglón se elige SOLO en la tabla de la receta.
//   · El scraping se dispara SOLO desde el menú "Actualizar precios".
// Si algo hace falta desde otro lado, se navega hasta el dueño, no se duplica.

const pesos = new Intl.NumberFormat("es-AR",
  { style: "currency", currency: "ARS", maximumFractionDigits: 0 });
const pesosExactos = new Intl.NumberFormat("es-AR",
  { style: "currency", currency: "ARS", minimumFractionDigits: 2 });

const UNIDADES_RECETA = ["g", "kg", "ml", "l", "un"];
const UNIDADES_BASE = ["g", "ml", "un"];

// Lo que sigue vivo entre re-dibujados. Cada acción vuelve a pedir los datos y
// repinta, así que sin esto se perdían el buscador, el orden, las listas
// desplegadas y la posición de la página en cada guardado.
const estado = {
  vista: "recetas",
  recetaId: null,
  ingredienteId: null,
  filtroIngredientes: "",
  buscarProductos: "",
  ordenProductos: "relevancia",
  expandidos: new Set(),
  precios: null,
};

/** Repinta sin que la página salte al principio.
 *
 * Reemplazar el contenido deja la página momentáneamente más corta, así que un
 * único `scrollTo` se recorta y no llega. Se reintenta unos frames hasta que el
 * alto vuelve y la posición entra.
 */
async function conservandoScroll(accion) {
  const y = window.scrollY;
  await accion();
  for (let intento = 0; intento < 12 && Math.abs(window.scrollY - y) > 2; intento++) {
    window.scrollTo(0, y);
    await new Promise(requestAnimationFrame);
  }
}

// --- utilidades -----------------------------------------------------------

async function api(url, opciones = {}) {
  if (opciones.body !== undefined) {
    opciones.headers = { "Content-Type": "application/json" };
    opciones.body = JSON.stringify(opciones.body);
  }
  const r = await fetch(url, opciones);
  const texto = await r.text();
  const datos = texto ? JSON.parse(texto) : null;
  if (!r.ok) throw new Error(datos?.detail || `Error ${r.status}`);
  return datos;
}

function el(tag, props = {}, hijos = []) {
  const nodo = Object.assign(document.createElement(tag), props);
  for (const hijo of [hijos].flat()) if (hijo != null && hijo !== false) nodo.append(hijo);
  return nodo;
}

function boton(texto, alClick, clase = "") {
  const b = el("button", { className: `btn ${clase}`.trim(), textContent: texto, type: "button" });
  b.addEventListener("click", async (ev) => {
    ev.stopPropagation();
    b.disabled = true;
    try { await alClick(ev); } catch (e) { avisar(e.message, true); } finally { b.disabled = false; }
  });
  return b;
}

function avisar(texto, esError = false) {
  const nodo = el("div", { className: `aviso ${esError ? "error" : ""}`.trim(), textContent: texto });
  document.getElementById("avisos").append(nodo);
  setTimeout(() => nodo.remove(), esError ? 6000 : 3000);
}

const modal = document.getElementById("modal");

function abrirModal({ titulo, meta, cuerpo, acciones = [], accionSecundaria = null }) {
  modal.replaceChildren(
    el("div", { className: "modal-cabecera" }, [
      el("div", {}, [
        el("h3", { textContent: titulo }),
        meta ? el("p", { className: "meta", textContent: meta }) : null,
      ]),
      boton("✕", cerrarModal, "discreto"),
    ]),
    el("div", { className: "modal-cuerpo" }, cuerpo),
    el("div", { className: "modal-pie" }, [
      el("div", {}, accionSecundaria),
      el("div", { className: "acciones" }, acciones),
    ]),
  );
  modal.showModal();
}

function cerrarModal() { modal.close(); modal.replaceChildren(); }

modal.addEventListener("click", (ev) => { if (ev.target === modal) cerrarModal(); });

/** Confirmación destructiva con el modal propio.
 *
 * No usa `confirm()` del navegador: si el usuario tildó "no permitir más
 * diálogos", `confirm()` pasa a devolver false para siempre y el borrado se
 * cancela solo, sin decir nada. */
function confirmar({ titulo, mensaje, confirmar = "Borrar" }) {
  return new Promise((resolver) => {
    let respuesta = false;
    const cerrar = (valor) => { respuesta = valor; cerrarModal(); };
    modal.addEventListener("close", () => resolver(respuesta), { once: true });
    abrirModal({
      titulo,
      cuerpo: el("p", { className: "texto-confirmacion", textContent: mensaje }),
      acciones: [
        boton("Cancelar", () => cerrar(false), "discreto"),
        boton(confirmar, () => cerrar(true), "peligro-solido"),
      ],
    });
  });
}

/** Formulario simple en modal. `campos` describe inputs; resuelve al confirmar. */
function pedirDatos({ titulo, meta, campos, confirmar = "Guardar", alConfirmar }) {
  const inputs = {};
  const cuerpo = el("form", { className: "formulario", id: "form-modal" },
    campos.map((campo) => {
      const comun = { className: "campo", value: campo.valor ?? "", required: campo.requerido !== false };
      const input = campo.opciones
        ? el("select", { className: "campo" }, campo.opciones.map((o) =>
            el("option", {
              value: typeof o === "string" ? o : o.valor,
              textContent: typeof o === "string" ? o : o.texto,
              selected: (typeof o === "string" ? o : o.valor) == campo.valor,
            })))
        : el("input", { ...comun, type: campo.tipo || "text", step: campo.paso || "any",
                        placeholder: campo.ejemplo || "" });
      inputs[campo.nombre] = input;
      return el("label", {}, [campo.etiqueta, input, campo.ayuda ? el("span", { className: "ayuda", textContent: campo.ayuda }) : null]);
    }));

  const guardar = async () => {
    if (!cuerpo.reportValidity()) return;
    const valores = Object.fromEntries(Object.entries(inputs).map(([k, i]) => [k, i.value]));
    await alConfirmar(valores);
    cerrarModal();
  };
  cuerpo.addEventListener("submit", (ev) => { ev.preventDefault(); guardar(); });

  abrirModal({ titulo, meta, cuerpo, acciones: [boton("Cancelar", cerrarModal, "discreto"), boton(confirmar, guardar, "primario")] });
  setTimeout(() => Object.values(inputs)[0]?.focus(), 50);
}

/** Precio llevado a una unidad comparable: el número que decide cuál conviene.
 *
 * Un frasco de 200 ml a $2.800 y uno de 1 l a $6.900 no se comparan de memoria.
 * Devuelve null si el producto no declara tamaño. */
function precioUnitario(p) {
  if (!p.tamano_efectivo) return null;
  const porUnidad = p.precio / p.tamano_efectivo;
  if (p.unidad_efectiva === "un") return { valor: porUnidad, texto: "c/u" };
  return { valor: porUnidad * 1000, texto: p.unidad_efectiva === "ml" ? "el litro" : "el kilo" };
}

function precioUnitarioLegible(p) {
  const u = precioUnitario(p);
  return u ? `${pesos.format(u.valor)} ${u.texto}` : null;
}

const ORDENES = {
  relevancia: { texto: "Más relevantes", comparar: (a, b) => a.orden - b.orden },
  unitario: {
    texto: "Más barato por kilo/litro",
    comparar: (a, b) => {
      const ua = precioUnitario(a), ub = precioUnitario(b);
      if (!ua) return ub ? 1 : 0;      // los que no declaran tamaño van al final
      if (!ub) return -1;
      return ua.valor - ub.valor;
    },
  },
  precio: { texto: "Más barato el envase", comparar: (a, b) => a.precio - b.precio },
};

/** Control de orden compartido por las tres columnas. */
function selectorOrden(valor, alCambiar) {
  const select = el("select", { className: "campo campo-orden" },
    Object.entries(ORDENES).map(([clave, o]) =>
      el("option", { value: clave, textContent: o.texto, selected: clave === valor })));
  select.addEventListener("change", alCambiar);
  return select;
}

function tamanoLegible(p) {
  if (!p.tamano_efectivo) return "tamaño sin declarar";
  const manual = p.tamano_manual ? " (a mano)" : "";
  return `${(+p.tamano_efectivo).toLocaleString("es-AR")} ${p.unidad_efectiva}${manual}`;
}

// --- cabecera: estado de los precios y único disparador del scraping ------

async function refrescarPrecios() {
  try { estado.precios = await api("/api/estado"); } catch { return; }
  pintarPrecios();
  if (estado.precios.scrape.corriendo) setTimeout(refrescarPrecios, 1500);
}

function pintarPrecios() {
  const { ultima_actualizacion: ultima, desactualizado, vencidos, scrape } = estado.precios;
  const contenedor = document.getElementById("precios");

  if (scrape.corriendo) {
    const pct = scrape.total ? Math.round((scrape.hecho / scrape.total) * 100) : 4;
    contenedor.replaceChildren(el("div", { className: "progreso" }, [
      el("div", { className: "barra" }, el("span", { style: `width:${pct}%` })),
      el("span", { textContent: scrape.total ? `Buscando precios… ${scrape.hecho}/${scrape.total}` : "Buscando precios…" }),
    ]));
    return;
  }

  if (scrape.error) avisar(`La última búsqueda falló: ${scrape.error}`, true);

  const dias = ultima ? Math.floor((Date.now() - new Date(ultima)) / 86400000) : null;
  const texto = ultima === null ? "Sin precios todavía"
    : dias === 0 ? "Precios de hoy"
    : dias === 1 ? "Precios de ayer"
    : `Precios de hace ${dias} días`;

  contenedor.replaceChildren(
    el("span", { className: `sello ${desactualizado ? "vencido" : ""}`.trim(), textContent: texto }),
    menuActualizar(vencidos),
  );
}

function menuActualizar(vencidos) {
  const caja = el("div", { className: "desplegable" });
  const disparar = async (cuerpo, texto) => {
    caja.querySelector(".menu")?.remove();
    const r = await api("/api/actualizar", { method: "POST", body: cuerpo });
    avisar(r.iniciado ? texto : r.motivo, !r.iniciado);
    refrescarPrecios();
  };

  const abrir = () => {
    if (caja.querySelector(".menu")) return caja.querySelector(".menu").remove();
    const menu = el("div", { className: "menu" }, [
      boton("Todo el catálogo", () => disparar({ alcance: "todo" }, "Buscando precios en los tres supermercados."), "discreto"),
      (() => {
        const b = boton(`Solo los vencidos (${vencidos})`,
          () => disparar({ alcance: "vencidos" }, `Actualizando ${vencidos} ingrediente(s).`), "discreto");
        b.disabled = !vencidos;
        return b;
      })(),
      el("div", { className: "separador" }),
      boton("Un ingrediente…", elegirIngredienteParaActualizar, "discreto"),
    ]);
    caja.append(menu);
    setTimeout(() => document.addEventListener("click", function fuera(ev) {
      if (!caja.contains(ev.target)) { menu.remove(); document.removeEventListener("click", fuera); }
    }), 0);
  };

  caja.append(boton("Actualizar precios", abrir, "primario"));
  return caja;
}

/** Espera a que termine la búsqueda en curso. Devuelve el estado final. */
async function esperarScrape(intentos = 120) {
  for (let i = 0; i < intentos; i++) {
    const e = await api("/api/estado");
    if (!e.scrape.corriendo) return e.scrape;
    await new Promise((r) => setTimeout(r, 1500));
  }
  return null;
}

/** Al crear un ingrediente todavía no hay productos, así que no hay categorías
 *  que ofrecer. Cuando la búsqueda termina, sí: recién ahí se pregunta. */
async function elegirCategoriaAlCrear(ingredienteId) {
  const scrape = await esperarScrape();
  refrescarPrecios();
  if (!scrape || scrape.error) return;

  const datos = await api(`/api/ingredientes/${ingredienteId}`);
  const cuantas = datos.supers.reduce((n, c) => n + c.categorias.length, 0);
  if (!cuantas) return avisar(`No se encontraron productos para ${datos.ingrediente.nombre}.`, true);

  await cargarIngredientes(ingredienteId);
  if (modal.open) return avisar(`Listo ${datos.ingrediente.nombre}: elegí su categoría con "Editar".`);
  editarTerminos(datos);
}

async function elegirIngredienteParaActualizar() {
  const ingredientes = await api("/api/ingredientes");
  pedirDatos({
    titulo: "Actualizar un ingrediente",
    meta: "Vuelve a buscarlo en los tres supermercados.",
    campos: [{ nombre: "ingrediente_id", etiqueta: "Ingrediente",
               opciones: ingredientes.map((i) => ({ valor: i.id, texto: i.nombre })) }],
    confirmar: "Buscar",
    alConfirmar: async (v) => {
      const r = await api("/api/actualizar", { method: "POST", body: { alcance: "ingrediente", ingrediente_id: +v.ingrediente_id } });
      avisar(r.iniciado ? "Buscando sus precios." : r.motivo, !r.iniciado);
      refrescarPrecios();
    },
  });
}

// --- navegación: la URL dice dónde estás ----------------------------------
//
// Sin esto, recargar en Ingredientes devolvía a Recetas y se perdía qué estabas
// mirando. Va con replaceState y no pushState: guardar algo no es un paso del
// historial.

function rutaActual() {
  const m = (location.hash || "").match(/^#\/(recetas|ingredientes)(?:\/(\d+))?$/);
  return m ? { vista: m[1], id: m[2] ? +m[2] : null } : null;
}

function fijarUrl() {
  const id = estado.vista === "recetas" ? estado.recetaId : estado.ingredienteId;
  const nueva = `#/${estado.vista}${id ? `/${id}` : ""}`;
  if (location.hash !== nueva) history.replaceState(null, "", nueva);
}

function mostrarVista(vista) {
  estado.vista = vista;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("activa", t.dataset.vista === vista));
  document.getElementById("vista-recetas").hidden = vista !== "recetas";
  document.getElementById("vista-ingredientes").hidden = vista !== "ingredientes";
  fijarUrl();
}

document.querySelectorAll(".tab").forEach((tab) =>
  tab.addEventListener("click", () => mostrarVista(tab.dataset.vista)));

window.addEventListener("hashchange", () => {
  const ruta = rutaActual();
  if (!ruta) return;
  if (ruta.vista !== estado.vista) mostrarVista(ruta.vista);
  const actual = ruta.vista === "recetas" ? estado.recetaId : estado.ingredienteId;
  if (ruta.id && ruta.id !== actual) {
    const ver = ruta.vista === "recetas" ? verReceta : verIngrediente;
    ver(ruta.id).catch((e) => avisar(e.message, true));
  }
});

function irA(vista) { mostrarVista(vista); }

// --- recetas: lista -------------------------------------------------------

async function cargarRecetas(seleccionar = estado.recetaId) {
  const recetas = await api("/api/recetas");
  const lista = document.getElementById("lista-recetas");
  lista.replaceChildren(...recetas.map((r) => {
    const item = el("button", { className: "item", type: "button" }, [
      el("span", { textContent: r.nombre }),
      el("span", { className: "cuenta", textContent: r.cantidad_ingredientes }),
    ]);
    item.dataset.id = r.id;
    item.classList.toggle("activa", r.id === seleccionar);
    item.addEventListener("click", () => verReceta(r.id));
    return item;
  }));

  if (!recetas.length) return pintarRecetaVacia("Todavía no hay recetas.");
  const elegida = recetas.some((r) => r.id === seleccionar) ? seleccionar : recetas[0].id;
  await verReceta(elegida);
}

function pintarRecetaVacia(texto) {
  document.getElementById("detalle-receta").replaceChildren(
    el("div", { className: "vacio" }, [
      el("p", { textContent: texto }),
      boton("+ Nueva receta", nuevaReceta, "primario"),
    ]));
}

function nuevaReceta() {
  pedirDatos({
    titulo: "Nueva receta",
    campos: [
      { nombre: "nombre", etiqueta: "Nombre", ejemplo: "Lemon pie" },
      { nombre: "ganancia_pct", etiqueta: "Ganancia %", tipo: "number", valor: 100 },
    ],
    confirmar: "Crear",
    alConfirmar: async (v) => {
      const { id } = await api("/api/recetas", { method: "POST", body: { nombre: v.nombre, ganancia_pct: +v.ganancia_pct } });
      estado.recetaId = id;
      await cargarRecetas(id);
    },
  });
}

document.getElementById("btn-nueva-receta").addEventListener("click", nuevaReceta);

// --- recetas: detalle -----------------------------------------------------

async function verReceta(recetaId) {
  estado.recetaId = recetaId;
  fijarUrl();
  document.querySelectorAll("#lista-recetas .item").forEach((i) =>
    i.classList.toggle("activa", +i.dataset.id === recetaId));

  const datos = await api(`/api/recetas/${recetaId}`);
  document.getElementById("detalle-receta").replaceChildren(
    cabeceraReceta(datos),
    veredicto(datos),
    tablaComparativa(datos),
    tarjetaPackaging(datos),
  );
}

function cabeceraReceta({ receta }) {
  const nombre = el("input", { className: "editable", value: receta.nombre, size: Math.max(receta.nombre.length, 8) });
  const ganancia = el("input", { className: "editable", type: "number", value: receta.ganancia_pct, min: 0, step: 5, style: "width:64px" });

  const guardar = async () => {
    if (nombre.value.trim() === receta.nombre && +ganancia.value === receta.ganancia_pct) return;
    if (!nombre.value.trim()) return (nombre.value = receta.nombre);
    await api(`/api/recetas/${receta.id}`, { method: "PUT", body: { nombre: nombre.value, ganancia_pct: +ganancia.value } });
    await cargarRecetas(receta.id);
  };
  for (const campo of [nombre, ganancia]) {
    campo.addEventListener("change", () => guardar().catch((e) => avisar(e.message, true)));
    campo.addEventListener("keydown", (ev) => ev.key === "Enter" && campo.blur());
  }

  return el("div", { className: "titulo-detalle" }, [
    el("div", {}, [
      el("h1", {}, nombre),
      el("div", { className: "meta" }, ["Ganancia ", ganancia, " % sobre el costo"]),
    ]),
    el("div", { className: "acciones" },
      boton("Borrar receta", async () => {
        const seguro = await confirmar({
          titulo: `¿Borrar "${receta.nombre}"?`,
          mensaje: "Se borran sus ingredientes y su packaging. No se puede deshacer.",
          confirmar: "Borrar receta",
        });
        if (!seguro) return;
        await api(`/api/recetas/${receta.id}`, { method: "DELETE" });
        estado.recetaId = null;
        await cargarRecetas(null);
      }, "peligro")),
  ]);
}

function veredicto(datos) {
  const completos = datos.supers.filter((s) => s.completo);
  if (!completos.length) {
    const cuantos = new Set(datos.supers.flatMap((s) => s.problemas.map((p) => p.ingrediente))).size;
    return el("div", { className: "veredicto sin-datos" }, [
      el("strong", { textContent: "Todavía no se puede comparar" }),
      el("span", { className: "detalle-texto",
        textContent: datos.supers[0].detalle.length
          ? `Faltan datos de ${cuantos} ingrediente(s) en los tres supermercados.`
          : "Agregá ingredientes a la receta para ver cuánto sale." }),
    ]);
  }

  const gana = completos.find((s) => s.gana);
  const resto = completos.filter((s) => !s.gana).sort((a, b) => a.costo_total - b.costo_total);
  const ahorro = resto.length ? resto[resto.length - 1].costo_total - gana.costo_total : 0;

  return el("div", { className: "veredicto" }, [
    el("strong", { textContent: completos.length === 1
      ? `Solo ${gana.super} tiene todos los datos`
      : `Conviene ${gana.super}` }),
    el("span", { textContent: `${pesos.format(gana.costo_total)} de materia prima` }),
    ahorro > 0
      ? el("span", { className: "detalle-texto", textContent: `· ahorrás ${pesos.format(ahorro)} contra el más caro` })
      : null,
    completos.length < datos.supers.length
      ? el("span", { className: "etiqueta aviso", textContent: `${datos.supers.length - completos.length} sin datos completos` })
      : null,
    // Un precio viejo no invalida la comparación, pero sí la desempareja: se
    // avisa justo donde se toma la decisión.
    gana.desactualizados.length
      ? el("span", { className: "etiqueta aviso",
                     title: gana.desactualizados.map((d) => `${d.ingrediente}: precio del ${d.fecha.slice(0, 10)}`).join("\n"),
                     textContent: `con ${gana.desactualizados.length} precio(s) viejo(s)` })
      : null,
  ]);
}

function tablaComparativa(datos) {
  const supers = datos.supers;
  const renglones = supers[0].detalle.map((d) => d.renglon_id);

  if (!renglones.length) {
    return el("div", { className: "tarjeta" }, [
      el("div", { className: "vacio", style: "min-height:180px" }, [
        el("p", { textContent: "Esta receta no tiene ingredientes todavía." }),
        boton("+ Agregar ingrediente", () => agregarRenglon(datos.receta.id), "primario"),
      ]),
    ]);
  }

  const tabla = el("table", { className: "comparativa" }, [
    el("colgroup", {}, [el("col", { className: "ingrediente" }), ...supers.map(() => el("col"))]),
    el("thead", {}, el("tr", {}, [
      el("th", { textContent: "Ingrediente" }),
      ...supers.map((s) => el("th", { className: `col-super ${s.gana ? "gana" : ""} ${s.completo ? "" : "fuera"}`.trim() },
        el("span", { className: "marca-super" }, [
          s.super,
          s.gana ? el("span", { className: "trofeo", textContent: "· más barato" }) : null,
          s.completo ? null : el("span", { className: "etiqueta aviso", textContent: "sin datos" }),
        ]))),
    ])),
    el("tbody", {}, renglones.map((renglonId) => filaRenglon(datos, renglonId))),
    pieComparativa(datos),
  ]);

  return el("div", { className: "tarjeta" }, [
    tabla,
    el("div", { className: "pie-tarjeta" }, boton("+ Agregar ingrediente", () => agregarRenglon(datos.receta.id), "discreto")),
  ]);
}

function filaRenglon(datos, renglonId) {
  const base = datos.supers[0].detalle.find((d) => d.renglon_id === renglonId);
  const propio = datos.supers.some((s) =>
    s.detalle.find((d) => d.renglon_id === renglonId)?.origen === "receta");

  const cantidad = el("input", { className: "editable", type: "number", value: base.cantidad, min: 0, step: "any", style: "width:70px" });
  const unidad = el("select", { className: "editable" }, UNIDADES_RECETA.map((u) =>
    el("option", { value: u, textContent: u, selected: u === base.unidad })));

  const guardar = async () => {
    if (+cantidad.value === base.cantidad && unidad.value === base.unidad) return;
    if (!(+cantidad.value > 0)) return (cantidad.value = base.cantidad);
    await api(`/api/renglones/${renglonId}`, { method: "PUT", body: { cantidad: +cantidad.value, unidad: unidad.value } });
    await verReceta(datos.receta.id);
  };
  for (const campo of [cantidad, unidad]) {
    campo.addEventListener("change", () => guardar().catch((e) => avisar(e.message, true)));
    campo.addEventListener("keydown", (ev) => ev.key === "Enter" && campo.blur());
  }

  // El botón vive en la columna del ingrediente porque la elección abarca los
  // tres supermercados. Antes se disparaba desde la celda de uno solo y no se
  // entendía por qué se abrían los tres.
  const cambiar = boton("Cambiar producto ›", () => elegirProductoDeRenglon(datos, base), "discreto enlace-fila");
  const quitar = boton("Quitar", async () => {
    await api(`/api/renglones/${renglonId}`, { method: "DELETE" });
    await verReceta(datos.receta.id);
  }, "discreto enlace-fila");

  return el("tr", {}, [
    el("td", {}, [
      el("div", { className: "nombre-ingrediente" }, [
        base.ingrediente,
        propio ? el("span", { className: "marca-mano", title: "Esta receta usa un producto distinto al de siempre", textContent: "a mano" }) : null,
      ]),
      el("div", { className: "cantidad-linea" }, [cantidad, unidad]),
      el("div", { className: "acciones-fila" }, [cambiar, quitar]),
    ]),
    ...datos.supers.map((s) => celdaProducto(datos, s, s.detalle.find((d) => d.renglon_id === renglonId))),
  ]);
}

const TEXTO_PROBLEMA = {
  sin_producto: "Sin producto en este súper",
  sin_tamano: "No sabemos cuánto trae",
  unidad_incompatible: "No se puede convertir a la unidad de la receta",
};

function celdaProducto(datos, columna, d) {
  const clase = `col-super ${columna.gana ? "gana" : ""} ${columna.completo ? "" : "fuera"}`.trim();
  const celda = el("td", { className: clase });

  if (d.problema === "sin_tamano" && d.producto) {
    celda.append(
      el("div", { className: "producto-titulo", textContent: d.producto.nombre_producto }),
      el("div", { className: "problema" }, [
        el("span", { className: "etiqueta aviso", textContent: TEXTO_PROBLEMA.sin_tamano }),
        boton("Cargar tamaño", () => cargarTamano(d.producto, () => verReceta(datos.receta.id)), "discreto"),
      ]));
    return celda;
  }

  if (d.problema) {
    celda.append(el("div", { className: "problema" }, [
      el("span", { className: "etiqueta aviso", textContent: TEXTO_PROBLEMA[d.problema] }),
      boton(d.problema === "sin_producto" ? "Elegir producto" : "Elegir otro",
        () => elegirProductoDeRenglon(datos, d), "discreto"),
    ]));
    return celda;
  }

  // Sin insignias por celda: con tres columnas por fila, repetir el estado en
  // cada una tapa lo único que se compara acá, que es el precio.
  const cuerpo = el("button", {
    className: "celda-producto", type: "button",
    title: `${d.producto.nombre_producto}\n${pesos.format(d.producto.precio)} · ${tamanoLegible(d.producto)}\nClic para cambiarlo`,
  }, [
    el("span", { className: "producto-titulo", textContent: d.producto.nombre_producto }),
    d.desactualizado
      ? el("span", { className: "producto-meta" },
          el("span", { className: "etiqueta aviso",
                       title: `Este producto no apareció en la última búsqueda. El precio es del ${d.producto.fecha_scrape.slice(0, 10)}.`,
                       textContent: "precio viejo" }))
      : null,
  ]);
  cuerpo.addEventListener("click", () => elegirProductoDeRenglon(datos, d));

  celda.append(cuerpo, el("div", { className: "costo", textContent: pesosExactos.format(d.costo) }));
  return celda;
}

function pieComparativa(datos) {
  // Dos filas, no cuatro: el desglose de ingredientes y packaging ya se ve
  // renglón por renglón y en la tarjeta de abajo.
  return el("tfoot", {}, [
    el("tr", { className: "fila-total" }, [
      el("td", { className: "etiqueta-total", textContent: "Cuesta hacerla" }),
      ...datos.supers.map((s) => el("td", {
        className: `col-super costo ${s.gana ? "gana" : ""} ${s.completo ? "" : "fuera"}`.trim(),
        textContent: s.completo ? pesos.format(s.costo_total) : "—",
      })),
    ]),
    el("tr", { className: "fila-venta" }, [
      el("td", { className: "etiqueta-total", textContent: `Precio de venta (+${datos.receta.ganancia_pct}%)` }),
      ...datos.supers.map((s) => el("td", {
        className: `col-super costo ${s.gana ? "gana" : ""} ${s.completo ? "" : "fuera"}`.trim(),
      }, s.completo
        ? pesos.format(s.precio_venta)
        : el("span", { className: "etiqueta aviso", textContent: `faltan ${s.problemas.length}` }))),
    ]),
  ]);
}

function tarjetaPackaging(datos) {
  const packaging = datos.supers[0].packaging;
  const filas = packaging.map((p) => el("tr", {}, [
    el("td", { textContent: p.descripcion }),
    el("td", { className: "costo", style: "text-align:right", textContent: pesos.format(p.costo) }),
    el("td", { style: "width:1%" }, boton("✕", async () => {
      await api(`/api/packaging/${p.id}`, { method: "DELETE" });
      await verReceta(datos.receta.id);
    }, "discreto")),
  ]));

  return el("div", { className: "tarjeta" }, [
    el("h3", { textContent: "Packaging · igual en los tres supermercados" }),
    packaging.length
      ? el("table", { className: "filas-simples" }, el("tbody", {}, filas))
      : el("div", { style: "padding:14px 16px;color:var(--tenue)", textContent: "Sin costos de packaging." }),
    el("div", { className: "pie-tarjeta" }, boton("+ Agregar packaging", () => pedirDatos({
      titulo: "Agregar packaging",
      meta: "Costos fijos de la receta: caja, cinta, etiqueta.",
      campos: [
        { nombre: "descripcion", etiqueta: "Qué es", ejemplo: "Caja de cartón" },
        { nombre: "costo", etiqueta: "Costo", tipo: "number", ejemplo: "500" },
      ],
      confirmar: "Agregar",
      alConfirmar: async (v) => {
        await api(`/api/recetas/${datos.receta.id}/packaging`, { method: "POST", body: { descripcion: v.descripcion, costo: +v.costo } });
        await verReceta(datos.receta.id);
      },
    }), "discreto")),
  ]);
}

async function agregarRenglon(recetaId) {
  const ingredientes = await api("/api/ingredientes");

  // `elegido` es un ingrediente que ya existe; `creando` es un nombre nuevo.
  // Sale uno de los dos: escribir y no encontrarlo tiene que poder terminar en
  // crearlo, sin mandar al usuario a la otra pantalla y perder la receta.
  let elegido = null;
  let creando = null;

  const buscar = el("input", { className: "campo", type: "text", placeholder: "Escribí el ingrediente…", autocomplete: "off" });
  const sugerencias = el("div", { className: "sugerencias" });
  const detalle = el("div", { className: "formulario" });
  const cuerpo = el("div", { className: "formulario", style: "min-width:420px" },
    [el("label", {}, ["Ingrediente", buscar, sugerencias]), detalle]);

  const cantidad = el("input", { className: "campo", type: "number", step: "any", placeholder: "250" });
  const unidad = el("select", { className: "campo" },
    UNIDADES_RECETA.map((u) => el("option", { value: u, textContent: u })));
  const unidadBase = el("select", { className: "campo" },
    UNIDADES_BASE.map((u) => el("option", { value: u, textContent: u })));
  const terminos = el("input", { className: "campo", type: "text", placeholder: "queso mascarpone" });

  const coincidencias = () => {
    const texto = buscar.value.trim();
    if (!texto) return ingredientes.slice(0, 8);
    return ingredientes.filter((i) => coincideBusqueda(i.nombre, texto)).slice(0, 8);
  };

  const elegirExistente = (ingrediente) => {
    elegido = ingrediente;
    creando = null;
    buscar.value = ingrediente.nombre;
    unidad.value = ingrediente.unidad_base;
    pintar();
    cantidad.focus();
  };

  const elegirNuevo = (nombre) => {
    elegido = null;
    creando = nombre;
    buscar.value = nombre;
    pintar();
    cantidad.focus();
  };

  const pintar = () => {
    const texto = buscar.value.trim();
    const opciones = coincidencias();
    const exacto = ingredientes.find((i) => sinAcentos(i.nombre) === sinAcentos(texto));

    sugerencias.replaceChildren(...(elegido || creando ? [] : [
      ...opciones.map((i) => {
        const b = el("button", { className: "sugerencia", type: "button" }, [
          el("span", { textContent: i.nombre }),
          el("span", { className: "producto-meta", textContent: `${i.supers_confirmados}/3 confirmados` }),
        ]);
        b.addEventListener("click", () => elegirExistente(i));
        return b;
      }),
      texto && !exacto
        ? (() => {
            const b = el("button", { className: "sugerencia nueva", type: "button" },
              el("span", {}, [`Crear «${texto}»`]));
            b.addEventListener("click", () => elegirNuevo(texto));
            return b;
          })()
        : null,
      !texto && !opciones.length ? el("p", { className: "producto-meta", textContent: "Todavía no hay ingredientes." }) : null,
    ].filter(Boolean)));

    detalle.replaceChildren(...(elegido || creando ? [
      creando
        ? el("div", { className: "tarjeta-nuevo" }, [
            el("div", { className: "producto-meta", textContent: `Se va a crear "${creando}" y se van a buscar sus precios.` }),
            el("label", {}, ["Se mide en", unidadBase]),
            el("label", {}, ["Cómo buscarlo en los supermercados", terminos,
              el("span", { className: "ayuda", textContent: "Separá con comas. Si lo dejás vacío se usa el nombre." })]),
          ])
        : null,
      el("div", { className: "fila-campos" }, [
        el("label", { style: "flex:1" }, ["Cantidad", cantidad]),
        el("label", {}, ["Unidad", unidad]),
      ]),
    ].filter(Boolean) : []));
  };

  buscar.addEventListener("input", () => { elegido = null; creando = null; pintar(); });
  buscar.addEventListener("keydown", (ev) => {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const opciones = coincidencias();
    if (opciones.length) elegirExistente(opciones[0]);
    else if (buscar.value.trim()) elegirNuevo(buscar.value.trim());
  });
  pintar();

  const agregar = async () => {
    if (!elegido && !creando) return avisar("Elegí un ingrediente de la lista o creá uno nuevo.", true);
    if (!(+cantidad.value > 0)) return avisar("Poné una cantidad mayor a cero.", true);

    let ingredienteId = elegido?.id;
    if (creando) {
      const r = await api("/api/ingredientes", {
        method: "POST",
        body: { nombre: creando, unidad_base: unidadBase.value,
                terminos: terminos.value ? terminos.value.split(",") : [] },
      });
      ingredienteId = r.id;
      avisar(`Creado "${creando}". Buscando sus precios; después elegile la categoría en Ingredientes.`);
      refrescarPrecios();
    }
    await api(`/api/recetas/${recetaId}/ingredientes`, {
      method: "POST",
      body: { ingrediente_id: ingredienteId, cantidad: +cantidad.value, unidad: unidad.value },
    });
    cerrarModal();
    await verReceta(recetaId);
    await cargarIngredientes(estado.ingredienteId);
  };

  abrirModal({
    titulo: "Agregar ingrediente",
    cuerpo,
    acciones: [boton("Cancelar", cerrarModal, "discreto"), boton("Agregar", agregar, "primario")],
  });
  setTimeout(() => buscar.focus(), 60);
}

// --- override de producto para un renglón --------------------------------

async function elegirProductoDeRenglon(datos, renglon) {
  const ingrediente = await api(`/api/ingredientes/${renglon.ingrediente_id}`);
  const hayOverride = datos.supers.some((s) =>
    s.detalle.find((d) => d.renglon_id === renglon.renglon_id)?.origen === "receta");

  // Selección local: se elige en los tres y se guarda de una sola vez, para
  // que la comparación no quede midiendo productos distintos.
  const seleccion = {};
  for (const s of datos.supers) {
    const fila = s.detalle.find((d) => d.renglon_id === renglon.renglon_id);
    if (fila?.producto) seleccion[s.super] = fila.producto.id;
  }
  const porId = (id) => ingrediente.supers.flatMap((c) => c.candidatos).find((p) => p.id === id);

  // Un solo buscador para las tres columnas: encontrar el mismo producto en los
  // tres supermercados es justamente para lo que se abre esta ventana, y cada
  // uno escribe el nombre distinto.
  const buscador = el("input", {
    className: "buscador", type: "search", autofocus: true,
    placeholder: "Buscar en los tres a la vez… ej: ledesma 1kg",
  });

  const orden = selectorOrden(estado.ordenProductos, () => {
    estado.ordenProductos = orden.value;
    pintar();
  });
  const desplegados = new Set();
  const columnas = el("div", { className: "columnas-super" });
  const pintar = () => columnas.replaceChildren(...ingrediente.supers.map((col) =>
    bloqueSuper(col, {
      arriba: { producto: porId(seleccion[col.super]), leyenda: "Vas a usar" },
      seleccionadoId: seleccion[col.super],
      filtro: buscador.value,
      orden: estado.ordenProductos,
      expandido: desplegados.has(col.super),
      alExpandir: () => { desplegados.add(col.super); pintar(); },
      alElegir: (p) => { seleccion[col.super] = p.id; pintar(); },
      alCambiarTamano: async () => {
        Object.assign(ingrediente, await api(`/api/ingredientes/${renglon.ingrediente_id}`));
        pintar();
      },
    })));
  buscador.addEventListener("input", pintar);
  pintar();

  abrirModal({
    titulo: `Qué producto usar para ${renglon.ingrediente}`,
    meta: `Solo en "${datos.receta.nombre}". Elegí en los tres supermercados para que la comparación sea pareja.`,
    cuerpo: [el("div", { className: "barra-buscador" }, [buscador, orden]), columnas],
    accionSecundaria: hayOverride
      ? boton("Volver al producto de siempre", async () => {
          await api(`/api/renglones/${renglon.renglon_id}/eleccion`, { method: "DELETE" });
          cerrarModal();
          await verReceta(datos.receta.id);
        }, "discreto")
      : null,
    acciones: [
      boton("Cancelar", cerrarModal, "discreto"),
      boton("Usar en esta receta", async () => {
        await api(`/api/renglones/${renglon.renglon_id}/eleccion`, { method: "PUT", body: { productos: seleccion } });
        cerrarModal();
        await verReceta(datos.receta.id);
      }, "primario"),
    ],
  });
  setTimeout(() => buscador.focus(), 60);
}

// --- bloque de un súper: producto en uso + candidatos ---------------------

function bloqueSuper(col, { arriba, seleccionadoId, alElegir, alCambiarTamano,
                            filtro = "", orden = "relevancia",
                            expandido = false, alExpandir = () => {} }) {
  const candidatos = col.candidatos
    .filter((p) => !(arriba.ocultarDeLista && p.id === arriba.producto?.id)
                   && coincideBusqueda(textoBuscable(p), filtro))
    .sort(ORDENES[orden].comparar);

  const producto = arriba.producto;
  const caja = producto
    ? el("div", { className: `en-uso ${arriba.aviso ? "automatico" : ""}`.trim() }, [
        el("div", { className: "producto-meta" }, [
          arriba.leyenda,
          // El aviso más útil va donde se mira primero: el que estás usando.
          producto.desactualizado
            ? el("span", { className: "etiqueta aviso",
                           title: `No apareció en la última búsqueda. Precio del ${(producto.fecha_scrape || "").slice(0, 10)}.`,
                           textContent: "precio viejo" })
            : null,
        ]),
        el("div", { className: "producto-titulo", textContent: producto.nombre_producto }),
        el("div", { className: "producto-meta", textContent:
          [`${pesos.format(producto.precio)} · ${tamanoLegible(producto)}`,
           precioUnitarioLegible(producto)].filter(Boolean).join(" · ") }),
      ])
    : el("div", { className: "en-uso ninguno", textContent: "Sin producto en este súper" });

  // Un súper puede traer 60 candidatos. Mostrarlos todos convierte la pantalla
  // en una pared de precios; con el buscador arriba, diez alcanzan para empezar.
  const TOPE = 10;
  const lista = el("div", { className: "candidatos" });

  const pintarLista = () => {
    const visibles = expandido ? candidatos : candidatos.slice(0, TOPE);
    lista.replaceChildren(
      ...(candidatos.length
        ? visibles.map((p) => tarjetaCandidato(p, seleccionadoId, alElegir, alCambiarTamano))
        : [el("p", { className: "producto-meta", textContent: filtro ? "Nada coincide acá." : "Sin productos." })]),
      candidatos.length > TOPE && !expandido
        ? boton(`Ver los otros ${candidatos.length - TOPE}`, alExpandir, "discreto ver-mas")
        : null,
    );
  };
  pintarLista();

  return el("div", { className: "columna-super" }, [
    el("header", {}, el("h4", { textContent: col.super })),
    caja,
    lista,
  ]);
}

function textoBuscable(p) {
  return `${p.nombre_producto} ${p.marca || ""} ${p.tamano_efectivo || ""}${p.unidad_efectiva || ""}`;
}

function sinAcentos(texto) {
  return (texto || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
}

/** Todas las palabras de la consulta tienen que estar, en cualquier orden.
 *
 * Compara también contra la versión sin espacios ni puntos, para que "1kg"
 * encuentre "bolsa 1 kg." y se pueda buscar el mismo producto en los tres
 * supermercados, que lo escriben distinto. */
function coincideBusqueda(texto, consulta) {
  const base = sinAcentos(texto);
  const compacto = base.replace(/[\s.]/g, "");
  return sinAcentos(consulta).split(/\s+/).filter(Boolean).every((palabra) =>
    base.includes(palabra) || compacto.includes(palabra.replace(/[\s.]/g, "")));
}

function tarjetaCandidato(p, seleccionadoId, alElegir, alCambiarTamano) {
  const tarjeta = el("button", { className: `candidato ${p.id === seleccionadoId ? "elegido" : ""}`.trim(), type: "button" }, [
    el("div", { className: "linea" }, [
      el("span", { className: "producto-titulo" }, [
        el("span", { className: "tilde", textContent: "✓" }),
        p.nombre_producto,
      ]),
      el("span", { className: "precio", textContent: pesos.format(p.precio) }),
    ]),
    el("div", { className: "producto-meta" }, [
      `${p.marca || "sin marca"} · ${tamanoLegible(p)}`,
      p.precio_descuento ? el("span", { className: "etiqueta oferta", textContent: " en oferta" }) : null,
      p.desactualizado
        ? el("span", { className: "etiqueta aviso",
                       title: `No apareció en la última búsqueda. Precio del ${(p.fecha_scrape || "").slice(0, 10)}.`,
                       textContent: "precio viejo" })
        : null,
    ]),
    precioUnitarioLegible(p)
      ? el("div", { className: "producto-unitario", textContent: precioUnitarioLegible(p) })
      : null,
  ]);
  // Al hacer clic, el navegador enfoca el botón y lo desplaza a la vista: la
  // página saltaba antes de que corriera nada. Con el mousedown cancelado no
  // toma el foco con el mouse, y con teclado se sigue pudiendo tabular.
  tarjeta.addEventListener("mousedown", (ev) => ev.preventDefault());
  tarjeta.addEventListener("click", () => alElegir(p));

  const pie = el("div", { className: "producto-meta pie-candidato", style: "display:flex;gap:8px;align-items:center;margin-top:4px" }, [
    p.link ? el("a", { className: "enlace", href: p.link, target: "_blank", rel: "noopener", textContent: "ver en el súper" }) : null,
    !p.tamano_efectivo && alCambiarTamano
      ? boton("cargar tamaño", () => cargarTamano(p, alCambiarTamano), "discreto")
      : null,
  ]);

  return el("div", { className: "candidato-caja" }, [tarjeta, pie]);
}

function cargarTamano(producto, alGuardar) {
  pedirDatos({
    titulo: "¿Cuánto trae este producto?",
    meta: producto.nombre_producto,
    campos: [
      { nombre: "tamano", etiqueta: "Cantidad", tipo: "number", valor: producto.tamano_manual ?? "", ejemplo: "500" },
      { nombre: "unidad", etiqueta: "Unidad", opciones: UNIDADES_BASE, valor: producto.unidad_manual || producto.unidad || "g" },
    ],
    confirmar: "Guardar",
    alConfirmar: async (v) => {
      await api(`/api/productos/${producto.id}/tamano`, { method: "PUT", body: { tamano: +v.tamano, unidad: v.unidad } });
      await alGuardar();
    },
  });
}

// --- ingredientes ---------------------------------------------------------

async function cargarIngredientes(seleccionar = estado.ingredienteId) {
  const ingredientes = await api("/api/ingredientes");
  estado.listaIngredientes = ingredientes;
  pintarListaIngredientes();

  if (!ingredientes.length) {
    return document.getElementById("detalle-ingrediente").replaceChildren(
      el("div", { className: "vacio" }, [
        el("p", { textContent: "Todavía no hay ingredientes." }),
        boton("+ Nuevo ingrediente", nuevoIngrediente, "primario"),
      ]));
  }
  const elegido = ingredientes.some((i) => i.id === seleccionar) ? seleccionar : ingredientes[0].id;
  await verIngrediente(elegido);
}

function pintarListaIngredientes() {
  const filtro = estado.filtroIngredientes.toLowerCase();
  const lista = document.getElementById("lista-ingredientes");
  const visibles = estado.listaIngredientes.filter((i) => i.nombre.toLowerCase().includes(filtro));

  lista.replaceChildren(...visibles.map((i) => {
    const item = el("button", { className: "item", type: "button" }, [
      el("span", { textContent: i.nombre }),
      // Un punto solo cuando queda algo por revisar. El "2/3" era ruido en
      // veinticinco filas y no decía qué hacer.
      i.supers_confirmados < 3
        ? el("span", { className: "punto-pendiente", title: "Falta revisar algún supermercado" })
        : null,
    ]);
    item.classList.toggle("activa", i.id === estado.ingredienteId);
    item.addEventListener("click", () => verIngrediente(i.id));
    return item;
  }));
}

document.getElementById("buscar-ingrediente").addEventListener("input", (ev) => {
  estado.filtroIngredientes = ev.target.value;
  pintarListaIngredientes();
});

async function verIngrediente(ingredienteId) {
  // Cambiar de ingrediente empieza de cero; repintar el mismo (por ejemplo tras
  // cargar un tamaño) conserva lo que el usuario había dejado abierto.
  if (ingredienteId !== estado.ingredienteId) {
    estado.buscarProductos = "";
    estado.expandidos = new Set();
  }
  estado.ingredienteId = ingredienteId;
  fijarUrl();
  pintarListaIngredientes();

  const datos = await api(`/api/ingredientes/${ingredienteId}`);

  const filtro = el("input", { className: "buscador", type: "search",
    value: estado.buscarProductos,
    placeholder: "Buscar en los tres a la vez… ej: ledesma 1kg" });
  const orden = selectorOrden(estado.ordenProductos, () => {
    estado.ordenProductos = orden.value;
    pintar();
  });
  const columnas = el("div", { className: "columnas-super" });

  const pintar = () => columnas.replaceChildren(...datos.supers.map((col) =>
    bloqueSuper(col, {
      arriba: { producto: col.en_uso, ocultarDeLista: true,
                aviso: col.origen === "automatico",
                leyenda: col.origen === "automatico" ? "Usando · sin revisar" : "Usando" },
      seleccionadoId: col.en_uso?.id,
      filtro: estado.buscarProductos,
      orden: estado.ordenProductos,
      expandido: estado.expandidos.has(col.super),
      alExpandir: () => { estado.expandidos.add(col.super); pintar(); },
      alCambiarTamano: () => conservandoScroll(() => verIngrediente(ingredienteId)),
      alElegir: (p) => conservandoScroll(async () => {
        await api(`/api/ingredientes/${ingredienteId}/eleccion`,
                  { method: "PUT", body: { super: col.super, producto_id: p.id } });
        avisar(`Listo: en ${col.super} usás ${p.nombre_producto}.`);
        await verIngrediente(ingredienteId);
      }),
    })));
  filtro.addEventListener("input", () => { estado.buscarProductos = filtro.value; pintar(); });
  pintar();

  document.getElementById("detalle-ingrediente").replaceChildren(
    cabeceraIngrediente(datos),
    el("div", { className: "barra-buscador" }, [filtro, orden]),
    columnas,
  );
}

function cabeceraIngrediente(datos) {
  const { ingrediente, terminos } = datos;

  return el("div", { className: "titulo-detalle" }, [
    el("div", {}, [
      el("h1", { textContent: ingrediente.nombre }),
      // Una sola línea: lo único que hace falta acá es qué se escribe en el
      // buscador de los supermercados y en qué unidad se mide.
      el("div", { className: "meta linea-ingrediente" }, [
        `Se busca como «${terminos.join("», «")}» · se mide en ${ingrediente.unidad_base}`,
        boton("Editar", () => editarTerminos(datos), "discreto enlace-fila"),
      ]),
    ]),
    el("div", { className: "acciones" },
      boton("Borrar ingrediente", async () => {
        const seguro = await confirmar({
          titulo: `¿Borrar "${ingrediente.nombre}"?`,
          mensaje: "Se borran también todos sus productos scrapeados. No se puede deshacer.",
          confirmar: "Borrar ingrediente",
        });
        if (!seguro) return;
        await api(`/api/ingredientes/${ingrediente.id}`, { method: "DELETE" });
        estado.ingredienteId = null;
        await cargarIngredientes(null);
      }, "peligro")),
  ]);
}

function editarTerminos(datos) {
  const { ingrediente, terminos } = datos;

  const campoTerminos = el("input", { className: "campo", type: "text", value: terminos.join(", ") });

  // Las opciones son las categorías que devolvió cada tienda para este
  // ingrediente, con cuántos productos cayeron en cada una. No hay taxonomía
  // propia: las tres nombran distinto y cualquier mapeo nuestro envejece mal.
  const selects = {};
  const columnas = datos.supers.map((col) => {
    const select = el("select", { className: "campo" }, [
      el("option", { value: "", textContent: "Todas las categorías" }),
      ...col.categorias.map((c) => el("option", {
        value: c.categoria,
        textContent: `${c.categoria}  (${c.productos})`,
        selected: c.categoria === col.categoria,
      })),
    ]);
    if (col.categoria && !col.categorias.some((c) => c.categoria === col.categoria)) {
      // La elegida puede no estar entre lo último scrapeado; no se pierde.
      select.append(el("option", { value: col.categoria, textContent: col.categoria, selected: true }));
    }
    selects[col.super] = select;
    return el("label", {}, [
      el("span", { style: "text-transform:capitalize" }, col.super),
      select,
      col.categorias.length
        ? null
        : el("span", { className: "ayuda", textContent: "Todavía no hay productos scrapeados acá." }),
    ]);
  });

  const cuerpo = el("div", { className: "formulario", style: "min-width:520px" }, [
    el("label", {}, ["Cómo buscarlo en los supermercados", campoTerminos,
      el("span", { className: "ayuda", textContent:
        "Separá con comas. Los buscadores son literales: «banana» trae la fruta, «bananas» trae yogur." })]),
    el("div", { className: "separador-campo" }),
    el("p", { className: "ayuda", style: "margin:0", textContent:
      "En qué parte del catálogo de cada tienda vive. Descarta lo que menciona el ingrediente sin serlo." }),
    ...columnas,
  ]);

  const guardar = async () => {
    const lista = campoTerminos.value.split(",").filter((t) => t.trim());
    if (!lista.length) return avisar("Hace falta al menos un término de búsqueda.", true);

    const categorias = Object.fromEntries(
      Object.entries(selects).map(([nombre, sel]) => [nombre, sel.value || null]));
    await api(`/api/ingredientes/${ingrediente.id}/categorias`, { method: "PUT", body: { categorias } });

    const cambiaronTerminos = lista.map((t) => t.trim().toLowerCase()).sort().join(",")
      !== [...terminos].sort().join(",");
    if (cambiaronTerminos) {
      const r = await api(`/api/ingredientes/${ingrediente.id}/terminos`,
                          { method: "PUT", body: { terminos: lista } });
      avisar(r.iniciado ? "Guardado. Buscando precios de nuevo." : "Guardado.");
      refrescarPrecios();
    } else {
      avisar("Guardado.");
    }
    cerrarModal();
    await verIngrediente(ingrediente.id);
  };

  abrirModal({
    titulo: `Cómo encontrar ${ingrediente.nombre}`,
    cuerpo,
    acciones: [boton("Cancelar", cerrarModal, "discreto"), boton("Guardar", guardar, "primario")],
  });
}

function nuevoIngrediente() {
  pedirDatos({
    titulo: "Nuevo ingrediente",
    meta: "Al crearlo se buscan sus precios en los tres supermercados.",
    campos: [
      { nombre: "nombre", etiqueta: "Nombre", ejemplo: "Mascarpone" },
      { nombre: "unidad_base", etiqueta: "Se mide en", opciones: UNIDADES_BASE },
      { nombre: "terminos", etiqueta: "Términos de búsqueda", requerido: false, ejemplo: "queso mascarpone",
        ayuda: "Separados por comas. Si lo dejás vacío se usa el nombre." },
    ],
    confirmar: "Crear y buscar",
    alConfirmar: async (v) => {
      const { id } = await api("/api/ingredientes", {
        method: "POST",
        body: { nombre: v.nombre, unidad_base: v.unidad_base, terminos: v.terminos ? v.terminos.split(",") : [] },
      });
      avisar("Creado. Buscando sus precios en los tres supermercados…");
      refrescarPrecios();
      await cargarIngredientes(id);
      elegirCategoriaAlCrear(id).catch((e) => avisar(e.message, true));
    },
  });
}

document.getElementById("btn-nuevo-ingrediente").addEventListener("click", nuevoIngrediente);

// --- pantalla de carga ----------------------------------------------------

const CLAVE_INTRO = "psapo-ultimo-arranque";

/** Muestra la intro una vez por cada vez que se abre pSapo.
 *
 * El server genera un id nuevo en cada arranque; el navegador guarda el último
 * que vio. Recargar la página no la repite, cerrar y volver a abrir la app sí.
 * Se marca como vista antes de reproducirla: si el usuario recarga a la mitad,
 * no vuelve a empezar.
 */
function mostrarIntro(arranque) {
  const caja = document.getElementById("intro");
  if (!arranque || !caja) return;

  let visto = null;
  try { visto = localStorage.getItem(CLAVE_INTRO); } catch { /* modo privado */ }
  if (visto === arranque) return;
  try { localStorage.setItem(CLAVE_INTRO, arranque); } catch { /* idem */ }

  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

  const video = document.getElementById("intro-video");
  let terminada = false;
  const terminar = () => {
    if (terminada) return;
    terminada = true;
    caja.classList.add("saliendo");
    video.pause();
    setTimeout(() => { caja.hidden = true; video.removeAttribute("src"); video.load(); }, 450);
    document.removeEventListener("keydown", porTecla);
  };
  const porTecla = () => terminar();

  caja.hidden = false;
  caja.addEventListener("click", terminar);
  document.addEventListener("keydown", porTecla);
  video.addEventListener("ended", terminar);
  // Si el video no está o el navegador no lo puede reproducir, la app no puede
  // quedar tapada por una pantalla negra.
  video.addEventListener("error", terminar);

  video.src = "/static/intro.webm";
  // Va muteado a propósito: los navegadores bloquean el autoplay con sonido, y
  // una intro que no arranca es peor que una intro sin audio.
  video.play().catch(terminar);
}

// --- arranque -------------------------------------------------------------

async function arrancar() {
  await refrescarPrecios();
  mostrarIntro(estado.precios?.arranque);

  const ruta = rutaActual() || { vista: "recetas", id: null };
  mostrarVista(ruta.vista);
  // Se cargan las dos listas; la URL manda a quién seleccionar en cada una.
  // `fijarUrl` mira `estado.vista`, así que la carga de la pestaña que no se
  // está viendo no pisa la dirección.
  await Promise.all([
    cargarRecetas(ruta.vista === "recetas" ? ruta.id : null).catch((e) => avisar(e.message, true)),
    cargarIngredientes(ruta.vista === "ingredientes" ? ruta.id : null).catch((e) => avisar(e.message, true)),
  ]);
  // Las dos cargas terminan en cualquier orden; la última palabra sobre la
  // dirección la tiene el estado ya asentado.
  fijarUrl();
}

arrancar();
