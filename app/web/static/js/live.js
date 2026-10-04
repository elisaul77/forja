import { parseLiveEvent } from "./live-core.js?v=11";
import { listarDocumentos } from "./api.js?v=11";

/** Owns the one SSE connection for this page and reconciles after every hello. */
export function startLive({ tabs, onLibraryChanged = () => {} }) {
  const indicator = document.getElementById("fj-live-status");
  const source = new EventSource("/eventos");
  let hideTimer;
  let dropTimer;
  function show(message, kind = "info", hideAfter = 0) {
    clearTimeout(hideTimer);
    indicator.hidden = false;
    indicator.dataset.kind = kind;
    indicator.textContent = message;
    if (hideAfter) hideTimer = setTimeout(() => { indicator.hidden = true; }, hideAfter);
  }
  function handle(name, event) {
    const data = parseLiveEvent(name, event.data);
    if (!data) return;
    if (name === "hola") {
      clearTimeout(dropTimer);
      tabs.reconciliar(data.documentos);
      tabs.reanudarPendientes();
      show("Vista en vivo conectada", "ok", 2000);
    } else if (name === "resincronizar") {
      void listarDocumentos().then(docs => tabs.reconciliar(docs)).catch(() => {});
    } else if (name === "construyendo") {
      show("Construyendo modelo…");
    } else if (name === "construccion_fallida") {
      show("El último cambio falló", "error", 5000);
    } else if (name === "construccion_terminada") {
      show("Construcción terminada", "ok", 2000);
    } else if (name === "documento_eliminado") {
      tabs.documentoEliminado(data.id);
      onLibraryChanged(name, data.id);
    } else if (name === "documento_creado" || name === "miniatura_lista") {
      onLibraryChanged(name, data.id);
      if (name === "documento_creado") show("Documento creado", "ok", 2000);
    } else if (name === "documento_actualizado") {
      tabs.aplicarCambioExterno(data.id, Array.isArray(data.cambios) ? data.cambios : ["geometria", "notas", "historial", "parametros", "ensamble"], data.revision);
      show("Modelo actualizado", "ok", 2000);
    } else if (name === "anotaciones_actualizadas") {
      tabs.aplicarCambioExterno(data.id, Array.isArray(data.cambios) ? data.cambios : ["notas", "historial"], data.revision);
    } else if (name === "ensamble_actualizado") {
      tabs.aplicarCambioExterno(data.id, Array.isArray(data.cambios) ? data.cambios : ["ensamble", "historial"], data.revision);
    }
  }
  for (const name of ["hola", "resincronizar", "documento_creado", "documento_actualizado", "documento_eliminado", "construyendo", "construccion_terminada", "construccion_fallida", "anotaciones_actualizadas", "ensamble_actualizado", "miniatura_lista"]) {
    source.addEventListener(name, event => handle(name, event));
  }
  source.onerror = () => {
    clearTimeout(dropTimer);
    dropTimer = setTimeout(() => show("Sin conexión en vivo", "error"), 5000);
  };
  source.onopen = () => clearTimeout(dropTimer);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tabs.reanudarPendientes(); });
  // A note/stroke or parameter edit can finish without another server event.
  const interval = setInterval(() => { if (!document.hidden) tabs.reanudarPendientes(); }, 500);
  return () => { clearInterval(interval); clearTimeout(dropTimer); clearTimeout(hideTimer); source.close(); };
}
