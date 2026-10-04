// Forja — panel «Perfil de impresora» (fdm-A): ver el perfil de tolerancias
// activo, generar la probeta de calibración y capturar las mediciones.
// Las funciones puras (compensar, construirMediciones) no tocan el DOM.
import { obtenerTokenSesion } from "./api.js?v=11";

const CLAVE_TOKEN_SESION = "fj_token";

export const GRUPOS = [
  { campo: "agujeros", titulo: "Agujeros verticales", nominales: [3, 5, 8] },
  { campo: "agujeros_horizontales", titulo: "Agujeros horizontales", nominales: [3, 5, 8] },
  { campo: "ejes", titulo: "Ejes", nominales: [3, 5, 8] },
  { campo: "ranuras", titulo: "Ranuras", nominales: [2, 3, 5] },
];
export const HOLGURAS = [0.1, 0.2, 0.3, 0.4, 0.5];

/** Medida a modelar para que la impresa mida `d` (offset = a + b·d). */
export function compensar(d, coef) {
  const a = Number(coef?.a ?? 0);
  const b = Number(coef?.b ?? 0);
  return Math.round(((d - a) / (1 + b)) * 1000) / 1000;
}

/** `{grupo: {nominal: "texto"}}` + holguras → cuerpo `mediciones` del POST.
 * Ignora campos vacíos; lanza Error si algo no es un número positivo. */
export function construirMediciones(valores, holguras = {}) {
  const mediciones = {};
  for (const { campo, nominales } of GRUPOS) {
    const lista = [];
    for (const nominal of nominales) {
      const texto = String(valores?.[campo]?.[nominal] ?? "").trim().replace(",", ".");
      if (!texto) continue;
      const medido = Number(texto);
      if (!Number.isFinite(medido) || medido <= 0) throw new Error(`Medida no válida: ${texto}`);
      lista.push({ nominal, medido });
    }
    if (lista.length) mediciones[campo] = lista;
  }
  for (const clave of ["holgura_deslizante", "holgura_presion"]) {
    const texto = String(holguras[clave] ?? "").trim();
    if (texto) mediciones[clave] = Number(texto);
  }
  return mediciones;
}

async function pedir(url, opciones = {}) {
  const resp = await fetch(url, opciones);
  if (resp.status === 401) sessionStorage.removeItem(CLAVE_TOKEN_SESION);
  if (!resp.ok) {
    let detalle = resp.statusText;
    try { detalle = (await resp.json()).detail ?? detalle; } catch { /* no JSON */ }
    throw new Error(`${resp.status} ${typeof detalle === "string" ? detalle : JSON.stringify(detalle)}`);
  }
  return resp.json();
}

function postConToken(url, cuerpo) {
  const token = obtenerTokenSesion().trim();
  if (!token) return Promise.reject(new Error("Operación cancelada: no se proporcionó la clave de edición"));
  return pedir(url, {
    method: "POST",
    headers: { "X-Forja-Token": token, "Content-Type": "application/json" },
    body: cuerpo === undefined ? undefined : JSON.stringify(cuerpo),
  });
}

function el(tag, attrs = {}, ...hijos) {
  const nodo = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") nodo.className = v;
    else nodo.setAttribute(k, v);
  }
  for (const hijo of hijos) nodo.append(hijo);
  return nodo;
}

function fmt(n) {
  return Number(n).toFixed(2);
}

function resumen(perfil) {
  const filas = [
    ["Perfil", `${perfil.nombre}${perfil.semilla ? " (valores semilla, sin medir)" : ""}`],
    ["Material · boquilla · capa", `${perfil.material ?? "—"} · ${perfil.boquilla ?? "—"} mm · ${perfil.altura_capa ?? "—"} mm`],
    ["Agujero Ø3 / Ø5 / Ø8 → modelar", [3, 5, 8].map(d => fmt(compensar(d, perfil.agujero))).join(" / ")],
    ["Eje Ø3 / Ø5 / Ø8 → modelar", [3, 5, 8].map(d => fmt(compensar(d, perfil.eje))).join(" / ")],
    ["Ranura 2 / 3 / 5 → modelar", [2, 3, 5].map(w => fmt(w - Number(perfil.ranura_offset ?? 0))).join(" / ")],
    ["Holgura deslizante · a presión", `${perfil.holgura_deslizante} · ${perfil.holgura_presion} mm`],
  ];
  const tabla = el("dl", { class: "fj-perfil__resumen" });
  for (const [k, v] of filas) tabla.append(el("dt", {}, k), el("dd", {}, v));
  return tabla;
}

