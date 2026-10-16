"""Equipo de research: Chart, News, Strategy y Risk.

Orden: Chart y News en paralelo, después Strategy y por último Risk, que tiene
veto. Antes de llamar a cualquier agente se evalúa el freno por drawdown: si no
permite abrir riesgo, no se gasta en modelos y no hay trade hoy.

Lo que se calcula en código (no lo decide ningún modelo):
- los candidatos y sus números, armados con la cadena real;
- los vencimientos descartados por eventos grandes de datos/eventos.csv;
- los límites de Risk y el freno por drawdown;
- el backtest aproximado.

Entrega: de 1 a 3 candidatos con su informe, o "no hay trade hoy".
"""

from __future__ import annotations

import asyncio
import json
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional

from agentes import config
from agentes.backtest import ResultadoBacktest, correr_backtest
from agentes.bitacora import TradeRegistrado, leer_trades
from agentes.estructuras import Candidato, ancho_objetivo, armar_candidatos
from agentes.freno import ResultadoFreno, evaluar_freno
from agentes.modelo import Modelo, Pedido, cargar_skill, esquema_informe
from agentes.riesgo import Revision, eventos_que_descartan, revisar
from broker.base import Barra, Broker, Noticia
from datos.cadena import cadena_en_ventana, hoy_nueva_york
from datos.eventos import Evento, cargar_eventos
from datos.informe import Informe, generar_informe

# Para ETFs, las empresas de mayor peso cuyos earnings mira News.
MAYORES_DEL_INDICE = {
    "SPY": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META"],
    "QQQ": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META"],
    "IWM": [],
}


@dataclass
class ResultadoResearch:
    subyacente: str
    fecha: date
    con_modelo: bool
    freno: ResultadoFreno
    capital: float
    precio: Optional[float]
    chart: Optional[dict] = None
    news: Optional[dict] = None
    strategy: Optional[dict] = None
    risk: Optional[dict] = None
    descartados: dict[str, list[str]] = field(default_factory=dict)  # id -> motivos
    candidatos: list[dict] = field(default_factory=list)  # los que pasan a consejo
    backtests: dict[str, dict] = field(default_factory=dict)
    no_hay_trade: bool = False
    motivo_no_trade: str = ""
    costo_usd: float = 0.0
    avisos: list[str] = field(default_factory=list)

    def a_dict(self) -> dict:
        return {
            "subyacente": self.subyacente,
            "fecha": self.fecha.isoformat(),
            "con_modelo": self.con_modelo,
            "capital": self.capital,
            "precio": self.precio,
            "freno": self.freno.a_dict(),
            "chart": self.chart,
            "news": self.news,
            "strategy": self.strategy,
            "risk": self.risk,
            "candidatos": self.candidatos,
            "descartados": self.descartados,
            "backtests": self.backtests,
            "no_hay_trade": self.no_hay_trade,
            "motivo_no_trade": self.motivo_no_trade,
            "costo_usd": round(self.costo_usd, 4),
            "avisos": self.avisos,
        }


# --- Datos que se calculan antes de los agentes ---------------------------


def niveles(barras: list[Barra], precio: float, ventana: int = 3, ultimos: int = 120) -> tuple[list[float], list[float]]:
    """Soportes y resistencias: mínimos y máximos locales de los últimos días."""
    tramo = barras[-ultimos:]
    soportes, resistencias = set(), set()
    for i in range(ventana, len(tramo) - ventana):
        vecinos = tramo[i - ventana : i + ventana + 1]
        if tramo[i].minimo == min(b.minimo for b in vecinos) and tramo[i].minimo < precio:
            soportes.add(round(tramo[i].minimo, 2))
        if tramo[i].maximo == max(b.maximo for b in vecinos) and tramo[i].maximo > precio:
            resistencias.add(round(tramo[i].maximo, 2))
    return sorted(soportes, reverse=True)[:4], sorted(resistencias)[:4]


def _noticia(n: Noticia) -> dict:
    return {"fecha": n.fecha, "titular": n.titular, "resumen": n.resumen[:300], "fuente": n.fuente, "url": n.url}


