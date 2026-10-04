// Forja — orquestación de la página: barra superior, lista de documentos
// del servidor, subida de archivos y apertura de pestañas.
import { listarDocumentos, obtenerDocumento, obtenerMallaConRevision, subirDocumento } from "./api.js?v=11";
import { createLibrary, latestStep } from "./library.js?v=11";
import { TabManager } from "./tabs.js?v=16";
import { startLive } from "./live.js?v=11";

const tabs = new TabManager({
  tabsEl: document.getElementById("fj-tabs"),
  mainEl: document.getElementById("fj-main"),
  vacioEl: document.getElementById("fj-main-vacio"),
  statusEls: {
    nombre: document.getElementById("fj-status-nombre"),
    volumen: document.getElementById("fj-status-volumen"),
    bbox: document.getElementById("fj-status-bbox"),
    solidos: document.getElementById("fj-status-solidos"),
    valido: document.getElementById("fj-status-valido"),
  },
});

const deletedDocuments = new Set();
async function abrirDocumentoPorId(id) {
  if (deletedDocuments.has(id)) return;
  if (tabs.tieneAbierto(id)) { tabs.activar(id); return; }
  const [ficha, malla] = await Promise.all([obtenerDocumento(id), obtenerMallaConRevision(id)]);
  if (deletedDocuments.has(id)) return;
  await tabs.abrirTab(ficha, malla.buffer, malla.revision);
  if (ficha.revision && ficha.revision !== malla.revision) tabs.aplicarCambioExterno(id, ["geometria"], ficha.revision);
  if (deletedDocuments.has(id)) tabs.cerrar(id);
}

const feedback = document.getElementById("fj-feedback");
let busy = false;
async function withLoading(message, action) {
  if (busy) return;
  busy = true;
  feedback.hidden = false;
  feedback.classList.remove("is-error");
  feedback.textContent = message;
  document.getElementById("fj-main").setAttribute("aria-busy", "true");
  btnAbrirArchivo.disabled = true;
  try {
    await action();
    feedback.hidden = true;
  } catch (error) {
    feedback.classList.add("is-error");
    feedback.textContent = `No se pudo abrir el documento: ${error.message}. Puedes volver a intentarlo.`;
  } finally {
    busy = false;
    btnAbrirArchivo.disabled = false;
    document.getElementById("fj-main").setAttribute("aria-busy", "false");
  }
}

// ---------------- Abrir archivo (subida) ----------------
const btnAbrirArchivo = document.getElementById("fj-btn-abrir-archivo");
const inputArchivo = document.getElementById("fj-input-archivo");

btnAbrirArchivo.addEventListener("click", () => inputArchivo.click());
document.getElementById("fj-empty-open").addEventListener("click", () => btnAbrirArchivo.click());

inputArchivo.addEventListener("change", async () => {
  const archivo = inputArchivo.files[0];
  inputArchivo.value = "";
  if (!archivo) return;
  await withLoading(`Importando ${archivo.name}…`, async () => {
    const ficha = await subirDocumento(archivo);
    const malla = await obtenerMallaConRevision(ficha.id);
    await tabs.abrirTab(ficha, malla.buffer, malla.revision);
    if (ficha.revision && ficha.revision !== malla.revision) tabs.aplicarCambioExterno(ficha.id, ["geometria"], ficha.revision);
  });
});

// ---------------- Documentos del servidor (dropdown) ----------------
const btnDocumentos = document.getElementById("fj-btn-documentos");
const dropdownDocumentos = document.getElementById("fj-dropdown-documentos");
const listaDocumentos = document.getElementById("fj-lista-documentos");
const documentSearch = document.getElementById("fj-document-search");
let documents = [];
const library = createLibrary({
  openDocument: doc => withLoading(`Abriendo ${doc.nombre}…`, () => abrirDocumentoPorId(doc.id)),
  onDeleted: id => {
    deletedDocuments.add(id);
    tabs.cerrar(id);
    feedback.hidden = false;
    feedback.classList.remove('is-error');
    feedback.textContent = 'Documento eliminado definitivamente de la biblioteca.';
    documents = documents.filter(doc => doc.id !== id);
    filterDocuments();
    const url = new URL(location.href);
    if (url.searchParams.has('abrir')) {
      const remaining = url.searchParams.get('abrir').split(',').filter(value => value.trim() !== id);
      if (remaining.length) url.searchParams.set('abrir', remaining.join(','));
      else url.searchParams.delete('abrir');
      history.replaceState(null, '', url);
    }
  },
});
document.getElementById('fj-btn-galeria').onclick = () => library.show();

