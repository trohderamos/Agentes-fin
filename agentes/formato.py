"""Texto del resultado de research para la terminal.

Lo que se imprime acá es lo que termina leyendo el usuario, así que la regla es
la misma que para los agentes: primero la conclusión en criollo, después los
números que la sostienen, y cada término técnico explicado. El pie
"QUÉ SIGNIFICA CADA COSA" lo arma agentes/glosario.py con los términos que de
verdad aparecieron.
"""

from __future__ import annotations

from agentes.glosario import con_glosario, envolver as _envolver
from agentes.noticias import seccion_mundo
from agentes.research import ResultadoResearch


def _n(v, dec: int = 2) -> str:
    return "-" if v is None else f"{v:,.{dec}f}"


def _informe(titulo: str, d: dict | None) -> list[str]:
    if not d:
        return []
    lineas = [titulo]
    if d.get("sin_modelo"):
        lineas.append("  (corrida sin modelo: regla fija)")
    if d.get("en_criollo"):
        lineas += _envolver(d["en_criollo"])
        lineas.append("")
    if d.get("tesis"):
        lineas += _envolver(f"En una frase: {d['tesis']}")
    if d.get("confianza"):
        lineas += _envolver(f"Qué tan seguro está: {d['confianza']}. {d.get('motivo_confianza', '')}".rstrip())
    if d.get("datos"):
        lineas.append("  Los números en que se apoya:")
        for dato in d.get("datos", [])[:8]:
            lineas.append(f"    - {dato}")
    if d.get("invalidaria"):
        lineas.append("  Qué lo daría vuelta: " + "; ".join(d["invalidaria"]))
    if d.get("faltantes"):
        lineas.append("  Datos que no pudo conseguir: " + "; ".join(d["faltantes"]))
    return lineas + [""]


def _patas_en_palabras(patas: list[dict]) -> tuple[str, str]:
    """Lo que vendemos y lo que compramos, sin signos ni cantidades negativas."""
    vendidas = [f"{p['tipo']} {p['strike']:g}" for p in patas if p["lado"] == "venta"]
    compradas = [f"{p['tipo']} {p['strike']:g}" for p in patas if p["lado"] != "venta"]
    return " y ".join(vendidas), " y ".join(compradas)


def _equilibrio_en_palabras(subyacente: str, c: dict) -> str:
    """Hasta dónde puede moverse el precio sin que el trade pierda, dicho sin tecnicismos."""
    bajo, alto = c.get("equilibrio_bajo"), c.get("equilibrio_alto")
    if bajo is not None and alto is not None:
        return (
            f"Al vencimiento no perdemos mientras {subyacente} quede entre {_n(bajo)} y {_n(alto)}"
            " (los puntos de equilibrio)."
        )
    if bajo is not None:
        return f"Al vencimiento no perdemos mientras {subyacente} se mantenga arriba de {_n(bajo)} (el punto de equilibrio)."
    if alto is not None:
        return f"Al vencimiento no perdemos mientras {subyacente} se mantenga debajo de {_n(alto)} (el punto de equilibrio)."
    return "No se pudo calcular el punto de equilibrio."


def _candidato_en_palabras(subyacente: str, c: dict, rev: dict) -> list[str]:
    vendidas, compradas = _patas_en_palabras(c["patas"])
    credito_usd = (c["credito"] or 0) * 100
    lineas = [
        f"     Vendemos: {vendidas or '-'}."
        + (f" Compramos como seguro, más lejos: {compradas}." if compradas else ""),
        f"     Cobramos hoy {_n(credito_usd)} USD por contrato (el crédito es {c['credito']:g} por acción y cada"
        f" contrato son 100 acciones). Esa plata entra ahora pero recién se gana al cerrar el trade.",
        f"     Lo máximo que podemos ganar: {_n(c['ganancia_maxima'])} USD."
        f" Lo máximo que podemos perder: {_n(c['perdida_maxima'])} USD.",
        f"     Por cada dólar arriesgado podemos ganar {c['retorno_sobre_riesgo']:.0%}"
        f" (retorno sobre riesgo).",
        f"     {_equilibrio_en_palabras(subyacente, c)} Chance aproximada de que eso pase:"
        f" {c['prob_ganancia']:.0%}.",
        f"     Arriesgamos {rev['riesgo_pct']:.2%} del capital por contrato; entran hasta"
        f" {rev['contratos_max']} contratos en el tope del 2%.",
        f"     Ficha técnica: crédito medio {_n(c['credito'])} y natural {_n(c['credito_natural'])}"
        f" (el natural es el precio al que se llena seguro, peor para nosotros) ·"
        f" delta {_n(c['delta'])} · gamma {_n(c['gamma'], 3)} · theta {_n(c['theta'])} · vega {_n(c['vega'])}.",
    ]
    return lineas