def _evento(e: Evento) -> dict:
    return {"fecha": e.fecha.isoformat(), "hora_et": e.hora_et, "tipo": e.tipo, "evento": e.evento, "fuente": e.fuente}


def _resumen_backtest(b: ResultadoBacktest) -> dict:
    return {
        "estructura": b.estructura,
        "dias": b.dias,
        "periodo": f"{b.desde.isoformat()} a {b.hasta.isoformat()}",
        "veredicto": b.veredicto,
        "razones": b.razones,
        "trades": b.total.trades,
        "tasa_acierto": b.total.tasa_acierto,
        "resultado_medio_usd": b.total.resultado_medio,
        "peor_trade_usd": b.total.peor_trade,
        "peor_drawdown_usd": b.total.peor_drawdown,
        "prueba_resultado_medio_usd": b.prueba.resultado_medio,
        "crisis_resultado_usd": {k: m.resultado_total for k, m in b.crisis.items()},
        "estres_resultado_medio_usd": {k: m.resultado_medio for k, m in b.estres.items()},
        "aviso": b.aviso,
    }


# --- Instrucciones de cada agente ----------------------------------------


def _sistema(agente: str, hoy: date, skills: list[str], tarea: str) -> str:
    partes = [
        f"Sos el agente {agente} del equipo de research de un sistema que vende primas de opciones "
        f"en una cuenta paper (dinero simulado). Hoy es {hoy.isoformat()}. Escribí en español rioplatense, "
        "claro y corto.",
        "Reglas del usuario: venta de primas, de 7 a 21 días al vencimiento, strike vendido con delta 0.10 "
        "a 0.20, y cualquier earnings o evento macro grande dentro del plazo descarta el trade.",
        "Usá solo los datos que te pasan en el mensaje (y lo que encuentres en la web con su fuente, si "
        "tenés búsqueda). Nunca inventes un dato: si falta, anotalo en 'faltantes'.",
        "## Tu tarea\n" + tarea,
    ]
    for s in skills:
        partes.append(f"## Skill {s}\n{cargar_skill(s)}")
    return "\n\n".join(partes)


TAREA_CHART = """Analizá precio, tendencia y volatilidad del subyacente con el informe de datos.
Decí la tendencia, los soportes y resistencias que importan para un plazo de 7 a 21 días, si la IV está
alta respecto de la realizada (IV rank > 30 como referencia) y de qué lado conviene vender prima:
puts, calls, los dos lados (iron condor) o ninguno. Los soportes y resistencias candidatos ya vienen
calculados como mínimos y máximos locales; podés descartar o priorizar, no inventar otros."""

TAREA_NEWS = """Armá el calendario de catalizadores entre hoy y el último vencimiento de la ventana,
y explicá por qué se mueve el mercado con las noticias que te paso (Benzinga) y lo que busques.
Buscá en la web y confirmá en la fuente oficial: earnings del activo; si es un ETF, earnings de las
empresas de mayor peso que te paso; Fed, CPI, PCE, empleo y discursos del presidente de la Fed;
feriados. Las fechas de Fed, CPI y empleo ya cargadas vienen en 'eventos_cargados'.
En 'eventos' poné impacto "alto" a: earnings del activo, decisión de la Fed, CPI, empleo, y para ETFs
los earnings de esas empresas de mayor peso. Todo evento de impacto alto descarta los vencimientos
que lo incluyan. Marcá estimada la fecha que la empresa no confirmó. Separá hechos de rumores."""

TAREA_STRATEGY = """Elegí de 0 a 3 candidatos de la lista 'candidatos', ordenados del mejor al peor, y decí
cuál preferís y por qué. Solo podés elegir por 'id' de esa lista: los strikes, créditos y griegas ya están
calculados con la cadena real y no se pueden cambiar. Tené en cuenta los informes de Chart y News, el
backtest aproximado de cada estructura y el retorno sobre riesgo (menos de 20-25% rara vez compensa).
Si ninguno conviene (por ejemplo IV baja, sesgo contrario de Chart o riesgo de evento), no elijas ninguno
y explicá por qué en 'no_hay_trade_motivo'."""

