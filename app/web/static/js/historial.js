// Forja — panel de historial (snapshots) de un documento (Fase 4).
import { obtenerHistorial, restaurarDocumento } from "./api.js";

const panelHistorial = document.getElementById("fj-panel-historial");

export class ForjaHistorialControlador {
  constructor(docId, alRestaurar) {
    this.docId = docId;
    this.entradas = [];
    // Llamado tras un restaurar exitoso, para que `tabs.js` recargue la
    // malla/ficha/notas de la pestaña con el estado restaurado.
    this._alRestaurar = alRestaurar;
  }

  async cargarInicial() {
    this.entradas = await obtenerHistorial(this.docId);
  }

  async restaurar(snapshotId) {
    const registro = await restaurarDocumento(this.docId, snapshotId);
    this.entradas = await obtenerHistorial(this.docId);
    await this._alRestaurar(registro);
  }
}

export function renderizarPanelHistorial(controlador) {
  panelHistorial.textContent = "";
  if (controlador.entradas.length === 0) {
    const vacio = document.createElement("div");
    vacio.className = "fj-panel-lateral__vacio";
    vacio.textContent = "Sin historial todavía.";
    panelHistorial.appendChild(vacio);
    return;
  }
  // Más reciente primero.
  for (const entrada of [...controlador.entradas].reverse()) {
    const fila = document.createElement("div");
    fila.className = "fj-item-historial";

    const cuerpo = document.createElement("div");
    cuerpo.className = "fj-item-historial__cuerpo";
    const mensaje = document.createElement("div");
    mensaje.className = "fj-item-nota__comentario";
    mensaje.textContent = entrada.mensaje;
    cuerpo.appendChild(mensaje);
    const meta = document.createElement("div");
    meta.className = "fj-item-historial__meta";
    meta.textContent = entrada.fecha;
    cuerpo.appendChild(meta);
    fila.appendChild(cuerpo);

    const acciones = document.createElement("div");
    acciones.className = "fj-item-historial__acciones";
    const btnRestaurar = document.createElement("button");
    btnRestaurar.type = "button";
    btnRestaurar.className = "fj-btn fj-btn--ghost";
    btnRestaurar.textContent = "Restaurar";
    btnRestaurar.addEventListener("click", async () => {
      btnRestaurar.disabled = true;
      try {
        await controlador.restaurar(entrada.id);
      } catch (err) {
        // token invalido/cancelado (401) u otro fallo: se avisa igual que
        // el resto del visor (ver app.js), nunca queda en silencio.
        alert(`No se pudo restaurar: ${err.message}`);
      } finally {
        btnRestaurar.disabled = false;
      }
    });
    acciones.appendChild(btnRestaurar);
    fila.appendChild(acciones);

    panelHistorial.appendChild(fila);
  }
}
