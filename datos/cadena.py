"""Cadena de opciones en la ventana de la estrategia (7 a 21 días)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from broker.base import Broker, ContratoOpcion

DIAS_MIN = 7
DIAS_MAX = 21


def hoy_nueva_york() -> date:
    """Fecha de hoy en Nueva York, que es la que cuenta para los vencimientos."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        return (datetime.utcnow() - timedelta(hours=4)).date()


@dataclass(frozen=True)
class Vencimiento:
    fecha: date
    dias: int
    contratos: list[ContratoOpcion]


def cadena_en_ventana(
    broker: Broker,
    subyacente: str,
    dias_min: int = DIAS_MIN,
    dias_max: int = DIAS_MAX,
    hoy: Optional[date] = None,
) -> list[Vencimiento]:
    hoy = hoy or hoy_nueva_york()
    desde = hoy + timedelta(days=dias_min)
    hasta = hoy + timedelta(days=dias_max)
    contratos = broker.cadena_opciones(subyacente, desde, hasta)

    por_fecha: dict[date, list[ContratoOpcion]] = {}
    for c in contratos:
        if desde <= c.vencimiento <= hasta:
            por_fecha.setdefault(c.vencimiento, []).append(c)

    return [
        Vencimiento(fecha=f, dias=(f - hoy).days, contratos=sorted(cs, key=lambda c: (c.tipo, c.strike)))
        for f, cs in sorted(por_fecha.items())
    ]


def filtrar_por_delta(
    contratos: list[ContratoOpcion], delta_min: float, delta_max: float
) -> list[ContratoOpcion]:
    """Contratos cuyo |delta| está entre los dos valores. Sin delta, quedan afuera."""
    return [
        c for c in contratos if c.delta is not None and delta_min <= abs(c.delta) <= delta_max
    ]
