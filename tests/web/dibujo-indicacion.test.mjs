import test from "node:test";
import assert from "node:assert/strict";
import {
  BorradorIndicacion,
  MAX_TRAZOS_INDICACION,
  claseModo,
  resolverRegionIndicacion,
} from "../../app/web/static/js/dibujo-indicacion.js";

// ---------------- Modelo puro: trazos de una indicación en curso ----------------

test("dos trazos seguidos quedan en el mismo borrador de indicación", () => {
  const borrador = new BorradorIndicacion();
  assert.equal(borrador.añadirTrazo([[0, 0, 0], [10, 0, 0]]), true);
  assert.equal(borrador.añadirTrazo([[0, 5, 0], [10, 5, 0]]), true);
  assert.equal(borrador.cantidad, 2);
  assert.deepEqual(borrador.finalizar(), [
    [[0, 0, 0], [10, 0, 0]],
    [[0, 5, 0], [10, 5, 0]],
  ]);
});

test("deshacer quita solo el último trazo y el borrador sigue abierto", () => {
  const borrador = new BorradorIndicacion();
  borrador.añadirTrazo([[0, 0, 0], [1, 0, 0]]);
  borrador.añadirTrazo([[0, 1, 0], [1, 1, 0]]);
  const quitado = borrador.deshacerTrazo();
  assert.deepEqual(quitado, [[0, 1, 0], [1, 1, 0]]);
  assert.equal(borrador.cantidad, 1);
  assert.deepEqual(borrador.trazos, [[[0, 0, 0], [1, 0, 0]]]);
  borrador.deshacerTrazo();
  assert.equal(borrador.deshacerTrazo(), null);
  assert.equal(borrador.finalizar(), null);
});

test("un trazo de un solo punto no cuenta y los puntos se copian", () => {
  const borrador = new BorradorIndicacion();
  assert.equal(borrador.añadirTrazo([[0, 0, 0]]), false);
  assert.equal(borrador.cantidad, 0);
  const puntos = [[0, 0, 0], [2, 0, 0]];
  borrador.añadirTrazo(puntos);
  puntos[0][0] = 99;
  assert.equal(borrador.trazos[0][0][0], 0);
});

test("la región de la indicación agrupa los puntos de todos los trazos", () => {
  const borrador = new BorradorIndicacion();
  borrador.añadirTrazo([[0, 0, 0], [1, 0, 0]]);
  borrador.añadirTrazo([[5, 5, 5], [6, 5, 5], [7, 5, 5]]);
  assert.equal(borrador.puntos().length, 5);
});

test("no se admiten más trazos que el tope por indicación", () => {
  const borrador = new BorradorIndicacion();
  for (let i = 0; i < MAX_TRAZOS_INDICACION; i++) borrador.añadirTrazo([[i, 0, 0], [i, 1, 0]]);
  assert.equal(borrador.añadirTrazo([[0, 0, 0], [1, 1, 0]]), false);
  assert.equal(borrador.cantidad, MAX_TRAZOS_INDICACION);
});

// ---------------- Controlador de notes.js con DOM y red falsos ----------------

function fakeEl() {
  return {
    style: {},
    className: "",
    textContent: "",
    disabled: false,
    hidden: false,
    setAttribute() {},
    addEventListener() {},
    removeEventListener() {},
    append() {},
    appendChild() {},
    remove() {},
  };
}

globalThis.document = { getElementById: () => null, createElement: () => fakeEl() };
globalThis.window = { addEventListener() {}, removeEventListener() {} };
globalThis.getComputedStyle = () => ({ position: "relative" });

