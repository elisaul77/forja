// Forja — Historial 2.0: lógica pura del grafo (carriles, aristas, filtros)
// y formateo (fechas relativas, chips de cambio). Sin DOM: se prueba con
// `node --test tests/web/historial-grafo.test.mjs`.

/** Paleta de carriles por rama (tokens --fj-* donde existen). */
export const PALETA_RAMAS = [
  "var(--fj-accent)", "var(--fj-info)", "var(--fj-run)", "var(--fj-ai)",
  "var(--fj-warn)", "oklch(0.7 0.12 200)", "oklch(0.7 0.14 120)", "var(--fj-err)",
];

/** Índice de color estable por rama: el orden que da el servidor (main,
 * activa, resto alfabético). */
export function coloresDeRamas(ramas) {
  const mapa = {};
  (ramas || []).forEach((r, i) => { mapa[r.nombre ?? r] = i % PALETA_RAMAS.length; });
  return mapa;
}

/** Rama «propia» de un paso: la primera de las que lo contienen según el
 * orden de `ramas` (un paso de main es de main aunque b lo contenga). */
export function ramaPropia(nodo, ordenRamas) {
  const contiene = new Set(nodo.ramas || []);
  for (const r of ordenRamas) if (contiene.has(r)) return r;
  return (nodo.ramas || [])[0] ?? null;
}

/** Rama de cada paso siguiendo cadenas de PRIMER padre desde cada punta,
 * en el orden de `ordenRamas` (main primero): así los pasos de una rama ya
 * fusionada conservan su color en vez de teñirse de main. Los que no
 * alcanza ninguna punta caen a `ramaPropia`. Devuelve `{sha: rama}`. */
export function ramasPorPrimerPadre(nodos, ordenRamas) {
  const por = new Map(nodos.map(n => [n.sha_corto, n]));
  const puntas = new Map();
  for (const n of nodos) for (const r of n.puntas || []) puntas.set(r, n.sha_corto);
  const salida = {};
  for (const r of ordenRamas) {
    let sha = puntas.get(r);
    while (sha && por.has(sha) && !(sha in salida)) {
      salida[sha] = r;
      sha = (por.get(sha).padres || [])[0];
    }
  }
  for (const n of nodos) if (!(n.sha_corto in salida)) salida[n.sha_corto] = ramaPropia(n, ordenRamas);
  return salida;
}

/**
 * Carriles al estilo git-graph. `nodos` en orden topológico (hijos antes
 * que padres), cada uno con `sha_corto` y `padres` (sha_corto).
 * Devuelve `{filas: [{sha, carril}], aristas: [{de, a, carrilDe, carrilVia,
 * carrilA, padre, orden}], carriles}`: `a` = fila del padre (o `filas.length`
 * si el padre no está cargado), `carrilVia` = carril por el que baja.
 */
export function calcularCarriles(nodos) {
  const fila = new Map(nodos.map((n, i) => [n.sha_corto, i]));
  const activos = []; // carril -> sha esperado (o null)
  const filas = [];
  const aristas = [];
  const pendientes = new Map(); // sha padre -> [{de, carrilDe, carrilVia, orden, padre}]
  let maximo = 0;
  const libre = () => {
    const i = activos.indexOf(null);
    if (i >= 0) return i;
    activos.push(null);
    return activos.length - 1;
  };
  nodos.forEach((n, i) => {
    let carril = activos.indexOf(n.sha_corto);
    if (carril < 0) carril = libre();
    // Otros carriles que también esperaban este paso convergen aquí.
    for (let k = 0; k < activos.length; k++) if (activos[k] === n.sha_corto) activos[k] = null;
    for (const p of pendientes.get(n.sha_corto) || []) aristas.push({ ...p, a: i, carrilA: carril });
    pendientes.delete(n.sha_corto);
    filas.push({ sha: n.sha_corto, carril });
    (n.padres || []).forEach((padre, orden) => {
      // El primer padre sigue en el carril del hijo (línea principal recta),
      // aunque otro carril ya lo espere: ese otro convergerá en él.
      let via = orden === 0 && activos[carril] === null ? carril : activos.indexOf(padre);
      if (via < 0) via = libre();
      activos[via] = padre;
      const arista = { de: i, carrilDe: carril, carrilVia: via, orden, padre };
      if (fila.has(padre)) {
        const lista = pendientes.get(padre) || [];
        lista.push(arista);
        pendientes.set(padre, lista);
      } else {
        aristas.push({ ...arista, a: nodos.length, carrilA: via, fuera: true });
        activos[via] = null; // el padre no está cargado: el carril sale por abajo
      }
    });
    maximo = Math.max(maximo, activos.length);
    while (activos.length && activos[activos.length - 1] === null) activos.pop();
  });
  return { filas, aristas, carriles: Math.max(1, maximo) };
}

