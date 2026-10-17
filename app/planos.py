"""Planos tecnicos (F22, ADR-0021): una hoja con vistas ortograficas
(frontal, superior, derecha e isometrica), cotas automaticas y cajetin.

Dos fuentes, un mismo dibujo:

- Solido B-rep (STEP): aristas exactas de `project_to_viewport` (lineas
  visibles y ocultas reales), cotas de bbox y de cilindros de agujero cuyo
  eje es paralelo a un eje global.
- Malla (STL): solo contornos (aristas de silueta y de rasgo visibles desde
  el observador); sin lineas ocultas ni agujeros; cotas de bbox. Se avisa
  en el cajetin y en la respuesta.

Todo es funcion pura: la salida es una lista de primitivas en milimetros de
papel (`trazo`, `texto`) que `escribir` convierte a SVG, PDF o DXF. Los
textos vienen de nombres de usuario: se escapan en cada escritor.
"""
from __future__ import annotations

import io
import math
import re
import time
from datetime import datetime, timezone
from typing import Any
from xml.sax.saxutils import escape as _escapar_xml

import ezdxf
import numpy as np
import trimesh
from build123d import GeomType
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import GeomAbs_Cylinder
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_IN

FORMATOS_PLANO = ("svg", "pdf", "dxf")
VISTAS = ("frontal", "superior", "derecha", "isometrica")
PAPELES = {"A4": (297.0, 210.0), "A3": (420.0, 297.0)}
# Orden de prueba: de la escala mas grande a la mas pequena.
ESCALAS = {"5:1": 5.0, "2:1": 2.0, "1:1": 1.0, "1:2": 0.5, "1:5": 0.2, "1:10": 0.1}
PROYECCIONES = ("tercer_angulo", "primer_angulo")
CAPAS = ("VISIBLES", "OCULTAS", "COTAS", "TEXTO")

MARGEN = 10.0
ALTO_CAJETIN = 45.0
ANCHO_CAJETIN = 130.0
PAD = 14.0  # papel (mm) alrededor de cada vista para cotas y rotulos
GROSOR_VISIBLE = 0.35
GROSOR_OCULTA = 0.18
GROSOR_COTA = 0.13
ALTURA_TEXTO = 3.2
CAP_ARISTAS = 200_000
# Meshes above this many triangles are refused (projection is per edge in Python).
MAX_TRIANGULOS_PLANO = 100_000
# Angular tolerance (rad) for a closed cylinder: a hole is a full turn of faces.
TOLERANCIA_GIRO = 1e-3
# First position dimension line sits this far (mm of paper) outside the view.
DISTANCIA_COTA_POSICION = 20.0
DESPLAZAMIENTO_COTA_POSICION = 6.0
PRESUPUESTO_S = 20.0
TOLERANCIA_COINCIDENCIA = 0.02  # mm (modelo)
ANGULO_RASGO_GRADOS = 30.0
MAX_PARAMETROS_TABLA = 12
MAX_COTAS_DETALLE = 40
MAX_LARGO_NOMBRE = 80
MAX_LARGO_PIEZAS = 120

# Base de cada vista: direccion hacia el observador (V) y eje vertical del
# papel (U). El eje horizontal es R = U x V (tercer diedro: frontal con +X a
# la derecha, superior con +Y arriba, derecha con +Y a la derecha).
_BASE_VISTA = {
    "frontal": ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
    "superior": ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    "derecha": ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
}
# Posicion (columna, fila; 1 = izquierda/arriba) de cada vista en la hoja.
_SLOTS = {
    "tercer_angulo": {"frontal": (1, 2), "superior": (1, 1), "derecha": (2, 2), "isometrica": (2, 1)},
    "primer_angulo": {"frontal": (2, 1), "superior": (2, 2), "derecha": (1, 1), "isometrica": (1, 2)},
}
_ROTULO_VISTA = {
    "frontal": "Alzado",
    "superior": "Planta",
    "derecha": "Lateral derecho",
    "isometrica": "Isométrica",
}
_VISTA_DE_EJE_COTA = {"x": "frontal", "y": "superior", "z": "frontal"}
_VISTA_DE_EJE_AGUJERO = {"x": "derecha", "y": "frontal", "z": "superior"}
_EJES = ("x", "y", "z")
_ORDEN_TIPO = {"tamano_total": 0, "diametro": 1, "posicion": 2}

_RE_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# ------------------------------------------------------------ validation


def validar(
    formato: str,
    vistas: Any = None,
    escala: str | None = None,
    papel: str = "A4",
    proyeccion: str = "tercer_angulo",
    detalle: bool = False,
) -> dict[str, Any]:
    """Enumerated options of a drawing request (security: nothing outside
    these fixed sets reaches the layout). ``vistas`` may be ``None`` (all),
    a comma-separated string or a list. Raises ValueError (Spanish)."""
    if formato not in FORMATOS_PLANO:
        raise ValueError(f"formato de plano no soportado: {formato!r} (usar svg, pdf o dxf)")
    if vistas is None or vistas == "":
        lista = list(VISTAS)
    else:
        partes = vistas.split(",") if isinstance(vistas, str) else list(vistas)
        lista = []
        for parte in partes:
            nombre = str(parte).strip()
            if nombre not in VISTAS:
                raise ValueError(f"vista no valida: {nombre!r} (usar {', '.join(VISTAS)})")
            if nombre not in lista:
                lista.append(nombre)
        if not lista:
            raise ValueError("vistas vacio")
    if escala in ("", "auto"):
        escala = None
    if escala is not None and escala not in ESCALAS:
        raise ValueError(f"escala no valida: {escala!r} (usar {', '.join(ESCALAS)})")
    if papel not in PAPELES:
        raise ValueError(f"papel no valido: {papel!r} (usar A4 o A3)")
    if proyeccion not in PROYECCIONES:
        raise ValueError(f"proyeccion no valida: {proyeccion!r} (usar tercer_angulo o primer_angulo)")
    return {
        "vistas": tuple(v for v in VISTAS if v in lista),
        "escala": escala,
        "papel": papel,
        "proyeccion": proyeccion,
        "detalle": bool(detalle),
    }