const llamadas = [];
let contadorTrazos = 0;
// Fallos simulados del servidor para la indicación (los tests lo activan y lo desactivan).
const reglas = { fallarIntencion: false, fallarTrazoDesde: 0 };
globalThis.fetch = async (url, opts = {}) => {
  const cuerpo = opts.body ? JSON.parse(opts.body) : null;
  llamadas.push({ url: String(url), method: opts.method ?? "GET", body: cuerpo });
  let respuesta = {};
  let status = 200;
  if (String(url).endsWith("/trazos")) {
    contadorTrazos += 1;
    if (reglas.fallarTrazoDesde && contadorTrazos >= reglas.fallarTrazoDesde) {
      status = 500;
      respuesta = { detail: "trazo simulado" };
    } else {
      respuesta = { ...cuerpo, n: `t${contadorTrazos}`, visible: true };
    }
  } else if (String(url).endsWith("/design/intents")) {
    // Igual que el servidor: una región (points) exige pieza.
    if (cuerpo.points && !cuerpo.piece) {
      status = 422;
      respuesta = { detail: "Una región necesita pieza" };
    } else if (reglas.fallarIntencion) {
      status = 500;
      respuesta = { detail: "fallo simulado" };
    } else {
      respuesta = {
        created_intent: "i1",
        design: { intents: [{ id: "i1", marks: cuerpo.marks }] },
      };
    }
  } else if (String(url).includes("/trazos/") && opts.method === "DELETE") {
    respuesta = { ok: true };
  } else if (String(url).includes("/notas?")) {
    respuesta = { resumen: [] };
  }
  return { ok: status < 400, status, json: async () => respuesta };
};

const { ForjaNotasControlador } = await import("../../app/web/static/js/notes.js");

function fakeViewer() {
  return {
    renderer: { domElement: fakeEl() },
    container: fakeEl(),
    controls: { mouseButtons: { LEFT: 0, MIDDLE: 1, RIGHT: 2 }, enabled: true, enableRotate: true },
    selectedPiece: null,
    _planoPizarra: { origen: [0, 0, 0], normal: [0, 0, 1] },
    mostrarAnotaciones() {},
    mostrarPlanoPizarra() {},
    ocultarPlanoPizarra() {},
    proyectarEnPlanoPizarra: (x, y) => [x, y, 0],
    raycastearMalla: () => null,
  };
}

/** Un trazo con el ratón: bajar, mover por los puntos y soltar. */
async function trazar(ctrl, puntos) {
  ctrl._alBajarBoton({ button: 0, clientX: puntos[0][0], clientY: puntos[0][1] });
  for (const p of puntos.slice(1)) ctrl._alMoverMouse({ clientX: p[0], clientY: p[1] });
  await ctrl._alSoltarBoton({ button: 0 });
}

function prepararControlador() {
  llamadas.length = 0;
  contadorTrazos = 0;
  const ctrl = new ForjaNotasControlador(fakeViewer(), "doc-dibujo");
  ctrl._configPizarraPendiente = { modo: "vista", offset: 0, tipo: "anadir", comentario: "Engrosar la base" };
  ctrl._entrarEnDibujo();
  return ctrl;
}

test("dos trazos con el ratón siguen en la misma indicación hasta Terminar", async () => {
  const ctrl = prepararControlador();
  await trazar(ctrl, [[0, 0], [10, 0]]);
  // Tras soltar el primer trazo el modo sigue activo: no se envía nada aún.
  assert.equal(ctrl.modo, "pizarra-dibujando");
  assert.equal(llamadas.length, 0);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  assert.equal(ctrl.modo, "pizarra-dibujando");
  assert.equal(ctrl._borrador.cantidad, 2);
  assert.equal(llamadas.length, 0);

  await ctrl._terminarIndicacion();

  const trazosEnviados = llamadas.filter((l) => l.url.endsWith("/trazos"));
  assert.equal(trazosEnviados.length, 2);
  const indicacion = llamadas.find((l) => l.url.endsWith("/design/intents"));
  assert.ok(indicacion, "debe crear la indicación");
  assert.deepEqual(indicacion.body.marks, ["t1", "t2"]);
  assert.equal(indicacion.body.text, "Engrosar la base");
  assert.equal(ctrl.modo, null);
});

test("Escape descarta todos los trazos sin llamar al servidor", async () => {
  const ctrl = prepararControlador();
  await trazar(ctrl, [[0, 0], [10, 0]]);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  ctrl._alTecla({ key: "Escape", target: { tagName: "BODY" }, preventDefault() {}, stopPropagation() {} });
  assert.equal(ctrl.modo, null);
  assert.equal(llamadas.length, 0);
});

test("Ctrl+Z quita el último trazo sin salir del modo", async () => {
  const ctrl = prepararControlador();
  await trazar(ctrl, [[0, 0], [10, 0]]);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  ctrl._alTecla({
    key: "z",
    ctrlKey: true,
    target: { tagName: "BODY" },
    preventDefault() {},
    stopPropagation() {},
  });
  assert.equal(ctrl.modo, "pizarra-dibujando");
  assert.equal(ctrl._borrador.cantidad, 1);
  assert.equal(llamadas.length, 0);
});

