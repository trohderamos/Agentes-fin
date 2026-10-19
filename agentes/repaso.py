"""Repaso semanal de los viernes (skill bitacora-trades).

Cuatro pasos sobre la bitácora y la cuenta:
1. Medir cada error: para cada trade cerrado con pérdida, si falló la tesis
   de lado, hubo un evento que no se vio, o la pérdida superó el peor
   escenario calculado. Y qué habría pasado con los trades rechazados.
2. Ficha de cada agente: cuántas veces acertó su voto, con todo el historial.
3. Lecciones: un agente lee la semana y propone de 1 a 3 cambios concretos.
   No se aplican solos: el usuario los aprueba y se escriben en
   agentes/lecciones.md, que todos los agentes leen.
4. Probar antes de aplicar: si un cambio toca un parámetro (delta, plazo o
   ancho), se compara con el backtest el valor actual y el propuesto.

Además arma el informe para el usuario: resultado de la semana, trades
abiertos y cerrados con su tesis, posiciones abiertas y decisiones.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from agentes import config
from agentes.backtest import correr_backtest
from agentes.bitacora import TradeRegistrado, leer_decisiones, leer_trades
from agentes.consejo import MIEMBROS, NOMBRES
from agentes.modelo import Modelo, Pedido, cargar_skill, lecciones_aprobadas
from broker.base import Broker
from datos.eventos import cargar_eventos

ARCHIVO_PROPUESTAS = Path(__file__).parent.parent / "bitacora" / "lecciones_propuestas.jsonl"
PARAMETROS = ("ninguno", "delta_objetivo", "dias", "ancho_cada_100")


@dataclass
class Repaso:
    desde: date
    hasta: date
    con_modelo: bool
    patrimonio_inicio: Optional[float] = None
    patrimonio_fin: Optional[float] = None
    por_dia: list[dict] = field(default_factory=list)
    drawdown_semana: Optional[float] = None
    trades_cerrados: list[dict] = field(default_factory=list)
    trades_abiertos_en_la_semana: list[dict] = field(default_factory=list)
    posiciones_abiertas: list[dict] = field(default_factory=list)
    decisiones: list[dict] = field(default_factory=list)
    errores: list[dict] = field(default_factory=list)
    rechazados: list[dict] = field(default_factory=list)
    fichas: dict[str, dict] = field(default_factory=dict)
    lecciones: Optional[dict] = None
    pruebas: list[dict] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)
    costo_usd: float = 0.0

    @property
    def resultado(self) -> Optional[float]:
        if self.patrimonio_inicio is None or self.patrimonio_fin is None:
            return None
        return self.patrimonio_fin - self.patrimonio_inicio

    def a_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["desde"], d["hasta"] = self.desde.isoformat(), self.hasta.isoformat()
        d["resultado"] = self.resultado
        return d


# --- Paso 1: errores y rechazados ------------------------------------------


def valor_al_vencimiento(patas: list[dict], precio: float) -> float:
    """Lo que cuesta cerrar la posición al vencimiento, por acción."""
    total = 0.0
    for p in patas:
        intrinseco = max(precio - p["strike"], 0) if p["tipo"] == "call" else max(p["strike"] - precio, 0)
        total += intrinseco if p["lado"] == "venta" else -intrinseco
    return total


def resultado_hipotetico(decision: dict, cierres: dict[date, float]) -> Optional[float]:
    """Resultado por contrato si el trade debatido se hubiera abierto y llevado al vencimiento."""
    if not decision.get("patas") or decision.get("credito") is None or not decision.get("vencimiento"):
        return None
    venc = date.fromisoformat(decision["vencimiento"])
    precio = cierres.get(venc)
    if precio is None:
        return None
    return round((decision["credito"] - valor_al_vencimiento(decision["patas"], precio)) * 100, 2)


def clasificar_error(trade: TradeRegistrado, decision: Optional[dict], cierres: dict[date, float]) -> dict:
    motivos = []
    if decision is None:
        return {"id": trade.id, "resultado": trade.resultado, "motivos": ["No se encontró la decisión en la bitácora."]}
    vendidos = {p["tipo"] for p in decision.get("patas", []) if p["lado"] == "venta"}
    precio_entrada = decision.get("precio")
    precio_salida = cierres.get(trade.cerrado_el) if trade.cerrado_el else None
    if precio_entrada and precio_salida:
        cambio = precio_salida / precio_entrada - 1
        if ("put" in vendidos and cambio < -0.01) or ("call" in vendidos and cambio > 0.01):
            motivos.append(
                f"Tesis de lado: se vendió {', '.join(sorted(vendidos))} y el precio se movió {cambio:+.1%} "
                f"(Chart había dicho vender {decision.get('lado_chart')})."
            )
    if trade.abierto_el and trade.cerrado_el:
        vistos = " ".join(decision.get("eventos", []))
        for e in cargar_eventos():
            if trade.abierto_el <= e.fecha <= trade.cerrado_el and e.tipo in config.TIPOS_EVENTO_GRANDE:
                if e.fecha.isoformat() not in vistos:
                    motivos.append(f"Evento no visto: {e.fecha.isoformat()} {e.evento}.")
    peor = decision.get("peor_escenario_usd")
    if peor is not None and trade.resultado is not None and trade.resultado < peor * trade.contratos:
        motivos.append(
            f"Riesgo mal medido: perdió {trade.resultado:,.0f} USD y el peor escenario calculado era "
            f"{peor * trade.contratos:,.0f} USD."
        )
    if not motivos:
        motivos.append("Dentro de lo esperado: la pérdida no supera el peor escenario y no hubo error de tesis visible.")
    return {"id": trade.id, "resultado": trade.resultado, "motivos": motivos}


# --- Paso 2: fichas --------------------------------------------------------


def fichas(decisiones: list[dict], resultados: dict[str, float]) -> dict[str, dict]:
    """Aciertos de cada consejero y del jefe, sobre todas las decisiones con resultado conocido.

    Un voto A FAVOR acierta si el trade ganó; EN CONTRA, si perdió. AJUSTAR no
    cuenta. El jefe acierta si aprobó uno que ganó o rechazó uno que perdió.
    """
    out = {m: {"aciertos": 0, "votos": 0} for m in (*MIEMBROS, "jefe")}
    for d in decisiones:
        res = resultados.get(d.get("trade_debatido", ""))
        if res is None:
            continue
        gano = res > 0
        for m, v in (d.get("votos") or {}).items():
            if m in out and v.get("voto") in ("A FAVOR", "EN CONTRA"):
                out[m]["votos"] += 1
                out[m]["aciertos"] += int((v["voto"] == "A FAVOR") == gano)
        if d.get("decision") in ("APROBAR", "RECHAZAR"):
            out["jefe"]["votos"] += 1
            out["jefe"]["aciertos"] += int((d["decision"] == "APROBAR") == gano)
    for f in out.values():
        f["tasa"] = round(f["aciertos"] / f["votos"], 2) if f["votos"] else None
    return out


# --- Paso 3: lecciones -----------------------------------------------------

ESQUEMA_LECCIONES = {
    "type": "object",
    "properties": {
        "resumen_semana": {"type": "string", "description": "3 a 5 líneas: qué funcionó y qué falló."},
        "cambios": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "leccion": {"type": "string", "description": "La regla, en una línea, como quedaría escrita."},
                    "motivo": {"type": "string"},
                    "evidencia": {"type": "string", "description": "Qué trades o cifras de la semana la sostienen."},
                    "parametro": {"type": "string", "enum": list(PARAMETROS)},
                    "valor_nuevo": {"type": "number", "description": "Si toca un parámetro; 0 si no."},
                },
                "required": ["leccion", "motivo", "evidencia", "parametro", "valor_nuevo"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["resumen_semana", "cambios"],
    "additionalProperties": False,
}

TAREA_LECCIONES = """Hacé el repaso semanal con los datos del mensaje: resultado, trades cerrados, errores
clasificados, rechazados con su resultado hipotético y las fichas de cada agente. Proponé de 0 a 3 cambios
concretos (un umbral, una regla, un filtro) que habrían evitado los errores o aprovechado lo que se dejó
pasar. Cada cambio con la evidencia de la semana. Si toca uno de estos parámetros, indicalo para probarlo
con el backtest: delta_objetivo (delta del strike vendido, entre 0.10 y 0.20), dias (plazo, entre 7 y 21)
o ancho_cada_100 (ancho de las alas por cada 100 de precio). Si la muestra es chica, decilo y no fuerces
cambios. Los cambios NO se aplican: se le proponen al usuario."""


def _probar(barras, cambio: dict) -> Optional[dict]:
    """Paso 4: backtest del valor actual contra el propuesto."""
    parametro, valor = cambio.get("parametro"), cambio.get("valor_nuevo")
    if parametro not in PARAMETROS[1:] or not valor:
        return None
    base = {"dias": 10, "delta": config.DELTA_OBJETIVO, "ancho_pct": config.ANCHO_CADA_100 / 100}
    nuevo = dict(base)
    if parametro == "delta_objetivo":
        if not config.DELTA_VENDIDO_MIN <= valor <= config.DELTA_VENDIDO_MAX:
            return {"leccion": cambio["leccion"], "error": "El delta propuesto queda fuera de 0.10 a 0.20."}
        nuevo["delta"] = valor
    elif parametro == "dias":
        if not config.DIAS_MIN <= valor <= config.DIAS_MAX:
            return {"leccion": cambio["leccion"], "error": "El plazo propuesto queda fuera de 7 a 21 días."}
        nuevo["dias"] = int(valor)
    else:
        nuevo["ancho_pct"] = valor / 100
    comparacion = {}
    for nombre, p in (("actual", base), ("propuesto", nuevo)):
        bt = correr_backtest(barras, "spread_put", p["dias"], p["delta"], p["ancho_pct"])
        comparacion[nombre] = {
            "parametros": p,
            "trades": bt.total.trades,
            "tasa_acierto": bt.total.tasa_acierto,
            "resultado_medio_usd": bt.total.resultado_medio,
            "peor_drawdown_usd": bt.total.peor_drawdown,
            "prueba_resultado_medio_usd": bt.prueba.resultado_medio,
            "veredicto": bt.veredicto,
        }
    return {"leccion": cambio["leccion"], "comparacion": comparacion, "aviso": "Backtest aproximado (spread de puts)."}


# --- Circuito --------------------------------------------------------------


def semana_de(d: date) -> tuple[date, date]:
    lunes = d - timedelta(days=d.weekday())
    return lunes, lunes + timedelta(days=4)


async def repasar(
    broker: Broker,
    modelo: Optional[Modelo],
    hoy: date,
    subyacente: str = "SPY",
    trades: Optional[list[TradeRegistrado]] = None,
    decisiones: Optional[list[dict]] = None,
) -> Repaso:
    desde, hasta = semana_de(hoy)
    r = Repaso(desde=desde, hasta=hasta, con_modelo=modelo is not None)
    trades = leer_trades() if trades is None else trades
    decisiones = leer_decisiones() if decisiones is None else decisiones

    # Resultado de la semana.
    cuenta = broker.cuenta()
    historial = broker.historial_patrimonio(desde - timedelta(days=10))
    previos = [p for p in historial if p.fecha < desde]
    r.patrimonio_inicio = previos[-1].patrimonio if previos else (historial[0].patrimonio if historial else None)
    r.patrimonio_fin = cuenta.patrimonio
    pico, peor = r.patrimonio_inicio or 0.0, 0.0
    for p in [p for p in historial if desde <= p.fecha <= hasta]:
        r.por_dia.append({"fecha": p.fecha.isoformat(), "patrimonio": p.patrimonio})
        pico = max(pico, p.patrimonio)
        peor = min(peor, p.patrimonio / pico - 1 if pico else 0.0)
    r.drawdown_semana = round(peor, 4)
    if r.patrimonio_inicio is None:
        r.avisos.append("No hay historial de patrimonio para la semana.")

    # Trades, decisiones y posiciones.
    por_id = {}
    for d in decisiones:  # la más nueva gana
        oid = (d.get("orden_final") or {}).get("id") or d.get("trade_debatido")
        if oid:
            por_id[oid] = d

    def con_tesis(t: TradeRegistrado) -> dict:
        d = por_id.get(t.id) or {}
        return {
            "id": t.id,
            "estado": t.estado,
            "abierto_el": t.abierto_el.isoformat() if t.abierto_el else None,
            "cerrado_el": t.cerrado_el.isoformat() if t.cerrado_el else None,
            "contratos": t.contratos,
            "credito": t.credito,
            "resultado": t.resultado,
            "perdida_maxima": t.perdida_maxima,
            "tesis": d.get("tesis_strategy"),
            "razon_jefe": d.get("razon"),
            "plan_salida": d.get("plan_salida"),
        }

    r.trades_cerrados = [con_tesis(t) for t in trades if t.estado == "cerrado" and t.cerrado_el and desde <= t.cerrado_el <= hasta]
    r.trades_abiertos_en_la_semana = [con_tesis(t) for t in trades if t.abierto_el and desde <= t.abierto_el <= hasta]
    abiertos = {t.id: t for t in trades if t.estado == "abierto"}
    posiciones = broker.posiciones()
    r.posiciones_abiertas = [con_tesis(t) for t in abiertos.values()]
    for p in posiciones:
        if p.clase == "us_option" and not any(p.simbolo in t.simbolos for t in abiertos.values()):
            r.avisos.append(f"La cuenta tiene {p.simbolo} abierta y no está en la bitácora.")
    resultados_pos = {p.simbolo: p.resultado_no_realizado for p in posiciones}
    for x in r.posiciones_abiertas:
        t = abiertos[x["id"]]
        valores = [resultados_pos.get(s) for s in t.simbolos if resultados_pos.get(s) is not None]
        x["resultado_no_realizado"] = round(sum(valores), 2) if valores else None

    r.decisiones = [
        {k: d.get(k) for k in ("fecha", "decision", "razon", "trade_debatido", "sin_trade", "objecion_que_mas_peso")}
        for d in decisiones
        if desde.isoformat() <= str(d.get("fecha", "")) <= hasta.isoformat()
    ]

    # Paso 1.
    barras = broker.barras_diarias(subyacente, date(hoy.year - config.BACKTEST_DESDE_ANIOS, 1, 1), hoy, ajustadas=False)
    cierres = {b.fecha: b.cierre for b in barras}
    for t in trades:
        if t.estado == "cerrado" and t.cerrado_el and desde <= t.cerrado_el <= hasta and (t.resultado or 0) < 0:
            r.errores.append(clasificar_error(t, por_id.get(t.id), cierres))
    resultados = {t.id: t.resultado for t in trades if t.estado == "cerrado" and t.resultado is not None}
    for d in decisiones:
        tid = d.get("trade_debatido")
        if not tid or tid in resultados or (d.get("orden_final") and tid in abiertos):
            continue
        hipotetico = resultado_hipotetico(d, cierres)
        if hipotetico is not None:
            resultados[tid] = hipotetico
            if desde.isoformat() <= str(d.get("fecha", "")) <= hasta.isoformat() or (
                desde.isoformat() <= d.get("vencimiento", "") <= hasta.isoformat()
            ):
                r.rechazados.append({"id": tid, "decision": d.get("decision"), "resultado_hipotetico": hipotetico})
        elif d.get("decision") != "APROBAR" and desde.isoformat() <= str(d.get("fecha", "")) <= hasta.isoformat():
            r.rechazados.append({"id": tid, "decision": d.get("decision"), "resultado_hipotetico": None, "nota": "todavía no venció"})

    # Paso 2.
    r.fichas = fichas(decisiones, resultados)

    # Pasos 3 y 4.
    if modelo is None:
        r.avisos.append("Sin modelo: no se proponen lecciones.")
        return r
    sistema = "\n\n".join(
        [
            f"Sos el encargado del repaso semanal de un sistema que vende primas de opciones en una cuenta paper. "
            f"Semana del {desde.isoformat()} al {hasta.isoformat()}. Escribí en español rioplatense, corto y con cifras.",
            "Usá solo los datos del mensaje. Nunca inventes resultados.",
            "## Tu tarea\n" + TAREA_LECCIONES,
            "## Skill bitacora-trades\n" + cargar_skill("bitacora-trades"),
            lecciones_aprobadas(),
        ]
    ).strip()
    datos = {k: v for k, v in r.a_dict().items() if k not in ("lecciones", "pruebas", "costo_usd")}
    respuesta = await modelo.pedir(
        Pedido(agente="Repaso", sistema=sistema, mensaje=json.dumps(datos, ensure_ascii=False, indent=1, default=str), esquema=ESQUEMA_LECCIONES)
    )
    r.costo_usd += respuesta.costo_usd or 0.0
    r.lecciones = respuesta.datos
    barras_aj = broker.barras_diarias(subyacente, date(hoy.year - config.BACKTEST_DESDE_ANIOS, 1, 1), hoy)
    barras_aj = [b for b in barras_aj if b.fecha < hoy]
    for cambio in r.lecciones.get("cambios", [])[:3]:
        prueba = _probar(barras_aj, cambio)
        if prueba:
            r.pruebas.append(prueba)
    return r


def guardar_propuestas(r: Repaso, archivo: Path = ARCHIVO_PROPUESTAS) -> None:
    """Las lecciones propuestas quedan registradas, aprobadas o no."""
    if not r.lecciones or not r.lecciones.get("cambios"):
        return
    archivo.parent.mkdir(parents=True, exist_ok=True)
    with archivo.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "registrado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "semana": r.desde.isoformat(),
                    "cambios": r.lecciones["cambios"],
                    "pruebas": r.pruebas,
                    "aprobado": None,
                },
                ensure_ascii=False,
            )
            + "\n"
        )


# --- Informe para el usuario ----------------------------------------------


def _usd(v) -> str:
    return "-" if v is None else f"{v:,.2f} USD"


def formatear_repaso(r: Repaso) -> str:
    l = [f"# Repaso semanal · {r.desde.isoformat()} al {r.hasta.isoformat()}", ""]
    l.append("## Resultado")
    if r.resultado is not None and r.patrimonio_inicio:
        l.append(
            f"Patrimonio de {_usd(r.patrimonio_inicio)} a {_usd(r.patrimonio_fin)}: "
            f"{r.resultado:+,.2f} USD ({r.resultado / r.patrimonio_inicio:+.2%}). "
            f"Peor caída dentro de la semana: {(r.drawdown_semana or 0):.2%}."
        )
    else:
        l.append("Sin datos de patrimonio.")
    l += ["", "## Trades cerrados"]
    if r.trades_cerrados:
        for t in r.trades_cerrados:
            l.append(f"- {t['id']}: {_usd(t['resultado'])}. Tesis: {t['tesis'] or '-'}")
    else:
        l.append("Ninguno.")
    l += ["", "## Trades abiertos esta semana"]
    if r.trades_abiertos_en_la_semana:
        for t in r.trades_abiertos_en_la_semana:
            l.append(f"- {t['id']} ({t['contratos']} contratos, crédito {t['credito']}). Por qué: {t['razon_jefe'] or '-'}")
    else:
        l.append("Ninguno.")
    l += ["", "## Posiciones abiertas"]
    if r.posiciones_abiertas:
        for t in r.posiciones_abiertas:
            plan = (t.get("plan_salida") or {}).get("cierre_por_tiempo", "-")
            l.append(
                f"- {t['id']}: resultado no realizado {_usd(t.get('resultado_no_realizado'))}, "
                f"pérdida máxima {_usd(t['perdida_maxima'])}. Tesis: {t['tesis'] or '-'}. Cierre: {plan}"
            )
    else:
        l.append("Ninguna.")
    l += ["", "## Decisiones de la semana"]
    if r.decisiones:
        for d in r.decisiones:
            sobre = d.get("trade_debatido") or "sin candidatos"
            l.append(f"- {d['fecha']}: {d['decision']} ({sobre}). {d.get('razon') or d.get('sin_trade') or ''}".rstrip())
    else:
        l.append("No hubo ciclos esta semana.")
    l += ["", "## 1. Errores"]
    if r.errores:
        for e in r.errores:
            l.append(f"- {e['id']} ({_usd(e['resultado'])}): " + " ".join(e["motivos"]))
    else:
        l.append("No hubo trades perdedores cerrados esta semana.")
    if r.rechazados:
        l.append("")
        l.append("No operados (rechazados o sin enviar), si se hubieran llevado al vencimiento:")
        for x in r.rechazados:
            valor = _usd(x["resultado_hipotetico"]) if x["resultado_hipotetico"] is not None else x.get("nota", "-")
            l.append(f"- {x['id']} ({x['decision']}): {valor}")
    l += ["", "## 2. Fichas (todo el historial)"]
    for m, f in r.fichas.items():
        nombre = NOMBRES.get(m, "Jefe")
        tasa = "-" if f["tasa"] is None else f"{f['tasa']:.0%}"
        l.append(f"- {nombre}: {f['aciertos']} de {f['votos']} ({tasa})")
    l += ["", "## 3. Lecciones propuestas"]
    if r.lecciones:
        l.append(r.lecciones.get("resumen_semana", ""))
        for i, c in enumerate(r.lecciones.get("cambios", []), 1):
            l.append(f"{i}. **{c['leccion']}** {c['motivo']} Evidencia: {c['evidencia']}")
        if not r.lecciones.get("cambios"):
            l.append("Ningún cambio esta semana.")
    else:
        l.append("No se propusieron (corrida sin modelo).")
    if r.pruebas:
        l += ["", "## 4. Prueba con el backtest"]
        for p in r.pruebas:
            if p.get("error"):
                l.append(f"- {p['leccion']}: {p['error']}")
                continue
            a, n = p["comparacion"]["actual"], p["comparacion"]["propuesto"]
            l.append(
                f"- {p['leccion']}: actual {a['resultado_medio_usd']} USD por trade (peor caída {a['peor_drawdown_usd']}), "
                f"propuesto {n['resultado_medio_usd']} USD (peor caída {n['peor_drawdown_usd']}). {p['aviso']}"
            )
    if r.avisos:
        l += ["", "## Avisos"] + [f"- {a}" for a in r.avisos]
    l += ["", "Las lecciones no se aplican solas: decime cuáles aprobás y las sumo a agentes/lecciones.md."]
    return "\n".join(l)