TAREA_RISK = """Revisá los candidatos que eligió Strategy. Los límites duros (pérdida máxima por trade,
riesgo total, riesgo por subyacente, griegas, eventos y freno por drawdown) ya están calculados en código
en 'revision_codigo': si un candidato está vetado ahí, queda vetado y no podés levantarlo.
Podés sumar vetos propios con motivo concreto (asignación anticipada, gap de precio, correlación con
posiciones abiertas, liquidez). Un veto no se negocia. Vigilá el gamma: con plazos cortos es el riesgo
principal."""


ESQUEMA_CHART = esquema_informe(
    {
        "tendencia": {"type": "string", "enum": ["alcista", "bajista", "lateral"]},
        "lado_a_vender": {"type": "string", "enum": ["puts", "calls", "ambos", "ninguno"]},
        "iv_favorable": {"type": "boolean"},
        "soportes": {"type": "array", "items": {"type": "number"}},
        "resistencias": {"type": "array", "items": {"type": "number"}},
    },
    ["tendencia", "lado_a_vender", "iv_favorable", "soportes", "resistencias"],
)

ESQUEMA_NEWS = esquema_informe(
    {
        "sentimiento": {"type": "string", "enum": ["positivo", "negativo", "mixto"]},
        "por_que_se_mueve": {"type": "string"},
        "hechos": {"type": "array", "items": {"type": "string"}},
        "rumores": {"type": "array", "items": {"type": "string"}},
        "eventos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fecha": {"type": "string", "description": "AAAA-MM-DD"},
                    "hora": {"type": "string"},
                    "evento": {"type": "string"},
                    "fuente": {"type": "string"},
                    "confirmado": {"type": "boolean"},
                    "impacto": {"type": "string", "enum": ["alto", "medio", "bajo"]},
                },
                "required": ["fecha", "hora", "evento", "fuente", "confirmado", "impacto"],
                "additionalProperties": False,
            },
        },
        "veredicto": {"type": "string", "enum": ["SIN EVENTOS", "ALERTA", "DESCARTAR"]},
        "motivo_veredicto": {"type": "string"},
    },
    ["sentimiento", "por_que_se_mueve", "hechos", "rumores", "eventos", "veredicto", "motivo_veredicto"],
)

ESQUEMA_STRATEGY = esquema_informe(
    {
        "elegidos": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "motivo": {"type": "string"},
                    "riesgo_principal": {"type": "string"},
                },
                "required": ["id", "motivo", "riesgo_principal"],
                "additionalProperties": False,
            },
        },
        "preferido": {"type": "string", "description": "id del preferido, o vacío si no hay trade"},
        "no_hay_trade_motivo": {"type": "string"},
    },
    ["elegidos", "preferido", "no_hay_trade_motivo"],
)

ESQUEMA_RISK = esquema_informe(
    {
        "vetos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "motivo": {"type": "string"}},
                "required": ["id", "motivo"],
                "additionalProperties": False,
            },
        },
        "comentarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "comentario": {"type": "string"}},
                "required": ["id", "comentario"],
                "additionalProperties": False,
            },
        },
    },
    ["vetos", "comentarios"],
)


def _json(datos: Any) -> str:
    return json.dumps(datos, ensure_ascii=False, indent=1, default=str)


# --- Versiones sin modelo (para probar el circuito sin gastar) -----------


def _chart_sin_modelo(informe: Informe, soportes, resistencias) -> dict:
    p, iv = informe.precio, informe.iv
    ref = p.actual or p.cierre_anterior
    tendencia = "lateral"
    if p.media_50 and p.media_200:
        if ref > p.media_50 > p.media_200:
            tendencia = "alcista"
        elif ref < p.media_50 < p.media_200:
            tendencia = "bajista"
    favorable = bool(iv.rank is not None and iv.rank > 30)
    lado = {"alcista": "puts", "bajista": "calls", "lateral": "ambos"}[tendencia]
    return {
        "tesis": f"Tendencia {tendencia}; IV rank {iv.rank if iv.rank is None else round(iv.rank)}.",
        "datos": [f"Precio {ref}", f"IV rank {iv.rank}", f"Vol. realizada 20d {p.vol_realizada_20}"],
        "confianza": "baja",
        "motivo_confianza": "Regla fija sin análisis del modelo.",
        "invalidaria": [],
        "faltantes": ["Análisis del agente Chart (corrida sin modelo)."],
        "tendencia": tendencia,
        "lado_a_vender": lado if favorable else "ninguno",
        "iv_favorable": favorable,
        "soportes": soportes,
        "resistencias": resistencias,
        "sin_modelo": True,
    }


