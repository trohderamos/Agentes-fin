"""Backtest aproximado de spreads de crédito e iron condors (herramienta compartida).

Es una SIMULACIÓN APROXIMADA, no un backtest con precios reales de opciones:
- La IV de cada día se aproxima como la volatilidad realizada de los 20 días
  previos por un factor fijo (config.FACTOR_IV_REALIZADA). Alpaca no da IV
  histórica de varios años ni precios de opciones viejos suficientes.
- Los precios de las opciones salen de Black-Scholes con esa IV.
- No hay skew: en la realidad los puts lejanos cuestan más, así que el crédito
  de los spreads de puts está subestimado y el de calls sobreestimado.

Reglas fijadas antes de mirar resultados (skill backtest-opciones-primas):
- Entrada: un trade por vez; se abre el día hábil siguiente al cierre del
  anterior. Strike vendido con el delta pedido, ala al ancho pedido como
  porcentaje del precio.
- Salida: al vencimiento, a precio de cierre. Sin gestión anticipada.
- Costos: comisión por pata al abrir y slippage por pata (abrir y cerrar si
  queda en el dinero).
- Sin look-ahead: la IV y los strikes usan solo datos hasta el día de entrada.
- Ajuste y prueba: primer 70% y último 30% del período por separado.
- Estrés: slippage doble, y delta vendido 20% más alto y más bajo.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional

from agentes import config
from broker.base import Barra
from datos.calculos import precio_bs, volatilidad_realizada

CRISIS = {
    "Volmageddon (feb 2018)": (date(2018, 1, 26), date(2018, 4, 30)),
    "COVID (feb-abr 2020)": (date(2020, 2, 19), date(2020, 4, 30)),
    "Mercado bajista 2022": (date(2022, 1, 3), date(2022, 10, 31)),
    "Aranceles (abr 2025)": (date(2025, 3, 25), date(2025, 5, 15)),
}


@dataclass
class TradeSimulado:
    entrada: date
    salida: date
    precio_entrada: float
    precio_salida: float
    credito: float
    resultado: float  # por contrato, en dólares, neto de costos
    perdida_maxima: float


@dataclass
class Metricas:
    trades: int
    tasa_acierto: Optional[float]
    resultado_medio: Optional[float]
    ganancia_media: Optional[float]
    perdida_media: Optional[float]
    peor_trade: Optional[float]
    peor_drawdown: Optional[float]
    resultado_total: float


@dataclass
class ResultadoBacktest:
    estructura: str
    dias: int
    delta: float
    ancho_pct: float
    desde: date
    hasta: date
    total: Metricas
    ajuste: Metricas
    prueba: Metricas
    por_anio: dict[str, Metricas]
    crisis: dict[str, Metricas]
    estres: dict[str, Metricas]
    veredicto: str
    razones: list[str] = field(default_factory=list)
    aviso: str = "Simulación aproximada con precios Black-Scholes, IV = vol. realizada x factor y sin skew."

    def a_dict(self) -> dict:
        def conv(v):
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            if isinstance(v, list):
                return [conv(x) for x in v]
            return v

        return conv(asdict(self))


def _ninv(p: float) -> float:
    """Inversa de la normal estándar por bisección (suficiente acá)."""
    bajo, alto = -10.0, 10.0
    for _ in range(100):
        medio = (bajo + alto) / 2
        if 0.5 * (1 + math.erf(medio / math.sqrt(2))) < p:
            bajo = medio
        else:
            alto = medio
    return (bajo + alto) / 2


def strike_por_delta(tipo: str, s: float, t: float, iv: float, delta: float) -> float:
    """Strike cuyo delta (sin dividendos ni tasa) es el pedido, en valor absoluto."""
    if tipo == "put":
        d1 = _ninv(1 - delta)  # delta put = N(d1) - 1 = -delta
    else:
        d1 = _ninv(delta)
    return s * math.exp(-(d1 * iv * math.sqrt(t)) + iv * iv / 2 * t)


def _redondear(k: float) -> float:
    return float(round(k))


def _valor_vencimiento(tipo: str, s: float, vendido: float, comprado: float) -> float:
    """Lo que cuesta cerrar el spread al vencimiento, por acción."""
    if tipo == "put":
        return max(vendido - s, 0) - max(comprado - s, 0)
    return max(s - vendido, 0) - max(s - comprado, 0)


def simular(
    barras: list[Barra],
    estructura: str,
    dias: int,
    delta: float,
    ancho_pct: float,
    slippage: float = config.SLIPPAGE_PATA,
    comision: float = config.COMISION_PATA,
) -> list[TradeSimulado]:
    cierres = [b.cierre for b in barras]
    habiles = max(1, round(dias * 5 / 7))
    lados = {"spread_put": ["put"], "spread_call": ["call"], "iron_condor": ["put", "call"]}[estructura]
    trades: list[TradeSimulado] = []
    i = 21
    while i + habiles < len(barras):
        s = cierres[i]
        vol = volatilidad_realizada(cierres[: i + 1], 20)
        if not vol:
            i += 1
            continue
        iv = max(vol * config.FACTOR_IV_REALIZADA, 0.08)
        salida = i + habiles
        t = (barras[salida].fecha - barras[i].fecha).days / 365
        ancho = max(1.0, round(s * ancho_pct))
        credito = 0.0
        costo_cierre = 0.0
        patas = 0
        for tipo in lados:
            k_v = _redondear(strike_por_delta(tipo, s, t, iv, delta))
            k_c = k_v - ancho if tipo == "put" else k_v + ancho
            credito += precio_bs(tipo, s, k_v, t, iv) - precio_bs(tipo, s, k_c, t, iv)
            valor = _valor_vencimiento(tipo, cierres[salida], k_v, k_c)
            costo_cierre += valor
            patas += 2
            if valor > 0:  # hay que cerrar o se asigna: se paga slippage otra vez
                costo_cierre += 2 * slippage
        credito_neto = credito - patas * slippage
        resultado = (credito_neto - costo_cierre) * 100 - patas * comision
        trades.append(
            TradeSimulado(
                entrada=barras[i].fecha,
                salida=barras[salida].fecha,
                precio_entrada=s,
                precio_salida=cierres[salida],
                credito=round(credito_neto, 4),
                resultado=round(resultado, 2),
                perdida_maxima=round((ancho - credito_neto) * 100 + patas * comision, 2),
            )
        )
        i = salida + 1
    return trades


def metricas(trades: list[TradeSimulado]) -> Metricas:
    if not trades:
        return Metricas(0, None, None, None, None, None, None, 0.0)
    res = [t.resultado for t in trades]
    ganadores = [r for r in res if r > 0]
    perdedores = [r for r in res if r <= 0]
    acumulado = pico = peor = 0.0
    for r in res:
        acumulado += r
        pico = max(pico, acumulado)
        peor = min(peor, acumulado - pico)
    return Metricas(
        trades=len(res),
        tasa_acierto=round(len(ganadores) / len(res), 3),
        resultado_medio=round(statistics.mean(res), 2),
        ganancia_media=round(statistics.mean(ganadores), 2) if ganadores else None,
        perdida_media=round(statistics.mean(perdedores), 2) if perdedores else None,
        peor_trade=round(min(res), 2),
        peor_drawdown=round(peor, 2),
        resultado_total=round(sum(res), 2),
    )


def correr_backtest(
    barras: list[Barra], estructura: str, dias: int, delta: float = config.DELTA_OBJETIVO, ancho_pct: float = 0.0075
) -> ResultadoBacktest:
    trades = simular(barras, estructura, dias, delta, ancho_pct)
    corte = int(len(trades) * 0.7)
    por_anio: dict[str, list[TradeSimulado]] = {}
    for t in trades:
        por_anio.setdefault(str(t.entrada.year), []).append(t)
    crisis = {
        nombre: metricas([t for t in trades if a <= t.entrada <= b or a <= t.salida <= b])
        for nombre, (a, b) in CRISIS.items()
        if barras and barras[0].fecha <= b and a <= barras[-1].fecha
    }
    estres = {
        "slippage doble": metricas(simular(barras, estructura, dias, delta, ancho_pct, slippage=2 * config.SLIPPAGE_PATA)),
        "delta +20%": metricas(simular(barras, estructura, dias, delta * 1.2, ancho_pct)),
        "delta -20%": metricas(simular(barras, estructura, dias, delta * 0.8, ancho_pct)),
    }
    total, ajuste, prueba = metricas(trades), metricas(trades[:corte]), metricas(trades[corte:])
    veredicto, razones = _veredicto(total, ajuste, prueba, estres)
    return ResultadoBacktest(
        estructura=estructura,
        dias=dias,
        delta=delta,
        ancho_pct=ancho_pct,
        desde=barras[0].fecha if barras else date.today(),
        hasta=barras[-1].fecha if barras else date.today(),
        total=total,
        ajuste=ajuste,
        prueba=prueba,
        por_anio={k: metricas(v) for k, v in sorted(por_anio.items())},
        crisis=crisis,
        estres=estres,
        veredicto=veredicto,
        razones=razones,
    )


def _veredicto(total: Metricas, ajuste: Metricas, prueba: Metricas, estres: dict[str, Metricas]) -> tuple[str, list[str]]:
    razones = []
    if total.trades < 30:
        razones.append(f"Muestra chica: {total.trades} trades.")
    if not total.trades or (total.resultado_medio or 0) <= 0:
        razones.append(f"Resultado medio por trade {total.resultado_medio} USD: no gana.")
        return "ABANDONAR", razones
    razones.append(f"Resultado medio por trade {total.resultado_medio} USD con {total.trades} trades.")
    fallas = [n for n, m in estres.items() if (m.resultado_medio or 0) <= 0]
    if (prueba.resultado_medio or 0) <= 0:
        razones.append("Gana en el período de ajuste pero no en el de prueba.")
        return "REFINAR", razones
    if fallas:
        razones.append("Deja de ganar con: " + ", ".join(fallas) + ".")
        return "REFINAR", razones
    if (total.tasa_acierto or 0) >= 0.9 and total.peor_trade is not None and total.peor_trade > -total.resultado_medio * 5:
        razones.append("Tasa de acierto muy alta sin pérdidas grandes: señal de alarma.")
        return "REFINAR", razones
    razones.append("Aguanta el período de prueba y las pruebas de estrés.")
    if total.trades < 30:
        return "REFINAR", razones
    return "DESPLEGAR", razones
