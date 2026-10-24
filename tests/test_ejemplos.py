"""Galería de ejemplos al abrir: scripts de `ejemplos/`, índice y rutas.

- Cada script de `ejemplos/` ejecuta con sus valores por defecto y produce
  sólidos válidos con nombre (`{nombre: Shape}`), y su PARAMETROS es válido.
- `ejemplos/index.json` y los `.py` del directorio coinciden por id.
- `GET /ejemplos` es público y no expone código; `POST /ejemplos/{id}/abrir`
  exige el token, valida el id contra el índice (nunca una ruta de la
  petición) y crea un documento nuevo «Ejemplo — <título>».

La ejecución de los scripts aquí es en proceso (son scripts del propio
repositorio, no entrada de usuario); el sandbox real no está disponible en
el contenedor de pruebas, así que la ruta de apertura usa un doble de
`scripts_runner.ejecutar_script` que hace lo mismo que el ejecutor.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import auth
import documents
import galeria_ejemplos
import parametros
import scripts_runner
import solids
from kernel import b123d_kernel
from main import app

client = TestClient(app)
DIRECTORIO = galeria_ejemplos.DIRECTORIO


def _indice() -> list[dict]:
    return json.loads((DIRECTORIO / "index.json").read_text(encoding="utf-8"))["ejemplos"]


def _ids() -> list[str]:
    return [entrada["id"] for entrada in _indice()]


def _cabecera(token: str | None = None) -> dict[str, str]:
    return {"X-Forja-Token": auth.obtener_token() if token is None else token}


def _ejecutar_en_proceso(codigo: str, timeout: float = 30, variables=None):
    """Doble de `scripts_runner.ejecutar_script`: ejecuta el script como lo
    haría el ejecutor (namespace, `construir(params)` si no hay `resultado`)
    y exporta a STEP en un directorio temporal del llamador."""
    espacio: dict = dict(variables or {})
    exec(compile(codigo, "<forja-script>", "exec"), espacio)
    resultado = espacio.get("resultado")
    if resultado is None:
        params = espacio.get("PARAMS")
        if not isinstance(params, dict):
            params = parametros.valores_por_defecto(parametros.esquema_desde_codigo(codigo))
        resultado = espacio["construir"](dict(params))
    tmp = Path(tempfile.mkdtemp(prefix="ejemplo-test-"))
    salida = tmp / "resultado.step"
    nombres = None
    if isinstance(resultado, dict):
        solids.validar_nombres(resultado.keys())
        nombres = list(resultado.keys())
        figura = b123d_kernel.combinar_nombrados(resultado)
    else:
        figura = resultado
    b123d_kernel.export_to_step(figura, salida)
    return salida, nombres


# ---------------------------------------------------------------- scripts


def test_indice_y_directorio_coinciden():
    ids = _ids()
    assert 5 <= len(ids) <= 6
    assert len(set(ids)) == len(ids), "ids repetidos en index.json"
    assert sorted(p.stem for p in DIRECTORIO.glob("*.py")) == sorted(ids)
    assert all(re.fullmatch(r"[a-z0-9_]+", i) for i in ids), "el id es el nombre de archivo: solo [a-z0-9_]"


def test_cada_entrada_del_indice_tiene_texto_y_tecnicas():
    for entrada in _indice():
        assert set(entrada) >= {"id", "titulo", "descripcion", "tecnicas"}
        assert entrada["titulo"].strip() and entrada["descripcion"].strip()
        assert isinstance(entrada["tecnicas"], list) and entrada["tecnicas"]
        assert all(isinstance(t, str) and t.strip() for t in entrada["tecnicas"])


@pytest.mark.parametrize("ejemplo_id", _ids())
def test_cada_ejemplo_declara_parametros_validos(ejemplo_id):
    codigo = (DIRECTORIO / f"{ejemplo_id}.py").read_text(encoding="utf-8")
    esquema = parametros.esquema_desde_codigo(codigo)
    assert esquema, "el ejemplo debe ser paramétrico (PARAMETROS)"
    assert "def construir(" in codigo


@pytest.mark.parametrize("ejemplo_id", _ids())
def test_cada_ejemplo_produce_solidos_validos_con_nombre(ejemplo_id):
    codigo = (DIRECTORIO / f"{ejemplo_id}.py").read_text(encoding="utf-8")
    salida, nombres = _ejecutar_en_proceso(codigo)
    try:
        assert nombres, "el ejemplo debe devolver {nombre: Shape}"
        shape = b123d_kernel.import_from_step(salida)
        info = b123d_kernel.analyze(shape)
        assert info.valido and info.solidos >= 1
        entradas = solids.construir_entradas(shape, nombres)
        assert [e["nombre"] for e in entradas] == nombres
        assert all(e["volumen"] > 0 for e in entradas)
    finally:
        shutil.rmtree(salida.parent, ignore_errors=True)


# ----------------------------------------------------------------- rutas


def test_listado_es_publico_y_no_expone_codigo():
    respuesta = client.get("/ejemplos")
    assert respuesta.status_code == 200
    listado = respuesta.json()
    assert [e["id"] for e in listado] == _ids()
    for entrada in listado:
        assert set(entrada) == {"id", "titulo", "descripcion", "tecnicas"}
        assert "codigo" not in json.dumps(entrada)


def test_abrir_exige_token():
    ejemplo = _ids()[0]
    url = f"/ejemplos/{ejemplo}/abrir"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"X-Forja-Token": "token-incorrecto"}).status_code == 401


@pytest.mark.parametrize("id_malo", ["no-existe", "..%2Fetc%2Fpasswd", "a.b", "tornillo_tuerca%2F..%2Fcaja"])
def test_abrir_rechaza_ids_fuera_del_indice(monkeypatch, id_malo):
    llamadas = []
    monkeypatch.setattr(scripts_runner, "ejecutar_script", lambda *a, **k: llamadas.append(a))
    respuesta = client.post(f"/ejemplos/{id_malo}/abrir", headers=_cabecera())
    assert respuesta.status_code == 404
    assert llamadas == []


def test_abrir_crea_documento_nuevo_con_nombre_del_ejemplo(monkeypatch):
    monkeypatch.setattr(scripts_runner, "ejecutar_script", _ejecutar_en_proceso)
    ejemplo = _indice()[0]
    respuesta = client.post(f"/ejemplos/{ejemplo['id']}/abrir", headers=_cabecera())
    assert respuesta.status_code == 200, respuesta.text
    creado = respuesta.json()
    assert creado["nombre"] == f"Ejemplo — {ejemplo['titulo']}.step"
    assert creado["id"] in documents._registry
    assert creado["solidos"] >= 1
    # Segunda apertura: documento nuevo, no reutiliza el anterior.
    otro = client.post(f"/ejemplos/{ejemplo['id']}/abrir", headers=_cabecera()).json()
    assert otro["id"] != creado["id"]
