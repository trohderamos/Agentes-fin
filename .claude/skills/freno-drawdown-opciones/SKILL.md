---
name: "freno-drawdown-opciones"
description: "Para decidir si se permite abrir riesgo nuevo según pérdidas diarias, semanales y mensuales y rachas de pérdidas, antes de que el consejo vea un trade."
---

# Freno por drawdown

Lo usa Risk antes de pasar cualquier trade al consejo. Es una regla de entrada: no cierra posiciones abiertas; eso lo decide el gestor de posición.

## Estado que necesitás
Capital inicial del período, resultado realizado y no realizado de hoy, de la semana y del mes, y los últimos trades cerrados con su resultado. **Si falta alguno de estos datos, el resultado es DETENIDO**: sin historial no se asume que todo va bien.

## Reglas por defecto (ajustables por el usuario)
- Pérdida del día > 2% del capital: **DETENIDO** hasta la próxima sesión.
- 2 trades perdedores seguidos: **ENFRIAMIENTO** (1 día sin abrir riesgo nuevo).
- Pérdida de la semana > 5%: **DETENIDO** hasta la semana siguiente.
- Pérdida del mes > 8%: **DETENIDO** hasta el mes siguiente y revisión de la estrategia con el usuario.

## Salida
Una de tres: **PERMITIDO**, **ENFRIAMIENTO** o **DETENIDO**, más la regla que lo disparó, el número que la disparó, y cuándo se vuelve a evaluar. Si dos reglas disparan a la vez, manda la más estricta.

## Reglas de uso
- Un freno no se anula para "recuperar" pérdidas. Solo el usuario puede cambiar los umbrales, y por escrito.
- Si los datos son contradictorios (por ejemplo, el capital no cuadra con los trades), el resultado es DETENIDO y se avisa al usuario.
- Registrá cada evaluación con fecha para poder auditarla.