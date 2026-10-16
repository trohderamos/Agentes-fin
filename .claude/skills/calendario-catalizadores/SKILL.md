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

## Regla de decisión
- Earnings del activo dentro del plazo: **DESCARTAR**.
- Fed, CPI o empleo dentro del plazo: **DESCARTAR** para acciones individuales; para ETFs, solo si el strike vendido queda dentro del movimiento esperado del evento.
- Ex-dividendo dentro del plazo con una call vendida: **ALERTA** de asignación anticipada; el gestor de posición tiene que tener un plan.
- Fecha estimada sin confirmar dentro del plazo: tratarla como si fuera confirmada.

## Formato de salida
Tabla con fecha, hora, evento, fuente, si está confirmado o estimado e impacto (alto, medio o bajo). Termina con una línea: **SIN EVENTOS**, **ALERTA** (con el motivo) o **DESCARTAR** (con el motivo).