test("Terminar sin trazos no envía nada", async () => {
  const ctrl = prepararControlador();
  await ctrl._terminarIndicacion();
  assert.equal(ctrl.modo, "pizarra-dibujando");
  assert.equal(llamadas.length, 0);
});

test("claseModo marca el botón de nota o de pizarra según el modo activo", () => {
  assert.deepEqual(claseModo(null), { nota: false, pizarra: false });
  assert.deepEqual(claseModo("nota"), { nota: true, pizarra: false });
  assert.deepEqual(claseModo("pizarra-esperando-cara"), { nota: false, pizarra: true });
  assert.deepEqual(claseModo("pizarra-vista-pendiente"), { nota: false, pizarra: true });
  assert.deepEqual(claseModo("pizarra-dibujando"), { nota: false, pizarra: true });
});

test("el controlador avisa cada cambio de modo: al entrar a dibujar y al terminar, cancelar o Esc", async () => {
  const cambios = [];
  const ctrl = new ForjaNotasControlador(fakeViewer(), "doc-modos");
  ctrl.alCambiarModo = (modo) => cambios.push(modo);
  ctrl._configPizarraPendiente = { modo: "vista", offset: 0, tipo: "anadir", comentario: "Engrosar" };
  ctrl._entrarEnDibujo();
  await trazar(ctrl, [[0, 0], [10, 0]]);
  assert.deepEqual(cambios, ["pizarra-dibujando"]);
  await ctrl._terminarIndicacion();
  assert.deepEqual(cambios, ["pizarra-dibujando", null]);

  cambios.length = 0;
  ctrl._entrarEnDibujo();
  ctrl._alTecla({ key: "Escape", target: { tagName: "BODY" }, preventDefault() {}, stopPropagation() {} });
  assert.deepEqual(cambios, ["pizarra-dibujando", null]);
});

test("Esc dentro de un campo no descarta los trazos de la indicación", async () => {
  const ctrl = prepararControlador();
  await trazar(ctrl, [[0, 0], [10, 0]]);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  let propagacionParada = false;
  ctrl._alTecla({
    key: "Escape",
    target: { tagName: "INPUT" },
    preventDefault() {},
    stopPropagation() {
      propagacionParada = true;
    },
  });
  assert.equal(ctrl.modo, "pizarra-dibujando");
  assert.equal(ctrl._borrador.cantidad, 2);
  assert.equal(propagacionParada, false);
  assert.equal(llamadas.length, 0);
});

// ---------------- Varios trazos: pieza por trazo y región sin huérfanos ----------------

test("cada trazo recuerda la pieza que tocó y deshacer la quita con él", () => {
  const borrador = new BorradorIndicacion();
  borrador.añadirTrazo([[0, 0, 0], [1, 0, 0]], "Base");
  borrador.añadirTrazo([[5, 5, 5], [6, 5, 5]]);
  assert.deepEqual(borrador.piezas, ["Base", null]);
  borrador.deshacerTrazo();
  assert.deepEqual(borrador.piezas, ["Base"]);
});

test("la región es la pieza que más trazos tocan, con solo los puntos de esos trazos", () => {
  const trazos = [
    [[0, 0, 0], [1, 0, 0]],
    [[0, 5, 0], [1, 5, 0]],
    [[9, 9, 9], [8, 8, 8]],
  ];
  assert.deepEqual(resolverRegionIndicacion(trazos, ["A", "B", "B"]), {
    pieza: "B",
    puntos: [[0, 5, 0], [1, 5, 0], [9, 9, 9], [8, 8, 8]],
  });
});

test("si ningún trazo toca una pieza la indicación no lleva región", () => {
  const trazos = [[[0, 0, 0], [1, 0, 0]], [[0, 5, 0], [1, 5, 0]]];
  assert.deepEqual(resolverRegionIndicacion(trazos, [null, null]), { pieza: null, puntos: [] });
});

function visorConPieza(piezaEn) {
  const visor = fakeViewer();
  visor.raycastearMalla = (x) => {
    const pieza = piezaEn(x);
    return pieza ? { punto: [x, 0, 0], pieceName: pieza } : null;
  };
  return visor;
}

