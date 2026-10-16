"""Informe de datos del día para un subyacente, sin agentes.

Junta precio, tendencia, ATR, volatilidad realizada, IV e IV rank, movimiento
esperado por vencimiento, los contratos cerca del delta objetivo con sus
griegas y los eventos del período. Es lo que van a leer los agentes de
research en la fase 3.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional

from broker.base import Broker, ContratoOpcion
from datos.cadena import DIAS_MAX, DIAS_MIN, cadena_en_ventana, hoy_nueva_york
from datos.calculos import atr, media_movil, movimiento_esperado, volatilidad_realizada
from datos.eventos import Evento, cubre_hasta, eventos_entre
from datos.iv import ResumenIV, historial_iv, iv_actual, resumen_iv

DELTA_OBJETIVO = 0.15


@dataclass
class Precio:
    actual: Optional[float]
    cierre_anterior: float
    variacion_pct: Optional[float]
    media_20: Optional[float]
    media_50: Optional[float]
    media_200: Optional[float]
    maximo_52s: float
    minimo_52s: float
    atr_14: Optional[float]
    atr_pct: Optional[float]
    vol_realizada_20: Optional[float]


@dataclass
class Pata:
    simbolo: str
    tipo: str
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    medio: Optional[float]
    delta: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]
    distancia_pct: Optional[float]
    distancia_atr: Optional[float]


@dataclass
class VencimientoInforme:
    fecha: date
    dias: int
    contratos: int
    iv_al_dinero: Optional[float]
    movimiento_iv: Optional[float]
    straddle: Optional[float]
    rango_bajo: Optional[float]
    rango_alto: Optional[float]
    put_objetivo: Optional[Pata]
    call_objetivo: Optional[Pata]


@dataclass
class Informe:
    subyacente: str
    fecha: date
    precio: Precio
    iv: ResumenIV
    vencimientos: list[VencimientoInforme]
    eventos: list[Evento]
    calendario_cubre_hasta: Optional[date]
    avisos: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        def convertir(v):
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: convertir(x) for k, x in v.items()}
            if isinstance(v, list):
                return [convertir(x) for x in v]
            return v

        return convertir(asdict(self))


def _pata(c: ContratoOpcion, precio: Optional[float], atr_14: Optional[float]) -> Pata:
    distancia = None if precio is None else c.strike - precio
    return Pata(
        simbolo=c.simbolo,
        tipo=c.tipo,
        strike=c.strike,
        bid=c.bid,
        ask=c.ask,
        medio=c.medio,
        delta=c.delta,
        theta=c.theta,
        vega=c.vega,
        iv=c.volatilidad_implicita,
        distancia_pct=None if distancia is None else distancia / precio * 100,
        distancia_atr=None if distancia is None or not atr_14 else distancia / atr_14,
    )


def _mas_cerca_del_delta(contratos: list[ContratoOpcion], tipo: str) -> Optional[ContratoOpcion]:
    candidatos = [c for c in contratos if c.tipo == tipo and c.delta is not None and c.medio]
    if not candidatos:
        return None
    return min(candidatos, key=lambda c: abs(abs(c.delta) - DELTA_OBJETIVO))


def _vencimiento(fecha, dias, contratos, precio, atr_14) -> VencimientoInforme:
    iv_dinero = movimiento = straddle = None
    if precio is not None and contratos:
        strike = min({c.strike for c in contratos}, key=lambda k: abs(k - precio))
        al_dinero = [c for c in contratos if c.strike == strike]
        ivs = [c.volatilidad_implicita for c in al_dinero if c.volatilidad_implicita]
        if ivs:
            iv_dinero = sum(ivs) / len(ivs)
            movimiento = movimiento_esperado(precio, iv_dinero, dias)
        medios = [c.medio for c in al_dinero if c.medio is not None]
        if len(medios) == 2:
            straddle = sum(medios)
    put = _mas_cerca_del_delta(contratos, "put")
    call = _mas_cerca_del_delta(contratos, "call")
    return VencimientoInforme(
        fecha=fecha,
        dias=dias,
        contratos=len(contratos),
        iv_al_dinero=iv_dinero,
        movimiento_iv=movimiento,
        straddle=straddle,
        rango_bajo=None if movimiento is None else precio - movimiento,
        rango_alto=None if movimiento is None else precio + movimiento,
        put_objetivo=None if put is None else _pata(put, precio, atr_14),
        call_objetivo=None if call is None else _pata(call, precio, atr_14),
    )


def generar_informe(broker: Broker, subyacente: str, hoy: Optional[date] = None) -> Informe:
    hoy = hoy or hoy_nueva_york()
    avisos: list[str] = []

    barras = broker.barras_diarias(subyacente, hoy - timedelta(days=400), hoy)
    if not barras:
        raise ValueError(f"No hay historial de precios para {subyacente}")
    previas = [b for b in barras if b.fecha < hoy]
    actual = broker.precio_subyacente(subyacente)
    cierre_ant = previas[-1].cierre
    # Para los indicadores se usa el precio de hoy como último cierre.
    cierres = [b.cierre for b in previas] + ([actual] if actual is not None else [])
    ultimo_anio = [b for b in barras if b.fecha >= hoy - timedelta(days=365)]
    atr_14 = atr(previas[-60:])
    referencia = actual if actual is not None else cierre_ant

    precio = Precio(
        actual=actual,
        cierre_anterior=cierre_ant,
        variacion_pct=None if actual is None else (actual / cierre_ant - 1) * 100,
        media_20=media_movil(cierres, 20),
        media_50=media_movil(cierres, 50),
        media_200=media_movil(cierres, 200),
        maximo_52s=max(b.maximo for b in ultimo_anio),
        minimo_52s=min(b.minimo for b in ultimo_anio),
        atr_14=atr_14,
        atr_pct=None if atr_14 is None else atr_14 / referencia * 100,
        vol_realizada_20=volatilidad_realizada(cierres, 20),
    )

    historial = historial_iv(broker, subyacente, hoy)
    cerca_30 = broker.cadena_opciones(subyacente, hoy + timedelta(days=23), hoy + timedelta(days=37))
    iv = resumen_iv(iv_actual(referencia, cerca_30, hoy), historial)
    if iv.dias_historial < 200:
        avisos.append(f"El IV rank usa solo {iv.dias_historial} días de historial.")
    if iv.actual is None:
        avisos.append("No se pudo calcular la IV de hoy (faltan cotizaciones al dinero).")

    vencimientos = [
        _vencimiento(v.fecha, v.dias, v.contratos, referencia, atr_14)
        for v in cadena_en_ventana(broker, subyacente, hoy=hoy)
    ]
    if not vencimientos:
        avisos.append(f"No hay vencimientos entre {DIAS_MIN} y {DIAS_MAX} días.")

    hasta = hoy + timedelta(days=DIAS_MAX)
    cubre = cubre_hasta()
    if cubre is None or cubre < hasta:
        avisos.append(
            "El calendario de eventos no está completo hasta "
            f"{hasta.isoformat()}: agregá las fechas nuevas en datos/eventos.csv."
        )

    return Informe(
        subyacente=subyacente,
        fecha=hoy,
        precio=precio,
        iv=iv,
        vencimientos=vencimientos,
        eventos=eventos_entre(hoy, hasta),
        calendario_cubre_hasta=cubre,
        avisos=avisos,
    )


def _n(v: Optional[float], dec: int = 2) -> str:
    return "-" if v is None else f"{v:,.{dec}f}"


def _pct(v: Optional[float], dec: int = 1) -> str:
    return "-" if v is None else f"{v * 100:.{dec}f}%"


def _signo(v: Optional[float], dec: int = 2) -> str:
    return "-" if v is None else f"{v:+.{dec}f}"


def formatear_informe(inf: Informe) -> str:
    p, iv = inf.precio, inf.iv
    ref = p.actual if p.actual is not None else p.cierre_anterior

    def vs(media):
        return "-" if media is None else f"{_n(media)} ({_signo((ref / media - 1) * 100, 1)}%)"

    lineas = [
        f"INFORME DE DATOS {inf.subyacente} · {inf.fecha.isoformat()}",
        "",
        "PRECIO Y TENDENCIA",
        f"  Precio:              {_n(p.actual)} ({_signo(p.variacion_pct)}% vs cierre anterior {_n(p.cierre_anterior)})",
        f"  Media 20 / 50 / 200: {vs(p.media_20)} / {vs(p.media_50)} / {vs(p.media_200)}",
        f"  Rango 52 semanas:    {_n(p.minimo_52s)} a {_n(p.maximo_52s)}",
        f"  ATR 14:              {_n(p.atr_14)} ({_n(p.atr_pct)}% del precio)",
        f"  Vol. realizada 20d:  {_pct(p.vol_realizada_20)}",
        "",
        "VOLATILIDAD IMPLÍCITA (al dinero, ~30 días)",
        f"  IV actual:           {_pct(iv.actual)}"
        + ("" if iv.vencimiento is None else f" (vence {iv.vencimiento.isoformat()}, strike {_n(iv.strike)})"),
        f"  IV rank:             {_n(iv.rank, 0)}   ·   IV percentil: {_n(iv.percentil, 0)}",
        f"  Rango del año:       {_pct(iv.minimo)} a {_pct(iv.maximo)} ({iv.dias_historial} días)",
    ]
    if iv.actual and p.vol_realizada_20:
        lineas.append(f"  IV / vol. realizada: {iv.actual / p.vol_realizada_20:.2f}")

    lineas += ["", f"VENCIMIENTOS DE {DIAS_MIN} A {DIAS_MAX} DÍAS"]
    if inf.vencimientos:
        lineas.append("  Vencimiento   Días   IV ATM   Mov. esp.   Straddle   Rango ±1σ")
        for v in inf.vencimientos:
            rango = "-" if v.rango_bajo is None else f"{_n(v.rango_bajo)} a {_n(v.rango_alto)}"
            lineas.append(
                f"  {v.fecha.isoformat()}  {v.dias:>4}   {_pct(v.iv_al_dinero):>6}   {_n(v.movimiento_iv):>9}"
                f"   {_n(v.straddle):>8}   {rango}"
            )
        lineas += ["", f"  Contratos con delta más cerca de {DELTA_OBJETIVO:.2f}:"]
        lineas.append("  Vencimiento  tipo   strike   medio    delta    theta    vega    IV    distancia")
        for v in inf.vencimientos:
            for pata in (v.put_objetivo, v.call_objetivo):
                if pata is None:
                    continue
                lineas.append(
                    f"  {v.fecha.isoformat()}  {pata.tipo:<4} {pata.strike:>8.2f} {_n(pata.medio):>7}"
                    f"  {_n(pata.delta, 3):>7}  {_n(pata.theta, 3):>7} {_n(pata.vega, 3):>7}"
                    f"  {_pct(pata.iv):>6}  {_signo(pata.distancia_pct, 1)}% / {_signo(pata.distancia_atr, 1)} ATR"
                )
    else:
        lineas.append("  No hay vencimientos en la ventana.")

    lineas += ["", f"EVENTOS HASTA {(inf.fecha + timedelta(days=DIAS_MAX)).isoformat()}"]
    if inf.eventos:
        for e in inf.eventos:
            hora = f" {e.hora_et} ET" if e.hora_et else ""
            lineas.append(f"  {e.fecha.isoformat()}{hora}  {e.evento}")
    else:
        lineas.append("  Ninguno cargado.")

    if inf.avisos:
        lineas += ["", "AVISOS"]
        lineas += [f"  - {a}" for a in inf.avisos]
    return "\n".join(lineas)
