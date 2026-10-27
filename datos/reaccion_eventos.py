"""Cuánto se movió el activo en los eventos macro del pasado.

En vez de descartar cualquier vencimiento que cruce un evento, los agentes
necesitan un número: cuánto suele moverse SPY o QQQ el día de una Fed, un CPI
o un informe de empleo, y cuánto se movió en el peor de esos días. Con eso se
compara la distancia al strike vendido y se decide operar o no.

Las fechas pasadas están en datos/eventos_historicos.csv, armado desde:
- el calendario y el archivo del FOMC (federalreserve.gov), para las decisiones
  de la Fed (no se incluyen los discursos de Jackson Hole);
- las fechas de publicación (vintages) de PAYEMS y CPIAUCSL en ALFRED, que son
  las fechas reales de los informes de empleo y de CPI.

El movimiento se mide sobre las barras diarias que ya trae el bróker para el
backtest, así que no cuesta ningún pedido extra.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

ARCHIVO = Path(__file__).parent / "eventos_historicos.csv"

TIPOS_MACRO = ("fed", "inflacion", "empleo")


@dataclass(frozen=True)
class EventoPasado:
    fecha: date
    tipo: str
    evento: str
    fuente: str


def cargar_historicos(archivo: Path = ARCHIVO) -> list[EventoPasado]:
    if not archivo.exists():
        return []
    with archivo.open(encoding="utf-8") as f:
        return sorted(
            (
                EventoPasado(
                    fecha=date.fromisoformat(fila["fecha"]),
                    tipo=fila["tipo"],
                    evento=fila["evento"],
                    fuente=fila["fuente"],
                )
                for fila in csv.DictReader(f)
            ),
            key=lambda e: e.fecha,
        )


def percentil(valores: list[float], p: float) -> Optional[float]:
    """Percentil p (0 a 100) por interpolación lineal, sin numpy."""
    if not valores:
        return None
    orden = sorted(valores)
    if len(orden) == 1:
        return orden[0]
    pos = (len(orden) - 1) * p / 100
    bajo = math.floor(pos)
    alto = math.ceil(pos)
    if bajo == alto:
        return orden[bajo]
    return orden[bajo] + (orden[alto] - orden[bajo]) * (pos - bajo)


@dataclass
class Reaccion:
    """Distribución del movimiento del activo en los eventos de un tipo."""

    tipo: str
    eventos: int
    desde: Optional[str] = None
    hasta: Optional[str] = None
    mediana_pct: Optional[float] = None
    media_pct: Optional[float] = None
    p75_pct: Optional[float] = None
    p90_pct: Optional[float] = None
    peor_pct: Optional[float] = None
    peor_fecha: Optional[str] = None
    # Fracción de eventos con movimiento mayor a 1% y a 2%.
    arriba_1_pct: Optional[float] = None
    arriba_2_pct: Optional[float] = None
    # Movimiento de los últimos eventos, del más nuevo al más viejo.
    ultimos: list[dict] = field(default_factory=list)

    def a_dict(self) -> dict:
        return asdict(self)


def _cierres(barras: Iterable) -> list[tuple[date, float]]:
    return sorted(((b.fecha, b.cierre) for b in barras), key=lambda x: x[0])


def movimiento_del_evento(cierres: list[tuple[date, float]], fecha: date) -> Optional[dict]:
    """Movimiento del día del evento y del día siguiente, en porcentaje.

    El de la Fed sale a las 14:00 y el mercado sigue reaccionando al día
    siguiente, así que se guardan los dos y se usa el mayor en valor absoluto.
    """
    indices = {f: i for i, (f, _) in enumerate(cierres)}
    i = indices.get(fecha)
    if i is None or i == 0:
        return None
    previo = cierres[i - 1][1]
    if not previo:
        return None
    dia = cierres[i][1] / previo - 1
    dos = cierres[i + 1][1] / previo - 1 if i + 1 < len(cierres) else None
    peor = dia if dos is None or abs(dia) >= abs(dos) else dos
    return {
        "fecha": fecha.isoformat(),
        "dia_pct": round(dia * 100, 2),
        "dos_dias_pct": None if dos is None else round(dos * 100, 2),
        "movimiento_pct": round(abs(peor) * 100, 2),
        "signo": "suba" if peor > 0 else "baja",
    }


def reaccion_de_tipo(
    barras: Iterable,
    eventos: list[EventoPasado],
    tipo: str,
    hasta: Optional[date] = None,
    ultimos: int = 6,
) -> Reaccion:
    cierres = _cierres(barras)
    movs = []
    for e in eventos:
        if e.tipo != tipo:
            continue
        if hasta is not None and e.fecha >= hasta:
            continue
        m = movimiento_del_evento(cierres, e.fecha)
        if m is not None:
            movs.append(m)
    if not movs:
        return Reaccion(tipo=tipo, eventos=0)
    valores = [m["movimiento_pct"] for m in movs]
    peor = max(movs, key=lambda m: m["movimiento_pct"])
    return Reaccion(
        tipo=tipo,
        eventos=len(movs),
        desde=movs[0]["fecha"],
        hasta=movs[-1]["fecha"],
        mediana_pct=round(percentil(valores, 50), 2),
        media_pct=round(sum(valores) / len(valores), 2),
        p75_pct=round(percentil(valores, 75), 2),
        p90_pct=round(percentil(valores, 90), 2),
        peor_pct=peor["movimiento_pct"],
        peor_fecha=peor["fecha"],
        arriba_1_pct=round(sum(1 for v in valores if v > 1) / len(valores), 3),
        arriba_2_pct=round(sum(1 for v in valores if v > 2) / len(valores), 3),
        ultimos=list(reversed(movs[-ultimos:])),
    )


def reacciones(
    barras: Iterable,
    hasta: Optional[date] = None,
    tipos: Iterable[str] = TIPOS_MACRO,
    eventos: Optional[list[EventoPasado]] = None,
) -> dict[str, dict]:
    """Un resumen por tipo de evento macro, listo para mandárselo a los agentes."""
    eventos = cargar_historicos() if eventos is None else eventos
    barras = list(barras)
    return {t: reaccion_de_tipo(barras, eventos, t, hasta=hasta).a_dict() for t in tipos}
