"""¿La regla nueva de eventos sirve? Comparación contra la regla vieja.

El 27 oct 2026 el usuario cambió la regla: antes un evento dentro del plazo
descartaba el trade; ahora el evento se mide y se opera si el strike vendido
queda más lejos que el movimiento típico del evento (ver agentes/eventos.py).

Este módulo lee la bitácora y arma la comparación: cuántos trades dejó tomar
cada regla, cómo salieron, y cuántas veces un evento fue el motivo de no operar.
Se escribe en bitacora/regla-eventos.md y entra en el repaso de los viernes, así
el usuario no tiene que acordarse de que hizo el cambio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from agentes import config
from agentes.bitacora import TradeRegistrado, leer_decisiones, leer_trades

ARCHIVO = Path(__file__).parent.parent / "bitacora" / "regla-eventos.md"

# Desde cuándo rige la regla nueva (la fecha en que el usuario la pidió).
DESDE = date(2026, 10, 27)

MODO_VIEJO = "vetar"
NOMBRES = {
    "operar": "Regla nueva: el evento se mide y se opera",
    MODO_VIEJO: "Regla vieja: el evento descarta el trade",
}


def modo_de(decision: dict) -> str:
    """Con qué regla se tomó esta decisión. Las viejas no tienen el campo."""
    modo = ((decision.get("regla_eventos") or {}).get("modo") or "").strip()
    return modo if modo in NOMBRES else MODO_VIEJO


def freno_por_evento(decision: dict) -> bool:
    """Si el motivo de no operar fue un evento."""
    if decision.get("decision") == "APROBAR":
        return False
    texto = " ".join(
        str(decision.get(k) or "") for k in ("sin_trade", "razon", "objecion_que_mas_peso", "motivo_no_trade")
    ).lower()
    return "evento" in texto or "earnings" in texto or "fed" in texto or "cpi" in texto


@dataclass
class Grupo:
    modo: str
    ciclos: int = 0
    aprobados: int = 0
    frenados_por_evento: int = 0
    trades: list[dict] = field(default_factory=list)

    @property
    def cerrados(self) -> list[dict]:
        return [t for t in self.trades if t.get("resultado") is not None]

    @property
    def resultado(self) -> float:
        return round(sum(t["resultado"] for t in self.cerrados), 2)

    @property
    def ganadores(self) -> int:
        return sum(1 for t in self.cerrados if t["resultado"] > 0)

    @property
    def peor(self) -> Optional[float]:
        return min((t["resultado"] for t in self.cerrados), default=None)

    def a_dict(self) -> dict:
        return {
            "modo": self.modo,
            "ciclos": self.ciclos,
            "aprobados": self.aprobados,
            "frenados_por_evento": self.frenados_por_evento,
            "trades": self.trades,
            "cerrados": len(self.cerrados),
            "resultado_usd": self.resultado,
            "ganadores": self.ganadores,
            "peor_trade_usd": self.peor,
        }


def comparar(
    decisiones: Optional[list[dict]] = None,
    trades: Optional[list[TradeRegistrado]] = None,
) -> dict:
    decisiones = leer_decisiones() if decisiones is None else decisiones
    trades = leer_trades() if trades is None else trades
    por_id = {t.id: t for t in trades}
    grupos = {m: Grupo(modo=m) for m in NOMBRES}
    for d in decisiones:
        g = grupos[modo_de(d)]
        g.ciclos += 1
        if freno_por_evento(d):
            g.frenados_por_evento += 1
        if d.get("decision") != "APROBAR":
            continue
        g.aprobados += 1
        tid = (d.get("orden_final") or {}).get("id") or d.get("trade_debatido") or ""
        t = por_id.get(str(tid))
        colchon = d.get("colchon_eventos") or {}
        g.trades.append(
            {
                "id": tid,
                "fecha": d.get("fecha"),
                "subyacente": d.get("subyacente"),
                "estructura": d.get("estructura"),
                "vencimiento": d.get("vencimiento"),
                "se_abrio": t is not None,
                "estado": None if t is None else t.estado,
                "resultado": None if t is None else t.resultado,
                "eventos_en_el_plazo": [f"{e.get('fecha')} {e.get('evento')}" for e in colchon.get("eventos", [])],
                "colchon_pct": colchon.get("colchon_pct"),
                "distancia_strike_pct": colchon.get("distancia_strike_pct"),
                "colchon_estres_pct": colchon.get("colchon_estres_pct"),
                "razon": d.get("razon"),
            }
        )
    return {
        "desde_que_rige": DESDE.isoformat(),
        "modo_vigente": config.MODO_EVENTOS,
        "percentil_colchon": config.PERCENTIL_COLCHON,
        "factor_contratos_evento": config.FACTOR_CONTRATOS_EVENTO,
        "grupos": {m: g.a_dict() for m, g in grupos.items()},
    }


def _pct(valor: Optional[float]) -> str:
    return "-" if valor is None else f"{valor:.2f}%"


def _usd(valor: Optional[float]) -> str:
    return "-" if valor is None else f"{valor:+,.2f} USD"


def _linea_grupo(g: dict) -> list[str]:
    lineas = [
        f"- Ciclos decididos: {g['ciclos']}",
        f"- Trades aprobados: {g['aprobados']} (abiertos de verdad: {sum(1 for t in g['trades'] if t['se_abrio'])})",
        f"- Ciclos sin trade por un evento: {g['frenados_por_evento']}",
        f"- Trades cerrados: {g['cerrados']}, resultado {g['resultado_usd']:+,.2f} USD"
        + (f", ganadores {g['ganadores']} de {g['cerrados']}" if g["cerrados"] else "")
        + (f", peor {g['peor_trade_usd']:+,.2f} USD" if g["peor_trade_usd"] is not None else ""),
    ]
    return lineas


def formatear(c: dict) -> str:
    """El informe que lee el usuario. Arranca recordándole qué cambió y cuándo."""
    lineas = [
        "# Regla de eventos: qué cambió y cómo viene",
        "",
        f"El {c['desde_que_rige']} cambiaste la regla de eventos. Antes, cualquier Fed, CPI, informe de empleo "
        "o balance dentro del plazo descartaba el trade sin más. Ahora el evento se mide: se calcula cuánto se "
        "movió el activo en los eventos parecidos del pasado y se compara con la distancia del precio al strike "
        "vendido. Si el strike está más lejos, se opera; si está más cerca, se descarta por la cuenta.",
        "",
        f"Está vigente el modo **{c['modo_vigente']}**, exigiendo el percentil {c['percentil_colchon']:g} del "
        f"movimiento histórico y con el tamaño multiplicado por {c['factor_contratos_evento']:g} cuando hay un "
        "macro en el plazo.",
        "",
        "Para volver atrás alcanza con poner `MODO_EVENTOS = \"vetar\"` en agentes/config.py (o la variable de "
        "entorno `AGENTES_MODO_EVENTOS`). Para ir más conservador sin volver atrás, subí `PERCENTIL_COLCHON` de "
        "50 a 75 o 90; para ir más agresivo, bajalo.",
        "",
    ]
    for modo, g in c["grupos"].items():
        lineas += [f"## {NOMBRES[modo]}", ""]
        if not g["ciclos"]:
            lineas += ["Todavía no hay ciclos decididos con esta regla.", ""]
            continue
        lineas += _linea_grupo(g) + [""]
        if g["trades"]:
            lineas += [
                "| Fecha | Activo | Trade | Eventos en el plazo | Colchón | Distancia al strike | Estado | Resultado |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for t in g["trades"]:
                estado = t["estado"] or ("no se llenó" if not t["se_abrio"] else "-")
                lineas.append(
                    f"| {t['fecha']} | {t['subyacente']} | {t['id']} | "
                    f"{'; '.join(t['eventos_en_el_plazo']) or '-'} | "
                    f"{_pct(t['colchon_pct'])} | {_pct(t['distancia_strike_pct'])} | "
                    f"{estado} | {_usd(t['resultado'])} |"
                )
            lineas.append("")
    return "\n".join(lineas).strip() + "\n"


def escribir(archivo: Path = ARCHIVO, c: Optional[dict] = None) -> Path:
    c = comparar() if c is None else c
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(formatear(c), encoding="utf-8")
    return archivo