/** Trazado SVG de una arista (coordenadas por fila/carril). */
export function trazoArista(e, { alto, ancho, margen = ancho / 2, mitad = alto / 2, ys = null }) {
  const x = c => margen + c * ancho;
  // `ys` (opcional) = centro medido de cada fila tras el render; si falta,
  // filas de altura fija.
  const y = f => (ys && ys[f] !== undefined ? ys[f] : f * alto + mitad);
  const x1 = x(e.carrilDe), y1 = y(e.de), xv = x(e.carrilVia), x2 = x(e.carrilA), y2 = y(e.a);
  const ab = (y2 - y1) / Math.max(1, e.a - e.de); // alto real de fila
  alto = ab;
  if (e.a === e.de + 1 && e.carrilVia === e.carrilA) {
    return x1 === x2 ? `M${x1} ${y1}L${x2} ${y2}` : `M${x1} ${y1}C${x1} ${y1 + alto * 0.55} ${x2} ${y2 - alto * 0.55} ${x2} ${y2}`;
  }
  let d = `M${x1} ${y1}`;
  const yb = y1 + alto; // la curva de salida ocupa una fila
  d += x1 === xv ? `L${xv} ${yb}` : `C${x1} ${y1 + alto * 0.55} ${xv} ${yb - alto * 0.55} ${xv} ${yb}`;
  if (xv === x2) return d + `L${x2} ${y2}`;
  const ya = y2 - alto;
  if (ya > yb) d += `L${xv} ${ya}`;
  return d + `C${xv} ${ya + alto * 0.55} ${x2} ${y2 - alto * 0.55} ${x2} ${y2}`;
}

/** Deja los nodos que cumplen `conservar` y reescribe `padres` a los
 * antepasados conservados más cercanos (como `git log -- ruta`). */
export function filtrarNodos(nodos, conservar) {
  const por = new Map(nodos.map(n => [n.sha_corto, n]));
  const memo = new Map();
  const cercanos = sha => {
    if (memo.has(sha)) return memo.get(sha);
    const n = por.get(sha);
    if (!n) { memo.set(sha, []); return []; }
    if (conservar(n)) { memo.set(sha, [sha]); return [sha]; }
    memo.set(sha, []); // corta ciclos (no debería haberlos)
    const salida = [];
    for (const p of n.padres || []) for (const c of cercanos(p)) if (!salida.includes(c)) salida.push(c);
    memo.set(sha, salida);
    return salida;
  };
  for (let i = nodos.length - 1; i >= 0; i--) cercanos(nodos[i].sha_corto);
  return nodos.filter(conservar).map(n => {
    const padres = [];
    for (const p of n.padres || []) for (const c of cercanos(p)) if (!padres.includes(c)) padres.push(c);
    return { ...n, padres };
  });
}

/** Predicado de la barra de filtros. */
export function predicadoFiltros({ texto = "", autor = "todos", soloHitos = false, rama = "" } = {}) {
  const t = texto.trim().toLocaleLowerCase("es");
  return n => (!t || (n.mensaje || "").toLocaleLowerCase("es").includes(t) || (n.sha_corto || "").startsWith(t))
    && (autor === "todos" || n.autor === autor)
    && (!soloHitos || (n.hitos || []).length > 0 || (n.puntas || []).length > 0)
    && (!rama || (n.ramas || []).includes(rama));
}

const UNIDADES = [
  [60, "s", "segundo", "segundos"],
  [60, "min", "minuto", "minutos"],
  [24, "h", "hora", "horas"],
  [7, "d", "día", "días"],
  [4.345, "sem", "semana", "semanas"],
  [12, "mes", "mes", "meses"],
  [Infinity, "a", "año", "años"],
];

/** «hace 2 h», «hace 3 días», «ahora mismo». `iso` en UTC (con Z). */
export function fechaRelativa(iso, ahora = Date.now()) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  let s = Math.max(0, (ahora - t) / 1000);
  if (s < 45) return "ahora mismo";
  for (const [factor, corta, uno, varios] of UNIDADES) {
    if (s < factor) {
      const n = Math.round(s);
      if (corta === "h" || corta === "min") return `hace ${n} ${corta}`;
      return `hace ${n} ${n === 1 ? uno : varios}`;
    }
    s /= factor;
  }
  return "";
}

/** Fecha absoluta legible en español (para title/detalle). */
export function fechaAbsoluta(iso) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso || "";
  return new Date(t).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" });
}

export function numeroEs(v, decimales = 2) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  const s = n.toLocaleString("es-CO", { maximumFractionDigits: decimales, minimumFractionDigits: 0 });
  return s.replace(/^-/, "−");
}

