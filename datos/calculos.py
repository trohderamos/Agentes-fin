"""Cálculos sobre precios y opciones. Funciones puras, sin red."""

from __future__ import annotations

import math
from typing import Optional, Sequence

from broker.base import Barra

DIAS_HABILES_ANIO = 252

# Supuestos para Black-Scholes. Solo afectan el cálculo propio de la IV histórica;
# en la IV promedio de put y call su efecto casi se cancela.
TASA_LIBRE_RIESGO = 0.04
DIVIDENDO_SPY = 0.011


def media_movil(cierres: Sequence[float], n: int) -> Optional[float]:
    if len(cierres) < n:
        return None
    return sum(cierres[-n:]) / n


def atr(barras: Sequence[Barra], n: int = 14) -> Optional[float]:
    """Average True Range con el suavizado de Wilder."""
    if len(barras) < n + 1:
        return None
    rangos = []
    for anterior, actual in zip(barras, barras[1:]):
        rangos.append(
            max(
                actual.maximo - actual.minimo,
                abs(actual.maximo - anterior.cierre),
                abs(actual.minimo - anterior.cierre),
            )
        )
    valor = sum(rangos[:n]) / n
    for r in rangos[n:]:
        valor = (valor * (n - 1) + r) / n
    return valor


def volatilidad_realizada(cierres: Sequence[float], n: int = 20) -> Optional[float]:
    """Desvío anualizado de los retornos logarítmicos diarios de los últimos n días."""
    if len(cierres) < n + 1:
        return None
    rets = [math.log(b / a) for a, b in zip(cierres[-n - 1 : -1], cierres[-n:])]
    media = sum(rets) / n
    var = sum((r - media) ** 2 for r in rets) / (n - 1)
    return math.sqrt(var) * math.sqrt(DIAS_HABILES_ANIO)


def movimiento_esperado(precio: float, iv: float, dias: int) -> float:
    """Movimiento de 1 desvío hasta el vencimiento (días corridos)."""
    return precio * iv * math.sqrt(max(dias, 0) / 365)


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def precio_bs(
    tipo: str,
    s: float,
    k: float,
    t: float,
    iv: float,
    r: float = TASA_LIBRE_RIESGO,
    q: float = DIVIDENDO_SPY,
) -> float:
    if t <= 0 or iv <= 0:
        intrinseco = s - k if tipo == "call" else k - s
        return max(intrinseco, 0.0)
    d1 = (math.log(s / k) + (r - q + iv * iv / 2) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if tipo == "call":
        return s * math.exp(-q * t) * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * math.exp(-q * t) * _ncdf(-d1)


def iv_implicita(
    tipo: str,
    precio: float,
    s: float,
    k: float,
    t: float,
    r: float = TASA_LIBRE_RIESGO,
    q: float = DIVIDENDO_SPY,
) -> Optional[float]:
    """Volatilidad implícita por bisección. None si el precio no tiene solución."""
    if precio <= 0 or t <= 0:
        return None
    bajo, alto = 1e-4, 5.0
    if not (precio_bs(tipo, s, k, t, bajo, r, q) <= precio <= precio_bs(tipo, s, k, t, alto, r, q)):
        return None
    for _ in range(100):
        medio = (bajo + alto) / 2
        if precio_bs(tipo, s, k, t, medio, r, q) < precio:
            bajo = medio
        else:
            alto = medio
        if alto - bajo < 1e-6:
            break
    return (bajo + alto) / 2


def iv_rank(actual: float, historial: Sequence[float]) -> Optional[float]:
    """Dónde está la IV actual entre el mínimo y el máximo del período, de 0 a 100."""
    if not historial:
        return None
    minimo, maximo = min(min(historial), actual), max(max(historial), actual)
    if maximo == minimo:
        return None
    return (actual - minimo) / (maximo - minimo) * 100


def iv_percentil(actual: float, historial: Sequence[float]) -> Optional[float]:
    """Porcentaje de días del período con IV menor que la actual."""
    if not historial:
        return None
    return sum(1 for v in historial if v < actual) / len(historial) * 100
