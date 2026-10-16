"""IV de 30 días al dinero e IV rank.

Alpaca no da IV histórica, así que se reconstruye: para cada día hábil del
último año se toma el vencimiento de viernes más cercano a 30 días y el strike
más cercano al cierre, y se calcula la IV del put y del call con el precio de
cierre de cada contrato. La IV del día es el promedio de las dos.

El resultado se guarda en datos/cache/ para no volver a pedir los días ya
calculados.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from broker.base import Barra, Broker, ContratoOpcion
from datos.calculos import iv_implicita, iv_percentil, iv_rank

DIAS_OBJETIVO = 30
CARPETA_CACHE = Path(__file__).parent / "cache"


def simbolo_occ(subyacente: str, vencimiento: date, tipo: str, strike: float) -> str:
    return f"{subyacente}{vencimiento:%y%m%d}{'C' if tipo == 'call' else 'P'}{round(strike * 1000):08d}"


def vencimiento_objetivo(dia: date, dias_habiles: set[date], ultimo_dia: date) -> date:
    """El viernes más cercano a 30 días. Si ese viernes fue feriado, el jueves anterior."""
    objetivo = dia + timedelta(days=DIAS_OBJETIVO)
    viernes = objetivo + timedelta(days=(4 - objetivo.weekday()) % 7)
    if (viernes - objetivo).days > 3:
        viernes -= timedelta(days=7)
    if viernes <= ultimo_dia and viernes not in dias_habiles:
        viernes -= timedelta(days=1)
    return viernes


@dataclass(frozen=True)
class PuntoIV:
    fecha: date
    iv: float
    vencimiento: date
    strike: float


def _leer_cache(ruta: Path) -> dict[date, PuntoIV]:
    if not ruta.exists():
        return {}
    puntos = {}
    with ruta.open() as f:
        for fila in csv.DictReader(f):
            p = PuntoIV(
                fecha=date.fromisoformat(fila["fecha"]),
                iv=float(fila["iv"]),
                vencimiento=date.fromisoformat(fila["vencimiento"]),
                strike=float(fila["strike"]),
            )
            puntos[p.fecha] = p
    return puntos


def _escribir_cache(ruta: Path, puntos: dict[date, PuntoIV]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["fecha", "iv", "vencimiento", "strike"])
        for p in sorted(puntos.values(), key=lambda p: p.fecha):
            w.writerow([p.fecha.isoformat(), f"{p.iv:.6f}", p.vencimiento.isoformat(), p.strike])


def calcular_puntos(
    subyacente: str,
    barras_crudas: list[Barra],
    barras_opciones: dict[str, list[Barra]],
    dias: list[date],
) -> dict[date, PuntoIV]:
    """IV de cada día a partir de las barras ya descargadas."""
    habiles = {b.fecha for b in barras_crudas}
    ultimo = max(habiles)
    cierres = {b.fecha: b.cierre for b in barras_crudas}
    cierres_op = {
        (s, b.fecha): b.cierre for s, barras in barras_opciones.items() for b in barras if b.volumen > 0
    }
    puntos = {}
    for d in dias:
        s = cierres[d]
        venc = vencimiento_objetivo(d, habiles, ultimo)
        strike = float(round(s))
        t = (venc - d).days / 365
        ivs = []
        for tipo in ("call", "put"):
            precio = cierres_op.get((simbolo_occ(subyacente, venc, tipo, strike), d))
            if precio is None:
                continue
            iv = iv_implicita(tipo, precio, s, strike, t)
            if iv is not None and 0.02 < iv < 2.0:
                ivs.append(iv)
        if ivs:
            puntos[d] = PuntoIV(d, sum(ivs) / len(ivs), venc, strike)
    return puntos


def historial_iv(
    broker: Broker, subyacente: str, hoy: date, dias_atras: int = 365, cache: bool = True
) -> list[PuntoIV]:
    """IV al dinero de ~30 días para cada día hábil del período (sin incluir hoy)."""
    ruta = CARPETA_CACHE / f"iv_{subyacente}.csv"
    puntos = _leer_cache(ruta) if cache else {}
    desde = hoy - timedelta(days=dias_atras)
    barras = broker.barras_diarias(subyacente, desde, hoy - timedelta(days=1), ajustadas=False)
    if not barras:
        return []
    habiles = {b.fecha for b in barras}
    ultimo = max(habiles)
    faltan = [b.fecha for b in barras if b.fecha not in puntos and b.fecha < hoy]

    if faltan:
        cierres = {b.fecha: b.cierre for b in barras}
        simbolos = set()
        for d in faltan:
            venc = vencimiento_objetivo(d, habiles, ultimo)
            k = float(round(cierres[d]))
            simbolos.add(simbolo_occ(subyacente, venc, "call", k))
            simbolos.add(simbolo_occ(subyacente, venc, "put", k))
        barras_op = broker.barras_opciones(sorted(simbolos), min(faltan), max(faltan))
        puntos.update(calcular_puntos(subyacente, barras, barras_op, faltan))
        # Un día que no se pudo calcular se vuelve a intentar la próxima vez.
        if cache:
            _escribir_cache(ruta, puntos)

    return sorted((p for p in puntos.values() if desde <= p.fecha < hoy), key=lambda p: p.fecha)


def iv_actual(
    precio: float, contratos: list[ContratoOpcion], hoy: date
) -> Optional[tuple[float, date, float]]:
    """IV al dinero de hoy con el mismo método: (iv, vencimiento, strike).

    Usa el punto medio entre bid y ask del put y del call al dinero en el
    vencimiento más cercano a 30 días.
    """
    if not contratos:
        return None
    venc = min({c.vencimiento for c in contratos}, key=lambda v: abs((v - hoy).days - DIAS_OBJETIVO))
    del_venc = [c for c in contratos if c.vencimiento == venc]
    strike = min({c.strike for c in del_venc}, key=lambda k: abs(k - precio))
    t = (venc - hoy).days / 365
    ivs = []
    for c in del_venc:
        if c.strike != strike or c.medio is None:
            continue
        iv = iv_implicita(c.tipo, c.medio, precio, strike, t)
        if iv is not None:
            ivs.append(iv)
    if not ivs:
        return None
    return sum(ivs) / len(ivs), venc, strike


@dataclass(frozen=True)
class ResumenIV:
    actual: Optional[float]
    vencimiento: Optional[date]
    strike: Optional[float]
    rank: Optional[float]
    percentil: Optional[float]
    minimo: Optional[float]
    maximo: Optional[float]
    dias_historial: int


def resumen_iv(actual: Optional[tuple[float, date, float]], historial: list[PuntoIV]) -> ResumenIV:
    valores = [p.iv for p in historial]
    if actual is None:
        return ResumenIV(None, None, None, None, None, min(valores, default=None), max(valores, default=None), len(valores))
    iv, venc, strike = actual
    return ResumenIV(
        actual=iv,
        vencimiento=venc,
        strike=strike,
        rank=iv_rank(iv, valores),
        percentil=iv_percentil(iv, valores),
        minimo=min(valores, default=None),
        maximo=max(valores, default=None),
        dias_historial=len(valores),
    )
