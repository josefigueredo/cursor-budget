# Referencia

Todo `cbudget` en un lugar: comandos, límites, archivos, cómo estima el costo y lo que no puede ver. Para instalarlo,
la [guía con prompts](instalacion.md) o la [detallada](instalacion-detallada.md); para verlo funcionar, el
[showcase](showcase.md).

## Comandos

`cbudget` es el alias que deja el instalador. Sin el alias (desde un agente, por ejemplo), el mismo comando es
`python3 ~/.cursor/budget/app/cbudget.py`.

```bash
cbudget report --days 7      # ciclo, presupuestos y gasto por día, modelo y chat (--color never o NO_COLOR para texto plano)
cbudget limits               # límites vigentes, gasto contra presupuesto por modelo y chequeo de coherencia
cbudget limits check         # solo los problemas (exit 1 si hay un error)
cbudget limits edit          # editor interactivo, solo en una terminal
cbudget limits set block_day 12              # cambia un límite general (también 80%, si/no)
cbudget limits model 'claude-sonnet-*' 70    # presupuesto del ciclo para un patrón de modelo (--remove lo quita)
cbudget limits block 'claude-opus-*'         # bloquea un patrón (unblock lo libera)
cbudget allow --minutes 30   # levanta todos los bloqueos un rato
cbudget seed --amount 84.30  # lo gastado este ciclo según la página de uso de Cursor
cbudget seed usage.csv       # lo cobrado este ciclo según el CSV de Cursor, con el detalle por modelo y por día
cbudget calibrate usage.csv  # cobrado contra estimado por modelo
cbudget notify-test          # manda una notificación de prueba y explica cómo habilitarlas
cbudget install-hook         # suma el hook a ~/.cursor/hooks.json sin tocar los demás (deja un .bak)
cbudget alias                # agrega o actualiza el alias en ~/.zshrc (o ~/.bashrc)
cbudget hook                 # lo que corre Cursor: lee el evento por stdin y contesta JSON
```

Los montos aceptan `$` y coma decimal (`12,5`); las partes, `%` (`80%`); los sí/no, `si`, `sí`, `no`.

## Límites por defecto

| Clave | Valor | Qué hace |
|---|---|---|
| `warn_conversation` | $2 | avisa: "abrí un chat nuevo" |
| `block_conversation` | $5 | bloquea los prompts siguientes de ese chat |
| `warn_day` | $8 | avisa |
| `block_day` | $15 | bloquea hasta mañana o hasta `allow` |
| `cycle_day` / `cycle_limit` | 1 / $150 | día en que se reinicia el ciclo y tope del plan |
| `cycle_warn` | 75%, 90%, 100% | avisa al cruzar esas partes del tope |
| `notify_each_turn` | sí | después de cada turno: costo del turno, del chat y del ciclo |
| `model_budgets` | ninguno | presupuesto del ciclo por patrón de modelo, p. ej. `{"claude-opus-*": 40}`: avisa al 80% (`model_warn`) y bloquea ese modelo al agotarlo |
| `blocked_models` | `claude-opus-*-high`, `*-xhigh`, `*-max` | bloquea esos modelos |

`cycle_limit` viene en $150; si tu plan tiene otro tope, cambialo primero (`cbudget limits set cycle_limit 200`).
Los cambios se guardan en `~/.cursor/budget/config.json`, solo las claves que cambiaste.

`cbudget limits check` marca como **error** un aviso que no es menor que su bloqueo (el bloqueo llegaría sin aviso), y
como **aviso** un tope diario mayor que el del ciclo, presupuestos por modelo que suman más que el ciclo, un patrón que
no coincide con ningún modelo usado y un patrón tapado por otro anterior.

## Presupuesto por modelo

`model_budgets` asigna un tope del ciclo a cada patrón (`fnmatch`; gana el primero que coincide, en el orden escrito).
Lo gastado sale de `seed` (por modelo) más las estimaciones posteriores. Las filas del CSV que Auto derivó
(`grok-4.7-high (Auto Balanced)`) cuentan para `auto*`, porque el hook solo ve `auto-smart`. Con `seed --amount` no hay
detalle por modelo, así que los presupuestos cuentan desde ese momento.

## Gasto del ciclo

El hook no puede preguntarle a Cursor cuánto llevás: suma lo que ve. Para que el total del ciclo sea el de Cursor:

- **Sin CSV:** el monto de "Your monthly usage" en cursor.com/dashboard → Usage (el primero de "$84.30 / $200") va a
  `cbudget seed --amount 84.30`. Es la vía para quien no exporta archivos.
- **Con CSV:** Usage → Export y `cbudget seed <csv>`. Guarda lo cobrado este ciclo, por modelo y por día; desde el
  último evento del CSV suma las estimaciones del hook. Un turno que termina justo después de ese evento puede contarse
  dos veces (centavos).

Si instalás el día que arranca el ciclo no hace falta ningún seed. Sin seed, el total cuenta desde la instalación y los
avisos lo dicen.

## Terminal y chat: un solo CLI

