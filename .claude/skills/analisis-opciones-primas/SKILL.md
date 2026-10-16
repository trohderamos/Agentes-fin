---
name: "analisis-opciones-primas"
description: "Para calcular y evaluar una estructura de venta de primas (put cubierto, call cubierta, spreads, iron condor) con griegas, movimiento esperado y riesgos de earnings."
---

# Análisis de estructuras de venta de primas

Todos los números que des deben decir si son datos reales (con fecha y fuente), cálculos tuyos o estimaciones. Si falta un dato de mercado, pedí el dato; no lo inventes.

## Perfil de cada estructura
- **Put cubierto con efectivo**: crédito = prima. Pérdida máxima = (strike - prima) x 100, si el activo cae a cero. Punto de equilibrio = strike - prima. Requiere efectivo por strike x 100.
- **Call cubierta**: crédito = prima. Ganancia limitada al strike. La pérdida es la del activo menos la prima (limitada, pues cae como máximo a cero). Riesgo: que te lo quiten (asignación) antes de un dividendo.
- **Spread de crédito**: pérdida máxima = (ancho - crédito) x 100. Ganancia máxima = crédito x 100. Punto de equilibrio = strike vendido -/+ crédito.
- **Iron condor**: suma de dos spreads; el riesgo es el del lado más ancho menos el crédito total, pues solo un lado puede perder.

## Cálculos
- Movimiento esperado hasta el vencimiento ≈ precio x IV x raíz(días/365). El strike vendido debería estar fuera de ese rango, salvo que la tesis justifique otra cosa.
- Probabilidad de ganancia aproximada ≈ 1 - |delta| del strike vendido (spreads: ajustar por el crédito). Es una estimación, no una garantía.
- Griegas de la posición: sumá delta, theta, vega y gamma por pata y por contrato (x100). Para primas se busca theta positivo y vega negativo; vigilá el gamma cerca del vencimiento.
- Retorno sobre riesgo = crédito / pérdida máxima. Como referencia, un spread con menos de 20-25% de retorno sobre riesgo rara vez compensa.

## Earnings y eventos
Vender primas antes de un earnings expone a un gap que el crédito no cubre. Si hay evento dentro del plazo: o se evita, o se dice explícitamente que se asume el riesgo y se reduce el tamaño. Después del evento la IV cae (IV crush), lo cual favorece al vendedor solo si el precio no se movió más que lo esperado.

## Riesgos a nombrar siempre
Asignación anticipada (especialmente con dividendos), gap de precio, liquidez de cada pata, y que la volatilidad sube justo cuando el mercado cae. Las estructuras con riesgo ilimitado (short straddle o strangle sin cobertura) quedan fuera de este sistema.

## Formato
Tabla con strikes, vencimiento, crédito, pérdida máxima, ganancia máxima, equilibrio, probabilidad aproximada, griegas y retorno sobre riesgo; luego una línea con el riesgo principal.