"""Guardia de noticias: el agente News fuera del ciclo.

Corre a la mañana temprano y a la tarde, además de la vez que corre dentro del
ciclo. Revisa qué pasó con cada activo del universo, actualiza el calendario de
eventos y avisa si algo amenaza a un trade abierto. No da órdenes: el plan de
salida lo ejecuta `gestionar`. Cada revisión queda en bitacora/noticias.jsonl y
el News del ciclo lee las del día.
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from agentes import config
from agentes.bitacora import TradeRegistrado, leer_trades
from agentes.modelo import Modelo, Pedido
from agentes.research import ESQUEMA_NEWS, MAYORES_DEL_INDICE, TAREA_NEWS, _evento, _json, _noticia, _sistema
from broker.base import Broker
from datos.eventos import cargar_eventos

ARCHIVO_NOTICIAS = Path(__file__).parent.parent / "bitacora" / "noticias.jsonl"

TAREA_GUARDIA = (
    TAREA_NEWS
    + """

Esta es una revisión fuera del ciclo (a la mañana o a la tarde): no hay candidatos que evaluar.
Contá qué cambió desde las revisiones anteriores de hoy, si las hay. Para cada trade abierto de este
activo, decí en 'trades_abiertos' si algo amenaza su tesis: "ninguna", "vigilar" o "cerrar"
(por ejemplo un evento de impacto alto nuevo antes del vencimiento, o una noticia que puede mover el
precio hasta el strike vendido). No das órdenes: el gestor de posición decide con su plan."""
)

ESQUEMA_GUARDIA = copy.deepcopy(ESQUEMA_NEWS)
ESQUEMA_GUARDIA["properties"]["trades_abiertos"] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "amenaza": {"type": "string", "enum": ["ninguna", "vigilar", "cerrar"]},
            "motivo": {"type": "string"},
        },
        "required": ["id", "amenaza", "motivo"],
        "additionalProperties": False,
    },
}
ESQUEMA_GUARDIA["required"] = ESQUEMA_GUARDIA["required"] + ["trades_abiertos"]


def _trade(t: TradeRegistrado) -> dict:
    return {"id": t.id, "simbolos": list(t.simbolos), "credito": t.credito, "abierto_el": t.abierto_el}


async def revisar_noticias(
    broker: Broker,
    modelo: Modelo,
    subyacente: str,
    hoy: date,
    trades: Optional[list[TradeRegistrado]] = None,
    previas: Optional[list[dict]] = None,
) -> dict:
    """Una revisión del agente News para un activo. Devuelve su informe con hora y costo."""
    trades = leer_trades() if trades is None else trades
    abiertos = [t for t in trades if t.estado == "abierto" and t.subyacente == subyacente]
    ultimo_venc = hoy + timedelta(days=config.DIAS_MAX)
    eventos = [e for e in cargar_eventos() if hoy <= e.fecha <= ultimo_venc]
    try:
        noticias = broker.noticias([subyacente], hoy - timedelta(days=2), 30)
    except Exception:  # las noticias de Benzinga no frenan la revisión
        noticias = []
    pedido = Pedido(
        agente="News",
        sistema=_sistema("News", hoy, ["research-opciones-primas", "calendario-catalizadores"], TAREA_GUARDIA),
        mensaje=_json(
            {
                "subyacente": subyacente,
                "ventana": {"desde": hoy.isoformat(), "hasta": ultimo_venc.isoformat()},
                "empresas_de_mayor_peso": MAYORES_DEL_INDICE.get(subyacente, []),
                "eventos_cargados": [_evento(e) for e in eventos],
                "noticias_benzinga": [_noticia(n) for n in noticias],
                "trades_abiertos": [_trade(t) for t in abiertos],
                "revisiones_previas_de_hoy": previas if previas is not None else notas_de_hoy(subyacente, hoy),
            }
        ),
        esquema=ESQUEMA_GUARDIA,
        busqueda_web=True,
        max_turnos=20,
    )
    respuesta = await modelo.pedir(pedido)
    return {
        "subyacente": subyacente,
        "fecha": hoy.isoformat(),
        "hora": datetime.now(ZoneInfo("America/New_York")).strftime("%H:%M ET"),
        "costo_usd": respuesta.costo_usd,
        **respuesta.datos,
    }


def registrar_noticias(registro: dict, archivo: Path = ARCHIVO_NOTICIAS) -> None:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    with archivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


def notas_de_hoy(subyacente: str, hoy: date, archivo: Path = ARCHIVO_NOTICIAS) -> list[dict]:
    """Lo esencial de las revisiones de hoy de ese activo, para el News del ciclo."""
    if not archivo.exists():
        return []
    notas = []
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        d = json.loads(linea)
        if d.get("subyacente") == subyacente and d.get("fecha") == hoy.isoformat():
            notas.append(
                {
                    "hora": d.get("hora"),
                    "por_que_se_mueve": d.get("por_que_se_mueve"),
                    "hechos": d.get("hechos", []),
                    "eventos": d.get("eventos", []),
                    "trades_abiertos": d.get("trades_abiertos", []),
                }
            )
    return notas


def formatear_noticias(d: dict) -> str:
    lineas = [f"NOTICIAS {d['subyacente']} · {d['fecha']} {d['hora']}"]
    lineas.append(f"  Sentimiento: {d.get('sentimiento')}. {d.get('por_que_se_mueve', '')}")
    for h in d.get("hechos", [])[:6]:
        lineas.append(f"  - {h}")
    if d.get("rumores"):
        lineas.append("  Rumores: " + "; ".join(d["rumores"][:3]))
    for e in d.get("eventos", []):
        if e.get("impacto") == "alto":
            lineas.append(f"  Evento alto: {e['fecha']} {e.get('hora', '')} {e['evento']} ({e.get('tipo', '')})")
    lineas.append(f"  Veredicto: {d.get('veredicto')} {d.get('motivo_veredicto', '')}")
    for t in d.get("trades_abiertos", []):
        lineas.append(f"  Trade {t['id']}: amenaza {t['amenaza'].upper()}. {t['motivo']}")
    return "\n".join(lineas)
