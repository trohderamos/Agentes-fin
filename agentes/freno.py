"""Freno por drawdown (skill freno-drawdown-opciones).

Regla de entrada: decide si hoy se puede abrir riesgo nuevo. No cierra nada.
Si falta un dato o los datos no cuadran, el resultado es DETENIDO.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from agentes import config
from agentes.bitacora import TradeRegistrado
from broker.base import Cuenta, Posicion, PuntoPatrimonio

PERMITIDO = "PERMITIDO"
ENFRIAMIENTO = "ENFRIAMIENTO"
DETENIDO = "DETENIDO"
_ORDEN = {PERMITIDO: 0, ENFRIAMIENTO: 1, DETENIDO: 2}


@dataclass
class ResultadoFreno:
    estado: str
    fecha: date
    motivos: list[str] = field(default_factory=list)
    reevaluar: Optional[str] = None
    resultado_dia: Optional[float] = None
    resultado_semana: Optional[float] = None
    resultado_mes: Optional[float] = None
    racha_perdedora: int = 0

    @property
    def permite(self) -> bool:
        return self.estado == PERMITIDO

    def a_dict(self) -> dict:
        d = asdict(self)
        d["fecha"] = self.fecha.isoformat()
        return d


def _base(historial: list[PuntoPatrimonio], inicio: date) -> Optional[float]:
    """Patrimonio al cierre del último día anterior al período.

    Si la cuenta es más nueva que el período, el primer valor que haya.
    """
    previos = [p for p in historial if p.fecha < inicio]
    if previos:
        return previos[-1].patrimonio
    return historial[0].patrimonio if historial else None


def evaluar_freno(
    hoy: date,
    cuenta: Cuenta,
    historial: list[PuntoPatrimonio],
    posiciones: list[Posicion],
    trades: list[TradeRegistrado],
) -> ResultadoFreno:
    r = ResultadoFreno(estado=PERMITIDO, fecha=hoy)

    def disparar(estado: str, motivo: str, reevaluar: str) -> None:
        r.motivos.append(motivo)
        if _ORDEN[estado] > _ORDEN[r.estado]:
            r.estado, r.reevaluar = estado, reevaluar
        elif _ORDEN[estado] == _ORDEN[r.estado] and reevaluar[:1].isdigit():
            # Con dos reglas iguales manda la que frena por más tiempo (una
            # fecha concreta es más larga que "próxima sesión").
            actual = r.reevaluar or ""
            r.reevaluar = max(actual, reevaluar) if actual[:1].isdigit() else reevaluar

    # Datos que tienen que estar.
    if cuenta.patrimonio <= 0 or cuenta.patrimonio_cierre_anterior is None:
        disparar(DETENIDO, "Falta el patrimonio de la cuenta o el del cierre anterior.", "cuando el bróker lo informe")
        return r
    if not historial:
        disparar(DETENIDO, "Falta el historial de patrimonio de la cuenta.", "cuando el bróker lo informe")
        return r

    # Datos que tienen que cuadrar: toda opción abierta en el bróker tiene que
    # estar registrada en la bitácora.
    abiertas_bitacora = {s for t in trades if t.estado == "abierto" for s in t.simbolos}
    sueltas = sorted(
        p.simbolo for p in posiciones if p.clase == "us_option" and p.simbolo not in abiertas_bitacora
    )
    if sueltas:
        disparar(
            DETENIDO,
            "Hay opciones abiertas en la cuenta que no están en la bitácora: "
            + ", ".join(sueltas)
            + ". Los datos no cuadran; hay que revisarlo con el usuario.",
            "cuando la bitácora y la cuenta coincidan",
        )

    actual = cuenta.patrimonio
    r.resultado_dia = actual / cuenta.patrimonio_cierre_anterior - 1
    lunes = hoy - timedelta(days=hoy.weekday())
    base_semana = _base(historial, lunes)
    base_mes = _base(historial, hoy.replace(day=1))
    r.resultado_semana = None if not base_semana else actual / base_semana - 1
    r.resultado_mes = None if not base_mes else actual / base_mes - 1

    if r.resultado_dia < -config.FRENO_DIA:
        disparar(
            DETENIDO,
            f"Pérdida del día {r.resultado_dia:.2%} (límite {config.FRENO_DIA:.0%}).",
            "próxima sesión",
        )
    if r.resultado_semana is not None and r.resultado_semana < -config.FRENO_SEMANA:
        disparar(
            DETENIDO,
            f"Pérdida de la semana {r.resultado_semana:.2%} (límite {config.FRENO_SEMANA:.0%}).",
            (lunes + timedelta(days=7)).isoformat(),
        )
    if r.resultado_mes is not None and r.resultado_mes < -config.FRENO_MES:
        siguiente = (hoy.replace(day=28) + timedelta(days=4)).replace(day=1)
        disparar(
            DETENIDO,
            f"Pérdida del mes {r.resultado_mes:.2%} (límite {config.FRENO_MES:.0%}). "
            "Hay que revisar la estrategia con el usuario.",
            siguiente.isoformat(),
        )

    cerrados = sorted(
        (t for t in trades if t.estado == "cerrado" and t.cerrado_el is not None),
        key=lambda t: t.cerrado_el,
    )
    racha = 0
    for t in reversed(cerrados):
        if t.resultado is None:
            disparar(DETENIDO, f"El trade {t.id} está cerrado sin resultado en la bitácora.", "cuando se complete")
            break
        if t.resultado < 0:
            racha += 1
        else:
            break
    r.racha_perdedora = racha
    if racha >= config.FRENO_RACHA:
        ultimo = cerrados[-1].cerrado_el
        if (hoy - ultimo).days < 2:
            disparar(
                ENFRIAMIENTO,
                f"{racha} trades perdedores seguidos (el último cerró el {ultimo.isoformat()}).",
                (ultimo + timedelta(days=2)).isoformat(),
            )

    if not r.motivos:
        r.motivos.append("Ninguna regla disparó.")
    return r
