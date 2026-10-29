"""Equipo de research: Chart, News, Fundamentals, Strategy y Risk.

Orden: Chart, News y Fundamentals en paralelo, después Strategy y por último Risk, que tiene
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
from agentes.estructuras import Candidato, anchos_objetivo, armar_candidatos, clave_backtest, resumen_candidato
from agentes.freno import ResultadoFreno, evaluar_freno
from agentes.glosario import COMO_ESCRIBIR
from agentes.modelo import Modelo, Pedido, a_json, cargar_skill, esquema_informe, lecciones_aprobadas
from agentes.riesgo import Revision, eventos_que_descartan, revisar
from broker.base import Barra, Broker, Noticia
from datos.cadena import cadena_en_ventana, hoy_nueva_york
from datos.eventos import Evento, cargar_eventos
from datos.informe import Informe, generar_informe
from datos.reaccion_eventos import reacciones as reacciones_historicas

# Para ETFs, las empresas de mayor peso cuyos earnings mira News.
MAYORES_DEL_INDICE = {
    "SPY": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META"],
    "QQQ": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META"],
    "IWM": [],
    "SMH": ["NVDA", "TSM", "AVGO", "AMD", "ASML", "MU"],
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
    fundamentals: Optional[dict] = None
    strategy: Optional[dict] = None
    risk: Optional[dict] = None
    descartados: dict[str, list[str]] = field(default_factory=dict)  # id -> motivos
    candidatos: list[dict] = field(default_factory=list)  # los que pasan a consejo
    backtests: dict[str, dict] = field(default_factory=dict)
    # Cuánto se movió el activo en los Fed, CPI y empleo del pasado (regla de
    # eventos nueva, 27 oct 2026): es el insumo que reemplaza al veto.
    reaccion_historica: dict = field(default_factory=dict)
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
            "fundamentals": self.fundamentals,
            "strategy": self.strategy,
            "risk": self.risk,
            "candidatos": self.candidatos,
            "descartados": self.descartados,
            "backtests": self.backtests,
            "reaccion_historica": self.reaccion_historica,
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


def _sistema(agente: str, hoy: date, skills: list[str], tarea: str, subyacente: str = "") -> str:
    partes = [
        f"Sos el agente {agente} del equipo de research de un sistema que vende primas de opciones "
        f"en una cuenta paper (dinero simulado). Hoy es {hoy.isoformat()}. Escribí en español rioplatense, "
        "claro y corto.",
        "Reglas del usuario: venta de primas, de 7 a 21 días al vencimiento, strike vendido con delta 0.10 "
        "a 0.20. " + config.regla_eventos(hoy),
        "Usá solo los datos que te pasan en el mensaje (y lo que encuentres en la web con su fuente, si "
        "tenés búsqueda). Nunca inventes un dato: si falta, anotalo en 'faltantes'.",
        "## Tu tarea\n" + tarea,
        COMO_ESCRIBIR,
    ]
    if subyacente in config.ACTIVOS_24_7:
        partes.append(
            f"## Ojo: {subyacente} sigue a una cripto que cotiza 24/7\nEl ETF no opera el fin de semana pero la cripto sí: "
            "una posición abierta el viernes puede amanecer el lunes con un salto de precio, sin poder cerrarla "
            "antes. Tenelo en cuenta en la distancia del strike, el ancho y el tamaño, sobre todo de jueves a viernes."
        )
    for s in skills:
        partes.append(f"## Skill {s}\n{cargar_skill(s)}")
    if lecciones_aprobadas():
        partes.append(lecciones_aprobadas())
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
los earnings de esas empresas de mayor peso. En 'tipo' poné "earnings" a los balances de empresas,
"macro" a Fed, CPI, PCE y empleo, y "otro" al resto. Todo evento de impacto alto descarta los
vencimientos que lo incluyan, salvo cuando la regla del usuario dice que los eventos no descartan.
Marcá estimada la fecha que la empresa no confirmó. Separá hechos de rumores.
Además, por cada evento de impacto alto dentro de la ventana armá su tesis en 'vista_eventos': qué pasó
la vez anterior (el dato del mes pasado y cómo reaccionó el mercado ese día), qué espera el mercado ahora
(consenso, probabilidades implícitas de la Fed, lo que digan los analistas, con fuente), tu propio
pronóstico y de qué lado ves el riesgo. En 'movimiento_esperado_pct' poné cuánto pensás que puede moverse
el activo ese día, en porcentaje; en 'reaccion_historica' te paso cuánto se movió de verdad en los
eventos parecidos del pasado (mediana, percentil 90 y peor día), así podés comparar.
Ya no vetás por calendario: un evento no descarta el trade. Usá 'veto_duro' solo para algo que los
números no ven (un fallo judicial, una suspensión de cotización, un dato que cambia la empresa).

Tu informe es el que más lee el usuario, así que es el que más claro tiene que estar:
- En 'en_criollo', tres o cuatro frases que se entiendan solas: qué pasó, por qué se movió el
  mercado y qué le cambia eso a nuestros trades. Si alguien que no sabe de opciones lo lee y no
  entiende qué hacemos con esa noticia, está mal escrito.
- En 'por_que_se_mueve', una explicación en cadena, no una lista de palabras: qué dato salió, qué
  hizo el mercado con ese dato y por qué. Cada cifra con su unidad y con su fecha.
- En 'hechos', una línea por hecho, empezando por lo que nos afecta y terminando con lo que es solo
  contexto. Un hecho es algo que ya pasó y tiene fuente; lo que todavía no está confirmado va en
  'rumores', dicho como rumor.
- En 'motivo_veredicto', decí en una frase qué significa el veredicto para nosotros: SIN EVENTOS es
  "no hay nada grande en el camino", ALERTA es "hay algo grande pero se puede operar midiendo el
  golpe", DESCARTAR es "hoy no".
- En 'vista_eventos', 'la_vez_anterior' y 'que_espera_el_mercado' se escriben con números y en
  palabras: cuánto dio el dato, cuánto se esperaba y cuánto se movió el activo ese día en por ciento.
- Si hay trades abiertos, decí siempre qué les pasa a ellos, aunque la respuesta sea "nada cambia"."""

