"""Los eventos como insumo del trade, no como veto.

El usuario pidió el 27 oct 2026 operar los eventos en vez de rechazarlos. Acá
se traduce "hay una Fed en el plazo" a un número comparable con el trade:

    colchón = cuánto se movió este activo, históricamente, en los eventos que
              caen dentro del plazo (sumados en cuadratura, porque son golpes
              independientes), en porcentaje del precio.

Si el strike vendido está más lejos que ese colchón, el trade se puede operar y
el evento queda como riesgo a juzgar por el consejo y el jefe. Si está más
cerca, el candidato se descarta en código: no es prudencia abstracta, es que el
movimiento típico del evento lo pone dentro del strike.

Los movimientos históricos salen de datos/reaccion_eventos.py (barras diarias
del bróker + fechas reales de Fed, CPI y empleo). El balance de una empresa
suelta se pondera por su peso en el índice: a QQQ el balance de NVDA le pega
mucho más que el de cualquier otra, y mucho menos que a NVDA misma.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Iterable, Optional

from agentes import config
from datos.eventos import Evento

# Palabras con las que se reconoce un macro en el texto que escribe News.
PALABRAS_MACRO = {
    "fed": ("fomc", "reserva federal", "decision de tasas", "decisión de tasas", "tasa de interes", "powell", " fed"),
    "inflacion": ("cpi", "ipc", "inflacion", "inflación", "pce"),
    "empleo": ("empleo", "nominas", "nóminas", "payroll", "desempleo", "jolts"),
}

NOMBRES_EMPRESAS = {
    "NVDA": ("nvda", "nvidia"),
    "MSFT": ("msft", "microsoft"),
    "AAPL": ("aapl", "apple"),
    "AMZN": ("amzn", "amazon"),
    "GOOGL": ("googl", "goog", "google", "alphabet"),
    "META": ("meta", "facebook"),
    "TSM": ("tsm", "taiwan semi"),
    "AVGO": ("avgo", "broadcom"),
    "AMD": ("amd",),
    "MU": ("mu", "micron"),
    "ASML": ("asml",),
}


def _sin_acentos(texto: str) -> str:
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        texto = texto.replace(a, b)
    return texto


def tipo_macro(texto: str) -> Optional[str]:
    """Qué macro es el evento que describe News, o None si no es macro."""
    t = " " + _sin_acentos(str(texto).lower()) + " "
    for tipo, palabras in PALABRAS_MACRO.items():
        if any(_sin_acentos(p) in t for p in palabras):
            return tipo
    return None


def empresa_de(texto: str) -> Optional[str]:
    """Ticker de la empresa que presenta balance, si se reconoce en el texto."""
    t = _sin_acentos(str(texto).lower())
    for ticker, nombres in NOMBRES_EMPRESAS.items():
        if any(re.search(r"\b" + re.escape(n.strip()) + r"\b", t) for n in nombres):
            return ticker
    return None


@dataclass
class EventoDelPlazo:
    fecha: str
    evento: str
    # "fed", "inflacion", "empleo", "earnings" u "otro".
    tipo: str
    origen: str  # "calendario" (datos/eventos.csv) o "news"
    empresa: Optional[str] = None
    impacto: Optional[str] = None

    def a_dict(self) -> dict:
        return asdict(self)


def eventos_del_plazo(
    eventos: Iterable[Evento],
    eventos_news: Optional[Iterable[dict]],
    hoy: date,
    vencimiento: date,
    subyacente: str = "",
) -> list[EventoDelPlazo]:
    """Los eventos entre hoy y el vencimiento, del calendario y de News, sin repetir.

    Un macro que News repite del calendario se cuenta una sola vez: la clave es
    la fecha más el tipo. Los earnings quedan identificados con su ticker cuando
    se lo reconoce en el texto.
    """
    out: dict[tuple[str, str], EventoDelPlazo] = {}
    for e in eventos:
        if not hoy <= e.fecha <= vencimiento:
            continue
        if e.tipo not in config.TIPOS_EVENTO_GRANDE:
            continue
        out[(e.fecha.isoformat(), e.tipo)] = EventoDelPlazo(
            fecha=e.fecha.isoformat(), evento=e.evento, tipo=e.tipo, origen="calendario", impacto="alto"
        )
    for e in eventos_news or []:
        try:
            fecha = date.fromisoformat(str(e.get("fecha", ""))[:10])
        except ValueError:
            continue
        if not hoy <= fecha <= vencimiento:
            continue
        texto = str(e.get("evento", ""))
        tipo = tipo_macro(texto)
        empresa = None
        if tipo is None:
            if str(e.get("tipo")) == "earnings":
                tipo = "earnings"
                empresa = empresa_de(texto)
                if empresa is None and subyacente and subyacente.upper() in texto.upper():
                    empresa = subyacente.upper()
            else:
                tipo = "otro"
        clave = (fecha.isoformat(), tipo if tipo != "earnings" else f"earnings:{empresa or texto[:20]}")
        if clave in out:
            continue
        out[clave] = EventoDelPlazo(
            fecha=fecha.isoformat(),
            evento=texto,
            tipo=tipo,
            origen="news",
            empresa=empresa,
            impacto=str(e.get("impacto") or ""),
        )
    return sorted(out.values(), key=lambda x: (x.fecha, x.tipo))


@dataclass
class Colchon:
    """Movimiento que hay que aguantar por los eventos del plazo."""

    percentil: float
    colchon_pct: float = 0.0  # al percentil pedido (el día típico del evento)
    colchon_estres_pct: float = 0.0  # al percentil 90 (el mal día)
    distancia_strike_pct: Optional[float] = None  # del precio al strike vendido más cercano
    alcanza: Optional[bool] = None  # distancia >= colchón
    hay_macro: bool = False
    earnings_propio: Optional[str] = None  # ticker, si el balance es del subyacente
    aportes: list[dict] = field(default_factory=list)
    eventos: list[dict] = field(default_factory=list)

    def a_dict(self) -> dict:
        return asdict(self)

    @property
    def sobra_pct(self) -> Optional[float]:
        if self.distancia_strike_pct is None:
            return None
        return round(self.distancia_strike_pct - self.colchon_pct, 2)


def _aporte_macro(reacciones: dict, tipo: str, percentil: float) -> tuple[Optional[float], str]:
    r = (reacciones or {}).get(tipo) or {}
    clave = {50: "mediana_pct", 75: "p75_pct", 90: "p90_pct"}.get(int(percentil))
    valor = r.get(clave) if clave else None
    if valor is None:
        valor = r.get("mediana_pct")
        clave = "mediana_pct"
    if valor is None:
        return None, f"sin datos históricos de {tipo}"
    return float(valor), f"{clave} de {r.get('eventos')} eventos de tipo {tipo} ({r.get('desde')} a {r.get('hasta')})"


def _aporte_earnings(subyacente: str, empresa: Optional[str]) -> tuple[float, str]:
    pesos = config.PESOS_MAYORES.get(subyacente.upper(), {})
    peso = pesos.get((empresa or "").upper())
    if peso is None:
        # Empresa que no está entre las de mayor peso: aporte chico pero no cero.
        peso = 0.01
        return round(config.MOVIMIENTO_EARNINGS_PCT * peso, 2), "peso supuesto 1% en el índice"
    return round(config.MOVIMIENTO_EARNINGS_PCT * peso, 2), f"peso {peso:.1%} en el índice x {config.MOVIMIENTO_EARNINGS_PCT:g}% de movimiento"


def calcular_colchon(
    subyacente: str,
    eventos_plazo: list[EventoDelPlazo],
    reacciones: dict,
    distancia_strike_pct: Optional[float] = None,
    percentil: Optional[float] = None,
    es_etf: bool = True,
) -> Colchon:
    """Colchón de los eventos del plazo, en porcentaje del precio.

    Los aportes se suman en cuadratura (raíz de la suma de cuadrados): dos
    eventos independientes no suman sus movimientos enteros.
    """
    percentil = config.PERCENTIL_COLCHON if percentil is None else percentil
    c = Colchon(percentil=percentil, distancia_strike_pct=distancia_strike_pct)
    cuadrados, cuadrados_estres = 0.0, 0.0
    for e in eventos_plazo:
        if e.tipo in config.TIPOS_EVENTO_GRANDE:
            valor, de_donde = _aporte_macro(reacciones, e.tipo, percentil)
            estres, _ = _aporte_macro(reacciones, e.tipo, config.PERCENTIL_COLCHON_ESTRES)
            if valor is None:
                c.aportes.append({"evento": e.evento, "fecha": e.fecha, "aporte_pct": None, "de_donde": de_donde})
                continue
            c.hay_macro = True
        elif e.tipo == "earnings":
            if not es_etf and (e.empresa or "").upper() == subyacente.upper():
                c.earnings_propio = subyacente.upper()
            valor, de_donde = _aporte_earnings(subyacente, e.empresa)
            estres = round(valor * 1.6, 2)
        else:
            continue
        cuadrados += valor**2
        cuadrados_estres += (estres or valor) ** 2
        c.aportes.append({"evento": e.evento, "fecha": e.fecha, "tipo": e.tipo, "aporte_pct": valor, "de_donde": de_donde})
    c.colchon_pct = round(math.sqrt(cuadrados), 2)
    c.colchon_estres_pct = round(math.sqrt(cuadrados_estres), 2)
    c.eventos = [e.a_dict() for e in eventos_plazo]
    if distancia_strike_pct is not None:
        c.alcanza = distancia_strike_pct >= c.colchon_pct
    return c


def distancia_al_strike_pct(candidato, precio: float) -> Optional[float]:
    """Distancia del precio al strike vendido más cercano, en porcentaje."""
    if not precio:
        return None
    vendidos = [p.strike for p in candidato.vendidos]
    if not vendidos:
        return None
    return round(min(abs(s - precio) for s in vendidos) / precio * 100, 2)