def formatear_research(r: ResultadoResearch, glosario: bool = True) -> str:
    lineas = [
        f"RESEARCH {r.subyacente} · {r.fecha.isoformat()}"
        + ("" if r.con_modelo else "  (SIN MODELO: solo cálculos en código)"),
        f"  Capital de la cuenta: {_n(r.capital)} USD · {r.subyacente} cotiza a {_n(r.precio)}",
        f"  Freno por drawdown: {r.freno.estado} ({'; '.join(r.freno.motivos)})",
        "",
    ]
    lineas += _informe("CHART (precio, tendencia y volatilidad)", r.chart)
    if r.chart and "tendencia" in r.chart:
        lineas.insert(
            -1,
            f"  Lectura del gráfico: tendencia {r.chart['tendencia']} · conviene vender "
            f"{r.chart['lado_a_vender']} · pisos (soportes) {r.chart.get('soportes')} · techos "
            f"(resistencias) {r.chart.get('resistencias')}",
        )
    lineas += _informe("NEWS (noticias y calendario de eventos)", r.news)
    if r.news:
        lineas.insert(-1, f"  Veredicto: {r.news.get('veredicto')}. {r.news.get('motivo_veredicto', '')}".rstrip())
        if r.news.get("eventos"):
            lineas.insert(-1, "  Eventos en el camino hasta el vencimiento:")
        for e in r.news.get("eventos", []):
            estado = "fecha confirmada" if e.get("confirmado") else "fecha estimada"
            lineas.insert(-1, f"    {e.get('fecha')} {e.get('hora', '')} {e.get('evento')} [impacto {e.get('impacto')}, {estado}]")
        for linea in seccion_mundo(r.news.get("contexto_mundo")):
            lineas.insert(-1, linea)
    lineas += _informe("FUNDAMENTALS (cómo viene la empresa o el índice)", r.fundamentals)
    if r.fundamentals and "valuacion" in r.fundamentals:
        f = r.fundamentals
        lineas.insert(
            -1,
            f"  Valuación {f['valuacion']} · estimaciones {f['estimaciones']} · conviene vender {f['lado_a_vender']}"
            f" · ¿nos quedaríamos con el activo si nos lo asignan?: {'sí' if f['se_querria_tener'] else 'no'}",
        )
    lineas += _informe("STRATEGY (qué estructura armamos)", r.strategy)
    lineas += _informe("RISK (qué puede salir mal y cuánto arriesgamos)", r.risk)

    if r.no_hay_trade:
        lineas += ["NO HAY TRADE HOY", f"  {r.motivo_no_trade}"]
    else:
        lineas.append(f"CANDIDATOS PARA EL CONSEJO ({len(r.candidatos)})")
        for i, x in enumerate(r.candidatos, 1):
            c = x["candidato"]
            rev = x["revision"]
            lineas += [
                "",
                f"  {i}. {c['estructura'].replace('_', ' ')} de {r.subyacente} que vence el {c['vencimiento']}"
                f" (dentro de {c['dias']} días)" + ("  ← el preferido" if x["preferido"] else ""),
            ]
            lineas += _candidato_en_palabras(r.subyacente, c, rev)
            if x.get("motivo"):
                lineas.append(f"     Por qué lo eligió Strategy: {x['motivo']}")
            if x.get("riesgo_principal") and x["riesgo_principal"] != "-":
                lineas.append(f"     Riesgo principal: {x['riesgo_principal']}")
            if x.get("comentario_risk"):
                lineas.append(f"     Qué dice Risk: {x['comentario_risk']}")
            for a in rev["alertas"]:
                lineas.append(f"     ALERTA: {a}")
            bt = x.get("backtest")
            if bt:
                lineas.append(
                    f"     Backtest aproximado (la misma estructura probada en {bt['periodo']},"
                    f" {bt['trades']} trades): {bt['veredicto']} · salió bien el "
                    f"{_n((bt['tasa_acierto'] or 0) * 100, 0)}% de las veces · resultado medio {_n(bt['resultado_medio_usd'])} USD"
                    f" · el peor caso dio {_n(bt['peor_trade_usd'])} USD"
                )

    if r.descartados:
        lineas += ["", f"DESCARTADOS ANTES DE EMPEZAR ({len(r.descartados)})"]
        for cid, motivos in list(r.descartados.items())[:12]:
            lineas.append(f"  {cid}: {motivos[0]}" + (f" (+{len(motivos) - 1})" if len(motivos) > 1 else ""))
    if r.avisos:
        lineas += ["", "AVISOS"] + [f"  - {a}" for a in r.avisos]
    if r.con_modelo:
        lineas += ["", f"Costo de los modelos: {_n(r.costo_usd, 4)} USD"]
    lineas += ["", "Research no envía órdenes."]
    texto = "\n".join(lineas)
    return con_glosario(texto) if glosario else texto


