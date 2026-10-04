# ADR 0013: Sandbox en contenedor propio sin red y token como secreto

Date: 2026-10-03
Status: Aceptado

Origen: `plans/maestro/adr-propuestos/ADR-P01-sandbox-y-secretos.md` (fase F0.2,
`plans/maestro/fases/F0a-seguridad-nucleo.md`), con los hallazgos 1-4 y 16 de
`plans/maestro/revisiones/revision-tecnica.md`.

## Context

Hasta F0.1 el script del usuario corría con `subprocess` como **root**, en el netns de
uvicorn, con `documentos_data` (que contenía `.forja_token`), `/data/fuentes` y `./app`
montados, heredando el entorno. Desde ahí alcanzaba la API local sin token, Blender MCP en
`host.docker.internal:9877` (ejecuta Python), Moonraker :7125 y kuNNA. Hubo 2 intentos
reales del agente de leer el token y la skill lo ordenaba. Un script de 4 GB provocó un OOM
global (uvicorn de 638 MB muerto, ~69 s de reinicio).

Restricciones medidas: un servidor uid 1000 no puede lanzar hijos con otro uid ni hacer
`chown` (hallazgo 1); `preexec_fn` es inseguro con el threadpool de 40 hilos (ADR-0009);
`RLIMIT_DATA` 1,5 GB solo funciona con BLAS/OMP a 1 hilo; `RLIMIT_NPROC` cuelga numpy.

## Decision

- **Servicio `forja-sandbox`** (misma imagen, su `/app` de la imagen): `user 65534:65534`,
  `network_mode: none`, `read_only`, `tmpfs /tmp`, `pids_limit 128`, `cap_drop ALL`,
  `no-new-privileges`, y **solo** el volumen `staging`. Sin `/data/documentos`, sin
  `/data/fuentes`, sin el secreto. Sin `mem_limit` hasta P-3.
- **Ejecutor** (`app/sandbox/ejecutor.py`) = PID 1 del sandbox, marcado no volcable
  (`PR_SET_DUMPABLE 0`): un script no puede leer `/proc/1/environ`. Escucha un socket Unix
  en `staging/ejecutor/`; por trabajo lanza un hijo nuevo con `posix_spawn` (sesión propia,
  `HOME` privado en `/tmp` cuyo nombre no lleva el id, entorno mínimo, la ruta del trabajo
  por una tubería en stdin y **nunca en argv**, que es legible en `/proc/<pid>/cmdline`),
  espera con plazo, mata el grupo y barre los procesos escapados (huérfanos reparentados a
  PID 1; los de `ppid 0` son `docker exec`/healthchecks y se respetan). Un solo hilo segador
  recoge todos los estados (por eso no `subprocess.Popen.wait`).
- **Un trabajo a la vez**: un cerrojo envuelve todo el trabajo en el ejecutor (la espera en
  cola se descuenta del `timeout` del propio trabajo) y `scripts_runner` encola sus llamadas
  con otro cerrojo (hasta 300 s, luego «sandbox ocupado») para que el plazo del socket
  empiece cuando el trabajo puede correr. Sin hermanos vivos no hay a quién espiar, y el host
  (sin `mem_limit`) nunca sostiene dos hijos de 1,5 GB.
- **`ejecutar` solo desde otro uid** (`SO_PEERCRED`): el servidor es 1000, los hijos 65534 como
  el ejecutor, así que un script no puede encolar trabajos. `ping` queda abierto (healthcheck).
- **Señales**: SIGTERM/SIGINT bloqueadas en todos los hilos y recogidas por uno con
  `sigwaitinfo`; el ejecutor solo para si `si_pid == 0` (emisor fuera de su espacio de pids:
  `docker stop`, medido 0,3 s, salida 0). Un `os.kill(1, SIGTERM)` desde dentro se ignora. El
  resto de señales con disposición por defecto nunca llegan a un PID 1 desde dentro.
