// Scheduling is independent of the DOM so bursts and stale responses can be tested.
export function createRefreshScheduler({ fetchMesh, applyMesh, canRun, onPending = () => {}, onError = () => {}, delay = 200 }) {
  let shown = null;
  let wanted = null;
  let pending = false;
  let running = false;
  let disposed = false;
  let timer = null;
  let controller = null;
  let retryDelay = delay;
  const idleWaiters = [];

  function schedule(ms = delay) {
    if (timer !== null || disposed || !pending || running || !canRun()) return;
    timer = setTimeout(() => { timer = null; void flush(); }, ms);
  }

  async function flush(force = false) {
    if (disposed || !pending || running || !canRun()) return;
    if (timer !== null && !force) return;
    if (timer !== null) { clearTimeout(timer); timer = null; }
    running = true;
    pending = false;
    const requested = wanted;
    controller = new AbortController();
    try {
      const result = await fetchMesh(controller.signal);
      if (disposed) return;
      if (!canRun()) { pending = true; return; }
      // A newer event arrived while downloading: do not show the old result.
      if (wanted !== requested && wanted && result.revision !== wanted) {
        pending = true;
      } else {
        const applied = await applyMesh(result);
        if (disposed) return;
        if (applied === false) pending = true;
        else {
          shown = result.revision;
          pending = Boolean(wanted && wanted !== shown);
        }
        retryDelay = delay;
      }
    } catch (error) {
      if (!disposed && error.name !== "AbortError") {
        pending = true;
        onError(error);
        retryDelay = 3000;
      }
    } finally {
      running = false;
      controller = null;
      for (const resolve of idleWaiters.splice(0)) resolve();
      if (!disposed) {
        onPending(pending);
        schedule(retryDelay);
      }
    }
  }

  return {
    setShown(revision) {
      shown = revision;
      if (wanted === shown) {
        pending = false;
        clearTimeout(timer);
        timer = null;
        onPending(false);
      }
    },
    notify(revision) {
      if (disposed || (revision && revision === shown && !pending)) return;
      wanted = revision || null;
      pending = true;
      onPending(true);
      schedule();
    },
    flush,
    cancelAndWait() {
      if (!running) return Promise.resolve();
      controller?.abort();
      pending = true;
      return new Promise(resolve => idleWaiters.push(resolve));
    },
    get pending() { return pending; },
    dispose() {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
      onPending(false);
    },
  };
}

export function parseLiveEvent(name, raw) {
  const names = new Set(["hola", "documento_creado", "documento_actualizado", "documento_eliminado", "construyendo", "construccion_terminada", "construccion_fallida", "anotaciones_actualizadas", "ensamble_actualizado", "miniatura_lista", "resincronizar"]);
  if (!names.has(name)) return null;
  try {
    const data = JSON.parse(raw);
    if (!data || typeof data !== "object" || Array.isArray(data)) return null;
    if (name === "hola") return Array.isArray(data.documentos) ? data : null;
    if (name === "resincronizar") return data;
    return typeof data.id === "string" ? data : null;
  } catch { return null; }
}
