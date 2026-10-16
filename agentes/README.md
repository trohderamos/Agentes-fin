# Agentes

Acá van a vivir los nueve agentes, en tres niveles:

1. **Research** (4): Chart, News, Strategy y Risk. Risk tiene veto antes del consejo. El backtest es una herramienta compartida, no un agente.
2. **Consejo** (4): ejecutor de primas, escéptico, gestor de posición, y liquidez y costos. Debaten dos rondas.
3. **Jefe** (1): escucha el debate y decide solo si aprueba, ajusta (vuelve a research) o rechaza.

Los agentes nunca hablan con el bróker directamente: piden datos a `datos/` y, cuando haya órdenes, al bróker a través de la interfaz de `broker/base.py`.

Vacío en la fase 1.