- **Socket**: el hijo no puede escribir en `staging/ejecutor/` (Landlock, abajo). Segunda
  línea: el bucle de `accept` comprueba cada segundo con `lstat` que el socket sigue siendo
  el suyo (mismo inodo, tipo socket); si no, lo borra y re-enlaza. Landlock no cubre `chmod`,
  así que el bucle también repone 0666/0755, y tras cada trabajo, antes de responder, se borra
  todo lo que haya en ese directorio salvo el socket y se reponen ambos modos.
- **Tope de escritura**: un hilo vigía por trabajo mata su grupo si `salida/` pasa de 600 MB
  o de 10 000 entradas, si no se puede medir, o si el volumen de staging baja de 2 GB libres.
  La limpieza de `salida/`, `HOME`, `/tmp` y el directorio del socket va por descriptores de
  directorio, iterativa, dando 0700 a los directorios propios antes de listarlos (un
  directorio 000 ya no sobrevive).
- **`bootstrap.py`** es lo primero del hijo: `PR_SET_DUMPABLE 0` (su cwd, fds y environ dejan
  de ser legibles para otro proceso del uid 65534), `umask 077`, `RLIMIT_DATA` 1,5 GB,
  `RLIMIT_CPU` timeout + 10 s, `RLIMIT_FSIZE` 512 MB, sin core, `oom_score_adj 1000`,
  entorno de lista blanca (`OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1`, `PYTHONPATH=/app`,
  `PYTHONNOUSERSITE=1`, `HOME`=`TMPDIR` privado), y `python -P` para que nada plantado en
  `/tmp` sombree módulos. Sin `RLIMIT_NPROC` (lo cubre `pids_limit`). `preexec_fn` prohibido
  (grep = 0). Después se **confina con Landlock** (sin privilegios; el contenedor ya tiene
  `no-new-privileges`; ABI 8 en este host): escribir, borrar, renombrar o crear nodos solo bajo
  su `salida/` y su `HOME` (+ escribir en `/dev/null`); con ABI ≥ 6, señales y sockets
  abstractos solo dentro de su dominio (no puede señalar a PID 1). Las lecturas no se
  restringen (raíz de solo lectura, sin secretos). Si el núcleo no tuviera Landlock, el hijo
  sigue sin él y quedan las comprobaciones del ejecutor.
- **Intercambio por `staging`**: `trabajos/` y `trabajos/<id>/` son 0711 del servidor (el
  sandbox llega por el id aleatorio de 32 hex pero no lista), `peticion.json` 0644,
  `salida/` 0777 (único sitio donde escribe el hijo). Al terminar, el ejecutor deja solo
  `resultado.step` y `resultado.nombres.json` como archivos regulares de un enlace (0644) y
  borra todo lo demás. El servidor **igual** trata la salida como dato: `lstat` regular con
  un enlace, `open(O_NOFOLLOW|O_NONBLOCK)` al mismo inodo, tope de tamaño, copia en bloques a
  un temporal propio y borra el trabajo; luego la validación de nombres de siempre.
- **Servidor `forja`**: `app/entrypoint.sh` como root → token legado a
  `/run/secrets/forja_token` (volumen nombrado `secretos`, solo de `forja`; 0400 uid 1000;
  `install` + `cmp -s` + `rm`, nunca impreso) → migración de uid **única** con marcador
  `.migrado-uid` y `--dry-run` (lista lo que no es 1000:1000 y hace `chown -h` solo de eso,
  nunca `-R`, sin tocar contenidos) → prepara `staging` →
  `exec setpriv --reuid 1000 --regid 1000 --clear-groups --inh-caps=-all
  --bounding-set=-all --no-new-privs uvicorn …` (en vivo: `NoNewPrivs 1`, `CapBnd 0`). Como
  esos volúmenes los escribe el servidor, root nunca sigue un enlace: `chown -h`, arranque
  rechazado si `$STAGING*` o el secreto son symlinks, y los temporales (`.migrado-uid`,
  secreto) con `mktemp` en el directorio destino, nunca con nombre fijo.