# ------------------------------------------------------------ geometry helpers


def _unitario(v: np.ndarray) -> np.ndarray:
    norma = float(np.linalg.norm(v))
    if norma < 1e-12:
        raise ValueError("vector nulo en la base de la vista")
    return v / norma


def _base(vista: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(V, U, R) of a view: direction to the viewer, paper up, paper right."""
    if vista == "isometrica":
        v = _unitario(np.array([1.0, -1.0, 1.0]))
        u = _unitario(np.array([0.0, 0.0, 1.0]) - np.dot([0.0, 0.0, 1.0], v) * v)
    else:
        vv, uu = _BASE_VISTA[vista]
        v, u = np.array(vv), np.array(uu)
    r = np.cross(u, v)
    return v, u, r


def _marco(lo: np.ndarray, hi: np.ndarray, u: np.ndarray, r: np.ndarray) -> tuple[float, float, float, float]:
    """(umin, umax, vmin, vmax) of the box corners in a view frame."""
    esquinas = np.array(
        [[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
    )
    us, vs = esquinas @ r, esquinas @ u
    return float(us.min()), float(us.max()), float(vs.min()), float(vs.max())


def _dist_punto_segmento(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    largo2 = dx * dx + dy * dy
    t = 0.0 if largo2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / largo2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _quitar_coincidentes(
    visibles: list[list[tuple[float, float]]], ocultas: list[list[tuple[float, float]]]
) -> list[list[tuple[float, float]]]:
    """Drop the hidden segments that lie on a visible one (a solid's bottom
    outline projects onto its top outline). Each hidden segment is kept
    unless all three of its interior samples are covered by a visible one."""
    tol = TOLERANCIA_COINCIDENCIA
    celda = 1.0
    rejilla: dict[tuple[int, int], list[tuple[tuple[float, float], tuple[float, float]]]] = {}
    for pl in visibles:
        for a, b in zip(pl, pl[1:]):
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for i in range(math.floor((x0 - tol) / celda), math.floor((x1 + tol) / celda) + 1):
                for j in range(math.floor((y0 - tol) / celda), math.floor((y1 + tol) / celda) + 1):
                    rejilla.setdefault((i, j), []).append((a, b))

    def cubierto(p: tuple[float, float]) -> bool:
        for a, b in rejilla.get((math.floor(p[0] / celda), math.floor(p[1] / celda)), ()):
            if _dist_punto_segmento(p, a, b) <= tol:
                return True
        return False

    resultado: list[list[tuple[float, float]]] = []
    for pl in ocultas:
        for a, b in zip(pl, pl[1:]):
            muestras = [(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t) for t in (0.25, 0.5, 0.75)]
            if not all(cubierto(p) for p in muestras):
                resultado.append([a, b])
    return resultado


def _polilinea_arista(arista: Any, dx: float = 0.0, dy: float = 0.0) -> list[tuple[float, float]]:
    if arista.geom_type == GeomType.LINE:
        pts = [arista.position_at(0.0), arista.position_at(1.0)]
    else:
        n = max(8, min(96, math.ceil(arista.length / 0.5)))
        pts = [arista.position_at(i / (n - 1)) for i in range(n)]
    return [(float(p.X) + dx, float(p.Y) + dy) for p in pts]


def _proyectar_brep(
    shape: Any, v: np.ndarray, u: np.ndarray, r: np.ndarray, centro: np.ndarray, diag: float
) -> tuple[list, list, int]:
    """(visibles, ocultas, aristas) from the OCC projection. build123d reports
    the points relative to `look_at`, here the bbox centre (without it the
    centre of mass is used, which shifts the sheet). They are moved back to the
    absolute frame of the view (`centro @ r`, `centro @ u`), the frame in which
    the layout and the dimensions measure."""
    origen = centro + v * (4.0 * diag)
    vis, hid = shape.project_to_viewport(tuple(origen), tuple(u), tuple(centro))
    dx, dy = float(centro @ r), float(centro @ u)
    visibles = [_polilinea_arista(e, dx, dy) for e in vis]
    ocultas = [_polilinea_arista(e, dx, dy) for e in hid]
    return visibles, ocultas, len(visibles) + len(ocultas)


def _proyectar_malla(malla: trimesh.Trimesh, v: np.ndarray, r: np.ndarray, u: np.ndarray) -> list[list[tuple[float, float]]]:
    """Silhouette and crease edges of a mesh seen from V: an edge is drawn when
    one of its faces faces the viewer and either the faces disagree on facing
    (silhouette) or they meet at a crease. Open boundary edges are not drawn."""
    frente = (malla.face_normals @ v) > 1e-9
    adj = malla.face_adjacency
    f1, f2 = frente[adj[:, 0]], frente[adj[:, 1]]
    rasgo = malla.face_adjacency_angles > math.radians(ANGULO_RASGO_GRADOS)
    visible = (f1 | f2) & ((f1 != f2) | rasgo)
    aristas = malla.face_adjacency_edges[visible]
    vertices = malla.vertices
    segmentos: list[list[tuple[float, float]]] = []
    for ia, ib in aristas:
        pa, pb = vertices[ia], vertices[ib]
        segmentos.append([(float(pa @ r), float(pa @ u)), (float(pb @ r), float(pb @ u))])
    return segmentos


def _eje_global(d: tuple[float, float, float]) -> str | None:
    for eje, idx in zip(_EJES, range(3)):
        if abs(abs(d[idx]) - 1.0) < 1e-6:
            return eje
    return None


def _agujeros(shape: Any, lo: np.ndarray) -> tuple[list[dict[str, Any]], int]:
    """Holes: closed cylinders (a full turn, possibly split into several faces)
    in material-free space. Returns ([{eje, d, c}], omitidas): `c` is the centre
    in the two in-plane axes measured from the bbox min corner, in the order of
    `_ejes_plano(eje)`. Cylinders whose axis is not parallel to a global axis
    are counted in `omitidas`, never guessed. A partial cylinder (a concave
    fillet or a round) covers less than a turn and is not a hole."""
    solidos = list(shape.solids())
    grupos: dict[tuple, dict[str, Any]] = {}
    for cara in shape.faces():
        if cara.geom_type != GeomType.CYLINDER:
            continue
        superficie = BRepAdaptor_Surface(cara.wrapped)
        if superficie.GetType() != GeomAbs_Cylinder:
            continue
        cilindro = superficie.Cylinder()
        eje_ax = cilindro.Axis()
        d = eje_ax.Direction()
        loc = eje_ax.Location()
        dvec = (d.X(), d.Y(), d.Z())
        # A hole has no material on its axis at the face's height; a boss does.
        c = cara.center()
        t = (c.X - loc.X()) * dvec[0] + (c.Y - loc.Y()) * dvec[1] + (c.Z - loc.Z()) * dvec[2]
        punto = gp_Pnt(loc.X() + t * dvec[0], loc.Y() + t * dvec[1], loc.Z() + t * dvec[2])
        en_material = any(
            BRepClass3d_SolidClassifier(s.wrapped, punto, 1e-6).State() == TopAbs_IN for s in solidos
        )
        if en_material:
            continue
        radio = cilindro.Radius()
        eje = _eje_global(dvec)
        if eje is None:
            clave: tuple = (
                "oblicuo", round(dvec[0], 3), round(dvec[1], 3), round(dvec[2], 3),
                round(loc.X(), 3), round(loc.Y(), 3), round(loc.Z(), 3), round(radio, 3),
            )
            entrada = grupos.setdefault(clave, {"eje": None, "d": round(2 * radio, 3), "c": None, "giro": 0.0})
        else:
            coord = {"x": loc.X(), "y": loc.Y(), "z": loc.Z()}
            ejes_plano = _ejes_plano(eje)
            c1 = coord[ejes_plano[0]] - float(lo[_EJES.index(ejes_plano[0])])
            c2 = coord[ejes_plano[1]] - float(lo[_EJES.index(ejes_plano[1])])
            clave = (eje, round(c1, 3), round(c2, 3), round(2 * radio, 3))
            entrada = grupos.setdefault(
                clave, {"eje": eje, "d": round(2 * radio, 3), "c": (round(c1, 3), round(c2, 3)), "giro": 0.0}
            )
        umin, umax, _vmin, _vmax = BRepTools.UVBounds_s(cara.wrapped)
        entrada["giro"] += float(umax - umin)
    agujeros: list[dict[str, Any]] = []
    omitidas = 0
    for entrada in grupos.values():
        if entrada["giro"] < 2.0 * math.pi - TOLERANCIA_GIRO:
            continue  # a fillet or a round: part of a turn, not a hole
        if entrada["eje"] is None:
            omitidas += 1
            continue
        agujeros.append({"eje": entrada["eje"], "d": entrada["d"], "c": entrada["c"]})
    return agujeros, omitidas


def _ejes_plano(eje: str) -> tuple[str, str]:
    return tuple(e for e in _EJES if e != eje)  # type: ignore[return-value]


# ------------------------------------------------------------ formatting


def _fmt(valor: float) -> str:
    texto = f"{valor:.3f}".rstrip("0").rstrip(".")
    return "0" if texto in ("-0", "") else texto


def _limpio(texto: Any, largo: int) -> str:
    """Untrusted text for the sheet: no control characters, bounded length.
    Escaping per format happens in the writers."""
    s = _RE_CONTROL.sub(" ", str(texto))
    return s if len(s) <= largo else s[: largo - 3] + "..."


# ------------------------------------------------------------ dimensions


def _vinculo(valor: float, parametros: dict[str, float]) -> str | None:
    """PARAMETROS key whose value equals ``valor`` (1e-6), only when unique."""
    claves = [k for k, v in parametros.items() if abs(float(v) - valor) <= 1e-6]
    return claves[0] if len(claves) == 1 else None


def _cota(tipo: str, eje: str, valor: float, vista: str, parametros: dict[str, float], id_: str, **extra: Any) -> dict[str, Any]:
    clave = _vinculo(valor, parametros)
    return {
        "id": id_,
        "tipo": tipo,
        "valor_mm": round(float(valor), 3),
        "eje": eje,
        "vista": vista,
        "origen": "parametro" if clave else "reconocido",
        "parametro": clave,
        "operacion": None,
        **extra,
    }


def _id_tamano(eje: str, valor: float) -> str:
    return f"tamano_total:{eje}:{valor:.3f}"


def _id_diametro(eje: str, d: float, cantidad: int) -> str:
    return f"diametro:{eje}:{d:.3f}:n{cantidad}"


def _id_posicion(eje_p: str, valor: float, ag: dict[str, Any]) -> str:
    return f"posicion:{eje_p}:{valor:.3f}:{ag['eje']}{ag['c'][0]:.3f}_{ag['c'][1]:.3f}:d{ag['d']:.3f}"


def _cotas(
    dimensiones: tuple[float, float, float],
    agujeros: list[dict[str, Any]],
    parametros: dict[str, float],
) -> list[dict[str, Any]]:
    """Every dimension the sheet can carry. `construir` keeps only the ones it
    actually draws, so the title block count always equals the drawn count."""
    cotas: list[dict[str, Any]] = []
    for eje, valor in zip(_EJES, dimensiones):
        cotas.append(_cota("tamano_total", eje, valor, _VISTA_DE_EJE_COTA[eje], parametros, _id_tamano(eje, valor)))
    grupos: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for ag in agujeros:
        grupos.setdefault((ag["eje"], ag["d"]), []).append(ag)
    for (eje, d), miembros in sorted(grupos.items()):
        vista = _VISTA_DE_EJE_AGUJERO[eje]
        cotas.append(
            _cota("diametro", eje, d, vista, parametros, _id_diametro(eje, d, len(miembros)), cantidad=len(miembros))
        )
        for ag in sorted(miembros, key=lambda a: a["c"]):
            for indice, eje_p in enumerate(_ejes_plano(eje)):
                valor = ag["c"][indice]
                cotas.append(_cota("posicion", eje_p, valor, vista, parametros, _id_posicion(eje_p, valor, ag)))
    return sorted(cotas, key=lambda c: (_ORDEN_TIPO[c["tipo"]], c["eje"], c["valor_mm"], c["id"]))


# ------------------------------------------------------------ layout and primitives


def _rectangulo(x0: float, y0: float, x1: float, y1: float, capa: str, grosor: float) -> list[dict[str, Any]]:
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return [_trazo(capa, pts[i : i + 2], grosor) for i in range(4)]


def _trazo(capa: str, puntos: list[tuple[float, float]], grosor: float, discontinua: bool = False) -> dict[str, Any]:
    return {"tipo": "trazo", "capa": capa, "puntos": puntos, "grosor": grosor, "discontinua": discontinua}


def _texto(capa: str, x: float, y: float, texto: str, altura: float = ALTURA_TEXTO, anclaje: str = "izq") -> dict[str, Any]:
    return {"tipo": "texto", "capa": capa, "x": x, "y": y, "texto": texto, "altura": altura, "anclaje": anclaje}


def _celdas(
    vistas: tuple[str, ...],
    tamanos: dict[str, tuple[float, float]],
    escala: float,
    proyeccion: str,
) -> tuple[dict[str, tuple[float, float]], list[float], list[float]]:
    """Origins of each view cell and the column widths / row heights (mm of
    paper) for the scale. Cells carry a PAD margin for dimensions and titles."""
    columnas: dict[int, float] = {}
    filas: dict[int, float] = {}
    slots = {v: _SLOTS[proyeccion][v] for v in vistas}
    for vista in vistas:
        col, fila = slots[vista]
        ancho, alto = tamanos[vista]
        columnas[col] = max(columnas.get(col, 0.0), ancho * escala + 2 * PAD)
        filas[fila] = max(filas.get(fila, 0.0), alto * escala + 2 * PAD)
    anchos = [columnas.get(c, 0.0) for c in range(1, 3)]
    altos = [filas.get(f, 0.0) for f in range(1, 3)]
    origenes = {}
    for vista in vistas:
        col, fila = slots[vista]
        origenes[vista] = (sum(anchos[: col - 1]), sum(altos[: fila - 1]))
    return origenes, anchos, altos


def _elegir_escala(
    vistas: tuple[str, ...],
    tamanos: dict[str, tuple[float, float]],
    papel: str,
    proyeccion: str,
    pedida: str | None,
) -> str:
    ancho_p, alto_p = PAPELES[papel]
    area_w = ancho_p - 2 * MARGEN
    area_h = alto_p - 2 * MARGEN - ALTO_CAJETIN - 6.0
    candidatos = [pedida] if pedida else list(ESCALAS)
    for nombre in candidatos:
        _, anchos, altos = _celdas(vistas, tamanos, ESCALAS[nombre], proyeccion)
        if sum(anchos) <= area_w + 1e-9 and sum(altos) <= area_h + 1e-9:
            return nombre
    if pedida:
        raise ValueError(f"la pieza no cabe en papel {papel} a {pedida}; usa otra escala o papel A3")
    raise ValueError(f"la pieza no cabe en papel {papel} ni a 1:10; usa papel A3")


def _cajetin(
    primitivas: list[dict[str, Any]],
    nombre: str,
    piezas: list[str],
    revision: str | None,
    opciones: dict[str, Any],
    escala: str,
    cotas: int,
    es_malla: bool,
    papel: str,
    completo: bool,
) -> None:
    ancho_p, alto_p = PAPELES[papel]
    x1 = ancho_p - MARGEN
    y1 = alto_p - MARGEN
    x0 = x1 - ANCHO_CAJETIN
    y0 = y1 - ALTO_CAJETIN
    primitivas.extend(_rectangulo(x0, y0, x1, y1, "TEXTO", GROSOR_VISIBLE))
    proy = "tercer ángulo" if opciones["proyeccion"] == "tercer_angulo" else "primer ángulo"
    filas = [
        f"Documento: {_limpio(nombre, MAX_LARGO_NOMBRE)}",
        f"Piezas: {_limpio(', '.join(piezas) or 'pieza', MAX_LARGO_PIEZAS)}",
        f"Revisión: {_limpio(revision or 'sin revisión', 40)}   Fecha: {datetime.now(timezone.utc).date().isoformat()}",
        f"Escala: {escala}   Papel: {papel}   Unidad: mm",
        f"Proyección: {proy}   Cotas: {cotas}",
    ]
    if es_malla:
        filas.append("Malla STL: solo contornos, sin líneas ocultas ni agujeros")
    if not completo:
        filas.append("Vista isométrica sin líneas ocultas (pieza muy grande)")
    for indice, texto in enumerate(filas[:8]):
        primitivas.append(_texto("TEXTO", x0 + 3, y0 + 6 + indice * 5.0, texto))


def _tabla_parametros(
    primitivas: list[dict[str, Any]],
    parametros: dict[str, Any],
    papel: str,
) -> None:
    if not parametros:
        return
    ancho_p, alto_p = PAPELES[papel]
    x0 = MARGEN
    y1 = alto_p - MARGEN
    y0 = y1 - ALTO_CAJETIN
    x1 = ancho_p - MARGEN - ANCHO_CAJETIN - 5.0
    primitivas.extend(_rectangulo(x0, y0, x1, y1, "TEXTO", GROSOR_VISIBLE))
    primitivas.append(_texto("TEXTO", x0 + 3, y0 + 6, "Parámetros"))
    entradas = list(parametros.items())[:MAX_PARAMETROS_TABLA]
    mitad = math.ceil(len(entradas) / 2) if entradas else 0
    for indice, (clave, dato) in enumerate(entradas):
        columna, fila = divmod(indice, mitad) if mitad else (0, 0)
        valor, unidad = dato
        linea = f"{_limpio(clave, 40)} = {_fmt(float(valor))}{(' ' + _limpio(unidad, 12)) if unidad else ''}"
        primitivas.append(
            _texto("TEXTO", x0 + 3 + columna * ((x1 - x0) / 2), y0 + 11 + fila * 4.6, linea, altura=2.8)
        )
    restantes = len(parametros) - len(entradas)
    if restantes > 0:
        primitivas.append(_texto("TEXTO", x1 - 3, y1 - 3, f"y {restantes} más", altura=2.8, anclaje="der"))


def _marco_vistas(
    vistas: tuple[str, ...],
    lo: np.ndarray,
    hi: np.ndarray,
) -> dict[str, dict[str, Any]]:
    marcos: dict[str, dict[str, Any]] = {}
    for vista in vistas:
        v, u, r = _base(vista)
        umin, umax, vmin, vmax = _marco(lo, hi, u, r)
        marcos[vista] = {"v": v, "u": u, "r": r, "umin": umin, "umax": umax, "vmin": vmin, "vmax": vmax}
    return marcos


class Plano:
    """Result of `construir`: primitives in paper mm plus the descriptors."""

    def __init__(self, primitivas: list[dict[str, Any]], cotas: list[dict[str, Any]], opciones: dict[str, Any],
                 escala: str, papel: str, completo: bool, omitidas: int, aviso: str | None, es_malla: bool) -> None:
        self.primitivas = primitivas
        self.cotas = cotas
        self.vistas = opciones["vistas"]
        self.proyeccion = opciones["proyeccion"]
        self.escala = escala
        self.papel = papel
        self.completo = completo
        self.omitidas = omitidas
        self.aviso = aviso
        self.es_malla = es_malla

    @property
    def tamano_mm(self) -> tuple[float, float]:
        return PAPELES[self.papel]

    def resumen(self, detalle: bool) -> dict[str, Any]:
        respuesta: dict[str, Any] = {
            "vistas": list(self.vistas),
            "escala": self.escala,
            "papel": self.papel,
            "proyeccion": self.proyeccion,
            "cotas": len(self.cotas),
            "parametros_ligados": sum(1 for c in self.cotas if c["parametro"]),
            "omitidas": self.omitidas,
        }
        if not self.completo:
            respuesta["completo"] = False
        if self.aviso:
            respuesta["aviso"] = self.aviso
        if detalle:
            respuesta["cotas_detalle"] = self.cotas[:MAX_COTAS_DETALLE]
            if len(self.cotas) > MAX_COTAS_DETALLE:
                respuesta["cotas_mas"] = len(self.cotas) - MAX_COTAS_DETALLE
        return respuesta


def construir(
    fuente: Any,
    *,
    nombre: str,
    piezas: list[str] | None = None,
    revision: str | None = None,
    parametros: dict[str, Any] | None = None,
    opciones: dict[str, Any] | None = None,
) -> Plano:
    """One sheet for a B-rep shape or a trimesh. ``parametros`` is
    ``{clave: valor}`` or ``{clave: (valor, unidad)}`` (PARAMETROS of a
    script document, used for linking dimensions and for the table)."""
    opts = opciones or validar("svg")
    inicio = time.monotonic()
    es_malla = isinstance(fuente, trimesh.Trimesh)
    if es_malla and len(fuente.faces) > MAX_TRIANGULOS_PLANO:
        raise ValueError(
            f"malla demasiado grande para un plano (máximo {MAX_TRIANGULOS_PLANO} triángulos); "
            "usa un STEP o reduce la malla"
        )
    if es_malla:
        lo, hi = (np.array(b, dtype=float) for b in fuente.bounds)
    else:
        bb = fuente.bounding_box()
        lo = np.array([bb.min.X, bb.min.Y, bb.min.Z], dtype=float)
        hi = np.array([bb.max.X, bb.max.Y, bb.max.Z], dtype=float)
    dimensiones = tuple(round(float(h - l), 3) for l, h in zip(lo, hi))
    if min(dimensiones) <= 0:
        raise ValueError("la geometria no tiene volumen para dibujar")
    centro = (lo + hi) / 2.0
    diag = float(np.linalg.norm(hi - lo))
    vistas_orden = opts["vistas"]
    extra_vistas = ("isometrica",) if "isometrica" in vistas_orden else ()
    todas = tuple(v for v in ("frontal", "superior", "derecha") if v in vistas_orden) + extra_vistas

    marcos = _marco_vistas(todas, lo, hi)
    proyecciones: dict[str, tuple[list, list]] = {}
    completo = True
    total_aristas = 0
    for vista in todas:
        marco = marcos[vista]
        if es_malla:
            visibles = _proyectar_malla(fuente, marco["v"], marco["r"], marco["u"])
            ocultas: list = []
        else:
            visibles, ocultas, n = _proyectar_brep(fuente, marco["v"], marco["u"], marco["r"], centro, diag)
            total_aristas += n
            # Over the cap or the wall-clock budget: the isometric view keeps its
            # visible edges only (ADR-0021), stated by completo=false.
            if vista == "isometrica" and (
                total_aristas > CAP_ARISTAS or time.monotonic() - inicio > PRESUPUESTO_S
            ):
                ocultas = []
                completo = False
            else:
                ocultas = _quitar_coincidentes(visibles, ocultas)
        if len(visibles) > CAP_ARISTAS:
            visibles = visibles[:CAP_ARISTAS]
            completo = False
        proyecciones[vista] = (visibles, ocultas)

    agujeros: list[dict[str, Any]] = []
    omitidas = 0
    if not es_malla:
        agujeros, omitidas = _agujeros(fuente, lo)
    cotas = _cotas(dimensiones, agujeros, _valores_parametros(parametros))

    tamanos = {}
    for vista in todas:
        m = marcos[vista]
        tamanos[vista] = (m["umax"] - m["umin"], m["vmax"] - m["vmin"])
    escala = _elegir_escala(vistas_orden, tamanos, opts["papel"], opts["proyeccion"], opts["escala"])
    factor = ESCALAS[escala]
    origenes, anchos, altos = _celdas(vistas_orden, tamanos, factor, opts["proyeccion"])
    ancho_p, alto_p = PAPELES[opts["papel"]]
    area_w = ancho_p - 2 * MARGEN
    area_h = alto_p - 2 * MARGEN - ALTO_CAJETIN - 6.0
    desplaza_x = MARGEN + (area_w - sum(anchos)) / 2.0
    desplaza_y = MARGEN + (area_h - sum(altos)) / 2.0

    primitivas: list[dict[str, Any]] = []
    dibujadas: set[str] = set()
    for vista in vistas_orden:
        marco = marcos[vista]
        cx, cy = origenes[vista]
        x_ref = desplaza_x + cx + PAD
        y_ref = desplaza_y + cy + PAD

        def a_papel(p: tuple[float, float], marco=marco, x_ref=x_ref, y_ref=y_ref) -> tuple[float, float]:
            return (x_ref + (p[0] - marco["umin"]) * factor, y_ref + (marco["vmax"] - p[1]) * factor)

        visibles, ocultas = proyecciones[vista]
        for pl in visibles:
            primitivas.append(_trazo("VISIBLES", [a_papel(p) for p in pl], GROSOR_VISIBLE))
        for pl in ocultas:
            primitivas.append(_trazo("OCULTAS", [a_papel(p) for p in pl], GROSOR_OCULTA, discontinua=True))
        ancho_v = (marco["umax"] - marco["umin"]) * factor
        alto_v = (marco["vmax"] - marco["vmin"]) * factor
        primitivas.append(_texto("TEXTO", x_ref, y_ref - 4.0, _ROTULO_VISTA[vista], altura=3.5))
        if vista != "isometrica":
            dibujadas |= _cotas_de_vista(primitivas, vista, cotas, x_ref, y_ref, ancho_v, alto_v)
            dibujadas |= _agujeros_de_vista(
                primitivas, vista, agujeros, marco, lo, a_papel, factor, x_ref, y_ref, alto_v
            )
    # Only what is drawn is reported: the title block count equals the sheet.
    cotas = [c for c in cotas if c["id"] in dibujadas]

    _tabla_parametros(primitivas, _tabla_datos(parametros), opts["papel"])
    _cajetin(
        primitivas, nombre, list(piezas or []), revision, opts, escala, len(cotas),
        es_malla, opts["papel"], completo,
    )
    aviso = None
    if es_malla:
        aviso = "malla STL: solo contornos y cotas de bbox; sin líneas ocultas ni agujeros"
    elif omitidas:
        aviso = f"{omitidas} cilindro(s) con eje no paralelo a un eje global: sin cota"
    return Plano(primitivas, cotas, opts, escala, opts["papel"], completo, omitidas, aviso, es_malla)


def _valores_parametros(parametros: dict[str, Any] | None) -> dict[str, float]:
    resultado: dict[str, float] = {}
    for clave, dato in (parametros or {}).items():
        valor = dato[0] if isinstance(dato, (tuple, list)) else dato
        if isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor):
            resultado[clave] = float(valor)
    return resultado


def _tabla_datos(parametros: dict[str, Any] | None) -> dict[str, tuple[float, str]]:
    tabla: dict[str, tuple[float, str]] = {}
    for clave, dato in (parametros or {}).items():
        if isinstance(dato, (tuple, list)) and len(dato) == 2:
            tabla[clave] = (float(dato[0]), str(dato[1] or ""))
        elif isinstance(dato, (int, float)) and not isinstance(dato, bool):
            tabla[clave] = (float(dato), "")
    return tabla


def _punto_agujero(ag: dict[str, Any], marco: dict[str, Any], lo: np.ndarray, a_papel: Any) -> tuple[float, float]:
    """Paper position of a hole centre in the view whose axis is the hole axis.
    The axis component is irrelevant there (it is orthogonal to R and U)."""
    punto = np.array(lo, dtype=float)
    for indice, eje in enumerate(_ejes_plano(ag["eje"])):
        punto[_EJES.index(eje)] = float(lo[_EJES.index(eje)]) + ag["c"][indice]
    return a_papel((float(punto @ marco["r"]), float(punto @ marco["u"])))


def _marca_agujero(primitivas: list[dict[str, Any]], x: float, y: float, radio: float) -> None:
    """Centre mark of a hole: a crosshair (no text)."""
    brazo = radio + 2.0
    primitivas.append(_trazo("COTAS", [(x - brazo, y), (x + brazo, y)], GROSOR_COTA))
    primitivas.append(_trazo("COTAS", [(x, y - brazo), (x, y + brazo)], GROSOR_COTA))


def _cotas_de_vista(
    primitivas: list[dict[str, Any]],
    vista: str,
    cotas: list[dict[str, Any]],
    x_ref: float,
    y_ref: float,
    ancho_v: float,
    alto_v: float,
) -> set[str]:
    """Overall sizes drawn in the view that owns the axis (dimension lines).
    Returns the ids of the descriptors drawn."""
    eje_de_horizontal = {"frontal": "x", "superior": "x", "derecha": "y"}.get(vista)
    eje_de_vertical = {"frontal": "z", "superior": "y", "derecha": "z"}.get(vista)
    dibujadas: set[str] = set()
    for cota in cotas:
        if cota["tipo"] != "tamano_total" or cota["vista"] != vista:
            continue
        if cota["eje"] == eje_de_horizontal:
            x0, y0 = x_ref, y_ref + alto_v
            linea_y = y0 + 8.0
            primitivas.append(_trazo("COTAS", [(x0, y0 + 2.0), (x0, linea_y + 2.0)], GROSOR_COTA))
            primitivas.append(_trazo("COTAS", [(x0 + ancho_v, y0 + 2.0), (x0 + ancho_v, linea_y + 2.0)], GROSOR_COTA))
            primitivas.append(_trazo("COTAS", [(x0, linea_y), (x0 + ancho_v, linea_y)], GROSOR_COTA))
            primitivas.append(_texto("COTAS", x0 + ancho_v / 2.0, linea_y + 4.0, _fmt(cota["valor_mm"]), anclaje="centro"))
        elif cota["eje"] == eje_de_vertical:
            x0 = x_ref
            linea_x = x0 - 8.0
            primitivas.append(_trazo("COTAS", [(x0 - 2.0, y_ref), (linea_x - 2.0, y_ref)], GROSOR_COTA))
            primitivas.append(_trazo("COTAS", [(x0 - 2.0, y_ref + alto_v), (linea_x - 2.0, y_ref + alto_v)], GROSOR_COTA))
            primitivas.append(_trazo("COTAS", [(linea_x, y_ref), (linea_x, y_ref + alto_v)], GROSOR_COTA))
            primitivas.append(_texto("COTAS", linea_x - 1.5, y_ref + alto_v / 2.0 + 1.0, _fmt(cota["valor_mm"]), anclaje="der"))
        else:
            continue
        dibujadas.add(cota["id"])
    return dibujadas


def _agujeros_de_vista(
    primitivas: list[dict[str, Any]],
    vista: str,
    agujeros: list[dict[str, Any]],
    marco: dict[str, Any],
    lo: np.ndarray,
    a_papel: Any,
    factor: float,
    x_ref: float,
    y_ref: float,
    alto_v: float,
) -> set[str]:
    """Holes seen along their own axis: a crosshair per hole, one Ø label per
    group (`n x Ø d` when several), and the two position dimensions of every
    hole measured from the datum corner (left edge and bottom edge of the view,
    which are the bbox min corner). Returns the ids of the descriptors drawn."""
    dibujadas: set[str] = set()
    grupos: dict[tuple[str, float], list[dict[str, Any]]] = {}
    for ag in agujeros:
        if _VISTA_DE_EJE_AGUJERO[ag["eje"]] == vista:
            grupos.setdefault((ag["eje"], ag["d"]), []).append(ag)
    niveles = [0, 0]  # dimension lines already placed: [horizontal, vertical]
    for (eje, d), miembros in sorted(grupos.items()):
        miembros = sorted(miembros, key=lambda a: a["c"])
        dibujadas.add(_id_diametro(eje, d, len(miembros)))
        radio = d / 2.0 * factor
        etiqueta = f"Ø{_fmt(d)}" if len(miembros) == 1 else f"{len(miembros)} x Ø{_fmt(d)}"
        for indice_grupo, ag in enumerate(miembros):
            hx, hy = _punto_agujero(ag, marco, lo, a_papel)
            _marca_agujero(primitivas, hx, hy, radio)
            if indice_grupo == 0:
                primitivas.append(_texto("COTAS", hx + radio + 1.0, hy - 1.0, etiqueta, altura=2.8))
            for indice, eje_p in enumerate(_ejes_plano(eje)):
                valor = ag["c"][indice]
                nivel = niveles[indice]
                niveles[indice] += 1
                desplazamiento = DISTANCIA_COTA_POSICION + DESPLAZAMIENTO_COTA_POSICION * nivel
                if indice == 0:  # horizontal, measured from the left edge
                    y_linea = y_ref + alto_v + desplazamiento
                    primitivas.append(_trazo("COTAS", [(x_ref, y_ref + alto_v + 2.0), (x_ref, y_linea + 2.0)], GROSOR_COTA))
                    primitivas.append(_trazo("COTAS", [(hx, hy + radio + 1.0), (hx, y_linea + 2.0)], GROSOR_COTA))
                    primitivas.append(_trazo("COTAS", [(x_ref, y_linea), (hx, y_linea)], GROSOR_COTA))
                    primitivas.append(
                        _texto("COTAS", (x_ref + hx) / 2.0, y_linea + 4.0, _fmt(valor), anclaje="centro")
                    )
                else:  # vertical, measured from the bottom edge
                    x_linea = x_ref - desplazamiento
                    y_base = y_ref + alto_v
                    primitivas.append(_trazo("COTAS", [(x_ref - 2.0, y_base), (x_linea - 2.0, y_base)], GROSOR_COTA))
                    primitivas.append(_trazo("COTAS", [(hx - radio - 1.0, hy), (x_linea - 2.0, hy)], GROSOR_COTA))
                    primitivas.append(_trazo("COTAS", [(x_linea, y_base), (x_linea, hy)], GROSOR_COTA))
                    primitivas.append(
                        _texto("COTAS", x_linea - 1.5, (y_base + hy) / 2.0 + 1.0, _fmt(valor), anclaje="der")
                    )
                dibujadas.add(_id_posicion(eje_p, valor, ag))
    return dibujadas


# ------------------------------------------------------------ writers


def _texto_pdf(texto: str) -> bytes:
    crudo = texto.encode("cp1252", errors="replace")
    return crudo.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def escribir_svg(plano: Plano) -> bytes:
    ancho, alto = plano.tamano_mm
    partes = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{ancho:g}mm" height="{alto:g}mm" viewBox="0 0 {ancho:g} {alto:g}">',
    ]
    for capa in CAPAS:
        partes.append(f'<g id="{capa}">')
        for p in plano.primitivas:
            if p["capa"] != capa:
                continue
            if p["tipo"] == "trazo":
                d = "M" + " L".join(f"{x:.3f} {y:.3f}" for x, y in p["puntos"])
                dash = ' stroke-dasharray="1.2 0.8"' if p["discontinua"] else ""
                partes.append(f'<path d="{d}" fill="none" stroke="#000" stroke-width="{p["grosor"]:g}"{dash}/>')
            else:
                anclaje = {"izq": "start", "centro": "middle", "der": "end"}[p["anclaje"]]
                partes.append(
                    f'<text x="{p["x"]:.3f}" y="{p["y"]:.3f}" font-size="{p["altura"]:g}" '
                    f'font-family="Helvetica, Arial, sans-serif" text-anchor="{anclaje}">'
                    f"{_escapar_xml(p['texto'])}</text>"
                )
        partes.append("</g>")
    partes.append("</svg>")
    return ("\n".join(partes) + "\n").encode("utf-8")


def escribir_pdf(plano: Plano) -> bytes:
    """Minimal PDF 1.4 by hand (no dependency): one page, Helvetica base-14
    with WinAnsiEncoding, lines as path operators, one xref table."""
    k = 72.0 / 25.4
    ancho, alto = plano.tamano_mm
    flujo: list[bytes] = []
    for capa in CAPAS:
        for p in plano.primitivas:
            if p["capa"] != capa:
                continue
            if p["tipo"] == "trazo":
                dash = b"[3 2] 0 d" if p["discontinua"] else b"[] 0 d"
                pts = p["puntos"]
                ops = [f"{p['grosor'] * k:.3f} w".encode(), dash]
                ops.append(f"{pts[0][0] * k:.3f} {(alto - pts[0][1]) * k:.3f} m".encode())
                for x, y in pts[1:]:
                    ops.append(f"{x * k:.3f} {(alto - y) * k:.3f} l".encode())
                ops.append(b"S")
                flujo.append(b" ".join(ops))
            else:
                tamano = p["altura"] * k
                ancho_txt = 0.5 * p["altura"] * len(p["texto"])
                x = p["x"]
                if p["anclaje"] == "centro":
                    x -= ancho_txt / 2.0
                elif p["anclaje"] == "der":
                    x -= ancho_txt
                flujo.append(
                    b"BT /F1 %.2f Tf %.3f %.3f Td (" % (tamano, x * k, (alto - p["y"]) * k)
                    + _texto_pdf(p["texto"])
                    + b") Tj ET"
                )
    contenido = b"\n".join(flujo) + b"\n"
    objetos = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        (
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 %.3f %.3f]/Contents 4 0 R"
            b"/Resources<</Font<</F1 5 0 R>>>>>>" % (ancho * k, alto * k)
        ),
        b"<</Length %d>>\nstream\n" % len(contenido) + contenido + b"endstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>",
    ]
    salida = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    desplazamientos = []
    for i, cuerpo in enumerate(objetos, start=1):
        desplazamientos.append(len(salida))
        salida += b"%d 0 obj\n" % i + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    for pos in desplazamientos:
        salida += b"%010d 00000 n \n" % pos
    salida += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio_xref)
    return bytes(salida)


def escribir_dxf(plano: Plano) -> bytes:
    """DXF R2013 with layers VISIBLES, OCULTAS (DASHED), COTAS, TEXTO; units mm;
    coordinates are paper millimetres (y up)."""
    from ezdxf.enums import TextEntityAlignment

    alto = plano.tamano_mm[1]
    doc = ezdxf.new("R2013")
    doc.header["$INSUNITS"] = 4  # millimetres
    if not doc.linetypes.has_entry("DASHED"):
        doc.linetypes.add("DASHED", pattern=[3.0, 2.0, -1.0], description="Dashed")
    for capa in CAPAS:
        if not doc.layers.has_entry(capa):
            doc.layers.add(capa)
    doc.layers.get("OCULTAS").dxf.linetype = "DASHED"
    msp = doc.modelspace()
    alineacion = {
        "izq": TextEntityAlignment.LEFT,
        "centro": TextEntityAlignment.CENTER,
        "der": TextEntityAlignment.RIGHT,
    }
    for p in plano.primitivas:
        if p["tipo"] == "trazo":
            msp.add_lwpolyline([(x, alto - y) for x, y in p["puntos"]], dxfattribs={"layer": p["capa"]})
        else:
            texto = p["texto"].replace("%", "％")  # '%%' control codes in TEXT entities
            entidad = msp.add_text(texto, dxfattribs={"height": p["altura"], "layer": p["capa"]})
            entidad.set_placement((p["x"], alto - p["y"]), align=alineacion[p["anclaje"]])
    flujo = io.StringIO()
    doc.write(flujo)
    return flujo.getvalue().encode("utf-8")


def escribir(plano: Plano, formato: str) -> bytes:
    if formato == "svg":
        return escribir_svg(plano)
    if formato == "pdf":
        return escribir_pdf(plano)
    if formato == "dxf":
        return escribir_dxf(plano)
    raise ValueError(f"formato de plano no soportado: {formato!r}")
