"""Assembly rest poses, topology constraints, references, persistence."""
import copy
import json
import math

import pytest
from build123d import Box

import assemblies
import naming
import solids
from kernel import b123d_kernel as kernel


def pre_63_state(state):
    """The same persisted state as a pre-6.3 build wrote it: `version: 1` and
    the English joint vocabulary. The one real instance on disk is the orphan
    `.ensambles/1652…/`, and it is what `assemblies` must refuse to read."""
    renombres = {"tipo": "type", "padre": "parent", "hijo": "child", "origen": "origin",
                 "eje": "axis", "limites": "limits", "valor": "value"}
    joints = [{renombres.get(key, key): value for key, value in joint.items()}
              for joint in state["articulaciones"]]
    return {**{k: v for k, v in state.items() if k not in ("articulaciones", "valores")},
            "version": 1, "joints": joints, "values": state["valores"]}


@pytest.fixture
def assembly(tmp_path, monkeypatch):
    monkeypatch.setattr(assemblies, "DOCUMENTOS_DIR", tmp_path)
    shapes = {"base": Box(2, 2, 2), "arm": Box(2, 2, 2).translate((10, 0, 0)),
              "tip": Box(2, 2, 2).translate((20, 0, 0))}
    combined = kernel.combinar_nombrados(shapes)
    path = tmp_path / "example.step"
    kernel.export_to_step(combined, path)
    entries = solids.construir_entradas(combined, list(shapes))
    joints = [dict(id="hinge", tipo="giro", padre="base", hijo="arm",
                   origen=[0, 0, 0], eje=[0, 0, 2], limites=[-180, 180]),
              dict(id="slide", tipo="deslizamiento", padre="arm", hijo="tip",
                   origen=[10, 0, 0], eje=[1, 0, 0], limites=[-5, 5])]
    return path, entries, joints


def center(shape):
    c = shape.center()
    return (c.X, c.Y, c.Z)


def test_absolute_pose_propagates_and_does_not_drift(assembly):
    path, entries, joints = assembly
    assemblies.define("example", path, entries, joints)
    posed = assemblies.pose_shapes("example", {"hinge": 90, "slide": 3})
    assert center(posed["base"]) == pytest.approx((0, 0, 0), abs=1e-7)
    assert center(posed["arm"]) == pytest.approx((0, 10, 0), abs=1e-7)
    assert center(posed["tip"]) == pytest.approx((0, 23, 0), abs=1e-7)
    kernel.export_to_step(kernel.combinar_nombrados(posed), path)
    assemblies.set_revision("example", path.read_bytes(), {"hinge": 90, "slide": 3})
    again = assemblies.pose_shapes("example", {})
    assert center(again["tip"]) == pytest.approx((0, 23, 0), abs=1e-7)
    reset = assemblies.pose_shapes("example", {"hinge": 0, "slide": 0})
    assert center(reset["tip"]) == pytest.approx((20, 0, 0), abs=1e-7)


def test_snapshot_restore_stale_and_explicit_clear(assembly):
    path, entries, joints = assembly
    assemblies.define("example", path, entries, joints)
    original = path.read_bytes()
    files = assemblies.snapshot_files("example")
    with pytest.raises(ValueError, match="ya existe"):
        assemblies.define("example", path, entries, joints)
    path.write_bytes(original + b"\n")
    assert assemblies.is_stale("example")
    with pytest.raises(ValueError, match="desactualizado"):
        assemblies.pose_shapes("example", {})
    path.write_bytes(original)
    assemblies.clear("example")
    assert assemblies.load("example") is None
    assemblies.restore_files("example", files)
    assert not assemblies.is_stale("example")
    assert center(assemblies.pose_shapes("example", {})["tip"])[0] == pytest.approx(20)
    assemblies.restore_files("example", {})
    assert assemblies.load("example") is None
    assert path.read_bytes() == original


@pytest.mark.parametrize("change", [
    {"eje": [0, 0, 0]}, {"limites": [2, 1]}, {"origen": [math.nan, 0, 0]},
    {"valor": 10}, {"padre": "missing"}, {"hijo": "base"},
    {"tipo": "unknown"}, {"id": "../bad"}, {"eje": [True, 0, 1]},
    {"junk": {"anidado": 1}},  # unknown keys are rejected, never persisted
])
def test_invalid_definition_leaves_no_state(assembly, change):
    path, entries, joints = assembly
    joints[0].update(change)
    with pytest.raises(ValueError):
        assemblies.define("example", path, entries, joints)
    assert assemblies.load("example") is None


def test_english_keys_are_not_accepted_as_aliases(assembly):
    """Phase 6.3: the definition vocabulary is the project's Spanish and there
    are no compatibility aliases — nothing had shipped on the English keys."""
    path, entries, _ = assembly
    english = [dict(id="slide", type="prismatic", parent="base", child="arm",
                    origin=[0, 0, 0], axis=[1, 0, 0], limits=[-5, 5], value=0)]
    with pytest.raises(ValueError, match="claves desconocidas"):
        assemblies.define("example", path, entries, english)
    assert assemblies.load("example") is None