startLive({
  tabs,
  onLibraryChanged: (name, id) => {
    if (name === "documento_eliminado") {
      deletedDocuments.add(id);
      documents = documents.filter(doc => doc.id !== id);
      filterDocuments();
    }
    if (!dropdownDocumentos.hidden) void refrescarListaDocumentos();
    document.dispatchEvent(new CustomEvent("forja:library-changed", { detail: { name, id } }));
  },
});
if (new URLSearchParams(location.search).get("depurar") === "1") {
  window.__forjaDepurar = Object.freeze({ pestanas: () => tabs.depurar() });
}

function filterDocuments() {
  const query = documentSearch.value.trim().toLocaleLowerCase("es");
  renderDocuments(documents.filter(doc => doc.nombre.toLocaleLowerCase("es").includes(query)));
}
documentSearch.addEventListener("input", filterDocuments);
document.getElementById("fj-empty-documents").addEventListener("click", (event) => {
  event.stopPropagation();
  library.show();
});

async function refrescarListaDocumentos() {
  listaDocumentos.textContent = "Cargando documentos…";
  let documentos = [];
  try {
    documentos = await listarDocumentos();
  } catch (err) {
    documents = [];
    listaDocumentos.replaceChildren();
    const aviso = document.createElement("div");
    aviso.className = "fj-dropdown__empty";
    aviso.textContent = `Error al listar: ${err.message}`;
    listaDocumentos.appendChild(aviso);
    return;
  }
  documents = documentos.filter(doc => !deletedDocuments.has(doc.id));
  filterDocuments();
}

function renderDocuments(documentos) {
  listaDocumentos.replaceChildren();
  if (documentos.length === 0) {
    const vacio = document.createElement("div");
    vacio.className = "fj-dropdown__empty";
    vacio.textContent = documents.length ? "No hay documentos que coincidan con la búsqueda." : "Sin documentos en el servidor todavía.";
    listaDocumentos.appendChild(vacio);
    return;
  }
  for (const doc of documentos) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "fj-doc-item";
    item.innerHTML = `<span class="fj-doc-item__nombre"></span><span class="fj-doc-item__meta"></span>`;
    item.querySelector(".fj-doc-item__nombre").textContent = doc.nombre;
    item.querySelector(".fj-doc-item__meta").textContent =
      `${doc.volumen.toFixed(2)} mm³ · ${doc.solidos} sólido(s)`;
    item.addEventListener("click", async () => {
      dropdownDocumentos.hidden = true;
      btnDocumentos.setAttribute("aria-expanded", "false");
      await withLoading(`Abriendo ${doc.nombre}…`, async () => {
        await abrirDocumentoPorId(doc.id);
      });
    });
    const row = document.createElement('div');
    row.className = 'fj-library-row';
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'fj-btn';
    remove.textContent = 'Eliminar';
    remove.setAttribute('aria-label', `Eliminar ${doc.nombre}`);
    remove.onclick = () => library.remove(doc);
    row.append(item, remove);
    listaDocumentos.appendChild(row);
  }
}

btnDocumentos.addEventListener("click", async (ev) => {
  ev.stopPropagation();
  const abrir = dropdownDocumentos.hidden;
  dropdownDocumentos.hidden = !abrir;
  btnDocumentos.setAttribute("aria-expanded", String(abrir));
  if (abrir) {
    documentSearch.focus();
    await refrescarListaDocumentos();
  }
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !dropdownDocumentos.hidden) {
    dropdownDocumentos.hidden = true;
    btnDocumentos.setAttribute("aria-expanded", "false");
    btnDocumentos.focus();
  }
});

document.addEventListener("click", (ev) => {
  if (!dropdownDocumentos.hidden && !dropdownDocumentos.contains(ev.target) && ev.target !== btnDocumentos) {
    dropdownDocumentos.hidden = true;
    btnDocumentos.setAttribute("aria-expanded", "false");
  }
});

// ---------------- Apertura vía ?abrir=id1,id2 (usada por pruebas/capturas) ----------------
async function abrirDesdeQueryParam() {
  const params = new URLSearchParams(location.search);
  const abrir = params.get("abrir");
  if (!params.has('abrir')) {
    await withLoading('Cargando el último STEP…', async () => {
      const latest = latestStep(await listarDocumentos());
      if (latest) await abrirDocumentoPorId(latest.id);
    });
    return;
  }
  if (!abrir) return;
  const ids = abrir.split(",").map((s) => s.trim()).filter(Boolean);
  for (const id of ids) {
    await withLoading("Cargando modelo 3D…", async () => {
      await abrirDocumentoPorId(id);
    });
  }
  // Fase 5C: `&panel=parametros|notas|historial` abre ese panel lateral
  // (mismo uso que `?abrir=`: pruebas/capturas sin clics).
  const panel = params.get("panel");
  if (panel && ["notas", "historial", "parametros", "ensamble", "piezas"].includes(panel)) tabs.abrirPanel(panel);
}

abrirDesdeQueryParam();
