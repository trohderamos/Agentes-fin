---
name: ordenes-paper-alpaca
description: "Para el jefe y el ejecutor de primas: armar, revisar y enviar órdenes de opciones de una o varias patas en la cuenta paper de Alpaca, y qué hacer si no se llenan."
---

# Órdenes en paper trading (Alpaca)

Solo se opera en la cuenta **paper** (dinero simulado). Antes de cada orden, confirmá que la conexión apunta al entorno paper. Si apunta a una cuenta real, no envíes nada y avisá al usuario. Pasar a dinero real requiere que el usuario lo pida por escrito.

## Antes de enviar (checklist)
1. El jefe aprobó este trade exacto y Risk no puso veto.
2. El freno por drawdown dice PERMITIDO.
3. Los símbolos de cada pata son correctos: subyacente, vencimiento, tipo (put o call) y strike.
4. Cada pata tiene el lado correcto (vender o comprar) y la cantidad de contratos correcta. En un spread, la pata comprada protege a la vendida.
5. Hay efectivo o margen suficiente para la pérdida máxima.
6. El mercado está abierto y no faltan menos de 15 minutos para el cierre.

## Cómo armar la orden
- Las estructuras de varias patas (spread, iron condor) van en **una sola orden multi-pata**, nunca pata por pata: si una se llena y la otra no, queda riesgo sin cubrir.
- Siempre con **precio límite**, nunca a mercado. Empezá en el punto medio del crédito total.
- Vigencia del día: si no se llena, vence al cierre.

## Si no se llena
Esperá unos minutos y bajá el crédito pedido de a un centavo o dos, como máximo 3 veces. Si el crédito que queda ya no cumple el mínimo aprobado por el consejo, cancelá y registrá que no se ejecutó. Nunca persigas el precio por debajo de ese mínimo.

## Después de enviar
Confirmá el estado de la orden (llenada, parcial, cancelada) y el precio real. Si se llenó parcialmente una orden multi-pata, avisá al gestor de posición enseguida. Registrá todo en la bitácora.

## Cierre de posiciones
Mismas reglas: una sola orden multi-pata con precio límite. Si se cierra por el stop de pérdida o por el vencimiento cercano y no se llena, se puede ir ajustando el precio sin el tope de 3 intentos: salir es la prioridad.
