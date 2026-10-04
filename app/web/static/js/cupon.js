// Forja — «Cupón de prueba» (fdm-B): pide al servidor un documento nuevo con
// solo las zonas donde las piezas encajan, lo abre en una pestaña y ofrece
// abrirlo en Orca. Funciones puras salvo `crearBotonCupon`, que recibe el
// `document` y el `fetch` (inyectables en las pruebas).

import { buildOrcaLink } from "./orca.js?v=3";

/** Cuerpo JSON de `POST /documentos/{id}/cupon`: solo la pieza si la hay. */
export function cuerpoCupon(pieza) {
  return typeof pieza === "string" && pieza ? { pieza } : {};
}

/** Resumen en una línea de la respuesta del servidor. */
export function resumenCupon(datos) {
  const piezas = (datos && Array.isArray(datos.piezas) ? datos.piezas : []).map((p) => p.nombre);
  const zonas = datos && Array.isArray(datos.zonas) ? datos.zonas.length : 0;
  const z = zonas === 1 ? "1 zona" : `${zonas} zonas`;
  return `Cupón creado (${z}): ${piezas.join(", ") || "sin piezas"}.`;
}

/** Mensaje de error legible desde el `detail` de FastAPI. */
export function errorCupon(status, detalle) {
  if (status === 401) return "Clave de edición incorrecta.";
  const texto = typeof detalle === "string" ? detalle : "error del servidor";
  return `No se pudo crear el cupón: ${texto}.`;
}

/**
 * Botón «Cupón de prueba». `opciones`: `{ id, obtenerPieza, obtenerToken,
 * abrirDocumento(id), mostrar(texto, esError, enlace), origin, fetchFn }`.
 */
export function crearBotonCupon(doc, opciones) {
  const { id, obtenerPieza, obtenerToken, abrirDocumento, mostrar, origin, fetchFn } = opciones;
  const boton = doc.createElement("button");
  boton.type = "button";
  boton.className = "fj-btn";
  boton.textContent = "Cupón de prueba";
  boton.title = "Crear un documento nuevo solo con las zonas donde las piezas encajan (usa la pieza seleccionada si hay)";
  boton.addEventListener("click", async () => {
    const token = (obtenerToken() || "").trim();
    if (!token) { mostrar("Operación cancelada: no se proporcionó la clave de edición", true); return; }
    boton.disabled = true;
    mostrar("Generando cupón de prueba…", false);
    try {
      const resp = await (fetchFn || fetch)(`/documentos/${encodeURIComponent(id)}/cupon`, {
        method: "POST",
        headers: { "X-Forja-Token": token, "Content-Type": "application/json" },
        body: JSON.stringify(cuerpoCupon(obtenerPieza ? obtenerPieza() : null)),
      });
      const datos = await resp.json().catch(() => ({}));
      if (!resp.ok) { mostrar(errorCupon(resp.status, datos.detail), true); return; }
      if (typeof abrirDocumento === "function") await abrirDocumento(datos.id_nuevo);
      const orca = buildOrcaLink(origin, datos.id_nuevo, datos.nombre, "3mf");
      mostrar(resumenCupon(datos), false, orca ? { texto: "Abrir en Orca", href: orca } : null);
    } catch (error) {
      mostrar(`No se pudo crear el cupón: ${error.message}.`, true);
    } finally {
      boton.disabled = false;
    }
  });
  return boton;
}
