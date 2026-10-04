import { listarDocumentos, eliminarDocumento } from "./api.js?v=11";

export function latestStep(documents) {
  return documents.filter(doc => /^(step|stp)$/i.test(doc.formato ?? doc.nombre.split('.').pop()))
    .sort((a, b) => (Date.parse(b.actualizado) || 0) - (Date.parse(a.actualizado) || 0) || a.id.localeCompare(b.id))[0];
}

export function createLibrary({ openDocument, onDeleted }) {
  const dialog = document.createElement("dialog");
  dialog.className = "fj-gallery";
  dialog.setAttribute("aria-labelledby", "fj-gallery-title");
  dialog.innerHTML = `<header><div><h2 id="fj-gallery-title">Biblioteca</h2><p>Vista de galería · documentos guardados en este servidor</p></div><button class="fj-btn" type="button" data-close>Cerrar</button></header><label>Buscar documento<input class="fj-input" type="search" placeholder="Nombre del documento…"></label><p role="status"></p><div class="fj-gallery-grid"></div>`;
  document.body.append(dialog);
  const grid = dialog.querySelector('.fj-gallery-grid');
  const status = dialog.querySelector('[role=status]');
  const search = dialog.querySelector('input');
  let documents = [];
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

  async function remove(doc) {
    if (deleting) return;
    deleting = true;
    try {
      if (!await confirmRemoval(doc)) return;
      await eliminarDocumento(doc.id);
      deleted.add(doc.id);
      onDeleted(doc.id);
      documents = documents.filter(item => item.id !== doc.id);
      render();
      status.textContent = `Se eliminó «${doc.nombre}». No se puede recuperar desde Forja.`;
    } catch (error) {
      status.textContent = `No se eliminó el documento: ${error.message}`;
      if (!dialog.open) window.alert(status.textContent);
    } finally { deleting = false; }
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
      const open = document.createElement('button');
      open.className = 'fj-btn fj-btn--primary';
      open.textContent = 'Abrir';
      open.onclick = () => { dialog.close(); openDocument(doc); };
      const removeButton = document.createElement('button');
      removeButton.className = 'fj-btn';
      removeButton.textContent = 'Eliminar';
      removeButton.setAttribute('aria-label', `Eliminar ${doc.nombre}`);
      removeButton.onclick = () => remove(doc);
      const actions = document.createElement('div');
      actions.append(open, removeButton);
      card.append(preview, fallback, title, meta, actions);
      grid.append(card);
      observer.observe(card);
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
  document.addEventListener('forja:library-changed', () => {
    if (!dialog.open) return;
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => { void refresh(); }, 120);
  });
  search.oninput = render;
  return {
    remove,
    async show() {
      dialog.showModal();
      status.textContent = 'Cargando biblioteca…';
      grid.replaceChildren();
      search.focus();
      await refresh();
    },
  };
}