TAREA_FUNDAMENTALS = """Evaluá los fundamentals del activo (o del índice, si es un ETF) con búsqueda web:
valuación contra su historia, crecimiento y revisiones de estimaciones, y riesgos que puedan provocar un
gap antes del vencimiento. Para ETFs mirá el índice y las empresas de mayor peso que te paso. Decí de qué
lado conviene vender prima según los fundamentals y si se querría tener el activo a estos precios (por si
un put vendido termina en asignación). Cada cifra con su fecha y su fuente; lo que no encuentres, en
'faltantes'. No predigas el precio de las próximas dos semanas con la valuación."""

TAREA_STRATEGY = """Si viene 'ajustes_pedidos_por_el_jefe', es una nueva vuelta: elegí teniendo en cuenta esos cambios.
Elegí de 0 a 3 candidatos de la lista 'candidatos', ordenados del mejor al peor, y decí
cuál preferís y por qué. Solo podés elegir por 'id' de esa lista: los strikes, créditos y griegas ya están
calculados con la cadena real y no se pueden cambiar. Tené en cuenta los informes de Chart, News y Fundamentals, el
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
                    "tipo": {"type": "string", "enum": ["earnings", "macro", "otro"]},
                    "fuente": {"type": "string"},
                    "confirmado": {"type": "boolean"},
                    "impacto": {"type": "string", "enum": ["alto", "medio", "bajo"]},
                },
                "required": ["fecha", "hora", "evento", "tipo", "fuente", "confirmado", "impacto"],
                "additionalProperties": False,
            },
        },
        "vista_eventos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evento": {"type": "string"},
                    "fecha": {"type": "string", "description": "AAAA-MM-DD"},
                    "la_vez_anterior": {"type": "string", "description": "el dato del mes pasado y qué hizo el mercado ese día"},
                    "que_espera_el_mercado": {"type": "string"},
                    "pronostico_propio": {"type": "string"},
                    "movimiento_esperado_pct": {"type": "number"},
                    "lado_del_riesgo": {"type": "string", "enum": ["baja", "suba", "los dos", "no se sabe"]},
                    "fuentes": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "evento",
                    "fecha",
                    "la_vez_anterior",
                    "que_espera_el_mercado",
                    "pronostico_propio",
                    "movimiento_esperado_pct",
                    "lado_del_riesgo",
                    "fuentes",
                ],
                "additionalProperties": False,
            },
        },
        "veredicto": {"type": "string", "enum": ["SIN EVENTOS", "ALERTA", "DESCARTAR"]},
        "motivo_veredicto": {"type": "string"},
        "veto_duro": {"type": "boolean", "description": "solo para algo que los números no ven"},
        "motivo_veto_duro": {"type": "string"},
    },
    [
        "sentimiento",
        "por_que_se_mueve",
        "hechos",
        "rumores",
        "eventos",
        "vista_eventos",
        "veredicto",
        "motivo_veredicto",
        "veto_duro",
        "motivo_veto_duro",
    ],
)

ESQUEMA_FUNDAMENTALS = esquema_informe(
    {
        "valuacion": {"type": "string", "enum": ["cara", "justa", "barata", "sin datos"]},
        "estimaciones": {"type": "string", "enum": ["subiendo", "estables", "bajando", "sin datos"]},
        "lado_a_vender": {"type": "string", "enum": ["puts", "calls", "ambos", "ninguno"]},
        "se_querria_tener": {"type": "boolean"},
        "motivo_tenencia": {"type": "string"},
        "riesgos_fundamentales": {"type": "array", "items": {"type": "string"}},
    },
    ["valuacion", "estimaciones", "lado_a_vender", "se_querria_tener", "motivo_tenencia", "riesgos_fundamentales"],
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


_json = a_json


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


def _fundamentals_sin_modelo() -> dict:
    return {
        "tesis": "Sin análisis fundamental.",
        "datos": [],
        "confianza": "baja",
        "motivo_confianza": "Los fundamentals se buscan en la web y la corrida es sin modelo.",
        "invalidaria": [],
        "faltantes": ["Análisis del agente Fundamentals (corrida sin modelo)."],
        "valuacion": "sin datos",
        "estimaciones": "sin datos",
        "lado_a_vender": "ambos",
        "se_querria_tener": False,
        "motivo_tenencia": "",
        "riesgos_fundamentales": [],
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
        "vista_eventos": [],
        "veredicto": "ALERTA" if grandes else "SIN EVENTOS",
        "motivo_veredicto": "Hay eventos grandes en la ventana."
        if grandes
        else "",
        "veto_duro": False,
        "motivo_veto_duro": "",
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

    async def correr(
        self,
        informe: Optional[Informe] = None,
        trades: Optional[list[TradeRegistrado]] = None,
        ajustes: Optional[list[str]] = None,
        previo: Optional[ResultadoResearch] = None,
    ) -> ResultadoResearch:
        """Corre el equipo de research.

        `ajustes` son los cambios que pidió el jefe al devolver un trade; con
        `previo` se reusan los informes de Chart, News y Fundamentals de la vuelta anterior.
        """
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
        # Cuánto se movió este activo en los eventos macro del pasado: el insumo
        # que reemplazó al veto por calendario (regla del 27 oct 2026).
        try:
            r.reaccion_historica = reacciones_historicas(barras, hasta=hoy)
        except Exception as e:  # nunca frena el research
            r.reaccion_historica = {}
            r.avisos.append(f"No se pudo medir la reacción histórica a los eventos: {e}")
        try:
            noticias = self.broker.noticias([self.subyacente], hoy - timedelta(days=3), 30)
        except Exception as e:  # las noticias no frenan el research
            noticias = []
            r.avisos.append(f"No se pudieron leer las noticias: {e}")
        informe_dict = informe.a_dict()

        # Chart, News y Fundamentals en paralelo.
        if previo is not None and previo.chart is not None and previo.news is not None:
            r.chart, r.news, r.fundamentals = previo.chart, previo.news, previo.fundamentals
        elif self.modelo is None:
            r.chart = _chart_sin_modelo(informe, soportes, resistencias)
            r.news = _news_sin_modelo(eventos)
            r.fundamentals = _fundamentals_sin_modelo()
        else:
            from agentes.noticias import notas_de_hoy

            pedido_chart = Pedido(
                agente="Chart",
                sistema=_sistema("Chart", hoy, ["research-opciones-primas"], TAREA_CHART, self.subyacente),
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
                sistema=_sistema("News", hoy, ["research-opciones-primas", "calendario-catalizadores"], TAREA_NEWS, self.subyacente),
                mensaje=_json(
                    {
                        "subyacente": self.subyacente,
                        "ventana": {"desde": hoy.isoformat(), "hasta": ultimo_venc.isoformat()},
                        "vencimientos": [v["fecha"] for v in informe_dict["vencimientos"]],
                        "empresas_de_mayor_peso": MAYORES_DEL_INDICE.get(self.subyacente, []),
                        "eventos_cargados": [_evento(e) for e in eventos],
                        "reaccion_historica": r.reaccion_historica,
                        "noticias_benzinga": [_noticia(n) for n in noticias],
                        "revisiones_previas_de_hoy": notas_de_hoy(self.subyacente, hoy),
                    }
                ),
                esquema=ESQUEMA_NEWS,
                busqueda_web=True,
                max_turnos=20,
            )
            pedido_fundamentals = Pedido(
                agente="Fundamentals",
                sistema=_sistema(
                    "Fundamentals", hoy, ["research-opciones-primas", "fundamentals-opciones-primas"], TAREA_FUNDAMENTALS
                ),
                mensaje=_json(
                    {
                        "subyacente": self.subyacente,
                        "precio": precio,
                        "es_etf": self.subyacente in MAYORES_DEL_INDICE,
                        "empresas_de_mayor_peso": MAYORES_DEL_INDICE.get(self.subyacente, []),
                        "ultimo_vencimiento_de_la_ventana": ultimo_venc.isoformat(),
                    }
                ),
                esquema=ESQUEMA_FUNDAMENTALS,
                busqueda_web=True,
                max_turnos=20,
            )
            r.chart, r.news, r.fundamentals = await asyncio.gather(
                self._pedir(pedido_chart), self._pedir(pedido_news), self._pedir(pedido_fundamentals)
            )

        # Candidatos con la cadena real, y filtro de límites duros.
        vencimientos = cadena_en_ventana(self.broker, self.subyacente, hoy=hoy)
        movimientos = {
            date.fromisoformat(v["fecha"]): v["movimiento_iv"]
            for v in informe_dict["vencimientos"]
            if v.get("movimiento_iv") is not None
        }
        todos = armar_candidatos(self.subyacente, vencimientos, precio, movimientos)
        eventos_news = (r.news or {}).get("eventos", [])
        es_etf = self.subyacente in MAYORES_DEL_INDICE
        revisiones = {
            c.id: revisar(
                c, hoy, cuenta.patrimonio, precio, freno, abiertos, eventos, eventos_news, r.reaccion_historica, es_etf
            )
            for c in todos
        }
        if (r.news or {}).get("veto_duro"):
            r.no_hay_trade = True
            r.motivo_no_trade = "News vetó el activo por algo que los números no ven: " + str(
                (r.news or {}).get("motivo_veto_duro") or ""
            )
            r.costo_usd = self.costo
            return r
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
        # Uno por cada ancho que tenga candidatos (clave "iron_condor" o "iron_condor alas 10").
        anchos = anchos_objetivo(precio)
        for estructura in sorted({c.estructura for c in validos}):
            dias = round(statistics.median(c.dias for c in validos if c.estructura == estructura))
            for ancho in anchos:
                clave = clave_backtest(estructura, ancho, anchos[0])
                if not any(c.clave_backtest == clave for c in validos):
                    continue
                bt = correr_backtest(barras, estructura, dias, config.DELTA_OBJETIVO, ancho / precio)
                r.backtests[clave] = _resumen_backtest(bt)

        # Strategy.
        if self.modelo is None:
            r.strategy = _strategy_sin_modelo(validos)
        else:
            r.strategy = await self._pedir(
                Pedido(
                    agente="Strategy",
                    sistema=_sistema(
                        "Strategy", hoy, ["research-opciones-primas", "analisis-opciones-primas"], TAREA_STRATEGY, self.subyacente
                    ),
                    mensaje=_json(
                        {
                            "subyacente": self.subyacente,
                            "precio": precio,
                            "capital": cuenta.patrimonio,
                            "iv": informe_dict["iv"],
                            "informe_chart": r.chart,
                            "informe_news": r.news,
                            "informe_fundamentals": r.fundamentals,
                            "candidatos": [resumen_candidato(c.a_dict()) for c in validos],
                            "descartados_por_limites": r.descartados,
                            "backtest_aproximado": r.backtests,
                            "reaccion_historica": r.reaccion_historica,
                            "vista_eventos": (r.news or {}).get("vista_eventos", []),
                            "ajustes_pedidos_por_el_jefe": ajustes or [],
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
            c.id: revisar(
                c, hoy, cuenta.patrimonio, precio, freno, abiertos, eventos, eventos_news, r.reaccion_historica, es_etf
            )
            for c, _ in elegidos
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
                    sistema=_sistema("Risk", hoy, ["research-opciones-primas", "freno-drawdown-opciones"], TAREA_RISK, self.subyacente),
                    mensaje=_json(
                        {
                            "capital": cuenta.patrimonio,
                            "precio": precio,
                            "freno_drawdown": freno.a_dict(),
                            "trades_abiertos": [t.__dict__ for t in abiertos],
                            "eventos_news": eventos_news,
                            "reaccion_historica": r.reaccion_historica,
                            "vista_eventos": (r.news or {}).get("vista_eventos", []),
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
                    "backtest": r.backtests.get(c.clave_backtest),
                }
            )
        if not r.candidatos:
            r.no_hay_trade = True
            r.motivo_no_trade = "Risk vetó todos los candidatos elegidos."
        r.costo_usd = self.costo
        return r
