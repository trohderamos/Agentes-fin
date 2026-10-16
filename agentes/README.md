# Agentes

Nueve agentes en tres niveles:

1. **Research** (4): Chart, News, Strategy y Risk. Risk tiene veto antes del consejo. El backtest es una herramienta compartida, no un agente. **Fase 3: listos.**
2. **Consejo** (4): ejecutor de primas, escéptico, gestor de posición, y liquidez y costos. Debaten dos rondas. Fase 4.
3. **Jefe** (1): escucha el debate y decide solo si aprueba, ajusta (vuelve a research) o rechaza. Fase 4.

Los agentes nunca hablan con el bróker directamente: piden datos a `datos/` y al bróker a través de la interfaz de `broker/base.py`.

## Research (`python orquestador.py research`)

Cada agente es un llamado a Claude con el Claude Agent SDK, con su skill en las instrucciones y una salida JSON de formato fijo (tesis, datos, confianza, qué invalidaría la tesis, lo que falta). No tienen herramientas de archivos ni terminal; News además puede buscar en la web.

| Paso | Quién | Skills | Qué hace |
|---|---|---|---|
| 1 | Freno por drawdown (código) | freno-drawdown-opciones | Si no está en PERMITIDO, no hay trade hoy y no se llama a ningún modelo. |
| 2 | Chart y News, en paralelo | research-opciones-primas; News también calendario-catalizadores | Chart: tendencia, soportes y resistencias, IV vs realizada, de qué lado vender. News: catalizadores hasta el último vencimiento (con búsqueda web y las noticias de Benzinga de Alpaca), sentimiento, hechos y rumores. |
| 3 | Candidatos (código) | | Spread de puts, de calls e iron condor por cada vencimiento de 7 a 21 días, con el strike vendido más cerca de delta 0.15 (entre 0.10 y 0.20) y los precios reales de la cadena. |
| 4 | Límites (código) | | Descarta los que tienen un evento grande en el plazo (Fed, CPI, empleo de `datos/eventos.csv`, o cualquier evento que News marque de impacto alto) o que superan algún límite de Risk. |
| 5 | Backtest aproximado (código) | backtest-opciones-primas | Una simulación por estructura desde 2018, con período de ajuste y de prueba, crisis y estrés. |
| 6 | Strategy | research-opciones-primas, analisis-opciones-primas | Elige de 0 a 3 candidatos por id y dice cuál prefiere. No puede cambiar strikes ni precios. |
| 7 | Risk | research-opciones-primas, freno-drawdown-opciones | Vuelve a pasar los límites en código y suma sus propios vetos. No puede levantar un veto del código. |

El resultado se imprime y se guarda en `datos/research/SPY-AAAA-MM-DD.json`. Nada de esto envía órdenes.

### Límites de Risk (`agentes/config.py`)

Del skill: pérdida máxima por trade 2% del capital, riesgo total abierto 10%, un subyacente hasta 20% del riesgo total permitido. Valores de partida míos, ajustables: pérdida estimada por delta y gamma ante un movimiento de 3% en contra hasta 1.5% del capital, y vega hasta 0.5% del capital por punto de IV.

### Backtest

Es una aproximación y lo dice en cada resultado: Alpaca no da IV histórica de varios años, así que la IV se aproxima con la volatilidad realizada de 20 días por 1.15, los precios salen de Black-Scholes y no hay skew. Sirve para comparar estructuras y plazos, no para estimar la ganancia real.

### Bitácora

El freno y Risk leen `bitacora/trades.jsonl` (formato en `agentes/bitacora.py`). Hasta la fase 4 está vacía. Si la cuenta tiene opciones abiertas que no están en la bitácora, el freno se pone en DETENIDO.

### Modelo y costo

Por defecto `claude-opus-5-5` (se cambia con `--modelo` o `AGENTES_MODELO`) y un tope de 1 dólar por agente y por corrida (`AGENTES_PRESUPUESTO_USD`). El costo de cada corrida se muestra al final.
