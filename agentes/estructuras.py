"""Candidatos de venta de primas armados con la cadena real.

Strategy elige entre estos candidatos y no puede inventar strikes ni precios:
todos los números (crédito, pérdida máxima, griegas) salen de las cotizaciones
del bróker.

Solo se arman estructuras de riesgo definido: spread de crédito con puts,
spread de crédito con calls e iron condor. El put cubierto con efectivo y la
call cubierta quedan afuera: en SPY el put cubierto arriesga strike x 100 (más
del 2% del capital por trade) y la call cubierta necesita tener las acciones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Optional

from agentes import config
from broker.base import ContratoOpcion
from datos.cadena import Vencimiento


@dataclass(frozen=True)
class Pata:
    simbolo: str
    tipo: str
    lado: str  # "venta" o "compra"
    strike: float
    bid: float
    ask: float
    delta: float
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    iv: Optional[float]

    @property
    def medio(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def signo(self) -> int:
        return -1 if self.lado == "venta" else 1


@dataclass(frozen=True)
class Candidato:
    id: str
    estructura: str
    subyacente: str
    vencimiento: date
    dias: int
    patas: tuple[Pata, ...]
    ancho: float
    credito: float  # al punto medio, por acción
    credito_natural: float  # vendiendo al bid y comprando al ask
    perdida_maxima: float  # por contrato, en dólares
    ganancia_maxima: float  # por contrato, en dólares
    equilibrio_bajo: Optional[float]
    equilibrio_alto: Optional[float]
    prob_ganancia: float  # aproximada: 1 - |delta| de los strikes vendidos
    retorno_sobre_riesgo: float
    delta: float  # griegas de la posición por contrato (x100)
    gamma: float
    theta: float
    vega: float
    fuera_movimiento_esperado: Optional[bool]
    # Clave de su backtest aproximado: la estructura, o la estructura con el
    # ancho si es uno de los anchos extra (config.ANCHOS_CADA_100).
    clave_backtest: str = ""

    @property
    def vendidos(self) -> list[Pata]:
        return [p for p in self.patas if p.lado == "venta"]

    def a_dict(self) -> dict:
        d = asdict(self)
        d["vencimiento"] = self.vencimiento.isoformat()
        d["patas"] = [asdict(p) for p in self.patas]
        return d


# Lo que se manda de cada pata a los agentes que comparan candidatos. El símbolo
# y las griegas de cada pata no se mandan: las griegas de la posición entera
# vienen en el candidato.
CAMPOS_PATA_RESUMEN = ("tipo", "lado", "strike", "bid", "ask", "delta", "iv")


def resumen_candidato(d: dict) -> dict:
    """El candidato (como dict) con las patas resumidas, para los mensajes a los agentes."""
    return {**d, "patas": [{k: p.get(k) for k in CAMPOS_PATA_RESUMEN} for p in d.get("patas", [])]}


def _pata(c: ContratoOpcion, lado: str) -> Optional[Pata]:
    if c.bid is None or c.ask is None or c.delta is None or c.ask <= 0 or c.bid < 0:
        return None
    return Pata(
        simbolo=c.simbolo,
        tipo=c.tipo,
        lado=lado,
        strike=c.strike,
        bid=c.bid,
        ask=c.ask,
        delta=c.delta,
        gamma=c.gamma,
        theta=c.theta,
        vega=c.vega,
        iv=c.volatilidad_implicita,
    )


def _vendido(contratos: list[ContratoOpcion], tipo: str) -> Optional[ContratoOpcion]:
    """El contrato con |delta| más cerca de 0.15 dentro de 0.10 a 0.20, con bid."""
    candidatos = [
        c
        for c in contratos
        if c.tipo == tipo
        and c.delta is not None
        and config.DELTA_VENDIDO_MIN <= abs(c.delta) <= config.DELTA_VENDIDO_MAX
        and c.bid
        and c.ask
    ]
    if not candidatos:
        return None
    return min(candidatos, key=lambda c: abs(abs(c.delta) - config.DELTA_OBJETIVO))


def _protector(
    contratos: list[ContratoOpcion], vendido: ContratoOpcion, ancho_objetivo: float
) -> Optional[ContratoOpcion]:
    """El strike más lejano con cotización a una distancia cercana al ancho buscado."""
    if vendido.tipo == "put":
        opciones = [c for c in contratos if c.tipo == "put" and c.strike < vendido.strike]
        objetivo = vendido.strike - ancho_objetivo
    else:
        opciones = [c for c in contratos if c.tipo == "call" and c.strike > vendido.strike]
        objetivo = vendido.strike + ancho_objetivo
    opciones = [c for c in opciones if c.ask and c.bid is not None and c.delta is not None]
    if not opciones:
        return None
    return min(opciones, key=lambda c: (abs(c.strike - objetivo), -abs(c.strike - vendido.strike)))


def _griega(patas: tuple[Pata, ...], nombre: str) -> float:
    total = 0.0
    for p in patas:
        valor = getattr(p, nombre)
        if valor is not None:
            total += p.signo * valor * 100
    return round(total, 4)


def _armar(
    estructura: str,
    subyacente: str,
    venc: Vencimiento,
    patas: tuple[Pata, ...],
    precio: Optional[float],
    movimiento: Optional[float],
    sufijo_ancho: str = "",
    clave_backtest: str = "",
) -> Optional[Candidato]:
    credito = sum(-p.signo * p.medio for p in patas)
    natural = sum((p.bid if p.lado == "venta" else -p.ask) for p in patas)
    puts = sorted([p for p in patas if p.tipo == "put"], key=lambda p: p.strike)
    calls = sorted([p for p in patas if p.tipo == "call"], key=lambda p: p.strike)
    ancho_put = puts[1].strike - puts[0].strike if len(puts) == 2 else 0.0
    ancho_call = calls[1].strike - calls[0].strike if len(calls) == 2 else 0.0
    ancho = max(ancho_put, ancho_call)
    if credito <= 0 or credito >= ancho:
        return None

    vendidos = [p for p in patas if p.lado == "venta"]
    put_v = next((p for p in vendidos if p.tipo == "put"), None)
    call_v = next((p for p in vendidos if p.tipo == "call"), None)
    perdida = (ancho - credito) * 100
    fuera = None
    if precio is not None and movimiento is not None:
        fuera = all(
            (p.strike < precio - movimiento) if p.tipo == "put" else (p.strike > precio + movimiento)
            for p in vendidos
        )
    sufijo = "-".join(f"{p.tipo[0].upper()}{p.strike:g}" for p in vendidos)
    return Candidato(
        id=f"{subyacente}-{estructura}-{venc.fecha.isoformat()}-{sufijo}{sufijo_ancho}",
        estructura=estructura,
        subyacente=subyacente,
        vencimiento=venc.fecha,
        dias=venc.dias,
        patas=patas,
        ancho=ancho,
        credito=round(credito, 4),
        credito_natural=round(natural, 4),
        perdida_maxima=round(perdida, 2),
        ganancia_maxima=round(credito * 100, 2),
        equilibrio_bajo=None if put_v is None else round(put_v.strike - credito, 2),
        equilibrio_alto=None if call_v is None else round(call_v.strike + credito, 2),
        prob_ganancia=round(1 - sum(abs(p.delta) for p in vendidos), 3),
        retorno_sobre_riesgo=round(credito / (ancho - credito), 3),
        delta=_griega(patas, "delta"),
        gamma=_griega(patas, "gamma"),
        theta=_griega(patas, "theta"),
        vega=_griega(patas, "vega"),
        fuera_movimiento_esperado=fuera,
        clave_backtest=clave_backtest or estructura,
    )


def ancho_objetivo(precio: float, cada_100: float = config.ANCHO_CADA_100) -> float:
    return max(1.0, round(precio * cada_100 / 100))


def anchos_objetivo(precio: Optional[float]) -> list[float]:
    """Los anchos de alas a probar, sin repetir; el primero es el de siempre."""
    if not precio:
        return [5.0]
    return list(dict.fromkeys(ancho_objetivo(precio, a) for a in config.ANCHOS_CADA_100))


def clave_backtest(estructura: str, ancho: float, principal: float) -> str:
    return estructura if ancho == principal else f"{estructura} alas {ancho:g}"


def armar_candidatos(
    subyacente: str,
    vencimientos: list[Vencimiento],
    precio: Optional[float],
    movimientos: Optional[dict[date, float]] = None,
) -> list[Candidato]:
    """Spread de puts, spread de calls e iron condor para cada vencimiento y cada ancho."""
    movimientos = movimientos or {}
    anchos = anchos_objetivo(precio)
    candidatos: list[Candidato] = []
    vistos: set[tuple[str, frozenset]] = set()
    for v in vencimientos:
        if not config.DIAS_MIN <= v.dias <= config.DIAS_MAX:
            continue
        mov = movimientos.get(v.fecha)
        for ancho in anchos:
            sufijo = "" if ancho == anchos[0] else f"-A{ancho:g}"
            lados: dict[str, tuple[Pata, Pata]] = {}
            for tipo in ("put", "call"):
                vendido = _vendido(v.contratos, tipo)
                if vendido is None:
                    continue
                protector = _protector(v.contratos, vendido, ancho)
                if protector is None:
                    continue
                pv, pc = _pata(vendido, "venta"), _pata(protector, "compra")
                if pv is None or pc is None:
                    continue
                lados[tipo] = (pc, pv) if tipo == "put" else (pv, pc)
            armados = [(e, lados[t]) for t, e in (("put", "spread_put"), ("call", "spread_call")) if t in lados]
            if "put" in lados and "call" in lados:
                armados.append(("iron_condor", lados["put"] + lados["call"]))
            for estructura, patas in armados:
                # Si la cadena no tiene un strike más lejos, el ancho extra da las mismas patas.
                marca = (estructura, frozenset(p.simbolo for p in patas))
                if marca in vistos:
                    continue
                vistos.add(marca)
                c = _armar(estructura, subyacente, v, patas, precio, mov, sufijo, clave_backtest(estructura, ancho, anchos[0]))
                if c:
                    candidatos.append(c)
    return candidatos
