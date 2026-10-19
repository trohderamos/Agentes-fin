"""Punto de entrada del sistema de agentes.

Por ahora es de solo lectura: no envía órdenes.

Uso:
    python orquestador.py ciclo                  # research, consejo y jefe; la decisión queda en la bitácora
    python orquestador.py ciclo --sin-modelo     # mismo ciclo sin llamar a Claude
    python orquestador.py ejecutar               # simula el envío de la orden que aprobó el jefe hoy
    python orquestador.py ejecutar --enviar      # la envía a la cuenta paper
    python orquestador.py gestionar [--enviar]   # revisa las posiciones abiertas y cierra según el plan
    python orquestador.py repaso                 # repaso semanal: resultado, trades, errores, fichas y lecciones
    python orquestador.py research               # agentes de research sobre SPY: 1 a 3 candidatos o "no hay trade"
    python orquestador.py research --sin-modelo  # mismo circuito sin llamar a Claude (solo cálculos)
    python orquestador.py informe                # informe de datos de SPY del día
    python orquestador.py estado                 # cuenta + cadena de SPY
    python orquestador.py estado --simbolo QQQ
    python orquestador.py estado --todo          # toda la cadena, sin filtro de delta
    python orquestador.py cuenta                 # solo la cuenta
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from broker import Broker, ContratoOpcion, Cuenta, CuentaRealDetectada, ErrorBroker, crear_broker
from agentes.modelo import ErrorModelo
from datos.informe import formatear_informe, generar_informe
from datos.cadena import DIAS_MAX, DIAS_MIN, Vencimiento, cadena_en_ventana, filtrar_por_delta


def _num(valor: Optional[float], decimales: int = 2) -> str:
    return "-" if valor is None else f"{valor:,.{decimales}f}"


def mostrar_cuenta(cuenta: Cuenta) -> str:
    lineas = [
        "CUENTA PAPER (dinero simulado)",
        f"  Número:                  {cuenta.numero}",
        f"  Estado:                  {cuenta.estado}",
        f"  Patrimonio:              {_num(cuenta.patrimonio)} {cuenta.moneda}",
        f"  Efectivo:                {_num(cuenta.efectivo)} {cuenta.moneda}",
        f"  Poder de compra:         {_num(cuenta.poder_de_compra)} {cuenta.moneda}",
        f"  Poder de compra opciones: {_num(cuenta.poder_de_compra_opciones)} {cuenta.moneda}",
        f"  Nivel de opciones:       {cuenta.nivel_opciones if cuenta.nivel_opciones is not None else '-'}",
    ]
    return "\n".join(lineas)


def _fila(c: ContratoOpcion) -> str:
    iv = None if c.volatilidad_implicita is None else c.volatilidad_implicita * 100
    return (
        f"  {c.tipo:<4} {c.strike:>8.2f}  {_num(c.bid):>7} {_num(c.ask):>7} {_num(c.medio):>7}"
        f"  {_num(c.delta, 3):>7} {_num(iv, 1):>6}  {_num(c.theta, 3):>7}  {c.simbolo}"
    )


def mostrar_cadena(
    simbolo: str,
    precio: Optional[float],
    vencimientos: list[Vencimiento],
    delta_min: Optional[float],
    delta_max: Optional[float],
) -> str:
    filtro = (
        "toda la cadena"
        if delta_min is None
        else f"|delta| entre {delta_min:.2f} y {delta_max:.2f}"
    )
    lineas = [
        f"CADENA DE OPCIONES {simbolo}  (precio: {_num(precio)}; vencimientos de {DIAS_MIN} a {DIAS_MAX} días; {filtro})"
    ]
    if not vencimientos:
        lineas.append("  No hay vencimientos en la ventana.")
        return "\n".join(lineas)

    lineas.append("")
    lineas.append("  Vencimiento   Días  Contratos")
    for v in vencimientos:
        lineas.append(f"  {v.fecha.isoformat()}  {v.dias:>4}  {len(v.contratos):>9}")

    for v in vencimientos:
        contratos = (
            v.contratos if delta_min is None else filtrar_por_delta(v.contratos, delta_min, delta_max)
        )
        lineas.append("")
        lineas.append(f"  {v.fecha.isoformat()} ({v.dias} días): {len(contratos)} contratos")
        if not contratos:
            continue
        lineas.append("  tipo   strike      bid     ask   medio    delta   IV%    theta  símbolo")
        lineas.extend(_fila(c) for c in contratos)
    return "\n".join(lineas)


def cmd_cuenta(broker: Broker, _args: argparse.Namespace) -> None:
    print(mostrar_cuenta(broker.cuenta()))


def cmd_estado(broker: Broker, args: argparse.Namespace) -> None:
    print(mostrar_cuenta(broker.cuenta()))
    print()
    simbolo = args.simbolo.upper()
    precio = broker.precio_subyacente(simbolo)
    vencimientos = cadena_en_ventana(broker, simbolo)
    delta_min, delta_max = (None, None) if args.todo else (args.delta_min, args.delta_max)
    print(mostrar_cadena(simbolo, precio, vencimientos, delta_min, delta_max))
    print()
    print("Fase 1: solo lectura. No se envió ninguna orden.")


CARPETA_INFORMES = Path(__file__).parent / "datos" / "informes"


def cmd_informe(broker: Broker, args: argparse.Namespace) -> None:
    broker.cuenta()  # verifica que sea paper antes de pedir nada más
    informe = generar_informe(broker, args.simbolo.upper())
    print(formatear_informe(informe))
    if not args.sin_guardar:
        CARPETA_INFORMES.mkdir(parents=True, exist_ok=True)
        ruta = CARPETA_INFORMES / f"{informe.subyacente}-{informe.fecha.isoformat()}.json"
        ruta.write_text(json.dumps(informe.a_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nGuardado en {ruta.relative_to(Path(__file__).parent)}")


CARPETA_RESEARCH = Path(__file__).parent / "datos" / "research"


def cmd_research(broker: Broker, args: argparse.Namespace) -> None:
    from agentes.formato import formatear_research
    from agentes.modelo import ModeloClaude
    from agentes.research import Research

    broker.cuenta()  # verifica que sea paper antes de pedir nada más
    modelo = None if args.sin_modelo else ModeloClaude(modelo=args.modelo)
    resultado = asyncio.run(Research(broker, modelo, args.simbolo.upper()).correr())
    print(formatear_research(resultado))
    if not args.sin_guardar:
        CARPETA_RESEARCH.mkdir(parents=True, exist_ok=True)
        sufijo = "" if modelo else "-sin-modelo"
        ruta = CARPETA_RESEARCH / f"{resultado.subyacente}-{resultado.fecha.isoformat()}{sufijo}.json"
        ruta.write_text(json.dumps(resultado.a_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nGuardado en {ruta.relative_to(Path(__file__).parent)}")


CARPETA_CICLOS = Path(__file__).parent / "datos" / "ciclos"


def cmd_ciclo(broker: Broker, args: argparse.Namespace) -> None:
    from agentes.ciclo import correr_ciclo
    from agentes.formato import formatear_ciclo
    from agentes.modelo import ModeloClaude

    broker.cuenta()  # verifica que sea paper antes de pedir nada más
    modelo = None if args.sin_modelo else ModeloClaude(modelo=args.modelo)
    ciclo = asyncio.run(
        correr_ciclo(broker, modelo, args.simbolo.upper(), guardar_en_bitacora=not args.sin_guardar)
    )
    print(formatear_ciclo(ciclo))
    if not args.sin_guardar:
        CARPETA_CICLOS.mkdir(parents=True, exist_ok=True)
        sufijo = "" if modelo else "-sin-modelo"
        hora = datetime.now().strftime("%H%M%S")
        ruta = CARPETA_CICLOS / f"{ciclo.subyacente}-{ciclo.fecha.isoformat()}-{hora}{sufijo}.json"
        ruta.write_text(json.dumps(ciclo.a_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nGuardado en {ruta.relative_to(Path(__file__).parent)} y en bitacora/decisiones.jsonl")


CARPETA_REPASOS = Path(__file__).parent / "datos" / "repasos"


def cmd_repaso(broker: Broker, args: argparse.Namespace) -> None:
    from agentes.modelo import ModeloClaude
    from agentes.repaso import formatear_repaso, guardar_propuestas, repasar
    from datos.cadena import hoy_nueva_york

    broker.cuenta()  # verifica que sea paper antes de pedir nada más
    modelo = None if args.sin_modelo else ModeloClaude(modelo=args.modelo)
    hoy = date.fromisoformat(args.fecha) if args.fecha else hoy_nueva_york()
    repaso = asyncio.run(repasar(broker, modelo, hoy, args.simbolo.upper()))
    texto = formatear_repaso(repaso)
    print(texto)
    if not args.sin_guardar:
        guardar_propuestas(repaso)
        CARPETA_REPASOS.mkdir(parents=True, exist_ok=True)
        base = CARPETA_REPASOS / f"repaso-{repaso.desde.isoformat()}"
        base.with_suffix(".md").write_text(texto, encoding="utf-8")
        base.with_suffix(".json").write_text(
            json.dumps(repaso.a_dict(), ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        print(f"\nGuardado en {base.with_suffix('.md').relative_to(Path(__file__).parent)}")


def cmd_ejecutar(broker: Broker, args: argparse.Namespace) -> None:
    from agentes.bitacora import leer_decisiones, leer_ejecuciones, leer_trades
    from agentes.ejecucion import abrir, decision_pendiente, formatear
    from datos.cadena import hoy_nueva_york

    hoy = hoy_nueva_york()
    trades = leer_trades()
    decision = decision_pendiente(hoy, leer_decisiones(), trades, leer_ejecuciones())
    if decision is None:
        print("No hay ninguna orden aprobada hoy pendiente de enviar.")
        return
    print(formatear([abrir(broker, decision, hoy, enviar=args.enviar, trades=trades)]))


def cmd_gestionar(broker: Broker, args: argparse.Namespace) -> None:
    from agentes.ejecucion import formatear, gestionar
    from datos.cadena import hoy_nueva_york

    print(formatear(gestionar(broker, hoy_nueva_york(), enviar=args.enviar)))


def armar_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agentes de venta de primas (solo lectura: no envía órdenes).")
    p.add_argument(
        "--feed",
        default="indicative",
        choices=["indicative", "opra"],
        help="Fuente de precios de opciones de Alpaca (opra requiere suscripción).",
    )
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("cuenta", help="Muestra la cuenta paper.").set_defaults(func=cmd_cuenta)

    e = sub.add_parser("estado", help="Cuenta paper y cadena de opciones de 7 a 21 días.")
    e.add_argument("--simbolo", default="SPY")
    e.add_argument("--delta-min", type=float, default=0.10)
    e.add_argument("--delta-max", type=float, default=0.20)
    e.add_argument("--todo", action="store_true", help="Muestra todos los strikes.")
    e.set_defaults(func=cmd_estado)

    i = sub.add_parser("informe", help="Informe de datos del día: precio, ATR, IV rank, eventos.")
    i.add_argument("--simbolo", default="SPY")
    i.add_argument("--sin-guardar", action="store_true", help="No guarda el JSON del informe.")
    i.set_defaults(func=cmd_informe)

    from agentes import config

    r = sub.add_parser("research", help="Agentes de research: 1 a 3 candidatos o 'no hay trade hoy'.")
    r.add_argument("--simbolo", default="SPY")
    r.add_argument("--sin-modelo", action="store_true", help="No llama a Claude: solo los cálculos en código.")
    r.add_argument("--modelo", default=config.MODELO, help="Modelo de Claude para los agentes.")
    r.add_argument("--sin-guardar", action="store_true", help="No guarda el JSON del resultado.")
    r.set_defaults(func=cmd_research)

    c = sub.add_parser("ciclo", help="Research, consejo y jefe. No envía órdenes.")
    c.add_argument("--simbolo", default="SPY")
    c.add_argument("--sin-modelo", action="store_true", help="No llama a Claude: reglas fijas.")
    c.add_argument("--modelo", default=config.MODELO, help="Modelo de Claude para los agentes.")
    c.add_argument("--sin-guardar", action="store_true", help="No guarda el JSON ni escribe la bitácora.")
    c.set_defaults(func=cmd_ciclo)

    rp = sub.add_parser("repaso", help="Repaso semanal: resultado, trades, errores, fichas y lecciones.")
    rp.add_argument("--simbolo", default="SPY")
    rp.add_argument("--fecha", help="Un día de la semana a repasar (AAAA-MM-DD). Por defecto, esta semana.")
    rp.add_argument("--sin-modelo", action="store_true", help="No propone lecciones (no llama a Claude).")
    rp.add_argument("--modelo", default=config.MODELO, help="Modelo de Claude para el repaso.")
    rp.add_argument("--sin-guardar", action="store_true", help="No guarda el informe ni las propuestas.")
    rp.set_defaults(func=cmd_repaso)

    ej = sub.add_parser("ejecutar", help="Envía a paper la orden que aprobó el jefe hoy (simula sin --enviar).")
    ej.add_argument("--enviar", action="store_true", help="Envía la orden de verdad a la cuenta paper.")
    ej.set_defaults(func=cmd_ejecutar)

    ge = sub.add_parser("gestionar", help="Revisa las posiciones abiertas y cierra según el plan (simula sin --enviar).")
    ge.add_argument("--enviar", action="store_true", help="Envía las órdenes de cierre a la cuenta paper.")
    ge.set_defaults(func=cmd_gestionar)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = armar_parser().parse_args(argv)
    try:
        permitir = bool(getattr(args, "enviar", False))
        broker = crear_broker("alpaca-paper", feed_opciones=args.feed, permitir_ordenes=permitir)
        args.func(broker, args)
    except CuentaRealDetectada as e:
        print(f"ALTO: {e}", file=sys.stderr)
        return 2
    except (ErrorBroker, ErrorModelo, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