def _news_sin_modelo(eventos: list[Evento]) -> dict:
    grandes = [e for e in eventos if e.tipo in config.TIPOS_EVENTO_GRANDE]
    return {
        "tesis": "Solo se revisó el calendario cargado a mano.",
        "datos": [f"{e.fecha.isoformat()} {e.evento}" for e in eventos],
        "confianza": "baja",
        "motivo_confianza": "No se buscaron earnings ni noticias (corrida sin modelo).",
        "invalidaria": [],
        "faltantes": ["Earnings de las empresas de mayor peso", "Noticias del día"],
        "sentimiento": "mixto",
        "por_que_se_mueve": "",
        "hechos": [],
        "rumores": [],
        "eventos": [
            {
                "fecha": e.fecha.isoformat(),
                "hora": e.hora_et,
                "evento": e.evento,
                "fuente": e.fuente,
                "confirmado": True,
                "impacto": "alto" if e.tipo in config.TIPOS_EVENTO_GRANDE else "bajo",
            }
            for e in eventos
        ],
        "veredicto": "ALERTA" if grandes else "SIN EVENTOS",
        "motivo_veredicto": "Hay eventos grandes en la ventana; descartan los vencimientos que los incluyen."
        if grandes
        else "",
        "sin_modelo": True,
    }


def _strategy_sin_modelo(candidatos: list[Candidato]) -> dict:
    orden = sorted(
        candidatos,
        key=lambda c: (c.fuera_movimiento_esperado is True, c.retorno_sobre_riesgo),
        reverse=True,
    )[:3]
    return {
        "tesis": "Los de mejor retorno sobre riesgo con strikes fuera del movimiento esperado.",
        "datos": [],
        "confianza": "baja",
        "motivo_confianza": "Regla fija sin análisis del modelo.",
        "invalidaria": [],
        "faltantes": ["Análisis del agente Strategy (corrida sin modelo)."],
        "elegidos": [
            {"id": c.id, "motivo": f"Retorno sobre riesgo {c.retorno_sobre_riesgo:.0%}.", "riesgo_principal": "-"}
            for c in orden
        ],
        "preferido": orden[0].id if orden else "",
        "no_hay_trade_motivo": "" if orden else "No hay candidatos que pasen los filtros.",
        "sin_modelo": True,
    }


# --- Circuito ------------------------------------------------------------


