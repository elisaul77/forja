"""Forja MCP server: one tool registry, two transports (ADR-0004/0005/0008).

`crear_servidor()` is the single source of truth for the server metadata and
the 21 tool registrations. Both transports build from it:

- **stdio** (fallback): `python -m mcp_server.server`, launched by Claude
  Code via `docker exec -i forja python -m mcp_server.server` -- a
  per-session process that dies with the container.
- **Streamable HTTP** (preferred since Phase 5D, ADR-0008):
  `mcp_server/http_app.py` builds a fresh instance with `crear_servidor()`
  inside the FastAPI lifespan and serves it at `POST /mcp` on the existing
  port (8710 on the host), stateless, so a container restart is invisible
  to the client beyond one failed request.

The tool table registered below MUST stay in sync with the tool table in
`~/.claude/skills/forja/SKILL.md` (Phase 3 contract: no drift). `captura`
(Phase 5B) is a real headless render, no longer a stub; `percibir`
(Phase 5E) is spatial perception as compact text -- prefer it over
`captura` as the default way to "see" a scene; `parametros` and `check_fdm`
(Phase 5C) iterate a parametric script by numbers and flag FDM print
problems before slicing; `ensamble`, `suspension` and `puentes` (Phase 6)
expose assemblies and the read-only local-service bridges.
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from mcp_server import tools


def crear_servidor() -> MCPServer:
    """Build a new `MCPServer` with Forja's metadata and all 21 tools
    registered. Called once per process for stdio (module-level `mcp`) and
    once per FastAPI lifespan for HTTP (a `StreamableHTTPSessionManager`
    can only `run()` once, so each lifespan needs its own instance)."""
    servidor = MCPServer(
        name="forja",
        version="0.7.0",
        instructions=(
            "Forja es un editor CAD 3D nativo para IA (build123d/OpenCascade). "
            "Flujo tipico: abrir_archivo -> resumen_documento -> ejecutar_script "
            "(con PARAMETROS + construir(params) si la pieza se va a iterar) "
            "-> percibir (contactos) -> cortes solo si hace falta ver una forma "
            "-> check_colisiones para el detalle -> parametros(id, valores) para "
            "iterar cambiando numeros sin reenviar el script -> check_fdm antes "
            "de laminar -> cupon (imprimir solo las zonas de encaje) -> exportar (3mf: un objeto con nombre por solido). "
            "'captura' (imagen) "
            "solo para estetica o para mostrarle algo al usuario -- 'percibir' "
            "da numeros exactos (que toca a que, a cuanto) por una fraccion de "
            "los tokens de una imagen, que ademas es ambigua en profundidad. "
            "Notas/pines y pizarra: crear_nota/leer_notas sobre un documento; "
            "cada cambio aceptado queda en leer_historial y se puede revertir "
            "con restaurar; 'rama' crea/cambia/compara ramas de diseño "
            "(cada cambio deja un paso con el estado completo). Piezas moviles: 'ensamble' define articulaciones "
            "(fijo|giro|deslizamiento) entre solidos con nombre, aplica una "
            "pose absoluta (valores) o quita las articulaciones conservando la "
            "posicion; 'suspension' consulta el simulador del mostertruck por "
            "el puente de solo lectura y 'puentes' dice que servicios locales "
            "estan encendidos y respondiendo. "
            "Todas las unidades son milimetros. Las respuestas "
            "son compactas (ids, numeros, mensajes cortos); pedir 'captura' "
            "solo cuando de verdad haga falta ver la geometria."
        ),
    )

    servidor.tool(name="estado")(tools.estado)
    servidor.tool(name="listar_documentos")(tools.listar_documentos)
    servidor.tool(name="abrir_archivo")(tools.abrir_archivo)
    servidor.tool(name="resumen_documento")(tools.resumen_documento)
    servidor.tool(name="ejecutar_script")(tools.ejecutar_script)
    servidor.tool(name="exportar")(tools.exportar)
    # Image wrappers are MCP content, not JSON structured output.
    servidor.tool(name="captura", structured_output=False)(tools.captura)
    servidor.tool(name="check_colisiones")(tools.check_colisiones)
    servidor.tool(name="percibir")(tools.percibir)
    servidor.tool(name="cupon")(tools.cupon)
    servidor.tool(name="parametros")(tools.parametros)
    servidor.tool(name="check_fdm")(tools.check_fdm)
    servidor.tool(name="leer_notas")(tools.leer_notas)
    servidor.tool(name="crear_nota")(tools.crear_nota)
    servidor.tool(name="borrar_nota")(tools.borrar_nota)
    servidor.tool(name="leer_historial")(tools.leer_historial)
    servidor.tool(name="restaurar")(tools.restaurar)
    servidor.tool(name="rama")(tools.rama)
    servidor.tool(name="ensamble")(tools.ensamble)
    servidor.tool(name="suspension")(tools.suspension)
    servidor.tool(name="puentes")(tools.puentes)
    return servidor


mcp = crear_servidor()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
