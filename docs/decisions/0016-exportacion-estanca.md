# ADR 0016: Exportación estanca (STL/3MF)

Date: 2026-10-12
Status: Aceptado — no altera ADR-0001 a 0015

## Context

Al exportar STL/3MF, la malla salía de `tessellate_to_trimesh`, que tesela
cara a cara (`face.tessellate`). Cada cara discretiza sus aristas compartidas
por su cuenta: los nodos de dos caras vecinas no coinciden (uniones en T) y la
malla queda abierta. La tuerca P5 real (`L_Tuerca_rotula_1_v5`) tenía 4680
aristas abiertas. Orca y otros laminadores «reparan» esas grietas rellenando
roscas y huecos finos, así que la pieza impresa no coincide con el B-rep.

La vista 3D necesita la correspondencia triángulo → cara (`caras_triangulos`,
raycast del visor) y se tesela cara a cara a propósito: no se toca.

## Decision

**Exportación con teselación de todo el sólido.** `kernel.mesh.tessellate_para_exportar`
trabaja sobre una copia del sólido (`shape.copy()` + `BRepTools.Clean_s`, para
no alterar el triangulado cacheado que usa el visor), la tesela entera con
`build123d.export_stl` (las caras comparten nodos de arista) y relee el STL
con `trimesh.load(process=True)`, que une vértices coincidentes, y quita los
triángulos de área cero que OCC emite en los polos de las esferas. Sin
`networkx` ni `shapely`: no se usan `split`, `section`, `fill_holes` ni grafos.

Un compuesto con varios sólidos se tesela sólido a sólido (cada uno con su
tolerancia) y las mallas se concatenan sin unir vértices: los sólidos que se
tocan conservan cada uno su cáscara cerrada en vez de compartir nodos, que
trimesh leería como aristas no-manifold.

**Tolerancia.** Lineal = `diagonal_bbox × 5e-5`, acotada a [0,005; 0,05] mm;
angular 0,1 rad. Pasar `tolerance` explícito la sustituye. El error de volumen
lo marca sobre todo la angular en superficies curvas: una esfera de radio 5 mm
queda 0,18 % por debajo del B-rep; una tuerca o una pieza plana, por debajo de 0,02 %.

**Qué cambia y qué no.**
- Cambian: STL y 3MF de `exportar` (un sólido y `por_solido`), las descargas
  (`descarga` STL y 3MF) e `incorporar` (la malla que luego se corta).
- No cambian: `tessellate_to_trimesh_con_caras*`, `_tessellate_faces`,
  `render.py`, `piece_mesh.py`, `naming.py`, la vista y el chequeo FDM.

**Aviso, no reparación.** Si alguna malla exportada sigue abierta (un B-rep
inválido, o una malla STL importada), la respuesta de `exportar` incluye
`advertencia_malla_abierta: {piezas: [...], mensaje}` (en español). El campo
falta cuando todas las mallas son cerradas. No se repara ni se rechaza la
exportación.

## Consequences

- Las mallas de exportación son más densas (tolerancia menor) y tardan más
  en modelos grandes; `MAX_TRIANGULOS_DESCARGA` sigue acotando las descargas.
- Los archivos exportados antes de este cambio no se regeneran solos.
- El STL de un documento importado como malla se entrega tal cual; solo se
  añade el aviso si está abierto.
