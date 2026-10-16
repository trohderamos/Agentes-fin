"""Ciclo completo: research, consejo y jefe. No envía órdenes.

Si el jefe pide ajustar, vuelve a research (se reusan Chart y News, Strategy y
Risk vuelven a correr con los cambios pedidos), como máximo dos vueltas. Cada
ciclo queda en bitacora/decisiones.jsonl, se opere o no.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from agentes.bitacora import TradeRegistrado, registrar_decision
from agentes.consejo import Debate, debatir
from agentes.jefe import MAX_VUELTAS, decidir
from agentes.modelo import Modelo
from agentes.research import Research, ResultadoResearch
from broker.base import Broker
from datos.informe import generar_informe


@dataclass
class Vuelta:
    numero: int
    research: ResultadoResearch
    debate: Optional[Debate]
    decision: dict


@dataclass
class ResultadoCiclo:
    subyacente: str
    fecha: date
    con_modelo: bool
    vueltas: list[Vuelta] = field(default_factory=list)
    costo_usd: float = 0.0

    @property
    def final(self) -> Vuelta:
        return self.vueltas[-1]

    def a_dict(self) -> dict:
        return {
            "subyacente": self.subyacente,
            "fecha": self.fecha.isoformat(),
            "con_modelo": self.con_modelo,
            "decision_final": self.final.decision,
            "vueltas": [
                {
                    "vuelta": v.numero,
                    "research": v.research.a_dict(),
                    "debate": None if v.debate is None else v.debate.a_dict(),
                    "decision": v.decision,
                }
                for v in self.vueltas
            ],
            "costo_usd": round(self.costo_usd, 4),
        }


def registro_bitacora(ciclo: ResultadoCiclo) -> dict:
    """Lo que pide el skill bitacora-trades al decidir."""
    v = ciclo.final
    r = v.research
    registro: dict = {
        "tipo": "decision",
        "registrado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fecha": ciclo.fecha.isoformat(),
        "subyacente": ciclo.subyacente,
        "con_modelo": ciclo.con_modelo,
        "vueltas": len(ciclo.vueltas),
        "decision": v.decision.get("decision"),
        "razon": v.decision.get("razon_principal"),
        "objecion_que_mas_peso": v.decision.get("objecion_que_mas_peso"),
        "orden_enviada": False,  # en la fase 4 no se envían órdenes
        "freno": r.freno.estado,
        "eventos": [f"{e.get('fecha')} {e.get('evento')}" for e in (r.news or {}).get("eventos", [])],
        "tesis_chart": (r.chart or {}).get("tesis"),
        "tesis_news": (r.news or {}).get("tesis"),
        "tesis_strategy": (r.strategy or {}).get("tesis"),
        "confianza_strategy": (r.strategy or {}).get("confianza"),
        "costo_usd": round(ciclo.costo_usd, 4),
    }
    if v.debate is not None:
        c = v.debate.trade["candidato"]
        registro.update(
            {
                "trade_debatido": c["id"],
                "estructura": c["estructura"],
                "vencimiento": c["vencimiento"],
                "strikes": [f"{p['lado']} {p['tipo']} {p['strike']:g}" for p in c["patas"]],
                "credito": c["credito"],
                "perdida_maxima": c["perdida_maxima"],
                "backtest": (v.debate.trade.get("backtest") or {}).get("veredicto"),
                "votos": {
                    m: {"voto": p.get("voto"), "argumento": p.get("postura")}
                    for m, p in v.debate.rondas[-1].items()
                },
            }
        )
    else:
        registro["sin_trade"] = r.motivo_no_trade
    if v.decision.get("orden_final"):
        registro["orden_final"] = v.decision["orden_final"]
        registro["plan_salida"] = v.decision.get("plan_salida")
    return registro


def _decision_sin_trade(r: ResultadoResearch) -> dict:
    return {
        "decision": "RECHAZAR",
        "razon_principal": "No hay trade hoy: " + r.motivo_no_trade,
        "objecion_que_mas_peso": "",
        "cambios_pedidos": [],
        "que_revisar_proximo_ciclo": "",
        "sin_candidatos": True,
    }


async def correr_ciclo(
    broker: Broker,
    modelo: Optional[Modelo],
    subyacente: str = "SPY",
    hoy: Optional[date] = None,
    trades: Optional[list[TradeRegistrado]] = None,
    guardar_en_bitacora: bool = True,
) -> ResultadoCiclo:
    cuenta = broker.cuenta()  # frena si no es paper
    research = Research(broker, modelo, subyacente, hoy=hoy)
    hoy = research.hoy
    ciclo = ResultadoCiclo(subyacente=subyacente, fecha=hoy, con_modelo=modelo is not None)
    informe = generar_informe(broker, subyacente, hoy=hoy)
    costo: list[float] = []
    ajustes: Optional[list[str]] = None
    previo: Optional[ResultadoResearch] = None

    for numero in range(1, MAX_VUELTAS + 1):
        r = await research.correr(informe=informe, trades=trades, ajustes=ajustes, previo=previo)
        if r.no_hay_trade:
            ciclo.vueltas.append(Vuelta(numero, r, None, _decision_sin_trade(r)))
            break
        datos = r.a_dict()
        debate = await debatir(datos, modelo, hoy, informe.precio.atr_14, costo)
        decision = await decidir(datos, debate, modelo, hoy, numero, cuenta.es_paper, costo)
        ciclo.vueltas.append(Vuelta(numero, r, debate, decision))
        if decision["decision"] != "AJUSTAR":
            break
        ajustes, previo = decision.get("cambios_pedidos") or [], r

    ciclo.costo_usd = research.costo + sum(costo)
    if guardar_en_bitacora:
        registrar_decision(registro_bitacora(ciclo))
    return ciclo