function valorCorto(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return numeroEs(v, 3);
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 14 ? s.slice(0, 13) + "…" : s;
}

/** Chips de un resumen de cambios: `[{tipo, texto, titulo}]`; `tipo` =
 * añadida | cambiada | quitada | volumen | parametro | material | script. */
export function chipsDeCambios(c, maximo = 6) {
  if (!c || c.error) return [{ tipo: "error", texto: "sin resumen", titulo: "No se pudo calcular el resumen" }];
  const chips = [];
  for (const n of c["añadidas"] || []) chips.push({ tipo: "añadida", texto: `+${n}`, titulo: `Pieza añadida: ${n}` });
  for (const n of c.cambiadas || []) chips.push({ tipo: "cambiada", texto: `~${n}`, titulo: `Pieza cambiada: ${n}` });
  for (const n of c.quitadas || []) chips.push({ tipo: "quitada", texto: `−${n}`, titulo: `Pieza quitada: ${n}` });
  if (typeof c.volumen_pct === "number" && Math.abs(c.volumen_pct) >= 0.005) {
    const signo = c.volumen_pct > 0 ? "+" : "";
    chips.push({ tipo: "volumen", texto: `Δvol ${signo}${numeroEs(c.volumen_pct, 2)} %`, titulo: "Cambio de volumen total" });
  }
  if (!c.raiz) for (const p of c.parametros || []) {
    chips.push({ tipo: "parametro", texto: `${p.nombre} ${valorCorto(p.antes)}→${valorCorto(p.despues)}`, titulo: `Parámetro ${p.nombre}` });
  }
  for (const m of c.materiales || []) chips.push({ tipo: "material", texto: `🎨 ${m}`, titulo: `Material cambiado: ${m}` });
  if (c.script_cambiado) chips.push({ tipo: "script", texto: "script", titulo: "El texto del script cambió" });
  if (chips.length > maximo) {
    const resto = chips.length - (maximo - 1);
    return [...chips.slice(0, maximo - 1), { tipo: "mas", texto: `+${resto} más`, titulo: chips.slice(maximo - 1).map(x => x.texto).join(", ") }];
  }
  if (!chips.length && c.sin_cambios) chips.push({ tipo: "neutro", texto: "sin cambios", titulo: "El estado no cambió" });
  return chips;
}

export const AUTORES = {
  agente: { icono: "🤖", texto: "Agente" },
  humano: { icono: "👤", texto: "Humano" },
  forja: { icono: "⚙", texto: "Forja" },
};

export function autor(a) { return AUTORES[a] ?? { icono: "•", texto: a || "desconocido" }; }

/** Piezas tocadas por un paso con su estado, ordenadas por estado. */
export function piezasTocadas(c) {
  if (!c || c.error) return [];
  const salida = [];
  for (const n of c["añadidas"] || []) salida.push({ nombre: n, estado: "añadida" });
  for (const n of c.cambiadas || []) salida.push({ nombre: n, estado: "cambiada" });
  for (const n of c.quitadas || []) salida.push({ nombre: n, estado: "quitada" });
  for (const n of c.materiales || []) if (!salida.some(s => s.nombre === n)) salida.push({ nombre: n, estado: "material" });
  return salida;
}

const capital = t => (t ? t[0].toLocaleUpperCase("es") + t.slice(1) : t);

/** Título humano de un paso (el mensaje crudo va al detalle/tooltip). */
export function tituloPaso(n) {
  const m = (n.mensaje || "").trim();
  const c = n.cambios || {};
  let r;
  if ((r = /^restaurar pieza (.+) a ([0-9a-f]{7,})$/.exec(m))) return `Restaurar «${r[1]}» a ${r[2].slice(0, 7)}`;
  if ((r = /^restaurar todo el documento a ([0-9a-f]{7,})$/.exec(m))) return `Restaurar todo a ${r[1].slice(0, 7)}`;
  if ((r = /^fusionar (\S+) en (\S+)$/.exec(m))) return `Fusión de «${r[1].slice(0, 24)}» en «${r[2]}»`;
  if ((r = /^traer (.+) de (\S+)$/.exec(m))) return `Traer ${r[1]} de «${r[2].slice(0, 10)}»`;
  if (/^documento creado/.test(m)) return "Documento creado";
  if (/^parametros:/i.test(m) && !c.raiz) {
    const ps = c.parametros || [];
    if (!ps.length) return "Parámetros sin cambios";
    const txt = ps.slice(0, 2).map(p => `${p.nombre} ${valorCorto(p.antes)}→${valorCorto(p.despues)}`).join(", ");
    return `Parámetros: ${txt}${ps.length > 2 ? ` (+${ps.length - 2})` : ""}`;
  }
  return capital(m) || "(sin mensaje)";
}
