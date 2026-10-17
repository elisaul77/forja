# ADR 0029 — Rasgos de modificación (fase 24d)

Date: 2026-10-17. Status: implemented in phase 24d (branch `phase-24d-modificar`). Extends ADR 0019 (intents y candidatos: cada cambio es un candidato verificable) y ADR 0003 (huellas geométricas). Numbering: 0026 is reserved for 24b.

## Decisión

Siete rasgos de modificación más la reparación de malla entran como `kind` nuevas de `Operation` dentro del pipeline de candidatos, no como herramientas MCP nuevas (los tests fijan el conjunto de herramientas):

| `kind` | Sobre | Referencia | Valor |
|---|---|---|---|
| `fillet` | B-rep | aristas (`huellas`, 1..64) | radio mm (> 0) |
| `chamfer` | B-rep | aristas | longitud mm (> 0) |
| `shell` | B-rep | caras que quedan abiertas | espesor de pared mm (> 0) |
| `push_pull` | B-rep | exactamente una cara | distancia mm (≠ 0; negativa quita material) |
| `pattern` | B-rep y malla | ninguna | `patron` lineal (separación mm) o circular (`angulo_deg`); `cantidad` 2..32; `eje` |
| `mirror` | B-rep y malla | ninguna | `eje` (normal del plano) y `plano_mm` |
| `split` | B-rep | ninguna | `eje` y `plano_mm`; conserva las dos mitades |
| `mesh_repair` | malla | ninguna | ninguno |

Lectura de datos: `GET /documentos/{id}/design/referencias?pieza=&tipo=arista|cara` lista las aristas o caras de una pieza B-rep con su huella (solo lectura, máximo 400, con `truncado`).

Aplicación: `app/design_features.py` (lógica pura sobre formas y mallas) y `app/kernel/b123d_kernel.py` (envoltorios de build123d; ADR 0001). `design_operations.edit_shape` y `edit_mesh` delegan en él. El flujo no cambia: `POST /design/candidates` → revisar → `apply`.

## Reglas

- **Referencias por huella, no por índice.** Una arista o cara se identifica con su huella (`naming.py`: tipo, subtipo, centroide, dirección, medida). El servidor la vuelve a resolver contra la geometría actual con `resolver_detallado`. Si ya no existe, o hay dos candidatas igual de buenas, la operación entera es 422 y no se crea candidato. Un cambio de revisión sigue siendo 409 (`expect`, sin cambios).
- **Un sólido por vez.** Las referencias se agrupan por sólido de la pieza; una referencia que no pertenece a ningún sólido es 422.
- **Fallo del núcleo = sin paso.** Si OCC rechaza el filete (radio mayor que la arista, por ejemplo), la respuesta es 422 con el motivo en español y no queda nada en `.design-candidates/`. Un candidato solo existe si su geometría es válida.
- **Solo B-rep o solo malla donde corresponde.** Un rasgo B-rep sobre un STL es 422 con «solo para sólidos B-rep (este documento es malla)». `mesh_repair` sobre un STEP es 422.
- **Acotado y estricto.** `Huella` y `Operation` son `extra=forbid`, sin valores no finitos, con topes: 64 referencias, 32 copias, ángulo ≤ 360°, valores en ±1 000 mm, separación y espesor > 0, `push_pull` con una sola cara y distancia ≠ 0. Cada `kind` admite solo sus campos (un `huellas` en una simetría es 422).
- **Matriz.** Vuelta completa: reparto uniforme de 360°. Arco: la última copia cae en `angulo_deg`.
- **Simetría y matriz de malla** usan la unión `manifold` existente (sin dependencia nueva). Una malla que no es volumen cerrado se rechaza.
- **Reparación de malla sin relleno de huecos.** `trimesh.repair.fill_holes` necesita `networkx`, que la imagen no trae; no se añade dependencia. La reparación une vértices duplicados, quita caras degeneradas y orienta las caras. Una malla que sigue abierta sale como candidato marcado, y el `apply` la bloquea por integridad (ya existente).
- **Nada se persiste fuera del candidato.** El `apply` es el que reescribe el STEP; el historial guarda el paso como cualquier otro cambio.

## Interfaz

La cinta (pestaña Sólido, grupo Modificar, Patrón) gana Filete, Chaflán, Vaciar, Desfasar cara, Dividir por plano, Reparar malla, Simetría y Matriz. Cada botón abre un formulario pequeño (`app/web/static/js/features.js`): elige pieza, elige aristas o caras de una lista con su medida y su posición, escribe el valor y pulsa «Preparar candidato». El candidato se revisa y se aplica en «Indicaciones». El formulario nunca cambia la geometría.

Los rasgos aún no construidos (Combinar / separar cuerpos, Malla a sólido, Ángulo de desmoldeo, Borrar cara y sanar) siguen visibles y deshabilitadas como «Próximamente · 24d».

## Lo que no prueba

- La selección de aristas es por lista, no por clic sobre el visor: el visor no tiene selección de aristas (su picking es de caras). Queda como mejora de interfaz.
- Un filete o chaflán sobre aristas de una pieza que se cruza con otra no comprueba la interferencia entre piezas; eso lo hace `checks` del candidato, igual que para cualquier otra operación.
- La reparación no garantiza una malla cerrada: solo corrige lo que se arregla sin relleno de huecos.
- El rasgo `split` deja dos cuerpos en la misma pieza (un Compound). No los separa en piezas con nombre nuevas.

## Pendiente (no construido en 24d)

- Combinar / separar cuerpos (fila 55): separar exige crear piezas con nombre nuevas en `solidos.json`.
- Malla a sólido (fila 56): reconstruir un B-rep desde triángulos sin una dependencia nueva.
- Ángulo de desmoldeo (49) y Borrar cara y sanar (51): fuera del alcance de la frase de 24d en el plan.

## Rechazado

- **Una herramienta MCP por rasgo.** Los tests fijan el conjunto de herramientas; los rasgos son `kind` del contrato que ya usa MCP y HTTP.
- **Referencias por índice de arista.** Un cambio de revisión reordena la topología de OCC: el índice sería una adivinanza silenciosa (ver ADR 0003).
- **Rellenar huecos con `networkx`.** Dependencia nueva de tiempo de ejecución sin necesidad demostrada para el caso habitual (vértices duplicados).
- **Aplicar el rasgo directamente sobre el documento.** Rompe la garantía «cada cambio es un candidato revisable».