function prepararControladorCon(visor, docId) {
  llamadas.length = 0;
  contadorTrazos = 0;
  const ctrl = new ForjaNotasControlador(visor, docId);
  ctrl._configPizarraPendiente = { modo: "vista", offset: 0, tipo: "anadir", comentario: "Engrosar la base" };
  ctrl._entrarEnDibujo();
  return ctrl;
}

test("varios trazos sobre una pieza: la indicación lleva esa pieza y solo sus puntos", async () => {
  const ctrl = prepararControladorCon(visorConPieza((x) => (x < 50 ? "Base" : null)), "doc-varios");
  await trazar(ctrl, [[0, 0], [10, 0]]);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  await trazar(ctrl, [[100, 0], [110, 0]]);
  await ctrl._terminarIndicacion();

  const indicacion = llamadas.find((l) => l.url.endsWith("/design/intents"));
  assert.ok(indicacion, "debe crear la indicación");
  assert.equal(indicacion.body.piece, "Base");
  assert.deepEqual(indicacion.body.points, [[0, 0, 0], [10, 0, 0], [0, 5, 0], [10, 5, 0]]);
  assert.deepEqual(indicacion.body.marks, ["t1", "t2", "t3"]);
  assert.equal(ctrl.modo, null);
});

test("trazos sobre el vacío: la indicación se crea sin región y sin error", async () => {
  const ctrl = prepararControladorCon(visorConPieza(() => null), "doc-vacio");
  await trazar(ctrl, [[0, 0], [10, 0]]);
  await trazar(ctrl, [[0, 5], [10, 5]]);
  await ctrl._terminarIndicacion();

  const indicacion = llamadas.find((l) => l.url.endsWith("/design/intents"));
  assert.ok(indicacion, "debe crear la indicación");
  assert.equal(indicacion.body.piece, undefined);
  assert.equal(indicacion.body.points, undefined);
  assert.deepEqual(indicacion.body.marks, ["t1", "t2"]);
  assert.equal(indicacion.body.text, "Engrosar la base");
  assert.deepEqual(llamadas.filter((l) => l.method === "DELETE"), []);
});

test("si la indicación no se crea, los trazos guardados se descartan y el aviso lo dice", async () => {
  const feedback = fakeEl();
  const documentoAnterior = globalThis.document;
  globalThis.document = {
    getElementById: (id) => (id === "fj-feedback" ? feedback : null),
    createElement: () => fakeEl(),
  };
  reglas.fallarIntencion = true;
  try {
    const ctrl = prepararControladorCon(visorConPieza((x) => (x < 50 ? "Base" : null)), "doc-fallo");
    await trazar(ctrl, [[0, 0], [10, 0]]);
    await trazar(ctrl, [[0, 5], [10, 5]]);
    await ctrl._terminarIndicacion();

    const borrados = llamadas.filter((l) => l.method === "DELETE").map((l) => l.url);
    assert.equal(borrados.length, 2, "cada trazo recién guardado se borra");
    assert.ok(borrados[0].endsWith("/trazos/t1") && borrados[1].endsWith("/trazos/t2"));
    assert.match(feedback.textContent, /No se guardó la indicación/);
    assert.match(feedback.textContent, /descartaron/);
  } finally {
    reglas.fallarIntencion = false;
    globalThis.document = documentoAnterior;
  }
});

test("si algún trazo no se llega a guardar, la región solo cubre los trazos guardados", async () => {
  reglas.fallarTrazoDesde = 2;
  try {
    const ctrl = prepararControladorCon(visorConPieza((x) => (x < 50 ? "Base" : null)), "doc-parcial");
    await trazar(ctrl, [[0, 0], [10, 0]]);
    await trazar(ctrl, [[0, 5], [10, 5]]);
    await ctrl._terminarIndicacion();

    const indicacion = llamadas.find((l) => l.url.endsWith("/design/intents"));
    assert.ok(indicacion, "las marcas guardadas se enlazan igualmente");
    assert.deepEqual(indicacion.body.marks, ["t1"]);
    assert.equal(indicacion.body.piece, "Base");
    assert.deepEqual(indicacion.body.points, [[0, 0, 0], [10, 0, 0]]);
  } finally {
    reglas.fallarTrazoDesde = 0;
  }
});
