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

import time
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
from broker.base import Broker, ContratoOpcion, EstadoOrden, PataOrden
from datos.iv import simbolo_occ

MINUTOS_ANTES_DEL_CIERRE = 15
ESPERA_SEGUNDOS = 120
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
    sello = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefijo}-{tid}-{sello}-{intento}"[:48]


def _negociar(
    broker: Broker,
    patas: list[PataOrden],
    contratos: int,
    precios: list[float],
    abrir: bool,
    tid: str,
    esperar: Callable[[float], None],
    r: Resultado,
) -> Optional[EstadoOrden]:
    """Manda la orden a cada precio de la lista hasta que se llene. Devuelve la orden llena o None."""
    for n, precio in enumerate(precios):
        orden = broker.enviar_orden_multipata(patas, contratos, round(precio, 2), abrir, _id_cliente("ab" if abrir else "ci", tid, n))
        esperar(ESPERA_SEGUNDOS)
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


def patas_de(decision: dict) -> list[PataOrden]:
    venc = date.fromisoformat(decision["vencimiento"])
    return [
        PataOrden(simbolo_occ(decision["subyacente"], venc, p["tipo"], p["strike"]), p["lado"])
        for p in decision["patas"]
    ]


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

    patas = patas_de(decision)
    cot = broker.cotizaciones([p.simbolo for p in patas])
    cotizadas = all(p.simbolo in cot and cot[p.simbolo].bid is not None and cot[p.simbolo].ask for p in patas)
    check("Símbolos con cotización", cotizadas, ", ".join(p.simbolo for p in patas))
    vendidas = [p for p in patas if p.lado == "venta"]
    protegidas = all(
        any(q.lado == "compra" and q.simbolo[len(decision["subyacente"]) + 6] == v.simbolo[len(decision["subyacente"]) + 6] for q in patas)
        for v in vendidas
    )
    check("Cada pata vendida tiene su protección", bool(vendidas) and protegidas)
    check("Contratos >= 1", r.contratos >= 1, str(r.contratos))
    perdida = (decision.get("perdida_maxima") or 0) * r.contratos
    check(
        "Pérdida máxima dentro del 2% y del poder de compra",
        0 < perdida <= config.PERDIDA_MAX_TRADE * cuenta.patrimonio and perdida <= (cuenta.poder_de_compra_opciones or cuenta.poder_de_compra),
        f"{perdida:,.2f} USD",
    )
    reloj = broker.reloj()
    minutos = (reloj.proximo_cierre - reloj.ahora).total_seconds() / 60
    check("Mercado abierto y a más de 15 minutos del cierre", reloj.abierto and minutos > MINUTOS_ANTES_DEL_CIERRE, f"{minutos:.0f} min al cierre")

    minimo = float(orden["precio_limite"])
    if cotizadas:
        medio, natural = credito_actual(patas, cot)
        check("El crédito medio actual alcanza el mínimo aprobado", medio >= minimo, f"medio {medio:.2f}, mínimo {minimo:.2f}")
    if not all(ok for _, ok, _ in r.checklist):
        r.motivo = "No pasa el checklist: " + "; ".join(n for n, ok, _ in r.checklist if not ok)
        return _cerrar_registro(r, registrar and enviar)

    inicio = round(medio, 2)
    precios = [inicio]
    for _ in range(MAX_REBAJAS):
        siguiente = round(precios[-1] - 2 * PASO if precios[-1] - minimo > 0.05 else precios[-1] - PASO, 2)
        if siguiente < minimo:
            break
        precios.append(siguiente)
    if not enviar:
        r.motivo = f"Simulación: se mandaría {r.contratos} contrato(s) a {precios} de crédito."
        return _cerrar_registro(r, False)

    r.enviado = True
    lleno = _negociar(broker, patas, r.contratos, precios, True, tid, esperar, r)
    if lleno is None or not lleno.llena:
        r.motivo = r.motivo or "No se llenó sin bajar del crédito mínimo aprobado: se canceló."
        return _cerrar_registro(r, registrar)
    r.ejecutado = True
    r.precio = abs(lleno.precio_promedio if lleno.precio_promedio is not None else lleno.limite)
    ancho = max(abs(a["strike"] - b["strike"]) for a in decision["patas"] for b in decision["patas"] if a["tipo"] == b["tipo"])
    if registrar:
        registrar_trade(
            {
                "id": tid,
                "estado": "abierto",
                "subyacente": decision["subyacente"],
                "simbolos": [p.simbolo for p in patas],
                "patas": [{"simbolo": p.simbolo, "lado": p.lado} for p in patas],
                "vencimiento": decision["vencimiento"],
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
        r.checklist.append(("Débito para cerrar", True, f"medio {medio:.2f}, natural {natural:.2f}, crédito {t.credito}"))
        if motivo is None:
            r.motivo = "Se mantiene: ninguna regla de salida disparó."
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


def formatear(resultados: list[Resultado]) -> str:
    if not resultados:
        return "No hay nada para ejecutar ni posiciones abiertas."
    lineas = []
    for r in resultados:
        estado = "EJECUTADO" if r.ejecutado else ("ENVIADO SIN LLENAR" if r.enviado else "NO ENVIADO")
        lineas.append(f"{r.accion.upper()} {r.id}: {estado}. {r.motivo}")
        for nombre, ok, detalle in r.checklist:
            lineas.append(f"  [{'ok' if ok else 'NO'}] {nombre}" + (f" ({detalle})" if detalle else ""))
        for i in r.intentos:
            lineas.append(f"  intento a {i['precio']:.2f}: {i['estado']} ({i['llenados']} llenados)")
    return "\n".join(lineas)
