"""Phase 4 stable-naming tests (ADR-0003 "rebuild-and-resolve" acceptance).

Tags a face of a box by its fingerprint, then rebuilds the shape so raw
kernel face indices shift (an unrelated hole elsewhere reorders OCC's
topology) and asserts the tag still resolves to the *same* geometric face.
A second case makes sure a genuinely lost reference (the tagged face itself
is cut away) is reported as unresolved, never silently rebound to the
wrong element.
"""
from __future__ import annotations

from build123d import Box, Cylinder, Location

import naming


def test_resuelve_referencia_tras_reconstruccion_con_cambio_no_relacionado():
    original = Box(20, 10, 5)
    cara_objetivo = original.faces()[0]
    huella_guardada = naming.fingerprint_cara(cara_objetivo)

    # Reconstruccion: mismo box, con un agujero cilindrico que atraviesa un
    # eje distinto al de la cara etiquetada -> reordena los indices de cara
    # del kernel (6 caras -> 7), pero no toca la cara etiquetada.
    reconstruido = original - Cylinder(1.0, 20)
    assert len(reconstruido.faces()) != len(original.faces())

    candidatas = naming.caras(reconstruido)
    resuelta = naming.resolver(huella_guardada, candidatas)

    assert resuelta is not None
    assert resuelta.centroide == huella_guardada.centroide
    assert resuelta.direccion == huella_guardada.direccion
    assert abs(resuelta.medida - huella_guardada.medida) < 1e-6


def test_resolver_referencia_dict_marca_resuelto_y_actualiza_huella():
    original = Box(20, 10, 5)
    cara_objetivo = original.faces()[0]
    huella = naming.fingerprint_cara(cara_objetivo)
    referencia = {
        "tipo": "cara",
        "id": huella.id,
        "punto": list(huella.centroide),
        "huella": huella.a_dict(),
    }

    reconstruido = original - Cylinder(1.0, 20)
    resultado = naming.resolver_referencia(reconstruido, referencia)

    assert resultado["referencia_perdida"] is False
    assert resultado["id"] == huella.id  # geometria sin cambios -> mismo id


def test_referencia_perdida_cuando_la_cara_etiquetada_desaparece():
    original = Box(20, 10, 5)
    cara_objetivo = original.faces()[0]
    huella = naming.fingerprint_cara(cara_objetivo)
    referencia = {
        "tipo": "cara",
        "id": huella.id,
        "punto": list(huella.centroide),
        "huella": huella.a_dict(),
    }

    # Agujero grande que atraviesa justo la cara etiquetada: ya no existe
    # nada geometricamente equivalente que resolver() pueda encontrar.
    reconstruido = original - Cylinder(3.0, 25, rotation=(0, 90, 0))
    resultado = naming.resolver_referencia(reconstruido, referencia)

    assert resultado["referencia_perdida"] is True
    # nunca se re-enlaza silenciosamente a una cara distinta: se conserva
    # el ultimo id/huella conocidos.
    assert resultado["id"] == huella.id


def test_referencia_tipo_punto_nunca_se_marca_perdida():
    referencia = {"tipo": "punto", "id": None, "punto": [1.0, 2.0, 3.0], "huella": None}
    reconstruido = Box(5, 5, 5)
    resultado = naming.resolver_referencia(reconstruido, referencia)
    assert resultado["referencia_perdida"] is False
    assert resultado["punto"] == [1.0, 2.0, 3.0]


def test_resolver_referencia_con_huella_corrupta_no_lanza_y_marca_perdida():
    referencia = {"tipo": "cara", "id": "x", "punto": [0, 0, 0], "huella": {"solo": "basura"}}
    resultado = naming.resolver_referencia(Box(5, 5, 5), referencia)
    assert resultado["referencia_perdida"] is True


def test_resolver_no_mezcla_caras_con_aristas():
    b = Box(10, 10, 10)
    huella_cara = naming.fingerprint_cara(b.faces()[0])
    candidatas_aristas = naming.aristas(b)
    assert naming.resolver(huella_cara, candidatas_aristas) is None


# ---------------------------------------------------------------- ADR-0007:
# ambiguity policy — two or more tied candidates must never be resolved by
# order/first-seen luck; only a clear winner over the runner-up is accepted.


