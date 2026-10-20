"""Límites duros de Risk, calculados en código.

El agente Risk escribe su informe y puede sumar vetos propios, pero no puede
levantar uno de estos: un veto no se negocia.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from agentes import config
from agentes.bitacora import TradeRegistrado
from agentes.estructuras import Candidato
from agentes.freno import ResultadoFreno
from datos.eventos import Evento


@dataclass
class Revision:
    id: str
    vetado: bool
    vetos: list[str] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)
    contratos_max: int = 0  # cuántos contratos entran en el 2% del capital
    riesgo_pct: float = 0.0  # pérdida máxima de 1 contrato sobre el capital
    riesgo_abierto_pct: float = 0.0
    perdida_estres_pct: float = 0.0  # con delta y gamma, movimiento de 3% en contra
    vega_punto_pct: float = 0.0

    def a_dict(self) -> dict:
        return asdict(self)


def eventos_en_plazo(eventos: list[Evento], hoy: date, vencimiento: date) -> list[Evento]:
    return [e for e in eventos if hoy <= e.fecha <= vencimiento]


def eventos_que_descartan(eventos: list[Evento], hoy: date, vencimiento: date) -> list[Evento]:
    return [e for e in eventos_en_plazo(eventos, hoy, vencimiento) if e.tipo in config.TIPOS_EVENTO_GRANDE]


def revisar(
    c: Candidato,
    hoy: date,
    capital: float,
    precio: float,
    freno: ResultadoFreno,
    abiertos: list[TradeRegistrado],
    eventos: list[Evento],
    eventos_news: Optional[list[dict]] = None,
) -> Revision:
    """Revisa un candidato de 1 contrato contra todos los límites."""
    r = Revision(id=c.id, vetado=False)

    def veto(motivo: str) -> None:
        r.vetos.append(motivo)
        r.vetado = True

    if capital <= 0:
        veto("No hay capital informado.")
        return r

    # Freno por drawdown.
    if not freno.permite:
        veto(f"Freno por drawdown: {freno.estado} ({'; '.join(freno.motivos)}).")

    # Reglas de estilo (Strategy no puede salirse de ellas).
    if c.estructura not in config.ESTRUCTURAS_PERMITIDAS:
        veto(f"Estructura no permitida: {c.estructura}.")
    if not config.DIAS_MIN <= (c.vencimiento - hoy).days <= config.DIAS_MAX:
        veto(f"Vence en {(c.vencimiento - hoy).days} días, fuera de {config.DIAS_MIN} a {config.DIAS_MAX}.")
    for p in c.vendidos:
        if not config.DELTA_VENDIDO_MIN <= abs(p.delta) <= config.DELTA_VENDIDO_MAX:
            veto(f"El {p.tipo} vendido {p.strike:g} tiene delta {p.delta:.3f}, fuera de 0.10 a 0.20.")
    compras = {p.tipo for p in c.patas if p.lado == "compra"}
    if any(p.tipo not in compras for p in c.vendidos):
        veto("Hay una pata vendida sin protección: riesgo ilimitado.")

    # Eventos dentro del plazo.
    descartan = config.eventos_descartan(hoy)
    for e in eventos_que_descartan(eventos, hoy, c.vencimiento):
        if descartan:
            veto(f"Evento grande dentro del plazo: {e.fecha.isoformat()} {e.evento}.")
        else:
            r.alertas.append(f"Evento grande dentro del plazo (no descarta en la semana de prueba): {e.fecha.isoformat()} {e.evento}.")
    for e in eventos_news or []:
        try:
            fecha = date.fromisoformat(str(e.get("fecha", ""))[:10])
        except ValueError:
            continue
        if hoy <= fecha <= c.vencimiento and e.get("impacto") == "alto":
            if not descartan:
                r.alertas.append(
                    f"News marcó un evento de impacto alto (no descarta en la semana de prueba): {fecha.isoformat()} {e.get('evento')}."
                )
            else:
                veto(f"News marcó un evento de impacto alto dentro del plazo: {fecha.isoformat()} {e.get('evento')}.")
    for e in eventos_en_plazo(eventos, hoy, c.vencimiento):
        if e.tipo == "dividendo" and any(p.tipo == "call" for p in c.vendidos):
            r.alertas.append(f"Ex-dividendo el {e.fecha.isoformat()} con una call vendida: riesgo de asignación anticipada.")

    # Tamaño y riesgo.
    r.riesgo_pct = c.perdida_maxima / capital
    r.contratos_max = int(config.PERDIDA_MAX_TRADE * capital // c.perdida_maxima) if c.perdida_maxima > 0 else 0
    if r.contratos_max < 1:
        veto(f"Un contrato arriesga {r.riesgo_pct:.2%} del capital (límite {config.PERDIDA_MAX_TRADE:.0%} por trade).")

    abierto = sum(t.perdida_maxima for t in abiertos if t.estado == "abierto")
    r.riesgo_abierto_pct = (abierto + c.perdida_maxima) / capital
    if r.riesgo_abierto_pct > config.RIESGO_TOTAL_MAX:
        veto(f"El riesgo abierto quedaría en {r.riesgo_abierto_pct:.2%} (límite {config.RIESGO_TOTAL_MAX:.0%}).")
    mismo = sum(t.perdida_maxima for t in abiertos if t.estado == "abierto" and t.subyacente == c.subyacente)
    tope_subyacente = config.RIESGO_SUBYACENTE_MAX * config.RIESGO_TOTAL_MAX * capital
    if mismo + c.perdida_maxima > tope_subyacente:
        veto(
            f"{c.subyacente} quedaría con {mismo + c.perdida_maxima:,.0f} USD de riesgo "
            f"(límite {tope_subyacente:,.0f}: {config.RIESGO_SUBYACENTE_MAX:.0%} del riesgo total permitido)."
        )

    # Griegas del portafolio con este trade sumado (1 contrato).
    abiertos_ok = [t for t in abiertos if t.estado == "abierto"]
    delta = c.delta + sum(t.delta for t in abiertos_ok)
    vega = c.vega + sum(t.vega for t in abiertos_ok)
    gamma = c.gamma + sum(t.gamma for t in abiertos_ok)
    movimiento = config.MOVIMIENTO_ESTRES * precio
    perdida_estres = max(-(delta * m + 0.5 * gamma * m * m) for m in (movimiento, -movimiento))
    r.perdida_estres_pct = max(perdida_estres, 0.0) / capital
    r.vega_punto_pct = abs(vega) / capital
    if r.perdida_estres_pct > config.PERDIDA_ESTRES_MAX:
        veto(
            f"Con un movimiento de {config.MOVIMIENTO_ESTRES:.0%} en contra, el portafolio perdería aprox. "
            f"{r.perdida_estres_pct:.2%} del capital por delta y gamma (límite {config.PERDIDA_ESTRES_MAX:.1%})."
        )
    if r.vega_punto_pct > config.VEGA_PUNTO_MAX:
        veto(f"Vega del portafolio {r.vega_punto_pct:.2%} del capital por punto de IV (límite {config.VEGA_PUNTO_MAX:.1%}).")

    # Liquidez mínima: si la diferencia entre crédito medio y natural se come
    # más de la mitad del crédito, el precio no es confiable.
    if c.credito > 0 and (c.credito - c.credito_natural) > c.credito * 0.5:
        r.alertas.append(
            f"Spread de compra-venta ancho: crédito medio {c.credito:.2f} vs natural {c.credito_natural:.2f}."
        )
    if c.fuera_movimiento_esperado is False:
        r.alertas.append("Algún strike vendido queda dentro del movimiento esperado hasta el vencimiento.")
    return r
