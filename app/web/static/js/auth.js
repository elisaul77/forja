// Async authentication for embedded browsers without native prompt support.
// Same credential and tab-scoped storage as the existing HTTP client.
const key = "fj_token";
let pending = null;

export function forgetSessionToken() {
  sessionStorage.removeItem(key);
}

// `reason` (opcional, en español) explica por qué se pide la clave; sin él
// el diálogo muestra el texto genérico de siempre.
export function requestSessionToken(reason = "") {
  const stored = sessionStorage.getItem(key)?.trim();
  if (stored) return Promise.resolve(stored);
  if (pending) return pending;
  pending = new Promise((resolve) => {
    const dialog = document.createElement("dialog");
    dialog.className = "fj-auth";
    dialog.setAttribute("aria-label", "Acceso a Forja");
    const form = document.createElement("form");
    const title = document.createElement("h3");
    title.textContent = "Acceso a Forja";
    const explanation = document.createElement("p");
    explanation.textContent = reason
      ? `${reason}. Esta clave protege los cambios en este equipo; se recuerda solo durante esta pestaña.`
      : "Introduce la clave de esta instancia para continuar. Se recuerda solo durante esta pestaña.";
    const label = document.createElement("label");
    label.textContent = "Clave de Forja";
    const input = document.createElement("input");
    input.type = "password";
    input.autocomplete = "off";
    input.required = true;
    label.append(input);
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "fj-btn";
    cancel.textContent = "Cancelar";
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "fj-btn fj-btn--primary";
    submit.textContent = "Continuar";
    let done = false;
    const finish = (token) => {
      if (done) return;
      done = true;
      if (token) sessionStorage.setItem(key, token);
      input.value = "";
      dialog.close();
      dialog.remove();
      resolve(token);
    };
    form.onsubmit = (event) => {
      event.preventDefault();
      const token = input.value.trim();
      if (token) finish(token);
    };
    cancel.onclick = () => finish("");
    dialog.oncancel = (event) => { event.preventDefault(); finish(""); };
    form.append(title, explanation, label, cancel, submit);
    dialog.append(form);
    document.body.append(dialog);
    dialog.showModal();
    input.focus();
  }).finally(() => { pending = null; });
  return pending;
}