def test_resolver_ambiguo_con_dos_candidatas_casi_empatadas_sin_importar_el_orden():
    guardada = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.0, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    candidata_a = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.05, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    candidata_b = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.06, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    # razon de distancias 1.2x (< FACTOR_GANADOR_CLARO=3x) -> ambiguo, nunca
    # depende del orden en que llegan las candidatas.
    assert naming.resolver(guardada, [candidata_a, candidata_b]) is None
    assert naming.resolver(guardada, [candidata_b, candidata_a]) is None

    resuelta1, ambigua1 = naming.resolver_detallado(guardada, [candidata_a, candidata_b])
    resuelta2, ambigua2 = naming.resolver_detallado(guardada, [candidata_b, candidata_a])
    assert resuelta1 is None and resuelta2 is None
    assert ambigua1 is True and ambigua2 is True


def test_resolver_acepta_ganador_claro_sin_importar_el_orden():
    guardada = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.0, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    cercana = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.01, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    lejana = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.05, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    # razon 5x (>= FACTOR_GANADOR_CLARO=3x) y la mejor esta bajo TOL_GANADOR_CLARO_MM=0.1
    assert naming.resolver(guardada, [cercana, lejana]) == cercana
    assert naming.resolver(guardada, [lejana, cercana]) == cercana

    _resuelta, ambigua = naming.resolver_detallado(guardada, [lejana, cercana])
    assert ambigua is False


def test_referencia_perdida_ambigua_marca_flag_extra_en_resolver_referencia(monkeypatch):
    guardada = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.0, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    referencia = {
        "tipo": "cara",
        "id": guardada.id,
        "punto": list(guardada.centroide),
        "huella": guardada.a_dict(),
    }
    candidata_a = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.05, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    candidata_b = naming.Fingerprint(
        tipo="cara", subtipo="PLANE", centroide=(0.06, 0.0, 0.0), direccion=(0.0, 0.0, 1.0), medida=10.0,
    )
    # aisla el caso ambiguo del kernel real: `resolver_referencia` construye
    # sus candidatas via `naming.caras(shape)`, asi que se sustituye esa
    # funcion por candidatas empatadas sinteticas.
    monkeypatch.setattr(naming, "caras", lambda shape: [candidata_a, candidata_b])

    resultado = naming.resolver_referencia(object(), referencia)

    assert resultado["referencia_perdida"] is True
    assert resultado["ambigua"] is True
    # nunca se re-enlaza a ciegas: conserva el id/huella previos.
    assert resultado["id"] == guardada.id


def test_no_confunde_dos_agujeros_simetricos_tras_reconstruccion():
    """Two mirror-symmetric holes (same radius/area/subtype) sit far enough
    apart (30 mm) that a pin on one must never resolve to the other after
    an unrelated rebuild reorders face indices -- resolver() keys off
    geometry (centroid), never symmetry-adjacent proximity or list order.
    """
    izquierdo = Cylinder(3, 10).moved(Location((-15, 0, 0)))
    derecho = Cylinder(3, 10).moved(Location((15, 0, 0)))
    original = Box(60, 20, 5) - izquierdo - derecho

    caras_cilindricas = [f for f in original.faces() if f.geom_type.name == "CYLINDER"]
    assert len(caras_cilindricas) == 2
    cara_izq = min(caras_cilindricas, key=lambda f: f.center().X)
    huella_izq = naming.fingerprint_cara(cara_izq)

    # Reconstruccion: un tercer agujero sin relacion, en otro lugar, que
    # reordena los indices de cara del kernel pero no toca ninguno de los
    # dos agujeros simetricos.
    ajeno = Cylinder(2, 10).moved(Location((0, 8, 0)))
    reconstruido = original - ajeno
    assert len(reconstruido.faces()) != len(original.faces())

    resuelta, ambigua = naming.resolver_detallado(huella_izq, naming.caras(reconstruido))
    assert ambigua is False
    assert resuelta is not None
    # sigue siendo el agujero izquierdo, nunca el simetrico derecho.
    assert resuelta.centroide == huella_izq.centroide
