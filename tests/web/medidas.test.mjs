import test from "node:test";
import assert from "node:assert/strict";
import {
  aceptarRespuesta, etiquetaEstado, textoDistancia, lineasResumen, renderizarMedidas, ForjaMedidasControlador,
} from "../../app/web/static/js/medidas.js";

// Documento falso mínimo: registra innerHTML y permite recorrer el texto.
function fakeDoc() {
  const doc = {
    innerHtmlUsado: false,
    createElement(tag) {
      const el = {
        tag, children: [], _text: "", className: "", classes: new Set(), hidden: false,
        append(...nodos) { this.children.push(...nodos); },
        replaceChildren(...nodos) { this.children = nodos; },
        classList: {
          toggle(clase, on) { if (on) el.classes.add(clase); else el.classes.delete(clase); return on; },
          contains(clase) { return el.classes.has(clase); },
        },
      };
      Object.defineProperty(el, "textContent", {
        get() { return this._text; },
        set(valor) { this._text = String(valor); },
      });
      Object.defineProperty(el, "innerHTML", {
        get() { return ""; },
        set() { doc.innerHtmlUsado = true; },
      });
      return el;
    },
  };
  return doc;
}

function textoDeArbol(el) {
  return el._text + el.children.map(textoDeArbol).join("");
}

function etiquetas(el, salida = []) {
  salida.push(el.tag);
  for (const hijo of el.children) etiquetas(hijo, salida);
  return salida;
}

const DATOS = {
  revision: "rev-2", solidos: 3, choques: 1, completo: true, tope_calculos: 500,
  nombres_inciertos: false, pares: [
    { a: "base", b: "x", estado: "choque", distancia_mm: 0, volumen_mm3: 800 },
    { a: "caja_a", b: "caja_b", estado: "holgura", distancia_mm: 3 },
  ],
  ocultos: 0, parametros: { lado: 10 },
};

test("una respuesta solo vale si es la última pedida y de la revisión vigente", () => {
  assert.equal(aceptarRespuesta({ seq: 1, seqActual: 2, revisionEsperada: "r", revision: "r" }), false);
  assert.equal(aceptarRespuesta({ seq: 2, seqActual: 2, revisionEsperada: "r2", revision: "r1" }), false);
  assert.equal(aceptarRespuesta({ seq: 2, seqActual: 2, revisionEsperada: "r2", revision: "r2" }), true);
  assert.equal(aceptarRespuesta({ seq: 2, seqActual: 2, revisionEsperada: null, revision: "r1" }), true);
});

test("textos en español: estados, distancias a 3 decimales y avisos de cálculo", () => {
  assert.equal(etiquetaEstado("choque"), "Choque");
  assert.equal(etiquetaEstado("holgura"), "Holgura");
  assert.equal(textoDistancia(3), "3.000 mm");
  assert.equal(textoDistancia(0.0004), "0.000 mm");
  const incompleto = lineasResumen({ ...DATOS, completo: false, nombres_inciertos: true });
  assert.match(incompleto[1], /tope de 500 comprobaciones/);
  assert.equal(incompleto[2], "Nombres de sólido inciertos.");
});

test("los nombres de sólido se pintan como texto, nunca como HTML", () => {
  const doc = fakeDoc();
  const contenedor = doc.createElement("div");
  const malicioso = {
    ...DATOS,
    pares: [{ a: "<img src=x onerror=alert(1)>", b: 'caja"><script>', estado: "contacto", distancia_mm: 0 }],
  };
  renderizarMedidas(contenedor, { tipo: "listo", datos: malicioso, obsoleto: false, cargando: false }, doc);
  const texto = textoDeArbol(contenedor);
  assert.match(texto, /<img src=x onerror=alert\(1\)> ↔ caja"><script>/);
  assert.equal(etiquetas(contenedor).includes("img"), false);
  assert.equal(etiquetas(contenedor).includes("script"), false);
  assert.equal(doc.innerHtmlUsado, false);
});

test("datos obsoletos quedan en gris y el aviso dice que se construye", () => {
  const doc = fakeDoc();
  const contenedor = doc.createElement("div");
  renderizarMedidas(contenedor, { tipo: "listo", datos: DATOS, obsoleto: true, cargando: false }, doc);
  assert.equal(contenedor.classes.has("is-obsoleto"), true);
  assert.match(textoDeArbol(contenedor), /Construyendo… \(cifras anteriores\)/);
  assert.match(textoDeArbol(contenedor), /base ↔ x/);
});

function controladorConFetch(respuestas) {
  const doc = fakeDoc();
  const contenedor = doc.createElement("div");
  const pedidos = [];
  const fetchImpl = (url) => {
    const pendiente = respuestas.shift();
    pedidos.push(url);
    return pendiente.promesa;
  };
  const controlador = new ForjaMedidasControlador({ docId: "doc-1", contenedor, fetchImpl, doc });
  return { controlador, contenedor, pedidos, doc };
}

function respuestaDiferida(cuerpo, { ok = true, status = 200 } = {}) {
  let resolver;
  const promesa = new Promise((resolve) => { resolver = resolve; });
  const resp = { ok, status, statusText: "", json: async () => cuerpo };
  return { promesa: promesa.then(() => resp), resolver: () => resolver(resp) };
}

test("una respuesta de una revisión anterior llega tarde y se descarta", async () => {
  const vieja = respuestaDiferida({ ...DATOS, revision: "rev-1" });
  const nueva = respuestaDiferida({ ...DATOS, revision: "rev-2" });
  const { controlador, contenedor, pedidos } = controladorConFetch([vieja, nueva]);
  controlador.mostrar("rev-1");
  controlador.nuevaRevision("rev-2");
  nueva.resolver();
  await new Promise((r) => setTimeout(r, 0));
  vieja.resolver();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(pedidos.length, 2);
  assert.equal(controlador.datos.revision, "rev-2");
  assert.match(textoDeArbol(contenedor), /base ↔ x/);
  assert.equal(pedidos[0], "/documentos/doc-1/medidas?top=20");
});

test("un documento de malla (400) muestra «sin desglose de sólidos»", async () => {
  const respuesta = respuestaDiferida({ detail: "medidas solo esta disponible para documentos STEP/script (no malla STL)" }, { ok: false, status: 400 });
  const { controlador, contenedor } = controladorConFetch([respuesta]);
  controlador.mostrar("rev-1");
  respuesta.resolver();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(controlador.error, "sin desglose de sólidos");
  assert.match(textoDeArbol(contenedor), /sin desglose de sólidos/);
});

test("un cambio de revisión con el panel cerrado no pide nada", () => {
  const { controlador, pedidos } = controladorConFetch([]);
  controlador.visible = () => false;
  controlador.nuevaRevision("rev-9");
  assert.equal(pedidos.length, 0);
  assert.equal(controlador.revisionObjetivo, "rev-9");
});

test("marcar construyendo deja las cifras actuales en gris", () => {
  const { controlador, contenedor } = controladorConFetch([]);
  controlador.datos = DATOS;
  controlador.marcarConstruyendo();
  assert.equal(contenedor.classes.has("is-obsoleto"), true);
});