def test_state_and_definition_speak_the_wire_vocabulary(assembly):
    """The persisted state is the wire's shape: `articulaciones`/`valores`,
    joints keyed `tipo`/`padre`/`hijo`/`origen`/`eje`/`limites`/`valor`, and
    the schema marker the vocabulary belongs to (`version: 2`)."""
    path, entries, joints = assembly
    state = assemblies.define("example", path, entries, joints)
    assert state["version"] == 2
    assert state["valores"] == {"hinge": 0.0, "slide": 0.0}
    assert set(state["articulaciones"][0]) == {
        "id", "tipo", "padre", "hijo", "origen", "eje", "limites", "valor",
    }
    assert state["articulaciones"][0]["tipo"] == "giro"
    assert assemblies.load("example") == state


def test_a_pre_6_3_state_is_never_read_silently(assembly):
    """Phase 6.3 fix round: a `state.json` written before the vocabulary
    rename (`version: 1`, English keys) must fail loudly and readably on
    every path that consumes it — never as a bare `KeyError` 500 — and
    `clear()` stays the recovery path because it never reads it."""
    path, entries, joints = assembly
    assemblies.define("example", path, entries, joints)
    state_path = assemblies._directory("example") / "state.json"
    state_path.write_text(json.dumps(pre_63_state(json.loads(state_path.read_text()))))
    for call in (lambda: assemblies.load("example"),
                 lambda: assemblies.is_stale("example"),
                 lambda: assemblies.pose_shapes("example", {}),
                 lambda: assemblies.set_revision("example", b"nada"),
                 lambda: assemblies.define("example", path, entries, joints)):
        with pytest.raises(ValueError, match="formato anterior"):
            call()
    assemblies.clear("example")
    assert assemblies.load("example") is None


def test_restore_files_refuses_a_pre_6_3_snapshot(assembly):
    """Phase 6.3 fix round: a snapshot whose `ensamble.json` is pre-6.3 must
    be refused, not planted — `POST /restaurar` used to answer 200 while
    writing a state the very next read could not parse. An empty file set is
    still exactly "no assembly" (`clear`), not an error."""
    path, entries, joints = assembly
    assemblies.define("example", path, entries, joints)
    state_path = assemblies._directory("example") / "state.json"
    files = dict(assemblies.snapshot_files("example"))
    files[assemblies.STATE_KEY] = json.dumps(
        pre_63_state(json.loads(files[assemblies.STATE_KEY]))).encode()
    antes = state_path.read_bytes()
    with pytest.raises(ValueError, match="formato anterior"):
        assemblies.validate_snapshot(files)
    with pytest.raises(ValueError, match="formato anterior"):
        assemblies.restore_files("example", files)
    assert state_path.read_bytes() == antes  # refused, nothing planted
    assert assemblies.load("example")["articulaciones"]
    assemblies.restore_files("example", {})
    assert assemblies.load("example") is None


def test_graph_constraints(assembly):
    path, entries, joints = assembly
    cycle = joints + [dict(id="cycle", tipo="fijo", padre="tip", hijo="base")]
    with pytest.raises(ValueError, match="ciclo"):
        assemblies.define("example", path, entries, cycle)
    duplicate = joints + [dict(id="dup", tipo="fijo", padre="base", hijo="tip")]
    with pytest.raises(ValueError, match="varios padres"):
        assemblies.define("example", path, entries, duplicate)
    entries[1]["nombre"] = entries[0]["nombre"]
    with pytest.raises(ValueError, match="repetido"):
        assemblies.define("example", path, entries, joints)


def test_references_and_value_validation(assembly):
    path, entries, joints = assembly
    shape = kernel.import_from_step(path)
    rows, _ = solids.solidos_por_indice(shape, entries)
    arm = next(row[2] for row in rows if row[0] == "arm")
    face = naming.caras(arm)[0]
    joints[0]["referencia_hijo"] = {"tipo": "cara", "id": face.id, "huella": face.a_dict()}
    invalid = copy.deepcopy(joints)
    invalid[0]["referencia_hijo"]["huella"]["centroide"] = [9999, 0, 0]
    invalid[0]["referencia_hijo"]["id"] = naming.fingerprint_desde_dict(invalid[0]["referencia_hijo"]["huella"]).id
    with pytest.raises(ValueError, match="perdida"):
        assemblies.define("example", path, entries, invalid)
    assemblies.define("example", path, entries, joints)
    for values in ({"hinge": 181}, {"slide": math.inf}, {"nope": 1}, {"hinge": True}):
        with pytest.raises(ValueError):
            assemblies.pose_shapes("example", values)


def test_corrupt_base_refused(assembly):
    path, entries, joints = assembly
    assemblies.define("example", path, entries, joints)
    base = assemblies._directory("example") / "base.step"
    base.write_bytes(base.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="dañada"):
        assemblies.pose_shapes("example", {})


def test_fixed_child_inherits_parent_and_cannot_move(assembly):
    path, entries, joints = assembly
    joints[1] = dict(id="weld", tipo="fijo", padre="arm", hijo="tip")
    assemblies.define("example", path, entries, joints)
    posed = assemblies.pose_shapes("example", {"hinge": 90})
    assert center(posed["tip"]) == pytest.approx((0, 20, 0), abs=1e-7)
    with pytest.raises(ValueError, match="límites"):
        assemblies.pose_shapes("example", {"weld": 1})
