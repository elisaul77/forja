import { listarDocumentos, enviarDocumentoAPapelera, listarPapelera, restaurarDePapelera, purgarDePapelera } from "./api.js?v=15";

export function latestStep(documents) {
  return documents.filter(doc => /^(step|stp)$/i.test(doc.formato ?? doc.nombre.split('.').pop()))
    .sort((a, b) => (Date.parse(b.actualizado) || 0) - (Date.parse(a.actualizado) || 0) || a.id.localeCompare(b.id))[0];
}

const TEXTO_GALERIA = 'Vista de galería · documentos guardados en este servidor';
const TEXTO_PAPELERA = 'Papelera · documentos movidos a la papelera; puedes restaurarlos o borrarlos para siempre';

export function createLibrary({ openDocument, onDeleted, onRestored }) {
  const dialog = document.createElement("dialog");
  dialog.className = "fj-gallery";
  dialog.setAttribute("aria-labelledby", "fj-gallery-title");
  dialog.innerHTML = `<header><div><h2 id="fj-gallery-title">Biblioteca</h2><p>${TEXTO_GALERIA}</p></div><button class="fj-btn" type="button" data-papelera aria-pressed="false">Papelera</button><button class="fj-btn" type="button" data-close>Cerrar</button></header><label>Buscar documento<input class="fj-input" type="search" placeholder="Nombre del documento…"></label><p role="status"></p><div class="fj-gallery-grid"></div>`;
  document.body.append(dialog);
  const grid = dialog.querySelector('.fj-gallery-grid');
  const status = dialog.querySelector('[role=status]');
  const search = dialog.querySelector('input');
  const subtitle = dialog.querySelector('header p');
  const papeleraToggle = dialog.querySelector('[data-papelera]');
  let documents = [];
  let trashed = [];
  let mode = 'galeria';
  let deleting = false;
  let refreshEpoch = 0;
  let refreshTimer;
  const deleted = new Set();
  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      observer.unobserve(entry.target);
      entry.target.loadPreview();
    }
  }, { root: dialog, rootMargin: '100px' });
  dialog.addEventListener('close', () => {
    observer.disconnect();
    clearTimeout(refreshTimer);
    refreshEpoch++;
  });
  dialog.querySelector('[data-close]').onclick = () => dialog.close();

  function confirmRemoval(doc) {
    return new Promise(resolve => {
      const confirmation = document.createElement('dialog');
      confirmation.className = 'fj-gallery';
      confirmation.setAttribute('aria-labelledby', 'fj-delete-title');
      const title = document.createElement('h2');
      title.id = 'fj-delete-title';
      title.textContent = `¿Eliminar «${doc.nombre}»?`;
      const warning = document.createElement('p');
      warning.textContent = 'Se borrarán definitivamente este documento del servidor, sus notas, historial, ensamble y exportaciones. No se puede deshacer. El archivo original importado no se modifica.';
      const cancel = document.createElement('button');
      cancel.className = 'fj-btn';
      cancel.textContent = 'Cancelar';
      const confirm = document.createElement('button');
      confirm.className = 'fj-btn fj-btn--primary';
      confirm.textContent = 'Eliminar definitivamente';
      const finish = value => { confirmation.close(); confirmation.remove(); resolve(value); };
      cancel.onclick = () => finish(false);
      confirm.onclick = () => finish(true);
      confirmation.oncancel = event => { event.preventDefault(); finish(false); };
      confirmation.append(title, warning, cancel, confirm);
      document.body.append(confirmation);
      confirmation.showModal();
      cancel.focus();
    });
  }

  // Reversible: a la papelera no pide confirmacion; nada se borra del disco.
  async function trash(doc) {
    if (deleting) return;
    deleting = true;
    try {
      await enviarDocumentoAPapelera(doc.id);
      deleted.add(doc.id);
      onDeleted(doc.id);
      documents = documents.filter(item => item.id !== doc.id);
      render();
      status.textContent = `Se movió «${doc.nombre}» a la papelera. Puedes restaurarla.`;
    } catch (error) {
      status.textContent = `No se movió el documento: ${error.message}`;
      if (!dialog.open) window.alert(status.textContent);
    } finally { deleting = false; }
  }

  async function restore(doc) {
    if (deleting) return;
    deleting = true;
    try {
      await restaurarDePapelera(doc.id);
      deleted.delete(doc.id);
      trashed = trashed.filter(item => item.id !== doc.id);
      onRestored(doc.id);
      renderTrash();
      status.textContent = `Se restauró «${doc.nombre}» en la galería.`;
    } catch (error) {
      status.textContent = `No se restauró el documento: ${error.message}`;
    } finally { deleting = false; }
  }

  async function purge(doc) {
    if (deleting) return;
    deleting = true;
    try {
      if (!await confirmRemoval(doc)) return;
      await purgarDePapelera(doc.id);
      trashed = trashed.filter(item => item.id !== doc.id);
      renderTrash();
      status.textContent = `Se eliminó «${doc.nombre}» para siempre. No se puede recuperar desde Forja.`;
    } catch (error) {
      status.textContent = `No se eliminó el documento: ${error.message}`;
    } finally { deleting = false; }
  }

  function button(texto, clase, onclick, etiqueta) {
    const boton = document.createElement('button');
    boton.className = clase;
    boton.textContent = texto;
    if (etiqueta) boton.setAttribute('aria-label', etiqueta);
    boton.onclick = onclick;
    return boton;
  }

  function render() {
    observer.disconnect();
    grid.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase('es');
    const matches = documents.filter(doc => doc.nombre.toLocaleLowerCase('es').includes(query));
    status.textContent = `${matches.length} de ${documents.length} documentos`;
    for (const doc of matches) {
      const card = document.createElement('article');
      card.className = 'fj-gallery-card';
      const preview = document.createElement('img');
      preview.alt = `Vista previa de ${doc.nombre}`;
      preview.loading = 'lazy';
      preview.onerror = () => { preview.hidden = true; fallback.hidden = false; fallback.textContent = 'Vista previa no disponible'; };
      const fallback = document.createElement('p');
      fallback.textContent = 'Cargando vista previa…';
      preview.hidden = true;
      card.loadPreview = async () => {
        try {
          const { previewUrl } = await import('./library-preview.js?v=9');
          const url = await previewUrl(doc);
          if (!card.isConnected) return;
          preview.src = url;
          preview.hidden = false;
          fallback.hidden = true;
        } catch { fallback.textContent = 'Vista previa no disponible'; }
      };
      const title = document.createElement('h3');
      title.textContent = doc.nombre;
      const meta = document.createElement('p');
      meta.textContent = `${doc.solidos} sólido(s) · ${doc.volumen.toLocaleString('es', { maximumFractionDigits: 1 })} mm³`;
      const open = button('Abrir', 'fj-btn fj-btn--primary', () => { dialog.close(); openDocument(doc); });
      const trashButton = button('Mover a la papelera', 'fj-btn', () => trash(doc), `Mover ${doc.nombre} a la papelera`);
      const actions = document.createElement('div');
      actions.append(open, trashButton);
      card.append(preview, fallback, title, meta, actions);
      grid.append(card);
      observer.observe(card);
    }
  }

  function renderTrash() {
    observer.disconnect();
    grid.replaceChildren();
    const query = search.value.trim().toLocaleLowerCase('es');
    const matches = trashed.filter(doc => (doc.nombre ?? doc.id).toLocaleLowerCase('es').includes(query));
    status.textContent = `${matches.length} de ${trashed.length} documentos en la papelera`;
    for (const doc of matches) {
      const card = document.createElement('article');
      card.className = 'fj-gallery-card';
      const title = document.createElement('h3');
      title.textContent = doc.nombre ?? doc.id;
      const meta = document.createElement('p');
      const cuando = doc.papelera ? new Date(doc.papelera).toLocaleString('es') : 'fecha desconocida';
      meta.textContent = `${doc.solidos ?? '?'} sólido(s) · movido ${cuando}`;
      const actions = document.createElement('div');
      actions.append(
        button('Restaurar', 'fj-btn fj-btn--primary', () => restore(doc), `Restaurar ${doc.nombre ?? doc.id}`),
        button('Eliminar para siempre', 'fj-btn', () => purge(doc), `Eliminar para siempre ${doc.nombre ?? doc.id}`),
      );
      card.append(title, meta, actions);
      grid.append(card);
    }
  }

  async function refresh() {
    const epoch = ++refreshEpoch;
    try {
      const next = await listarDocumentos();
      if (epoch !== refreshEpoch || !dialog.open) return;
      documents = next.filter(doc => !deleted.has(doc.id));
      render();
    } catch (error) {
      if (epoch === refreshEpoch && dialog.open) status.textContent = `No se pudo cargar: ${error.message}`;
    }
  }

  async function refreshTrash() {
    const epoch = ++refreshEpoch;
    try {
      const next = await listarPapelera();
      if (epoch !== refreshEpoch || !dialog.open) return;
      trashed = next;
      renderTrash();
    } catch (error) {
      if (epoch === refreshEpoch && dialog.open) status.textContent = `No se pudo cargar la papelera: ${error.message}`;
    }
  }

  function setMode(next) {
    mode = next;
    const enPapelera = mode === 'papelera';
    papeleraToggle.setAttribute('aria-pressed', String(enPapelera));
    papeleraToggle.textContent = enPapelera ? 'Galería' : 'Papelera';
    subtitle.textContent = enPapelera ? TEXTO_PAPELERA : TEXTO_GALERIA;
    grid.replaceChildren();
    status.textContent = enPapelera ? 'Cargando papelera…' : 'Cargando biblioteca…';
    void (enPapelera ? refreshTrash() : refresh());
  }

  papeleraToggle.onclick = () => setMode(mode === 'galeria' ? 'papelera' : 'galeria');
  document.addEventListener('forja:library-changed', () => {
    if (!dialog.open) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { void (mode === 'papelera' ? refreshTrash() : refresh()); }, 120);
  });
  search.oninput = () => (mode === 'papelera' ? renderTrash() : render());
  return {
    trash,
    async show() {
      dialog.showModal();
      search.focus();
      setMode('galeria');
    },
  };
}
