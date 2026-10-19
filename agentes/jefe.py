"""Jefe: lee research, Risk y el debate, y decide solo.

Decide una de tres: APROBAR, AJUSTAR (vuelve a research con los cambios, como
máximo dos veces) o RECHAZAR. No promedia votos: pesa argumentos.

En código se aseguran las reglas que no dependen del criterio:
- con veto de Risk no se aprueba (los vetados no llegan al consejo, pero se
  verifica igual);
- sin plan de salida escrito no se aprueba;
- la orden aprobada tiene que ser un candidato de research, dentro del
  tamaño que permite Risk y con precio límite no mayor que el punto medio;
- la cuenta tiene que ser paper.
Si el jefe aprueba algo que no cumple, la decisión pasa a AJUSTAR con el motivo.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Optional

from agentes.consejo import Debate
from agentes.modelo import Modelo, Pedido, cargar_skill

DECISIONES = ("APROBAR", "AJUSTAR", "RECHAZAR")
MAX_VUELTAS = 2

ESQUEMA_DECISION = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": list(DECISIONES)},
        "razon_principal": {"type": "string", "description": "2 o 3 líneas."},
        "objecion_que_mas_peso": {"type": "string"},
        "orden_final": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "contratos": {"type": "integer"},
                "precio_limite": {"type": "number"},
            },
            "required": ["id", "contratos", "precio_limite"],
            "additionalProperties": False,
        },
        "plan_salida": {
            "type": "object",
            "properties": {
                "tomar_ganancia": {"type": "string"},
                "stop": {"type": "string"},
                "cierre_por_tiempo": {"type": "string"},
                "si_toca_el_strike": {"type": "string"},
                "revision": {"type": "string"},
            },
            "required": ["tomar_ganancia", "stop", "cierre_por_tiempo", "si_toca_el_strike", "revision"],
            "additionalProperties": False,
        },
        "cambios_pedidos": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Si la decisión es AJUSTAR: los cambios exactos que tiene que hacer research.",
        },
        "que_revisar_proximo_ciclo": {"type": "string"},
    },
    "required": ["decision", "razon_principal", "objecion_que_mas_peso", "cambios_pedidos", "que_revisar_proximo_ciclo"],
    "additionalProperties": False,
}

TAREA = """Leé el informe de research, la revisión de Risk y el debate completo del consejo (dos rondas).
Decidí solo: APROBAR, AJUSTAR o RECHAZAR. Si aprobás, dejá la orden final (id de un candidato de research,
contratos dentro de 'contratos_max' y precio límite como crédito por acción, no mayor que el punto medio) y
el plan de salida. Si ajustás, poné en 'cambios_pedidos' los cambios exactos para research. Estás en la
vuelta 'vuelta' de un máximo de 2: si en la última vuelta seguís sin poder aprobar, rechazá.
En esta etapa la orden aprobada NO se envía: queda registrada en la bitácora."""


def _sistema(hoy: date) -> str:
    return "\n\n".join(
        [
            f"Sos el jefe de un sistema que vende primas de opciones en una cuenta paper (dinero simulado). Hoy es "
            f"{hoy.isoformat()}. Escribí en español rioplatense, corto y concreto.",
            "Usá solo los datos del mensaje. Nunca inventes precios ni strikes.",
            "## Tu tarea\n" + TAREA,
            "## Skill jefe-opciones-primas\n" + cargar_skill("jefe-opciones-primas"),
        ]
    )


def _sin_modelo(debate: Debate) -> dict:
    votos = debate.votos_finales()
    en_contra = [m for m, v in votos.items() if v == "EN CONTRA"]
    plan = debate.plan()
    if en_contra or plan is None:
        motivo = f"Votaron en contra: {', '.join(en_contra)}." if en_contra else "No hay plan de salida."
        return {
            "decision": "RECHAZAR",
            "razon_principal": motivo,
            "objecion_que_mas_peso": motivo,
            "cambios_pedidos": [],
            "que_revisar_proximo_ciclo": "",
            "sin_modelo": True,
        }
    return {
        "decision": "APROBAR",
        "razon_principal": "Nadie votó en contra y hay plan de salida.",
        "objecion_que_mas_peso": "",
        "orden_final": debate.orden(),
        "plan_salida": plan,
        "cambios_pedidos": [],
        "que_revisar_proximo_ciclo": "",
        "sin_modelo": True,
    }


def validar(decision: dict, research: dict, cuenta_paper: bool) -> dict:
    """Aplica las reglas fijas. Devuelve la decisión, corregida si hacía falta."""
    if decision.get("decision") not in DECISIONES:
        decision["decision"] = "RECHAZAR"
        decision.setdefault("correcciones", []).append("Decisión no reconocida: se rechaza.")
    if decision["decision"] != "APROBAR":
        decision.pop("orden_final", None)
        return decision

    problemas = []
    if not cuenta_paper:
        problemas.append("La cuenta no es paper.")
    por_id = {x["candidato"]["id"]: x for x in research.get("candidatos") or []}
    orden = decision.get("orden_final") or {}
    x = por_id.get(orden.get("id"))
    if x is None:
        problemas.append(f"La orden no es un candidato de research ({orden.get('id')}).")
    else:
        if x["revision"].get("vetado") or x["revision"].get("vetos"):
            problemas.append("El candidato tiene veto de Risk.")
        if not 1 <= int(orden.get("contratos", 0)) <= x["revision"]["contratos_max"]:
            problemas.append(f"Contratos fuera de 1 a {x['revision']['contratos_max']}.")
        if not 0 < float(orden.get("precio_limite", 0)) <= x["candidato"]["credito"] + 1e-9:
            problemas.append("Precio límite fuera de rango (tiene que ser un crédito no mayor que el punto medio).")
    plan = decision.get("plan_salida") or {}
    if not all(str(plan.get(k, "")).strip() for k in ("tomar_ganancia", "stop", "cierre_por_tiempo", "si_toca_el_strike")):
        problemas.append("Falta el plan de salida escrito.")
    if problemas:
        decision["decision"] = "AJUSTAR"
        decision["correcciones"] = problemas
        decision["cambios_pedidos"] = list(decision.get("cambios_pedidos") or []) + problemas
        decision.pop("orden_final", None)
    return decision


async def decidir(
    research: dict,
    debate: Debate,
    modelo: Optional[Modelo],
    hoy: date,
    vuelta: int,
    cuenta_paper: bool,
    costo: list[float],
) -> dict:
    if modelo is None:
        decision = _sin_modelo(debate)
    else:
        mensaje = {
            "vuelta": vuelta,
            "max_vueltas": MAX_VUELTAS,
            "cuenta_paper": cuenta_paper,
            "informe_research": {
                k: research.get(k) for k in ("chart", "news", "fundamentals", "strategy", "risk", "candidatos", "freno", "capital", "precio")
            },
            "debate": debate.a_dict(),
            "contratos_max": debate.trade["revision"]["contratos_max"],
        }
        respuesta = await modelo.pedir(
            Pedido(
                agente="Jefe",
                sistema=_sistema(hoy),
                mensaje=json.dumps(mensaje, ensure_ascii=False, indent=1, default=str),
                esquema=ESQUEMA_DECISION,
            )
        )
        costo.append(respuesta.costo_usd or 0.0)
        decision = respuesta.datos
    decision = validar(decision, research, cuenta_paper)
    if decision["decision"] == "AJUSTAR" and vuelta >= MAX_VUELTAS:
        decision["decision"] = "RECHAZAR"
        decision.setdefault("correcciones", []).append(
            f"Se pidió ajustar en la vuelta {vuelta} de {MAX_VUELTAS}: se rechaza."
        )
    return decision
