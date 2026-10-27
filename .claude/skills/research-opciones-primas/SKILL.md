---
name: "research-opciones-primas"
description: "Para los 4 agentes de research (Chart, News, Strategy, Risk) que preparan un trade de venta de primas y lo prueban con backtest."
---

# Research de opciones (venta de primas)

Trabajás en el equipo de research. Todo informe usa el mismo formato: **Tesis** (1 frase), **Datos** (cifras con fecha y fuente), **Confianza** (baja/media/alta y por qué), **Qué invalidaría la tesis**. Nunca inventes un dato: si no lo tenés, decí que falta.

## Chart
- Tendencia (medias 20/50/200), soportes y resistencias, ATR y rango esperado hasta el vencimiento.
- Volatilidad: IV rank, IV percentil, IV vs volatilidad realizada, skew. Para vender primas importa que la IV esté alta respecto de la realizada (IV rank > 30 como referencia).
- Marcá cuánto se esperaría mover el subyacente (movimiento esperado = precio x IV x raíz(días/365)). Con plazos cortos, mirá también el rango diario (ATR) y si el precio está cerca de un soporte o resistencia que pueda romper en pocos días.

## News
- Fecha de earnings, ex-dividendo, reuniones de la Fed, CPI y otros eventos antes del vencimiento. Con plazos de 7 a 21 días el evento es un insumo, no un veto (regla del 27 oct 2026): se mide cuánto se movió el activo en los eventos parecidos del pasado y se compara con la distancia al strike vendido. El balance del propio activo, si es una acción suelta, sigue descartando.
- Explicá en pocas líneas por qué se mueve el mercado o el activo, con fuente. Sentimiento: positivo, negativo o mixto.
- Separá hechos de rumores.

## Strategy
- Estructuras permitidas: put cubierto con efectivo (solo en activos que se quieran tener), call cubierta, spread de crédito (put o call), iron condor. Preferir spreads de crédito e iron condors por tener el riesgo definido.
- Plazo de 7 a 21 días al vencimiento. Delta del strike vendido entre 0.10 y 0.20: con poco tiempo el gamma es alto y el strike tiene que estar más lejos.
- Solo subyacentes con opciones semanales líquidas (por ejemplo SPY, QQQ, IWM y acciones grandes).
- Para cada propuesta: strikes, vencimiento, crédito, pérdida máxima, ganancia máxima, probabilidad de ganancia aproximada, punto de equilibrio y griegas.
- Entregá de 1 a 3 candidatas, ordenadas, y decí cuál preferís y por qué.

## Risk (con veto)
- Pérdida máxima por trade <= 2% del capital. Riesgo total abierto <= 10%. Un solo subyacente <= 20% del riesgo total. Delta, vega y theta del portafolio dentro de los límites definidos. Vigilá el gamma total: con plazos cortos es el riesgo principal.
- Revisá riesgo de asignación anticipada, gap de precio y correlación con posiciones abiertas.
- Si algo supera un límite, devolvé **VETO** con el motivo y no pasa al consejo. Un veto no se negocia.

## Backtest (herramienta compartida)
- Probá la estructura con datos históricos de opciones reales o, si no hay, con una simulación declarada como aproximada.
- Reportá: cantidad de trades, tasa de acierto, resultado medio, peor drawdown, peor trade y resultado en períodos de crisis.
- Evitá el sesgo de look-ahead, incluí comisiones y slippage, y separá período de ajuste y de prueba. Si la muestra es chica (menos de 30 trades), decilo.