def formatear_ciclo(ciclo) -> str:
    from agentes.consejo import NOMBRES

    partes = []
    for v in ciclo.vueltas:
        partes.append(f"======== VUELTA {v.numero} ========")
        partes.append(formatear_research(v.research, glosario=False))
        if v.debate is not None:
            d = v.debate
            c = d.costos
            partes += [
                "",
                f"CONSEJO sobre {d.trade['candidato']['id']}" + ("" if d.con_modelo else "  (sin modelo: reglas fijas)"),
                f"  Lo que cuesta entrar y salir: {c['costo_total_usd']} USD, o sea el "
                f"{(c['costos_sobre_credito'] or 0):.0%} de la prima que cobramos (el tope es 15%). La diferencia "
                f"entre la punta compradora y la vendedora se lleva otro "
                f"{(c['spread_compra_venta_sobre_credito'] or 0):.0%} del crédito.",
            ]
            if d.escenarios:
                partes.append("  Si el precio se mueve, por contrato nos quedaría:")
            for e in d.escenarios:
                partes.append(f"    {e['escenario']}: {e['resultado_por_contrato_usd']} USD")
            for i, ronda in enumerate(d.rondas, 1):
                partes.append(f"  Ronda {i} del debate:")
                for m, p in ronda.items():
                    extra = f" Cambio que propone: {p['cambio_propuesto']}" if p.get("cambio_propuesto") else ""
                    partes.append(f"    {NOMBRES[m]}: vota {p.get('voto')} (confianza {p.get('confianza')}).")
                    if p.get("en_criollo"):
                        partes += _envolver(p["en_criollo"], sangria="      ")
                    partes += _envolver(f"{p.get('postura', '')}{extra}".strip(), sangria="      ")
                    if p.get("respuesta_a_otros"):
                        partes += _envolver(f"Le responde a los otros: {p['respuesta_a_otros']}", sangria="      ")
                    if p.get("orden_invalida"):
                        partes.append(f"      Orden descartada: {p['orden_invalida']}")
        dec = v.decision
        partes += ["", f"JEFE: {dec.get('decision')}" + (" (sin modelo: regla fija)" if dec.get("sin_modelo") else "")]
        if dec.get("en_criollo"):
            partes += _envolver(dec["en_criollo"])
            partes.append("")
        partes.append(f"  Razón: {dec.get('razon_principal')}")
        if dec.get("objecion_que_mas_peso"):
            partes.append(f"  La objeción que más pesó: {dec['objecion_que_mas_peso']}")
        for x in dec.get("correcciones", []):
            partes.append(f"  Corrección aplicada por el código: {x}")
        if dec.get("orden_final"):
            o = dec["orden_final"]
            total = o["precio_limite"] * 100 * o["contratos"]
            partes.append(
                f"  Orden final: {o['id']} · {o['contratos']} contrato(s) · cobramos como mínimo "
                f"{o['precio_limite']:.2f} por acción"
                + (f", o sea {total:,.2f} USD en total" if o["contratos"] else "")
            )
            partes.append("  Plan de salida (cuándo cerramos y por qué):")
            for k, texto in (dec.get("plan_salida") or {}).items():
                partes.append(f"    {k.replace('_', ' ').capitalize()}: {texto}")
        if dec.get("cambios_pedidos"):
            partes.append("  Cambios que le pide a research: " + "; ".join(dec["cambios_pedidos"]))
        if dec.get("que_revisar_proximo_ciclo"):
            partes.append(f"  Para revisar en la próxima corrida: {dec['que_revisar_proximo_ciclo']}")
        partes.append("")
    if ciclo.con_modelo:
        partes.append(f"Costo total de los modelos: {ciclo.costo_usd:.4f} USD")
    partes.append("No se envió ninguna orden: la decisión quedó en la bitácora.")
    return con_glosario("\n".join(partes))
