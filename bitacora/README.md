# Bitácora

Registro de cada trade, aprobado o no: tesis, debate, decisión del jefe, ejecución y resultado. Sigue las reglas del skill `bitacora-trades`: se registra en el momento y no se borran registros viejos.

- `decisiones.jsonl`: una línea por cada ciclo de research, consejo y jefe (fase 4), con la tesis, los eventos, los votos del consejo, la decisión del jefe y la orden y el plan de salida si aprobó.
- `trades.jsonl`: los trades abiertos y cerrados en el bróker (fase 5). El freno por drawdown y Risk la leen.

Los archivos de registro no se suben al repositorio (ver `.gitignore`).
