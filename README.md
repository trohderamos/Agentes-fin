# Agentes-fin

Sistema de agentes que vende primas de opciones (plazo de 7 a 21 días, strike vendido con delta 0.10 a 0.20). Primero se prueba en la cuenta **paper** de Alpaca y después se migra a tastytrade.

## Estructura

| Carpeta / archivo | Qué hay |
|---|---|
| `broker/` | La única pieza que habla con el bróker. `base.py` define la interfaz; `alpaca_paper.py` es el adaptador de Alpaca paper. Para migrar a tastytrade se agrega otro adaptador acá y no se toca nada más. |
| `datos/` | Datos y cálculos para los agentes: cadena de opciones de 7 a 21 días, ATR, volatilidad realizada, IV e IV rank, movimiento esperado y el calendario de eventos (`eventos.csv`). |
| `agentes/` | Los diez agentes (research, consejo y jefe). Research (fase 3), consejo y jefe (fase 4). Ver `agentes/README.md`. |
| `.claude/skills/` | Copia de los skills del proyecto. Cada agente carga los suyos desde acá. |
| `bitacora/` | Registro de trades. |
| `orquestador.py` | Punto de entrada. |
| `tests/` | Pruebas sin red. |

## Uso (solo lectura)

Las claves se leen de las variables de entorno `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` y, para los agentes, `ANTHROPIC_API_KEY`. Nunca van en el código.

```bash
pip install -r requirements.txt
python orquestador.py ciclo                  # research, consejo y jefe; la decisión queda en bitacora/decisiones.jsonl
python orquestador.py ciclo --sin-modelo     # el mismo ciclo con reglas fijas, sin llamar a Claude
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

## Cómo se calcula el IV rank

Alpaca no da IV histórica, así que se reconstruye: para cada día hábil del último año se toma el vencimiento de viernes más cercano a 30 días y el strike más cercano al cierre, y se calcula la IV del put y del call con su precio de cierre (Black-Scholes, tasa 4%, dividendo 1,1%). La IV del día es el promedio de las dos. La IV de hoy sale igual, con el punto medio entre bid y ask. El historial se guarda en `datos/cache/` y cada corrida solo agrega los días nuevos.

## Calendario de eventos

`datos/eventos.csv` tiene las fechas de la Fed, el CPI y el informe de empleo, tomadas de los calendarios oficiales. Hay que agregar las nuevas a medida que se publican; si el archivo no cubre los próximos 21 días, el informe lo avisa.

## Seguridad

- El adaptador apunta siempre a `paper-api.alpaca.markets`; la URL no se puede cambiar a la de cuentas reales.
- Antes de pedir cualquier dato verifica que el número de cuenta sea de paper (empieza con `PA`). Si no lo es, se detiene con `ALTO` y no hace nada más.
- Todavía no existe ningún método para enviar órdenes: el adaptador solo hace pedidos GET.
- Los agentes usan solo `ANTHROPIC_API_KEY`: el proceso del Claude Agent SDK corre con una configuración propia y sin heredar credenciales de otra sesión de Claude Code.