class Research:
    def __init__(self, broker: Broker, modelo: Optional[Modelo], subyacente: str = "SPY", hoy: Optional[date] = None):
        self.broker = broker
        self.modelo = modelo
        self.subyacente = subyacente
        self.hoy = hoy or hoy_nueva_york()
        self.costo = 0.0

    async def _pedir(self, pedido: Pedido) -> dict:
        respuesta = await self.modelo.pedir(pedido)
        self.costo += respuesta.costo_usd or 0.0
        return respuesta.datos

    async def correr(self, informe: Optional[Informe] = None, trades: Optional[list[TradeRegistrado]] = None) -> ResultadoResearch:
        hoy = self.hoy
        cuenta = self.broker.cuenta()
        trades = leer_trades() if trades is None else trades
        abiertos = [t for t in trades if t.estado == "abierto"]
        freno = evaluar_freno(
            hoy,
            cuenta,
            self.broker.historial_patrimonio(hoy - timedelta(days=45)),
            self.broker.posiciones(),
            trades,
        )
        informe = informe or generar_informe(self.broker, self.subyacente, hoy=hoy)
        precio = informe.precio.actual or informe.precio.cierre_anterior
        r = ResultadoResearch(
            subyacente=self.subyacente,
            fecha=hoy,
            con_modelo=self.modelo is not None,
            freno=freno,
            capital=cuenta.patrimonio,
            precio=precio,
            avisos=list(informe.avisos),
        )
        if not freno.permite:
            r.no_hay_trade = True
            r.motivo_no_trade = f"Freno por drawdown: {freno.estado}. " + " ".join(freno.motivos)
            return r

        # Datos para los agentes.
        barras = self.broker.barras_diarias(
            self.subyacente, date(hoy.year - config.BACKTEST_DESDE_ANIOS, 1, 1), hoy
        )
        barras = [b for b in barras if b.fecha < hoy]
        soportes, resistencias = niveles(barras, precio)
        ultimo_venc = hoy + timedelta(days=config.DIAS_MAX)
        eventos = [e for e in cargar_eventos() if hoy <= e.fecha <= ultimo_venc]
        try:
            noticias = self.broker.noticias([self.subyacente], hoy - timedelta(days=3), 30)
        except Exception as e:  # las noticias no frenan el research
            noticias = []
            r.avisos.append(f"No se pudieron leer las noticias: {e}")
        informe_dict = informe.a_dict()

        # Chart y News en paralelo.
        if self.modelo is None:
            r.chart = _chart_sin_modelo(informe, soportes, resistencias)
            r.news = _news_sin_modelo(eventos)
        else:
            pedido_chart = Pedido(
                agente="Chart",
                sistema=_sistema("Chart", hoy, ["research-opciones-primas"], TAREA_CHART),
                mensaje=_json(
                    {
                        "informe_de_datos": {k: informe_dict[k] for k in ("subyacente", "fecha", "precio", "iv", "vencimientos", "avisos")},
                        "soportes_candidatos": soportes,
                        "resistencias_candidatas": resistencias,
                        "ultimas_20_barras": [
                            {"fecha": b.fecha.isoformat(), "apertura": b.apertura, "maximo": b.maximo, "minimo": b.minimo, "cierre": b.cierre}
                            for b in barras[-20:]
                        ],
                    }
                ),
                esquema=ESQUEMA_CHART,
            )
            pedido_news = Pedido(
                agente="News",
                sistema=_sistema("News", hoy, ["research-opciones-primas", "calendario-catalizadores"], TAREA_NEWS),
                mensaje=_json(
                    {
                        "subyacente": self.subyacente,
                        "ventana": {"desde": hoy.isoformat(), "hasta": ultimo_venc.isoformat()},
                        "vencimientos": [v["fecha"] for v in informe_dict["vencimientos"]],
                        "empresas_de_mayor_peso": MAYORES_DEL_INDICE.get(self.subyacente, []),
                        "eventos_cargados": [_evento(e) for e in eventos],
                        "noticias_benzinga": [_noticia(n) for n in noticias],
                    }
                ),
                esquema=ESQUEMA_NEWS,
                busqueda_web=True,
                max_turnos=20,
            )
            r.chart, r.news = await asyncio.gather(self._pedir(pedido_chart), self._pedir(pedido_news))

        # Candidatos con la cadena real, y filtro de límites duros.
        vencimientos = cadena_en_ventana(self.broker, self.subyacente, hoy=hoy)
        movimientos = {
            date.fromisoformat(v["fecha"]): v["movimiento_iv"]
            for v in informe_dict["vencimientos"]
            if v.get("movimiento_iv") is not None
        }
        todos = armar_candidatos(self.subyacente, vencimientos, precio, movimientos)
        eventos_news = (r.news or {}).get("eventos", [])
        revisiones = {
            c.id: revisar(c, hoy, cuenta.patrimonio, precio, freno, abiertos, eventos, eventos_news) for c in todos
        }
        validos = [c for c in todos if not revisiones[c.id].vetado]
        r.descartados = {c.id: revisiones[c.id].vetos for c in todos if revisiones[c.id].vetado}
        if not todos:
            r.no_hay_trade = True
            r.motivo_no_trade = "No se pudo armar ningún candidato con la cadena de 7 a 21 días."
            r.costo_usd = self.costo
            return r
        if not validos:
            r.no_hay_trade = True
            motivos = sorted({m for ms in r.descartados.values() for m in ms})
            r.motivo_no_trade = "Todos los candidatos superan algún límite: " + " | ".join(motivos[:6])
            r.costo_usd = self.costo
            return r

        # Backtest aproximado de cada estructura, al plazo típico de los candidatos.
        ancho_pct = ancho_objetivo(precio) / precio
        for estructura in sorted({c.estructura for c in validos}):
            dias = round(statistics.median(c.dias for c in validos if c.estructura == estructura))
            bt = correr_backtest(barras, estructura, dias, config.DELTA_OBJETIVO, ancho_pct)
            r.backtests[estructura] = _resumen_backtest(bt)

        # Strategy.
        if self.modelo is None:
            r.strategy = _strategy_sin_modelo(validos)
        else:
            r.strategy = await self._pedir(
                Pedido(
                    agente="Strategy",
                    sistema=_sistema(
                        "Strategy", hoy, ["research-opciones-primas", "analisis-opciones-primas"], TAREA_STRATEGY
                    ),
                    mensaje=_json(
                        {
                            "subyacente": self.subyacente,
                            "precio": precio,
                            "capital": cuenta.patrimonio,
                            "iv": informe_dict["iv"],
                            "informe_chart": r.chart,
                            "informe_news": r.news,
                            "candidatos": [c.a_dict() for c in validos],
                            "descartados_por_limites": r.descartados,
                            "backtest_aproximado": r.backtests,
                        }
                    ),
                    esquema=ESQUEMA_STRATEGY,
                )
            )
        por_id = {c.id: c for c in validos}
        elegidos = []
        for e in r.strategy.get("elegidos", [])[:3]:
            if e.get("id") in por_id and e["id"] not in [x[0].id for x in elegidos]:
                elegidos.append((por_id[e["id"]], e))
            else:
                r.avisos.append(f"Strategy eligió un id que no está entre los candidatos válidos: {e.get('id')}")
        if not elegidos:
            r.no_hay_trade = True
            r.motivo_no_trade = r.strategy.get("no_hay_trade_motivo") or "Strategy no eligió ningún candidato."
            r.costo_usd = self.costo
            return r

        # Risk: primero los límites en código (otra vez, por las dudas), después el agente.
        revision_final: dict[str, Revision] = {
            c.id: revisar(c, hoy, cuenta.patrimonio, precio, freno, abiertos, eventos, eventos_news) for c, _ in elegidos
        }
        if self.modelo is None:
            r.risk = {
                "tesis": "Solo límites en código.",
                "vetos": [],
                "comentarios": [],
                "sin_modelo": True,
            }
        else:
            r.risk = await self._pedir(
                Pedido(
                    agente="Risk",
                    sistema=_sistema("Risk", hoy, ["research-opciones-primas", "freno-drawdown-opciones"], TAREA_RISK),
                    mensaje=_json(
                        {
                            "capital": cuenta.patrimonio,
                            "precio": precio,
                            "freno_drawdown": freno.a_dict(),
                            "trades_abiertos": [t.__dict__ for t in abiertos],
                            "eventos_news": eventos_news,
                            "elegidos": [
                                {"candidato": c.a_dict(), "motivo_strategy": e, "revision_codigo": revision_final[c.id].a_dict()}
                                for c, e in elegidos
                            ],
                        }
                    ),
                    esquema=ESQUEMA_RISK,
                )
            )
        vetos_risk: dict[str, list[str]] = {}
        for v in r.risk.get("vetos", []):
            vetos_risk.setdefault(v.get("id", ""), []).append(v.get("motivo", ""))
        comentarios = {x.get("id"): x.get("comentario") for x in r.risk.get("comentarios", [])}

        for c, e in elegidos:
            rev = revision_final[c.id]
            motivos = rev.vetos + [f"Risk: {m}" for m in vetos_risk.get(c.id, [])]
            if motivos:
                r.descartados[c.id] = motivos
                continue
            r.candidatos.append(
                {
                    "candidato": c.a_dict(),
                    "preferido": c.id == r.strategy.get("preferido"),
                    "motivo": e.get("motivo"),
                    "riesgo_principal": e.get("riesgo_principal"),
                    "revision": rev.a_dict(),
                    "comentario_risk": comentarios.get(c.id),
                    "backtest": r.backtests.get(c.estructura),
                }
            )
        if not r.candidatos:
            r.no_hay_trade = True
            r.motivo_no_trade = "Risk vetó todos los candidatos elegidos."
        r.costo_usd = self.costo
        return r
