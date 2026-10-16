"""Texto del resultado de research para la terminal."""

from __future__ import annotations

from agentes.research import ResultadoResearch


def _n(v, dec: int = 2) -> str:
    return "-" if v is None else f"{v:,.{dec}f}"


def _informe(titulo: str, d: dict | None) -> list[str]:
    if not d:
        return []
    lineas = [titulo]
    if d.get("sin_modelo"):
        lineas.append("  (corrida sin modelo: regla fija)")
    if d.get("tesis"):
        lineas.append(f"  Tesis: {d['tesis']}")
    if d.get("confianza"):
        lineas.append(f"  Confianza: {d['confianza']}. {d.get('motivo_confianza', '')}".rstrip())
    for dato in d.get("datos", [])[:8]:
        lineas.append(f"  - {dato}")
    if d.get("invalidaria"):
        lineas.append("  Qué la invalidaría: " + "; ".join(d["invalidaria"]))
    if d.get("faltantes"):
        lineas.append("  Falta: " + "; ".join(d["faltantes"]))
    return lineas + [""]


def formatear_research(r: ResultadoResearch) -> str:
    lineas = [
        f"RESEARCH {r.subyacente} · {r.fecha.isoformat()}"
        + ("" if r.con_modelo else "  (SIN MODELO: solo cálculos en código)"),
        f"  Capital: {_n(r.capital)} USD · Precio: {_n(r.precio)}",
        f"  Freno por drawdown: {r.freno.estado} ({'; '.join(r.freno.motivos)})",
        "",
    ]
    lineas += _informe("CHART", r.chart)
    if r.chart and "tendencia" in r.chart:
        lineas.insert(
            -1,
            f"  Tendencia {r.chart['tendencia']} · vender {r.chart['lado_a_vender']} · "
            f"soportes {r.chart.get('soportes')} · resistencias {r.chart.get('resistencias')}",
        )
    lineas += _informe("NEWS", r.news)
    if r.news:
        lineas.insert(-1, f"  Veredicto: {r.news.get('veredicto')} {r.news.get('motivo_veredicto', '')}".rstrip())
        for e in r.news.get("eventos", []):
            estado = "confirmado" if e.get("confirmado") else "estimado"
            lineas.insert(-1, f"    {e.get('fecha')} {e.get('hora', '')} {e.get('evento')} [{e.get('impacto')}, {estado}]")
    lineas += _informe("STRATEGY", r.strategy)
    lineas += _informe("RISK", r.risk)

    if r.no_hay_trade:
        lineas += ["NO HAY TRADE HOY", f"  {r.motivo_no_trade}"]
    else:
        lineas.append(f"CANDIDATOS PARA EL CONSEJO ({len(r.candidatos)})")
        for i, x in enumerate(r.candidatos, 1):
            c = x["candidato"]
            rev = x["revision"]
            patas = ", ".join(
                f"{'vende' if p['lado'] == 'venta' else 'compra'} {p['tipo']} {p['strike']:g}" for p in c["patas"]
            )
            lineas += [
                "",
                f"  {i}. {c['estructura']} vence {c['vencimiento']} ({c['dias']} días)"
                + ("  ← preferido" if x["preferido"] else ""),
                f"     {patas}",
                f"     Crédito {_n(c['credito'])} (natural {_n(c['credito_natural'])}) · pérdida máx {_n(c['perdida_maxima'])} USD"
                f" · ganancia máx {_n(c['ganancia_maxima'])} USD · retorno/riesgo {c['retorno_sobre_riesgo']:.0%}",
                f"     Equilibrio {_n(c['equilibrio_bajo'])} / {_n(c['equilibrio_alto'])} · prob. aprox. {c['prob_ganancia']:.0%}"
                f" · delta {_n(c['delta'])} gamma {_n(c['gamma'], 3)} theta {_n(c['theta'])} vega {_n(c['vega'])}",
                f"     Riesgo {rev['riesgo_pct']:.2%} del capital por contrato · hasta {rev['contratos_max']} contratos en el 2%",
            ]
            if x.get("motivo"):
                lineas.append(f"     Strategy: {x['motivo']}")
            if x.get("riesgo_principal") and x["riesgo_principal"] != "-":
                lineas.append(f"     Riesgo principal: {x['riesgo_principal']}")
            if x.get("comentario_risk"):
                lineas.append(f"     Risk: {x['comentario_risk']}")
            for a in rev["alertas"]:
                lineas.append(f"     ALERTA: {a}")
            bt = x.get("backtest")
            if bt:
                lineas.append(
                    f"     Backtest aprox. ({bt['periodo']}, {bt['trades']} trades): {bt['veredicto']} · acierto "
                    f"{_n((bt['tasa_acierto'] or 0) * 100, 0)}% · medio {_n(bt['resultado_medio_usd'])} USD · peor {_n(bt['peor_trade_usd'])} USD"
                )

    if r.descartados:
        lineas += ["", f"DESCARTADOS ({len(r.descartados)})"]
        for cid, motivos in list(r.descartados.items())[:12]:
            lineas.append(f"  {cid}: {motivos[0]}" + (f" (+{len(motivos) - 1})" if len(motivos) > 1 else ""))
    if r.avisos:
        lineas += ["", "AVISOS"] + [f"  - {a}" for a in r.avisos]
    if r.con_modelo:
        lineas += ["", f"Costo de los modelos: {_n(r.costo_usd, 4)} USD"]
    lineas += ["", "Research no envía órdenes."]
    return "\n".join(lineas)