En la terminal, `cbudget limits edit` pregunta límite por límite (Enter deja el actual), muestra los cambios y el
chequeo, y guarda solo si confirmás. Sin terminal se niega con 2. Desde un chat de Cursor, Claude Code o Codex, la skill
`cursor-budget` (`SKILL.md`) le dice al agente qué subcomando no interactivo correr, y solo cambia un límite si se lo
pediste en ese chat. Igual, cada cambio queda en `changes.jsonl` con quién lo hizo (`terminal` o `agente`) y sale una
notificación, porque un agente podría subirse su propio presupuesto.

## Archivos

Todo vive en `~/.cursor/budget/`, fuera de cualquier repo:

| Archivo | Qué guarda | Lo escribe |
|---|---|---|
| `app/` | La copia del programa y de la skill que usan los hooks | `install.sh` (la reemplaza al actualizar) |
| `config.json` | Solo los límites que cambiaste | `limits set`, `limits model`, `limits block`, `limits edit` |
| `ledger.jsonl` | Un renglón por turno: modelo, chat, tokens y costo estimado | el hook `stop` |
| `baseline.json` | Lo gastado este ciclo según Cursor | `seed` |
| `blocks.jsonl` | Cada prompt bloqueado y por qué | el hook `beforeSubmitPrompt` |
| `changes.jsonl` | Cada cambio de límites: antes, después y quién | los comandos de `limits` |
| `allow.json` | Hasta cuándo están levantados los bloqueos | `allow` |
| `prices.json` | Opcional: tu propia tabla de precios, con prioridad sobre `app/cbudget/prices.json` | vos |

El instalador también toca `~/.cursor/hooks.json` (dos entradas, con un `.bak` antes de cambiarlo), una línea del rc de
la shell que termina en `# cursor-budget`, y los enlaces de la skill en `~/.agents/skills`, `~/.claude/skills` y
`~/.codex/skills`.

| Variable | Para qué |
|---|---|
| `CURSOR_BUDGET_NO_NOTIFY=1` | No manda ninguna notificación (pruebas, capturas) |
| `CURSOR_BUDGET_REPO` | De dónde clona `bootstrap.sh` (una ruta o URL) en lugar del repo de GitHub |
| `CURSOR_BUDGET_HOME` | Dónde deja `install.sh` la carpeta `app/` (por defecto `~/.cursor/budget`); para pruebas: los datos siguen en `~/.cursor/budget` |
| `NO_COLOR` | `report` y `limits` sin color |

## Cómo estima el costo

Por turno: entrada sin caché (entrada − lectura de caché − escritura de caché) a precio de entrada, escritura de caché a
precio de entrada × 1.25 (Claude), lectura de caché a su precio, salida a precio de salida, más el Token Rate de Cursor
($0.25 por millón, sobre todos los tokens) en los modelos de terceros. Grok y Composer no lo pagan. Los precios están en
`cbudget/prices.json`; gana el primer prefijo que coincide, en el orden del archivo, y un modelo desconocido se cobra caro a propósito.

## Lo medido (2026-10-24, cursor-agent 2026.10.22)

- El payload de `stop` trae `model`, `conversation_id`, `input_tokens`, `output_tokens`, `cache_read_tokens` y
  `cache_write_tokens`, por turno, no acumulado. `input_tokens` incluye los tokens de caché.
- `beforeSubmitPrompt` trae `model` y `conversation_id`. Responder `{"continue": false}` **bloquea en `cursor-agent`**:
  no hubo respuesta ni evento `stop`. El `user_message` no se ve en la terminal, por eso además sale una notificación.
  **En el IDE también bloquea** (Cursor 2026.10.22): muestra "Submission blocked by hook" con el `user_message` y el
  prompt no llega al modelo.
- La fórmula reproduce una factura real de Opus con un error de ±5%; con un CSV de un ciclo entero, el total estimado
  quedó 4% arriba de lo cobrado y cada modelo entre −7% y +6%. `test_opus_turn_follows_the_formula` fija la fórmula con
  números de ejemplo.
- Desde un chat de Cursor, escribir en `~/.cursor/budget` pidió correr el comando fuera del sandbox.

## Calibrar

`cbudget calibrate <csv>` muestra, por modelo, lo cobrado contra lo estimado, y marca con `<- ajustar` los que erran más
de 10% y los que no coinciden con ningún precio. Para corregir un precio, copiá `prices.json` a `~/.cursor/budget/` y
editá esa copia: el instalador no la pisa.

## Códigos de salida

| Código | Cuándo |
|---|---|
| 0 | Bien. El hook siempre sale con 0: si falla, contesta `{}` y deja pasar el prompt |
| 1 | `limits check` encontró un error |
| 2 | Uso: un valor que no es número o está fuera de rango, `limits edit` sin terminal, `seed` sin CSV ni `--amount` |

## Límites conocidos

- **Auto**: el payload dice `auto-smart` (el modo), nunca el modelo al que Auto derivó. Desde agosto de 2026 Auto cobra
  el precio de lista de ese modelo, así que su costo es una estimación a precio de Sonnet 5.5. No está bloqueado por
  defecto; un presupuesto `auto*` sí lo frena al agotarse.
- El total es una estimación: el tope real del ciclo lo pone Cursor.
- Si el hook falla, deja pasar el prompt (falla abierto) para no romper Cursor.
- No es seguridad: quien usa la máquina puede editar `config.json` o correr `allow`. Es un freno para uno mismo.
- Los precios marcados `estimate` en `prices.json` no están verificados.
