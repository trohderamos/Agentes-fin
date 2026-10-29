"""Fase 5: enviar la orden aprobada y gestionar las posiciones abiertas (skill ordenes-paper-alpaca).

Nada se envía salvo que se pida explícitamente (`enviar=True`) y el bróker se
haya creado con las órdenes permitidas. Sin eso, todo corre en simulación:
se hace el checklist y se muestra la orden que se mandaría.

Apertura:
- Solo la decisión APROBAR del día, con orden y plan de salida, que todavía
  no se haya ejecutado.
- Checklist del skill: freno en PERMITIDO, símbolos y lados correctos, la
  pata comprada protege a la vendida, capital suficiente, mercado abierto y a
  más de 15 minutos del cierre.
- Una sola orden de varias patas, con límite y vigencia del día. Empieza en
  el punto medio y baja de a 1 o 2 centavos, como máximo 3 veces, sin pasar
  el crédito mínimo que aprobó el jefe.

Gestión (una vez por día hábil):
- Tomar ganancia al 50% del crédito, stop en 2 veces el crédito, cerrar el
  día hábil anterior al vencimiento, y cerrar si el precio toca un strike
  vendido. Al cerrar por stop o por tiempo se sube el débito sin tope de
  intentos (hasta el precio natural): salir es la prioridad.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

from agentes import config
from agentes.bitacora import (
    TradeRegistrado,
    leer_decisiones,
    leer_ejecuciones,
    leer_trades,
    registrar_ejecucion,
    registrar_trade,
)
from agentes.consejo import STOP_VECES_CREDITO, TOMA_GANANCIA, dia_habil_anterior
from agentes.freno import evaluar_freno
from agentes.glosario import con_glosario, envolver
from broker.base import Broker, ContratoOpcion, EstadoOrden, PataOrden
from datos.iv import simbolo_occ

MINUTOS_ANTES_DEL_CIERRE = 15
ESPERA_SEGUNDOS = 120
ESPERA_APERTURA = 30
PASO = 0.01
MAX_REBAJAS = 3
MAX_INTENTOS_SALIDA = 15


@dataclass
class Resultado:
    accion: str  # "apertura" o "cierre"
    id: str
    enviado: bool
    ejecutado: bool = False
    motivo: str = ""
    checklist: list[tuple[str, bool, str]] = field(default_factory=list)
    intentos: list[dict] = field(default_factory=list)
    precio: Optional[float] = None
    contratos: int = 0

    def a_dict(self) -> dict:
        return dict(self.__dict__)


def _medio(c: ContratoOpcion) -> Optional[float]:
    return None if c.bid is None or c.ask is None else (c.bid + c.ask) / 2


def credito_actual(patas: list[PataOrden], cot: dict[str, ContratoOpcion]) -> tuple[float, float]:
    """Crédito al punto medio y natural (vender al bid, comprar al ask), por acción."""
    medio = natural = 0.0
    for p in patas:
        c = cot[p.simbolo]
        signo = 1 if p.lado == "venta" else -1
        medio += signo * _medio(c)
        natural += c.bid if p.lado == "venta" else -c.ask
    return medio, natural


def debito_actual(patas: list[PataOrden], cot: dict[str, ContratoOpcion]) -> tuple[float, float]:
    """Lo que cuesta cerrar: al punto medio y natural (recomprar al ask, vender al bid)."""
    medio = natural = 0.0
    for p in patas:
        c = cot[p.simbolo]
        signo = 1 if p.lado == "venta" else -1
        medio += signo * _medio(c)
        natural += c.ask if p.lado == "venta" else -c.bid
    return medio, natural


def _id_cliente(prefijo: str, tid: str, intento: int) -> str:
    """Id único para cada envío: hora, intento y un sufijo al azar van primero.

    Si hay que cortar, se corta el id del trade (que es solo para leerlo), nunca
    la parte que hace único al id: Alpaca rechaza un client_order_id repetido.
    """
    sello = datetime.now(timezone.utc).strftime("%m%d%H%M%S")
    return f"{prefijo}{sello}-{intento}-{uuid.uuid4().hex[:6]}-{tid}"[:48]


def _negociar(
    broker: Broker,
    patas: list[PataOrden],
    contratos: int,
    precios: list[float],
    abrir: bool,
    tid: str,
    esperar: Callable[[float], None],
    r: Resultado,
    espera: float = ESPERA_SEGUNDOS,
) -> Optional[EstadoOrden]:
    """Manda la orden a cada precio de la lista hasta que se llene. Devuelve la orden llena o None."""
    for n, precio in enumerate(precios):
        orden = broker.enviar_orden_multipata(patas, contratos, round(precio, 2), abrir, _id_cliente("ab" if abrir else "ci", tid, n))
        esperar(espera)
        estado = broker.estado_orden(orden.id)
        r.intentos.append({"precio": round(precio, 2), "estado": estado.estado, "llenados": estado.contratos_llenados})
        if estado.llena:
            return estado
        if estado.contratos_llenados:
            r.motivo = f"Llenado parcial ({estado.contratos_llenados} de {contratos}): avisar al gestor de posición."
            broker.cancelar_orden(orden.id)
            return estado
        if not estado.terminada:
            broker.cancelar_orden(orden.id)
    return None


# --- Apertura --------------------------------------------------------------


def decision_pendiente(hoy: date, decisiones: list[dict], trades: list[TradeRegistrado], ejecuciones: list[dict]) -> Optional[dict]:
    """La última decisión APROBAR de hoy que todavía no se ejecutó ni se intentó."""
    hechos = {t.id for t in trades} | {e.get("id") for e in ejecuciones if e.get("accion") == "apertura" and e.get("enviado")}
    for d in reversed(decisiones):
        if d.get("decision") == "APROBAR" and d.get("orden_final") and d.get("fecha") == hoy.isoformat():
            return None if d["orden_final"]["id"] in hechos else d
    return None


def patas_de(trade: dict) -> list[PataOrden]:
    venc = date.fromisoformat(trade["vencimiento"])
    return [
        PataOrden(simbolo_occ(trade["subyacente"], venc, p["tipo"], p["strike"]), p["lado"])
        for p in trade["patas"]
    ]


def _coincide_con_id(tid: str, subyacente: str, vencimiento: str, patas: list[dict]) -> bool:
    """El id de un candidato es SUBYACENTE-estructura-VENCIMIENTO-strikes vendidos (P689-C748)."""
    marca = f"-{vencimiento}-"
    if not tid.startswith(f"{subyacente}-") or marca not in tid:
        return False
    # Un "-A10" al final es el ancho de las alas, no un strike.
    en_id = {x for x in tid.split(marca, 1)[1].split("-") if not x.startswith("A")}
    vendidos = {f"{p['tipo'][0].upper()}{float(p['strike']):g}" for p in patas if p.get("lado") == "venta"}
    return bool(vendidos) and en_id == vendidos


def trade_aprobado(decision: dict) -> tuple[Optional[dict], str]:
    """Subyacente, vencimiento, patas y pérdida máxima del candidato que aprobó el jefe.

    El jefe puede aprobar un candidato distinto del que debatió el consejo: las
    patas se toman de `orden_final` (el ciclo guarda ahí las del candidato
    aprobado). Las del nivel de arriba son las del debatido y solo se usan si
    el jefe aprobó ese mismo. En cualquier caso las patas tienen que coincidir
    con el id aprobado; si no, devuelve None y el motivo, y no se envía nada.
    """
    orden = decision.get("orden_final") or {}
    tid = str(orden.get("id") or "")
    subyacente = decision.get("subyacente")
    if orden.get("patas") and orden.get("vencimiento"):
        fuente = orden
    elif decision.get("trade_debatido") not in (None, tid):
        return None, f"El jefe aprobó {tid} pero la decisión solo tiene las patas de {decision['trade_debatido']}."
    else:
        fuente = decision
    trade = {
        "id": tid,
        "subyacente": subyacente,
        "vencimiento": fuente.get("vencimiento"),
        "patas": fuente.get("patas") or [],
        "perdida_maxima": fuente.get("perdida_maxima"),
    }
    if not (subyacente and trade["vencimiento"] and trade["patas"]):
        return None, f"Faltan el vencimiento o las patas de {tid}."
    if not _coincide_con_id(tid, subyacente, trade["vencimiento"], trade["patas"]):
        strikes = ", ".join(f"{p.get('lado')} {p.get('tipo')} {p.get('strike')}" for p in trade["patas"])
        return None, f"Las patas ({trade['vencimiento']}: {strikes}) no coinciden con el id aprobado {tid}."
    return trade, ""


def escalera_entrada(medio: float) -> list[float]:
    """Créditos a pedir: del medio al piso (una fracción del medio), en los minutos de negociación."""
    inicio = round(medio, 2)
    piso = round(medio * config.FRACCION_PISO_ENTRADA, 2)
    intentos = max(1, int(config.MINUTOS_NEGOCIAR_ENTRADA * 60 // ESPERA_APERTURA))
    paso = max(PASO, math.ceil((inicio - piso) / max(1, intentos - 1) * 100 - 1e-9) / 100)
    precios = [inicio]
    while len(precios) < intentos and precios[-1] - paso >= piso - 1e-9:
        precios.append(round(precios[-1] - paso, 2))
    if precios[-1] > piso and len(precios) < intentos:
        precios.append(piso)
    return precios


def abrir(
    broker: Broker,
    decision: dict,
    hoy: date,
    enviar: bool = False,
    trades: Optional[list[TradeRegistrado]] = None,
    esperar: Callable[[float], None] = time.sleep,
    registrar: bool = True,
) -> Resultado:
    orden = decision["orden_final"]
    tid = orden["id"]
    r = Resultado(accion="apertura", id=tid, enviado=False, contratos=int(orden["contratos"]))
    trades = leer_trades() if trades is None else trades

    def check(nombre: str, ok: bool, detalle: str = "") -> bool:
        r.checklist.append((nombre, ok, detalle))
        return ok

    cuenta = broker.cuenta()  # frena si no es paper
    check("Cuenta paper", cuenta.es_paper)
    check("Aprobado por el jefe hoy, con plan de salida", decision.get("decision") == "APROBAR" and bool(decision.get("plan_salida")))
    freno = evaluar_freno(hoy, cuenta, broker.historial_patrimonio(hoy - timedelta(days=45)), broker.posiciones(), trades)
    check("Freno por drawdown en PERMITIDO", freno.permite, freno.estado)

    trade, problema = trade_aprobado(decision)
    if trade is None:
        # Sin poder identificar sin ambigüedad el candidato aprobado no se envía nada.
        check("Patas del candidato aprobado por el jefe", False, problema)
        r.motivo = "No se envía: " + problema
        return _cerrar_registro(r, registrar and enviar)
    check("Patas del candidato aprobado por el jefe", True, f"{trade['vencimiento']}")
    patas = patas_de(trade)
    cot = broker.cotizaciones([p.simbolo for p in patas])
    cotizadas = all(p.simbolo in cot and cot[p.simbolo].bid is not None and cot[p.simbolo].ask for p in patas)
    check("Símbolos con cotización", cotizadas, ", ".join(p.simbolo for p in patas))
    vendidas = [p for p in patas if p.lado == "venta"]
    protegidas = all(
        any(q.lado == "compra" and q.simbolo[len(trade["subyacente"]) + 6] == v.simbolo[len(trade["subyacente"]) + 6] for q in patas)
        for v in vendidas
    )
    check("Cada pata vendida tiene su protección", bool(vendidas) and protegidas)
    check("Contratos >= 1", r.contratos >= 1, str(r.contratos))
    perdida = (trade.get("perdida_maxima") or 0) * r.contratos
    check(
        "Pérdida máxima dentro del 2% y del poder de compra",
        0 < perdida <= config.PERDIDA_MAX_TRADE * cuenta.patrimonio and perdida <= (cuenta.poder_de_compra_opciones or cuenta.poder_de_compra),
        f"{perdida:,.2f} USD",
    )
    reloj = broker.reloj()
    minutos = (reloj.proximo_cierre - reloj.ahora).total_seconds() / 60
    margen = MINUTOS_ANTES_DEL_CIERRE + config.MINUTOS_NEGOCIAR_ENTRADA
    check(f"Mercado abierto y a más de {margen} minutos del cierre", reloj.abierto and minutos > margen, f"{minutos:.0f} min al cierre")

    # El piso sale del crédito medio de ahora, no del precio_limite del debate
    # (lección aprobada el 27 oct 2026: 2 de 3 aprobados no se llenaron por eso).
    if cotizadas:
        medio, natural = credito_actual(patas, cot)
        check("Crédito medio positivo", medio > 0, f"medio {medio:.2f}, natural {natural:.2f}")
    if not all(ok for _, ok, _ in r.checklist):
        r.motivo = "No pasa el checklist: " + "; ".join(n for n, ok, _ in r.checklist if not ok)
        return _cerrar_registro(r, registrar and enviar)

    precios = escalera_entrada(medio)
    if not enviar:
        r.motivo = f"Simulación: se mandaría {r.contratos} contrato(s) a {precios} de crédito."
        return _cerrar_registro(r, False)

    r.enviado = True
    lleno = _negociar(broker, patas, r.contratos, precios, True, tid, esperar, r, ESPERA_APERTURA)
    if lleno is None or not lleno.llena:
        r.motivo = r.motivo or f"No se llenó entre {precios[0]:.2f} y el piso de {precios[-1]:.2f}: se canceló."
        return _cerrar_registro(r, registrar)
    r.ejecutado = True
    r.precio = abs(lleno.precio_promedio if lleno.precio_promedio is not None else lleno.limite)
    ancho = max(abs(a["strike"] - b["strike"]) for a in trade["patas"] for b in trade["patas"] if a["tipo"] == b["tipo"])
    if registrar:
        registrar_trade(
            {
                "id": tid,
                "estado": "abierto",
                "subyacente": trade["subyacente"],
                "simbolos": [p.simbolo for p in patas],
                "patas": [{"simbolo": p.simbolo, "lado": p.lado} for p in patas],
                "vencimiento": trade["vencimiento"],
                "abierto_el": hoy.isoformat(),
                "contratos": r.contratos,
                "credito": r.precio,
                "credito_medio_al_enviar": round(medio, 4),
                "slippage_vs_medio": round(medio - r.precio, 4),
                "intentos": len(r.intentos),
                "perdida_maxima": round((ancho - r.precio) * 100 * r.contratos, 2),
                "delta": round(sum((-1 if p.lado == "venta" else 1) * (cot[p.simbolo].delta or 0) for p in patas) * 100 * r.contratos, 4),
                "gamma": round(sum((-1 if p.lado == "venta" else 1) * (cot[p.simbolo].gamma or 0) for p in patas) * 100 * r.contratos, 4),
                "vega": round(sum((-1 if p.lado == "venta" else 1) * (cot[p.simbolo].vega or 0) for p in patas) * 100 * r.contratos, 4),
                "plan_salida": decision.get("plan_salida"),
                # Con qué regla de eventos se tomó este trade, para compararlas
                # después (el usuario cambió la regla el 27 oct 2026).
                "regla_eventos": decision.get("regla_eventos"),
                "colchon_eventos": decision.get("colchon_eventos"),
            }
        )
    return _cerrar_registro(r, registrar)


def _cerrar_registro(r: Resultado, registrar: bool) -> Resultado:
    if registrar:
        registrar_ejecucion(
            {"registrado": datetime.now(timezone.utc).isoformat(timespec="seconds"), **r.a_dict()}
        )
    return r


# --- Gestión de posiciones abiertas ----------------------------------------


def motivo_de_cierre(
    t: TradeRegistrado, hoy: date, debito_medio: float, precio: Optional[float], strikes_vendidos: list[tuple[str, float]]
) -> Optional[str]:
    credito = t.credito or 0
    if credito and debito_medio <= credito * (1 - TOMA_GANANCIA):
        return "ganancia"
    if credito and debito_medio >= credito * (1 + STOP_VECES_CREDITO):
        return "stop"
    if t.vencimiento and hoy >= dia_habil_anterior(t.vencimiento):
        return "vencimiento cercano"
    if precio is not None:
        for tipo, k in strikes_vendidos:
            if (tipo == "put" and precio <= k) or (tipo == "call" and precio >= k):
                return "tocó el strike"
    return None


def gestionar(
    broker: Broker,
    hoy: date,
    enviar: bool = False,
    trades: Optional[list[TradeRegistrado]] = None,
    esperar: Callable[[float], None] = time.sleep,
    registrar: bool = True,
) -> list[Resultado]:
    from broker.alpaca_paper import parsear_simbolo_occ

    trades = leer_trades() if trades is None else trades
    resultados = []
    broker.cuenta()
    for t in [t for t in trades if t.estado == "abierto"]:
        r = Resultado(accion="cierre", id=t.id, enviado=False, contratos=t.contratos)
        if not t.patas:
            r.motivo = "El trade no tiene sus patas en la bitácora: revisarlo a mano."
            resultados.append(_cerrar_registro(r, registrar and enviar))
            continue
        patas = [PataOrden(s, lado) for s, lado in t.patas]
        cot = broker.cotizaciones([p.simbolo for p in patas])
        if not all(p.simbolo in cot and cot[p.simbolo].bid is not None and cot[p.simbolo].ask for p in patas):
            r.motivo = "Faltan cotizaciones de alguna pata."
            resultados.append(_cerrar_registro(r, registrar and enviar))
            continue
        medio, natural = debito_actual(patas, cot)
        vendidos = []
        for p in patas:
            if p.lado == "venta":
                _, _, tipo, k = parsear_simbolo_occ(p.simbolo)
                vendidos.append((tipo, k))
        motivo = motivo_de_cierre(t, hoy, medio, broker.precio_subyacente(t.subyacente), vendidos)
        # Cuánto dejaría el trade si lo cerráramos en este momento. No decide nada:
        # es para que el informe diga en plata cómo viene la posición.
        si_cierro = round(((t.credito or 0) - medio) * 100 * t.contratos, 2)
        r.checklist.append(
            (
                "Cuánto cuesta cerrarlo hoy",
                True,
                f"recomprarlo sale {medio:.2f} por acción y cobramos {t.credito}: si cerráramos ahora "
                f"quedaría en {si_cierro:+,.2f} USD (al precio que se llena seguro, {natural:.2f}, sería peor)",
            )
        )
        if motivo is None:
            r.motivo = (
                "Se mantiene abierto: no se cumplió ninguna de las condiciones de salida del plan. "
                f"Si lo cerráramos ahora quedaría en {si_cierro:+,.2f} USD."
            )
            resultados.append(_cerrar_registro(r, registrar and enviar))
            continue
        reloj = broker.reloj()
        precios = [round(max(medio, 0.01), 2)]
        tope = natural if motivo in ("stop", "vencimiento cercano", "tocó el strike") else medio + 0.03
        limite_intentos = MAX_INTENTOS_SALIDA if motivo != "ganancia" else MAX_REBAJAS
        while len(precios) <= limite_intentos and precios[-1] + PASO <= tope + 1e-9:
            precios.append(round(precios[-1] + PASO, 2))
        if not enviar or not reloj.abierto:
            r.motivo = f"{'Simulación' if reloj.abierto else 'Mercado cerrado'}: cerrar por {motivo}, débito {precios[0]:.2f} a {precios[-1]:.2f}."
            resultados.append(_cerrar_registro(r, registrar and enviar))
            continue
        r.enviado = True
        lleno = _negociar(broker, patas, t.contratos, precios, False, t.id, esperar, r)
        if lleno is None or not lleno.llena:
            r.motivo = r.motivo or f"Cerrar por {motivo}: no se llenó hasta {precios[-1]:.2f}. Reintentar."
            resultados.append(_cerrar_registro(r, registrar))
            continue
        r.ejecutado = True
        r.precio = abs(lleno.precio_promedio) if lleno.precio_promedio is not None else precios[-1]
        resultado = round(((t.credito or 0) - r.precio) * 100 * t.contratos - config.COMISION_PATA * len(patas) * t.contratos, 2)
        r.motivo = f"Cerrado por {motivo}: {resultado:+,.2f} USD."
        if registrar:
            registrar_trade(
                {
                    "id": t.id,
                    "estado": "cerrado",
                    "subyacente": t.subyacente,
                    "simbolos": list(t.simbolos),
                    "patas": [{"simbolo": s, "lado": lado} for s, lado in t.patas],
                    "vencimiento": t.vencimiento.isoformat() if t.vencimiento else None,
                    "abierto_el": t.abierto_el.isoformat() if t.abierto_el else None,
                    "cerrado_el": hoy.isoformat(),
                    "contratos": t.contratos,
                    "credito": t.credito,
                    "debito_cierre": r.precio,
                    "perdida_maxima": t.perdida_maxima,
                    "resultado": resultado,
                    "resultado_sobre_credito": round(resultado / ((t.credito or 1) * 100 * t.contratos), 3),
                    "motivo_cierre": motivo,
                    "dias_abierto": (hoy - t.abierto_el).days if t.abierto_el else None,
                }
            )
        resultados.append(_cerrar_registro(r, registrar))
    return resultados


ESTADOS = {
    "ejecutado": "HECHO, la orden se llenó",
    "enviado": "MANDADA PERO SIN LLENAR, nadie la tomó al precio que pedimos",
    "no_enviado": "NO SE MANDÓ NINGUNA ORDEN",
}

ACCIONES = {"apertura": "ABRIR", "cierre": "CERRAR"}


def formatear(resultados: list[Resultado]) -> str:
    if not resultados:
        return "No hay nada para ejecutar ni posiciones abiertas."
    lineas = []
    for r in resultados:
        clave = "ejecutado" if r.ejecutado else ("enviado" if r.enviado else "no_enviado")
        lineas.append(f"{ACCIONES.get(r.accion, r.accion.upper())} {r.id}: {ESTADOS[clave]}.")
        lineas += envolver(f"Qué pasó: {r.motivo}", sangria="  ") if r.motivo else []
        if r.checklist:
            lineas.append("  Controles antes de tocar la cuenta:")
            for nombre, ok, detalle in r.checklist:
                lineas.append(f"    [{'ok' if ok else 'NO'}] {nombre}" + (f": {detalle}" if detalle else ""))
        if r.intentos:
            lineas.append("  Intentos de llenado (se mejora el precio de a un centavo hasta el tope):")
            for i in r.intentos:
                lineas.append(f"    a {i['precio']:.2f} por acción: {i['estado']} ({i['llenados']} patas llenadas)")
    return con_glosario("\n".join(lineas))
