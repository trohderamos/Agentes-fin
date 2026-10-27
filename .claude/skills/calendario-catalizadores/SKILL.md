---
name: calendario-catalizadores
description: "Para el agente News: armar la lista de eventos (earnings, Fed, CPI, dividendos) que caen antes del vencimiento de un trade de primas y decidir si lo descartan."
---

# Calendario de catalizadores

Antes de cualquier trade, armá la lista de eventos entre hoy y el día del vencimiento, ambos incluidos. Con plazos de 7 a 21 días, un evento que se escape puede llevarse el trade entero.

## Qué buscar
**Del activo:** fecha de earnings (y si es antes de la apertura o después del cierre), fecha ex-dividendo, presentaciones a inversores, decisiones regulatorias o judiciales pendientes, splits.
**Macro de EE.UU.:** decisión de la Fed y conferencia, actas de la Fed, CPI, PCE, empleo (nonfarm payrolls), PIB, discursos del presidente de la Fed.
**Del mercado:** vencimiento mensual o trimestral de opciones (tercer viernes), feriados y días de media jornada, rebalanceo de índices.
**Para ETFs (SPY, QQQ, IWM):** earnings de las empresas que más pesan en el índice (por ejemplo, las 5 o 6 mayores).

## Fuentes
Usá la fuente oficial siempre que exista: el sitio de relación con inversores de la empresa para earnings, el calendario de la Fed (federalreserve.gov) y el del BLS (bls.gov) para CPI y empleo. Un calendario de terceros sirve para encontrar la fecha, pero confirmala en la fuente oficial. Si una fecha no está confirmada por la empresa, marcala como **estimada**.

## Regla de decisión (cambiada por el usuario el 27 oct 2026)
Un evento ya no descarta el trade por existir: se opera entendiéndolo. Por cada evento de impacto alto armá su tesis: qué pasó la vez anterior (el dato del mes pasado y cómo reaccionó el mercado ese día), qué espera el mercado ahora (consenso, probabilidades implícitas, analistas, siempre con fuente), tu propio pronóstico y de qué lado ves el riesgo.

- Earnings del activo (una acción suelta) dentro del plazo: **DESCARTAR**. Un balance mueve a la acción mucho más que cualquier macro y el gap no se puede gestionar.
- Earnings de una empresa de mayor peso de un ETF: **ALERTA**, pesada por lo que esa empresa pesa en el índice.
- Fed, CPI o empleo dentro del plazo: **ALERTA** con la tesis del evento. El código compara el movimiento típico del evento (medido sobre los eventos parecidos del pasado) con la distancia al strike vendido y descarta solo si el strike queda más cerca que ese movimiento.
- Ex-dividendo dentro del plazo con una call vendida: **ALERTA** de asignación anticipada; el gestor de posición tiene que tener un plan.
- Fecha estimada sin confirmar dentro del plazo: tratarla como si fuera confirmada.
- **DESCARTAR** por tu cuenta solo por algo que los números no ven: un fallo judicial, una suspensión de cotización, un dato que cambia la empresa.

Tené presente el riesgo real: vender prima antes de un evento cobra más IV y después viene la caída de volatilidad, pero las peores pérdidas son mucho peores que en un día normal. Mirá el peor caso, no el promedio.

## Formato de salida
Tabla con fecha, hora, evento, fuente, si está confirmado o estimado e impacto (alto, medio o bajo). Termina con una línea: **SIN EVENTOS**, **ALERTA** (con el motivo) o **DESCARTAR** (con el motivo).
