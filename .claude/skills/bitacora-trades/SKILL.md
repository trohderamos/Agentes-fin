---
name: bitacora-trades
description: "Para todos los agentes: registrar cada trade de primas (tesis, debate, decisión y resultado) y hacer un repaso semanal de qué funcionó y qué falló."
---

# Bitácora de trades

Cada trade, aprobado o no, queda registrado. Sin bitácora no hay forma de saber si el sistema funciona ni de mejorarlo.

## Qué se registra
**Al decidir:** fecha, subyacente, estructura, strikes, vencimiento, crédito, pérdida máxima, tesis de research en una línea, confianza, resultado del backtest, eventos del calendario, voto de cada consejero con su argumento principal, decisión del jefe y su razón. También los trades rechazados, con su motivo.
**Al ejecutar:** precio real, hora, intentos hasta llenarse y slippage frente al punto medio.
**Al cerrar:** fecha, precio de cierre, resultado en dinero y en % del crédito, motivo del cierre (ganancia del 50%, stop, vencimiento cercano, ajuste) y días que estuvo abierto.

## Repaso semanal
1. Resultado de la semana: cantidad de trades, ganados, perdidos, resultado total y drawdown.
2. Para cada pérdida: ¿la tesis estaba mal, el riesgo estaba mal medido, o fue mala suerte dentro de lo esperado? ¿Algún consejero lo había advertido?
3. Para los rechazados: ¿qué habría pasado? Si el jefe rechaza seguido trades que habrían ganado, o aprueba los que pierden, anotalo.
4. ¿Qué consejero acierta más con sus objeciones?
5. Entre 1 y 3 cambios concretos propuestos (un umbral, una regla, un filtro). Los cambios se le proponen al usuario; no se aplican solos.

## Reglas
- Registrá en el momento, no de memoria después.
- No borres ni corrijas registros viejos: si hubo un error, agregá una nota con fecha.
- Separá hechos (precios, fechas) de interpretaciones.
