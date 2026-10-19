"""Consejo: ejecutor de primas, escéptico, gestor de posición, y liquidez y costos.

Debaten dos rondas sobre el trade que prefirió research: en la primera cada
uno da su postura, en la segunda responde a los otros. Cada postura termina con
un voto (A FAVOR, AJUSTAR o EN CONTRA) y una confianza.

Lo que se calcula en código y se les pasa como dato: los costos (comisiones y
slippage), un escenario de pérdida con un movimiento de 2 días en contra y un
plan de salida por defecto con las reglas del skill.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from agentes import config
from agentes.modelo import Modelo, Pedido, cargar_skill, lecciones_aprobadas
from datos.calculos import precio_bs

MIEMBROS = ("ejecutor", "esceptico", "gestor", "liquidez")
NOMBRES = {
    "ejecutor": "Ejecutor de primas",
    "esceptico": "Escéptico",
    "gestor": "Gestor de posición",
    "liquidez": "Liquidez y costos",
}
VOTOS = ("A FAVOR", "AJUSTAR", "EN CONTRA")

# Reglas del skill consejo-opciones-primas.
TOMA_GANANCIA = 0.50  # del crédito
STOP_VECES_CREDITO = 2.0  # pérdida = 2 veces el crédito
SPREAD_MAX_CREDITO = 0.10
COSTOS_MAX_CREDITO = 0.15


# --- Cálculos en código --------------------------------------------------


def costos(candidato: dict, contratos: int = 1) -> dict:
    """Comisiones al abrir y slippage esperado (mitad del spread de cada pata)."""
    patas = candidato["patas"]
    comision = config.COMISION_PATA * len(patas) * contratos
    slippage = sum((p["ask"] - p["bid"]) / 2 for p in patas) * 100 * contratos
    credito = candidato["credito"] * 100 * contratos
    spread_total = sum(p["ask"] - p["bid"] for p in patas)
    return {
        "contratos": contratos,
        "credito_usd": round(credito, 2),
        "comisiones_usd": round(comision, 2),
        "slippage_esperado_usd": round(slippage, 2),
        "costo_total_usd": round(comision + slippage, 2),
        "costos_sobre_credito": round((comision + slippage) / credito, 3) if credito > 0 else None,
        "spread_compra_venta_sobre_credito": round(spread_total / candidato["credito"], 3)
        if candidato["credito"] > 0
        else None,
        "credito_neto_usd": round(credito - comision - slippage, 2),
        "limites": {
            "spread_max_sobre_credito": SPREAD_MAX_CREDITO,
            "costos_max_sobre_credito": COSTOS_MAX_CREDITO,
        },
        "falta": "Volumen y open interest por pata: la cadena de Alpaca no los trae.",
    }


def _valor(candidato: dict, s: float, dias: float, suba_iv: float) -> float:
    """Lo que cuesta cerrar la posición, por acción, con Black-Scholes."""
    t = max(dias, 0) / 365
    total = 0.0
    for p in candidato["patas"]:
        iv = (p.get("iv") or 0.15) + suba_iv
        precio = precio_bs(p["tipo"], s, p["strike"], t, max(iv, 0.01))
        total += precio if p["lado"] == "venta" else -precio
    return total


def escenarios(candidato: dict, precio: float, atr: Optional[float]) -> list[dict]:
    """Pérdida estimada si en 2 días el precio se mueve en contra."""
    movimientos = [("3%", precio * 0.03)]
    if atr:
        movimientos.insert(0, ("2 ATR", 2 * atr))
    lados = []
    if any(p["tipo"] == "put" and p["lado"] == "venta" for p in candidato["patas"]):
        lados.append(("baja", -1, 0.05))  # cuando el mercado cae, la IV sube
    if any(p["tipo"] == "call" and p["lado"] == "venta" for p in candidato["patas"]):
        lados.append(("suba", 1, 0.0))
    dias = candidato["dias"] - 2
    out = []
    for nombre, mov in movimientos:
        for lado, signo, iv in lados:
            s = precio + signo * mov
            cierre = _valor(candidato, s, dias, iv)
            resultado = (candidato["credito"] - cierre) * 100
            out.append(
                {
                    "escenario": f"{lado} de {nombre} ({mov:.2f}) en 2 días"
                    + (f", IV +{iv * 100:.0f} puntos" if iv else ""),
                    "precio": round(s, 2),
                    "resultado_por_contrato_usd": round(resultado, 2),
                    "sobre_perdida_maxima": round(-resultado / candidato["perdida_maxima"], 2)
                    if candidato["perdida_maxima"]
                    else None,
                }
            )
    return out


def dia_habil_anterior(d: date) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def plan_por_defecto(candidato: dict) -> dict:
    credito = candidato["credito"]
    vendidos = [p["strike"] for p in candidato["patas"] if p["lado"] == "venta"]
    return {
        "tomar_ganancia": f"Recomprar a {credito * (1 - TOMA_GANANCIA):.2f} (50% del crédito ganado).",
        "stop": f"Cerrar si recomprar cuesta {credito * (1 + STOP_VECES_CREDITO):.2f} o más (pérdida de 2 veces el crédito).",
        "cierre_por_tiempo": "Cerrar o rolar a más tardar el "
        + dia_habil_anterior(date.fromisoformat(candidato["vencimiento"])).isoformat()
        + " (día hábil anterior al vencimiento).",
        "si_toca_el_strike": f"Si el precio toca {', '.join(f'{k:g}' for k in vendidos)}: cerrar o rolar ese lado; no esperar.",
        "revision": "Una vez por día hábil.",
    }


def orden_por_defecto(candidato: dict, contratos_max: int) -> dict:
    medio, natural = candidato["credito"], candidato["credito_natural"]
    # Límite entre el natural y el medio, más cerca del medio, redondeado al centavo.
    limite = round(natural + (medio - natural) * 0.75, 2) if natural > 0 else round(medio, 2)
    return {"id": candidato["id"], "contratos": 1 if contratos_max >= 1 else 0, "precio_limite": limite}


# --- Debate --------------------------------------------------------------

TAREAS = {
    "ejecutor": "Armá la orden concreta (id del candidato, cantidad de contratos dentro de 'contratos_max' y "
    "precio límite como crédito por acción, cerca del punto medio y nunca mayor que él) y defendé la entrada con cifras.",
    "esceptico": "Buscá cómo pierde plata el trade. Usá los escenarios calculados y dá al menos un escenario "
    "concreto de pérdida con su magnitud en dólares. Revisá si el backtest es confiable.",
    "gestor": "Escribí el plan de salida (tomar ganancia, stop, cierre por tiempo, qué hacer si toca el strike). "
    "Podés partir del plan por defecto y ajustarlo. Sin plan escrito tu voto es AJUSTAR.",
    "liquidez": "Revisá el spread de compra y venta y los costos calculados. Si los costos se comen más del 15% "
    "del crédito o no se puede salir con facilidad, votá EN CONTRA.",
}

ESQUEMA_POSTURA = {
    "type": "object",
    "properties": {
        "postura": {"type": "string", "description": "Corta, con cifras."},
        "respuesta_a_otros": {"type": "string", "description": "Solo en la segunda ronda; vacío en la primera."},
        "voto": {"type": "string", "enum": list(VOTOS)},
        "cambio_propuesto": {"type": "string", "description": "Si el voto es AJUSTAR, el cambio concreto."},
        "confianza": {"type": "string", "enum": ["baja", "media", "alta"]},
        "orden": {
            "type": "object",
            "description": "Solo el ejecutor: la orden propuesta.",
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
            "description": "Solo el gestor: el plan de salida.",
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
    },
    "required": ["postura", "respuesta_a_otros", "voto", "cambio_propuesto", "confianza"],
    "additionalProperties": False,
}


@dataclass
class Debate:
    trade: dict  # el candidato de research que se debate, con su revisión y backtest
    alternativas: list[dict]
    costos: dict
    escenarios: list[dict]
    plan_por_defecto: dict
    orden_por_defecto: dict
    rondas: list[dict[str, dict]] = field(default_factory=list)
    con_modelo: bool = True

    def votos_finales(self) -> dict[str, str]:
        if not self.rondas:
            return {}
        return {m: p.get("voto", "") for m, p in self.rondas[-1].items()}

    def orden(self) -> dict:
        for ronda in reversed(self.rondas):
            o = ronda.get("ejecutor", {}).get("orden")
            if o:
                return o
        return self.orden_por_defecto

    def plan(self) -> Optional[dict]:
        for ronda in reversed(self.rondas):
            plan = ronda.get("gestor", {}).get("plan_salida")
            if plan:
                return plan
        return None

    def a_dict(self) -> dict:
        return {
            "trade": self.trade,
            "alternativas": [a["candidato"]["id"] for a in self.alternativas],
            "costos": self.costos,
            "escenarios": self.escenarios,
            "plan_por_defecto": self.plan_por_defecto,
            "orden_por_defecto": self.orden_por_defecto,
            "rondas": self.rondas,
            "votos_finales": self.votos_finales(),
            "con_modelo": self.con_modelo,
        }


def _sistema(miembro: str, hoy: date) -> str:
    return "\n\n".join(
        [
            f"Sos el {NOMBRES[miembro]} del consejo de un sistema que vende primas de opciones en una cuenta paper "
            f"(dinero simulado). Hoy es {hoy.isoformat()}. Escribí en español rioplatense, corto y con cifras.",
            "Usá solo los datos del mensaje. Nunca inventes precios, strikes ni datos de mercado.",
            "## Tu tarea\n" + TAREAS[miembro],
            "## Skill consejo-opciones-primas\n" + cargar_skill("consejo-opciones-primas"),
            lecciones_aprobadas(),
        ]
    ).strip()


def _sin_modelo(miembro: str, d: Debate, ronda: int) -> dict:
    """Posturas con reglas fijas, para probar el circuito sin gastar."""
    base = {"respuesta_a_otros": "", "cambio_propuesto": "", "confianza": "baja", "sin_modelo": True}
    if miembro == "ejecutor":
        return {**base, "postura": f"Orden {d.orden_por_defecto}.", "voto": "A FAVOR", "orden": d.orden_por_defecto}
    if miembro == "esceptico":
        peor = min(d.escenarios, key=lambda e: e["resultado_por_contrato_usd"]) if d.escenarios else None
        grave = peor is not None and (peor["sobre_perdida_maxima"] or 0) > 0.5
        texto = "Sin escenarios." if peor is None else f"Peor escenario: {peor['escenario']}, {peor['resultado_por_contrato_usd']} USD."
        return {**base, "postura": texto, "voto": "EN CONTRA" if grave else "A FAVOR"}
    if miembro == "gestor":
        return {**base, "postura": "Plan de salida por defecto.", "voto": "A FAVOR", "plan_salida": d.plan_por_defecto}
    c = d.costos
    malo = (c["costos_sobre_credito"] or 1) > COSTOS_MAX_CREDITO
    return {
        **base,
        "postura": f"Costos {c['costo_total_usd']} USD, {(c['costos_sobre_credito'] or 0):.0%} del crédito.",
        "voto": "EN CONTRA" if malo else "A FAVOR",
    }


def _validar_orden(postura: dict, d: Debate, por_id: dict[str, dict]) -> None:
    """La orden del ejecutor tiene que ser de un candidato de research y respetar sus límites."""
    o = postura.get("orden")
    if not o:
        return
    problemas = []
    x = por_id.get(o.get("id"))
    if x is None:
        problemas.append(f"el id {o.get('id')} no es un candidato de research")
    else:
        c = x["candidato"]
        if not 1 <= int(o.get("contratos", 0)) <= x["revision"]["contratos_max"]:
            problemas.append(f"contratos fuera de 1 a {x['revision']['contratos_max']}")
        if not 0 < float(o.get("precio_limite", 0)) <= c["credito"] + 1e-9:
            problemas.append(f"precio límite fuera de 0 a {c['credito']:.2f} (punto medio)")
    if problemas:
        postura["orden_invalida"] = "; ".join(problemas)
        postura["orden"] = None


async def debatir(
    research: dict,
    modelo: Optional[Modelo],
    hoy: date,
    atr: Optional[float],
    costo: list[float],
) -> Optional[Debate]:
    """Dos rondas sobre el trade preferido de research. None si no hay candidatos."""
    candidatos = research.get("candidatos") or []
    if not candidatos:
        return None
    trade = next((x for x in candidatos if x.get("preferido")), candidatos[0])
    c = trade["candidato"]
    d = Debate(
        trade=trade,
        alternativas=[x for x in candidatos if x is not trade],
        costos=costos(c),
        escenarios=escenarios(c, research["precio"], atr),
        plan_por_defecto=plan_por_defecto(c),
        orden_por_defecto=orden_por_defecto(c, trade["revision"]["contratos_max"]),
        con_modelo=modelo is not None,
    )
    por_id = {x["candidato"]["id"]: x for x in candidatos}
    contexto = {
        "trade": trade,
        "alternativas_de_research": d.alternativas,
        "informe_chart": research.get("chart"),
        "informe_news": research.get("news"),
        "informe_fundamentals": research.get("fundamentals"),
        "informe_risk": research.get("risk"),
        "costos_calculados": d.costos,
        "escenarios_de_perdida": d.escenarios,
        "plan_de_salida_por_defecto": d.plan_por_defecto,
        "orden_por_defecto": d.orden_por_defecto,
        "contratos_max": trade["revision"]["contratos_max"],
        "capital": research.get("capital"),
    }

    async def postura(miembro: str, ronda: int) -> dict:
        if modelo is None:
            p = _sin_modelo(miembro, d, ronda)
        else:
            mensaje: dict[str, Any] = {"ronda": ronda, **contexto}
            if ronda == 2:
                mensaje["posturas_ronda_1"] = d.rondas[0]
                mensaje["instruccion"] = "Respondé a los otros en 'respuesta_a_otros' y dá tu voto final."
            respuesta = await modelo.pedir(
                Pedido(
                    agente=f"{NOMBRES[miembro]} (ronda {ronda})",
                    sistema=_sistema(miembro, hoy),
                    mensaje=json.dumps(mensaje, ensure_ascii=False, indent=1, default=str),
                    esquema=ESQUEMA_POSTURA,
                )
            )
            costo.append(respuesta.costo_usd or 0.0)
            p = respuesta.datos
            if p.get("voto") not in VOTOS:
                p["voto"] = "EN CONTRA"
                p["voto_invalido"] = True
        if miembro != "ejecutor":
            p.pop("orden", None)
        if miembro != "gestor":
            p.pop("plan_salida", None)
        _validar_orden(p, d, por_id)
        return p

    for ronda in (1, 2):
        posturas = await asyncio.gather(*(postura(m, ronda) for m in MIEMBROS))
        d.rondas.append(dict(zip(MIEMBROS, posturas)))
    return d
