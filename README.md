# Agentes-fin

Sistema de agentes que vende primas de opciones (plazo de 7 a 21 días, strike vendido con delta 0.10 a 0.20). Opera SPY, QQQ (Nasdaq 100), GLD (oro), USO (petróleo), SMH (semiconductores) e IBIT (bitcoin), definidos en `agentes/config.py` (`UNIVERSO`). Primero se prueba en la cuenta **paper** de Alpaca y después se migra a tastytrade.

## Estructura

| Carpeta / archivo | Qué hay |
|---|---|
| `broker/` | La única pieza que habla con el bróker. `base.py` define la interfaz; `alpaca_paper.py` es el adaptador de Alpaca paper. Para migrar a tastytrade se agrega otro adaptador acá y no se toca nada más. |
| `datos/` | Datos y cálculos para los agentes: cadena de opciones de 7 a 21 días, ATR, volatilidad realizada, IV e IV rank, movimiento esperado y el calendario de eventos (`eventos.csv`). |
| `agentes/` | Los diez agentes (research, consejo y jefe). Research (fase 3), consejo y jefe (fase 4). Ver `agentes/README.md`. |
| `.claude/skills/` | Copia de los skills del proyecto. Cada agente carga los suyos desde acá. |
| `bitacora/` | Registro de trades. |
| `orquestador.py` | Punto de entrada. |
| `scripts/bitacora.sh` | Guarda la bitácora en la carpeta compartida del proyecto (o, si no está, en la rama `claude/bitacora-paper`), para que la rutina diaria, que corre cada día en una máquina nueva, no la pierda. |
| `tests/` | Pruebas sin red. |

## Uso

Las claves se leen de las variables de entorno `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` y, para los agentes, `ANTHROPIC_API_KEY` o, en su lugar, `CLAUDE_CODE_OAUTH_TOKEN` (sale de `claude setup-token` y usa el plan de Claude en vez de saldo de API). Si no hay ninguna de las dos, los agentes usan la sesión de Claude Code que los corre (la de la rutina), que también va contra el plan. Nunca van en el código.

```bash
pip install -r requirements.txt
python orquestador.py ronda                  # ciclo y ejecución (simulada) para cada activo del universo
python orquestador.py ronda --enviar         # lo mismo, mandando a paper lo que apruebe el jefe
python orquestador.py ciclo                  # research, consejo y jefe; la decisión queda en bitacora/decisiones.jsonl
python orquestador.py ciclo --sin-modelo     # el mismo ciclo con reglas fijas, sin llamar a Claude
python orquestador.py ejecutar               # revisa la última decisión aprobada con el checklist y simula la orden (no envía)
python orquestador.py ejecutar --enviar      # la envía a la cuenta paper (solo con el visto bueno del usuario)
python orquestador.py noticias               # el agente News revisa el universo y los trades abiertos (no envía órdenes)
python orquestador.py gestionar              # revisa los trades abiertos: toma de ganancia, stop, vencimiento cercano (simula)
python orquestador.py gestionar --enviar     # envía los cierres a la cuenta paper
python orquestador.py repaso                 # repaso semanal: resultado, trades, errores, fichas y lecciones propuestas
python orquestador.py research               # agentes de research sobre SPY: de 1 a 3 candidatos o "no hay trade hoy"
python orquestador.py research --sin-modelo  # el mismo circuito sin llamar a Claude (solo los cálculos)
python orquestador.py informe                # informe de datos de SPY del día (se guarda en datos/informes/)
python orquestador.py estado                 # cuenta paper + cadena de SPY de 7 a 21 días, |delta| 0.10 a 0.20
python orquestador.py estado --todo          # toda la cadena
python orquestador.py estado --simbolo QQQ --delta-min 0.05 --delta-max 0.30
python orquestador.py cuenta                 # solo la cuenta
python -m unittest discover -s tests -t .    # pruebas
```

Por defecto los precios de opciones vienen del feed `indicative` de Alpaca (gratis, con algo de demora). Con suscripción se puede usar `--feed opra`.

## Rutina diaria

La rutina corre cada día hábil en una máquina nueva, así que la bitácora (que no va en `main`) vive en `/mnt/project-files/bitacora-paper/`, la carpeta compartida del proyecto (o, si no existe, en la rama `claude/bitacora-paper`):

```bash
scripts/bitacora.sh bajar                    # trae la bitácora guardada
python orquestador.py gestionar --enviar     # cierra lo que pida el plan de salida
python orquestador.py ronda --enviar         # ciclo con el modelo en cada activo del universo; manda a paper lo que apruebe el jefe
scripts/bitacora.sh subir                    # guarda la bitácora
```


## Cómo se calcula el IV rank

Alpaca no da IV histórica, así que se reconstruye: para cada día hábil del último año se toma el vencimiento de viernes más cercano a 30 días y el strike más cercano al cierre, y se calcula la IV del put y del call con su precio de cierre (Black-Scholes, tasa 4%, dividendo 1,1%). La IV del día es el promedio de las dos. La IV de hoy sale igual, con el punto medio entre bid y ask. El historial se guarda en `datos/cache/` y cada corrida solo agrega los días nuevos.

## Calendario de eventos

`datos/eventos.csv` tiene las fechas de la Fed, el CPI y el informe de empleo, tomadas de los calendarios oficiales. Hay que agregar las nuevas a medida que se publican; si el archivo no cubre los próximos 21 días, el informe lo avisa.

`datos/eventos_historicos.csv` tiene las fechas pasadas de esos mismos eventos desde 2018 (calendario y archivo del FOMC; fechas de publicación de PAYEMS y CPIAUCSL en ALFRED). Con las barras diarias del bróker, `datos/reaccion_eventos.py` mide cuánto se movió cada activo esos días.

## Regla de eventos (cambiada el 27 oct 2026)

Antes, cualquier evento dentro del plazo descartaba el trade. Ahora el evento se opera: News arma su tesis (qué pasó la vez anterior, qué espera el mercado, su pronóstico) y en código se compara el movimiento típico de los eventos del plazo con la distancia del precio al strike vendido (`agentes/eventos.py`). El candidato se descarta solo si el strike queda más cerca que ese movimiento.

Se ajusta en `agentes/config.py`: `MODO_EVENTOS` (`operar` o `vetar`), `PERCENTIL_COLCHON` (50 por defecto; 75 o 90 son más conservadores) y `FACTOR_CONTRATOS_EVENTO` (el tamaño se recorta con un macro en el plazo).

`python orquestador.py regla-eventos` muestra el movimiento histórico medido y compara los trades que dejó tomar cada regla; también se escribe en `bitacora/regla-eventos.md` y entra en el repaso de los viernes.

## Seguridad

- El adaptador apunta siempre a `paper-api.alpaca.markets`; la URL no se puede cambiar a la de cuentas reales.
- Antes de pedir cualquier dato verifica que el número de cuenta sea de paper (empieza con `PA`). Si no lo es, se detiene con `ALTO` y no hace nada más.
- Las órdenes están bloqueadas por defecto: el adaptador solo escribe con `permitir_ordenes=True`, que el orquestador activa únicamente con `--enviar`. Solo puede escribir en `/v2/orders` y vuelve a verificar que la cuenta sea paper antes de cada envío.
- Con `ANTHROPIC_API_KEY` o `CLAUDE_CODE_OAUTH_TOKEN`, el proceso del Claude Agent SDK corre con una configuración propia y solo con esa credencial. Sin ninguna, usa la de la sesión que lo corre. Los agentes no tienen herramientas de archivos ni de terminal en ningún caso.
