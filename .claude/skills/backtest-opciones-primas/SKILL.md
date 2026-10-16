---
name: "backtest-opciones-primas"
description: "Para probar con rigor una estrategia de venta de primas con datos históricos y dar un veredicto de desplegar, refinar o abandonar."
---

# Backtest de venta de primas

Objetivo: descubrir si la estrategia aguanta, no demostrar que funciona. Desconfiá de los resultados buenos.

## Antes de correr
1. Escribí la hipótesis en una frase (por ejemplo: vender puts a 30-45 días con delta 0.20 cuando el IV rank > 30 gana más de lo que pierde).
2. Fijá las reglas de entrada, salida, tamaño y cantidad de parámetros ANTES de mirar resultados. Menos parámetros es mejor.
3. Decí de dónde salen los datos. Con precios reales de opciones el resultado es confiable; con precios modelados (Black-Scholes) es solo una aproximación y hay que declararlo.

## Cómo correrlo
- Separá período de ajuste y período de prueba. No toques las reglas después de ver la prueba.
- Walk-forward: ajustá en una ventana, probá en la siguiente, y repetí.
- Incluir comisiones, slippage (al menos la mitad del spread entre compra y venta) y asignaciones. Usar precios que de verdad se hubieran podido ejecutar.
- Evitá el look-ahead: nada que no se supiera en la fecha de entrada (por ejemplo, un earnings que se anunció después).
- Incluir períodos de crisis (2008, 2020, 2022) y de baja volatilidad.

## Pruebas de estrés
- Mové cada parámetro 20% hacia cada lado; si el resultado se desploma, está sobreajustado.
- Duplicá el slippage y empeorá los precios de ejecución.
- Mirá el peor mes y la peor racha de pérdidas, no solo el promedio.

## Métricas a reportar
Cantidad de trades, tasa de acierto, ganancia y pérdida media, resultado esperado por trade, peor drawdown, peor trade, resultado por año y por régimen de mercado. Con menos de 30 trades, decilo: la muestra es chica.

## Señales de alarma
Curva demasiado suave, tasa de acierto cerca de 90% sin pérdidas grandes en el historial (así se ven las estrategias que explotan de golpe), muchos parámetros, resultados que dependen de un solo año o de un solo activo.

## Veredicto
**DESPLEGAR** (en paper trading primero), **REFINAR** (con el cambio puntual a probar) o **ABANDONAR**. Siempre con las 3 razones principales y lo que habría que ver en paper trading para confirmarlo.