- **`auth.py`** lee primero el secreto (`FORJA_SECRET_FILE`, por defecto
  `/run/secrets/forja_token`), luego `FORJA_TOKEN`, luego el archivo legado; si no hay
  ninguno, genera uno 0400 en el directorio del secreto.
- El bind del puerto no cambia (P-2). Las 19 herramientas MCP y la REST no cambian.

## Alternativas rechazadas

| Opción | Por qué no |
|---|---|
| Servidor root + `Popen(user=…)` | Comparte netns (Blender :9877 alcanzable) y deja el servidor root |
| `preexec_fn` con `setrlimit` | Inseguro con el threadpool de 40 hilos (ADR-0009) |
| `sys.addaudithook` como frontera | Eludible con `ctypes`; falsa seguridad |
| `./app:ro` en `forja` | El sandbox ya no ve `/app`; rompería el flujo de desarrollo sin ganar aislamiento |
| Secreto en `/run/secrets` del sistema de archivos del contenedor | Se perdería al recrear el contenedor y el token registrado en Claude Code dejaría de valer: por eso es un volumen nombrado |
| `trabajos/<id>` 0700 (como decía el propuesto) | Con uids distintos el sandbox no podría ni leer la petición ni escribir la salida; 0711 + `salida/` 0777 da lo mismo (sin listado, id aleatorio) y deja al servidor dueño para borrar |
| `init: true` (tini como PID 1) | `/proc/1/environ` de tini sería legible por el script; el ejecutor como PID 1 no volcable lo impide |
| Socket en un directorio no escribible por 65534 | El ejecutor (65534) necesita escribir ahí para enlazarlo, y sin `CAP_CHOWN` no puede ceder el directorio después |
| Hijos con otro uid (`setuid` en el ejecutor) | Exige `CAP_SETUID`/`CAP_SETGID` en el ejecutor (o arrancarlo como root): más superficie que Landlock, que no necesita ninguna capacidad |
| Confiar en que el id de 32 hex (128 bits) aísla los trabajos | El id iba en argv y `/proc/<pid>/cmdline` es legible: no era secreto. Ahora tampoco se confía en él (ver serialización y Landlock) |

## Consequences

- (+) Las 11 pruebas de penetración de F0.2 son verificables y pasan
  (`tests/test_sandbox.py`); un `bytearray` de 3 GB muere en el hijo con `MemoryError`.
- (+) Latencia del salto medida: mediana 2,42 s y 2,52 s en dos corridas tras la ronda de
  corrección (2,51 s en la primera entrega) frente a 2,80 s antes (sin coste añadido; BLAS a 1 hilo compensa el
  socket).
- (−) Los trabajos se serializan: dos `ejecutar_script` simultáneos esperan uno al otro.
- (−) El sandbox usa el `/app` de la imagen: cambios en `app/sandbox/`, `kernel/` o
  `solids.py` exigen `docker compose build` para que el sandbox los vea.
- (−) Todos los trabajos del sandbox comparten uid 65534. El id del trabajo **no** es la
  frontera (en la primera entrega iba en argv y era legible por `/proc/<pid>/cmdline`): lo
  son la serialización (no hay otro trabajo vivo), Landlock (solo escribe en su `salida/` y
  su `HOME`), el hijo no volcable y `SO_PEERCRED`. Landlock no cubre `chmod`: un hijo
  puede poner 000 al socket o a su directorio (dueño 65534) mientras corre; el ejecutor lo
  restaura en ≤ 1 s y siempre antes de responder, así que como mucho falla un healthcheck. El barrido mata a los escapados al acabar cada trabajo y cada 30 s.
- (−) `docker compose exec forja …` sigue entrando como root (usuario por defecto del
  contenedor); el proceso servidor es uid 1000 (`/proc/1/status`).
- Diferido: URLs firmadas HMAC para descargas (paso 6 de F0.2) — cambia la respuesta de
  `exportar` y la REST de descarga, y sin P-2 no cierra nada; queda para cuando se decida
  el bind.
- Los puentes de Fase 6 siguen en `forja` y son inaccesibles desde scripts (DD12).
