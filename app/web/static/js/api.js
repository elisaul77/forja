// Forja — cliente HTTP mínimo contra el backend FastAPI.
// Contrato: la malla siempre viaja binaria (STL), nunca como arreglo JSON.

const BASE = "";

// Fase 4 (fix-review): `restaurar` exige el token compartido (ADR-0005: toda
// ruta que muta estado lo exige). El visor web no lo conoce de antemano, asi
// que se pide una sola vez por pestaña del navegador y se recuerda en
// sessionStorage (nunca en localStorage: no debe sobrevivir a cerrar el
// navegador).
const CLAVE_TOKEN_SESION = "fj_token";

export function obtenerTokenSesion() {
  let token = sessionStorage.getItem(CLAVE_TOKEN_SESION);
  if (!token) {
    token = window.prompt(
      "Esta acción modifica o elimina un documento y requiere el token de Forja.\n\n" +
        "Introduce el token configurado para esta instancia. Se recuerda solo durante esta pestaña."
    );
    if (token) sessionStorage.setItem(CLAVE_TOKEN_SESION, token);
  }
  return token ?? "";
}

export async function eliminarDocumento(id) {
  const token = obtenerTokenSesion().trim();
  if (!token) throw new Error("Operación cancelada: no se proporcionó la clave de edición");
  const resp = await fetch(`${BASE}/documentos/${encodeURIComponent(id)}`, {
    method: "DELETE", headers: { "X-Forja-Token": token },
  });
  if (resp.status === 401) sessionStorage.removeItem(CLAVE_TOKEN_SESION);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

async function parseError(resp) {
  let detalle = resp.statusText;
  try {
    const cuerpo = await resp.json();
    detalle = cuerpo.detail ?? detalle;
  } catch {
    // cuerpo no era JSON; se conserva statusText
  }
  return new Error(`${resp.status} ${detalle}`);
}

/** Lista compacta de documentos ya existentes en el servidor. */
export async function listarDocumentos() {
  const resp = await fetch(`${BASE}/documentos`);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

/** Ficha completa de un documento (incluye bbox/volumen/solidos/valido). */
export async function obtenerDocumento(id) {
  const resp = await fetch(`${BASE}/documentos/${id}`);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

/** Bytes binarios de la malla (STL) de un documento. */
export async function obtenerMalla(id) {
  return (await obtenerMallaConRevision(id)).buffer;
}

/** Malla y revisión del mismo snapshot del servidor. */
export async function obtenerMallaConRevision(id, { signal } = {}) {
  const resp = await fetch(`${BASE}/documentos/${encodeURIComponent(id)}/malla?componentes=true`, { signal });
  if (!resp.ok) throw await parseError(resp);
  const revision = resp.headers.get("X-Forja-Revision");
  return { buffer: await resp.arrayBuffer(), revision };
}

/** Sube un archivo STEP/STL nuevo; devuelve la ficha completa creada. */
export async function subirDocumento(file) {
  const datos = new FormData();
  datos.append("file", file);
  const resp = await fetch(`${BASE}/documentos`, { method: "POST", body: datos });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

// ---------------- Fase 4: nombrado estable, notas/pizarra, historial ----------------

/** Lista compacta de caras `[{id, tipo, centroide, normal, area}]` (solo STEP). */
export async function obtenerCaras(id) {
  const resp = await fetch(`${BASE}/documentos/${id}/caras`);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

/** `Uint32Array`: por cada triángulo de `/malla`, la posición en `obtenerCaras()`. */
export async function obtenerCarasTriangulos(id) {
  const resp = await fetch(`${BASE}/documentos/${id}/caras_triangulos`);
  if (!resp.ok) throw await parseError(resp);
  return new Uint32Array(await resp.arrayBuffer());
}

/** `{resumen: [...]}` — notas (pines) y trazos (pizarra) de un documento. */
export async function listarNotas(id, detalle = false) {
  const resp = await fetch(`${BASE}/documentos/${id}/notas?detalle=${detalle}`);
  if (!resp.ok) throw await parseError(resp);
  return (await resp.json()).resumen;
}

export async function crearNota(id, comentario, referencia) {
  const resp = await fetch(`${BASE}/documentos/${id}/notas`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ comentario, referencia }),
  });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function borrarNota(id, notaId) {
  const resp = await fetch(`${BASE}/documentos/${id}/notas/${notaId}`, { method: "DELETE" });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function actualizarVisibilidadNota(id, notaId, visible) {
  const resp = await fetch(`${BASE}/documentos/${id}/notas/${notaId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ visible }),
  });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function crearTrazo(id, cuerpo) {
  const resp = await fetch(`${BASE}/documentos/${id}/trazos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cuerpo),
  });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function borrarTrazo(id, trazoId) {
  const resp = await fetch(`${BASE}/documentos/${id}/trazos/${trazoId}`, { method: "DELETE" });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function actualizarVisibilidadTrazo(id, trazoId, visible) {
  const resp = await fetch(`${BASE}/documentos/${id}/trazos/${trazoId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ visible }),
  });
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

/** `[{id, fecha, mensaje}]` — historial compacto de un documento. */
export async function obtenerHistorial(id) {
  const resp = await fetch(`${BASE}/documentos/${id}/historial`);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

// ---------------- Fase 5C: scripts paramétricos ----------------

/** `{esquema, valores}` — ambos `{}` si el documento no declara PARAMETROS. */
export async function obtenerParametros(id) {
  const resp = await fetch(`${BASE}/documentos/${id}/parametros`);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

/** Re-ejecuta el script del documento con `valores`; devuelve la ficha + `valores` + `ms`. */
export async function aplicarParametros(id, valores) {
  const resp = await fetch(`${BASE}/documentos/${id}/parametros`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Forja-Token": obtenerTokenSesion(),
    },
    body: JSON.stringify({ valores }),
  });
  if (resp.status === 401) sessionStorage.removeItem(CLAVE_TOKEN_SESION);
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}

export async function restaurarDocumento(id, snapshot) {
  const resp = await fetch(`${BASE}/documentos/${id}/restaurar`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Forja-Token": obtenerTokenSesion(),
    },
    body: JSON.stringify({ snapshot }),
  });
  if (resp.status === 401) {
    // token vacio/incorrecto: se olvida para volver a pedirlo la proxima vez.
    sessionStorage.removeItem(CLAVE_TOKEN_SESION);
  }
  if (!resp.ok) throw await parseError(resp);
  return resp.json();
}