/** Monta el botón y el diálogo. `abrirDocumento(id)` abre la probeta creada. */
export function iniciarPanelPerfil({ boton, dialogo, abrirDocumento }) {
  const cuerpo = dialogo.querySelector("#fj-perfil-cuerpo");
  const estado = dialogo.querySelector("#fj-perfil-estado");
  const avisar = (texto, error = false) => {
    estado.textContent = texto;
    estado.classList.toggle("is-error", error);
  };

  async function pintar() {
    cuerpo.replaceChildren(el("p", { class: "fj-dialogo__ayuda" }, "Cargando perfil…"));
    let perfil;
    try {
      perfil = await pedir("/perfiles/activo");
    } catch (err) {
      cuerpo.replaceChildren();
      avisar(`No se pudo leer el perfil: ${err.message}`, true);
      return;
    }
    const form = el("div", { class: "fj-perfil" });
    form.append(resumen(perfil));
    form.append(el("p", { class: "fj-dialogo__ayuda" },
      "1) Genera la probeta e imprímela con tu material habitual. 2) Mide con el calibre cada elemento " +
      "(fila de arriba: agujeros 3/5/8 y ranuras 2/3/5; abajo: ejes y agujeros horizontales del bloque). " +
      "3) Escribe las medidas reales; deja en blanco lo que no midas."));

    const datos = el("div", { class: "fj-perfil__grid" });
    const campo = (etiqueta, id, valor, tipo = "text") => {
      const input = el("input", { id, class: "fj-input", type: tipo, value: valor ?? "" });
      if (tipo === "number") input.step = "0.01";
      datos.append(el("label", { class: "fj-campo" }, etiqueta, input));
      return input;
    };
    const nombre = campo("Nombre del perfil", "fj-perfil-nombre", perfil.nombre);
    const material = campo("Material", "fj-perfil-material", perfil.material);
    const boquilla = campo("Boquilla (mm)", "fj-perfil-boquilla", perfil.boquilla, "number");
    const capa = campo("Altura de capa (mm)", "fj-perfil-capa", perfil.altura_capa, "number");
    form.append(datos);

    const entradas = {};
    for (const { campo: grupo, titulo, nominales } of GRUPOS) {
      const fila = el("fieldset", { class: "fj-perfil__grupo" }, el("legend", {}, `${titulo} — medida real (mm)`));
      entradas[grupo] = {};
      for (const nominal of nominales) {
        const input = el("input", { class: "fj-input", type: "text", inputmode: "decimal", placeholder: String(nominal) });
        entradas[grupo][nominal] = input;
        fila.append(el("label", { class: "fj-campo" }, `Nominal ${nominal}`, input));
      }
      form.append(fila);
    }
    const selectorHolgura = (etiqueta, actual) => {
      const select = el("select", { class: "fj-select" }, el("option", { value: "" }, "Sin cambio"));
      for (const h of HOLGURAS) select.append(el("option", { value: String(h) }, `${h} mm${h === actual ? " (actual)" : ""}`));
      return { select, label: el("label", { class: "fj-campo" }, etiqueta, select) };
    };
    const deslizante = selectorHolgura("Menor holgura que entra deslizante", perfil.holgura_deslizante);
    const presion = selectorHolgura("Menor holgura que entra a presión", perfil.holgura_presion);
    form.append(el("fieldset", { class: "fj-perfil__grupo" },
      el("legend", {}, "Encaje del pin suelto de 5 mm en los agujeros .1 … .5"), deslizante.label, presion.label));

    const acciones = el("div", { class: "fj-dialogo__acciones" });
    const btnProbeta = el("button", { type: "button", class: "fj-btn" }, "Generar probeta");
    const btnGuardar = el("button", { type: "button", class: "fj-btn fj-btn--primary" }, "Guardar y activar");
    acciones.append(btnProbeta, btnGuardar);
    form.append(acciones);
    cuerpo.replaceChildren(form);

    btnProbeta.onclick = async () => {
      btnProbeta.disabled = true;
      avisar("Generando probeta… (unos segundos)");
      try {
        const doc = await postConToken("/perfiles/probeta");
        avisar("Probeta creada: «Calibración de tolerancias».");
        dialogo.close();
        await abrirDocumento(doc.id);
      } catch (err) {
        avisar(`No se pudo generar la probeta: ${err.message}`, true);
      } finally {
        btnProbeta.disabled = false;
      }
    };
    btnGuardar.onclick = async () => {
      let mediciones;
      try {
        const valores = {};
        for (const [grupo, porNominal] of Object.entries(entradas)) {
          valores[grupo] = Object.fromEntries(Object.entries(porNominal).map(([n, i]) => [n, i.value]));
        }
        mediciones = construirMediciones(valores, {
          holgura_deslizante: deslizante.select.value, holgura_presion: presion.select.value,
        });
      } catch (err) {
        avisar(err.message, true);
        return;
      }
      const cuerpoPost = { nombre: nombre.value.trim(), material: material.value.trim() || null, mediciones };
      if (boquilla.value) cuerpoPost.boquilla = Number(boquilla.value);
      if (capa.value) cuerpoPost.altura_capa = Number(capa.value);
      btnGuardar.disabled = true;
      try {
        await postConToken("/perfiles", cuerpoPost);
        await pintar();
        avisar("Perfil guardado y activo: los scripts nuevos ya lo usan.");
      } catch (err) {
        avisar(`No se pudo guardar: ${err.message}`, true);
      } finally {
        btnGuardar.disabled = false;
      }
    };
  }

  boton.addEventListener("click", () => {
    avisar("");
    dialogo.showModal();
    void pintar();
  });
  dialogo.querySelector("#fj-perfil-cerrar").addEventListener("click", () => dialogo.close());
}
