"""Cómo narran los agentes y cómo se explican los términos en la terminal.

Un solo lugar para tres cosas:
- COMO_ESCRIBIR: la consigna de redacción que leen todos los agentes (research,
  consejo y jefe), para que el informe se entienda sin saber de opciones.
- EN_CRIOLLO: el campo de los esquemas donde cada agente deja su conclusión en
  dos o tres frases simples. Es opcional a propósito: si el modelo no lo manda,
  el informe sale igual y no se rompe ninguna corrida.
- TERMINOS y glosario_de(): el pie "QUÉ SIGNIFICA CADA COSA" que se agrega a los
  informes de la terminal con los términos que de verdad aparecieron en el texto.

Acá no se decide nada: no toca la lógica de los trades ni los límites de riesgo.
"""

from __future__ import annotations

import textwrap

COMO_ESCRIBIR = """## Cómo escribir tu informe (el usuario lo lee tal cual)
Te lee una persona que opera su propia plata pero no es experta en opciones, y lee el informe
apurada. Si no se entiende de una pasada, el informe no sirve.
- Arrancá por la conclusión, en una frase: qué está pasando y qué significa para nosotros.
  Después los números que la sostienen. Nunca al revés.
- Castellano rioplatense simple y frases cortas. Nada de inglés evitable ni de abreviaturas.
- Cada término técnico va explicado ahí mismo, entre paréntesis, en palabras llanas y traduciendo
  el número. Así:
  - "IV rank 45 (las opciones están más caras que en el 45% de los días del último año)"
  - "delta 0,15 (el mercado le da más o menos 15% de chance de que el precio llegue a ese strike)"
  - "colchón 2,9% (el precio puede moverse 2,9% en contra antes de que empecemos a tener problemas)"
  - "skew de 6 puntos (los puts están más caros que los calls: el mercado paga por cubrirse de una caída)"
  - "gamma alto (faltando poco para el vencimiento la posición se mueve en contra cada vez más rápido)"
- Decí "vendemos" y "compramos", nunca un signo ni un número negativo suelto. Decí "lo máximo que
  podemos ganar" y "lo máximo que podemos perder" en dólares, no solo "retorno sobre riesgo".
- La prima que cobramos NO es ganancia: es plata que entra hoy y que recién se gana cuando el trade
  se cierra o vence. Escribilo así cada vez que hables de plata.
- Nada de relleno ni de repetir la consigna. Lo que no sepas, una línea diciendo que no se sabe."""

# Campo opcional de los esquemas: la conclusión en criollo, antes que nada.
EN_CRIOLLO = {
    "type": "string",
    "description": (
        "Lo primero que lee el usuario. Dos a cuatro frases en castellano simple, para alguien que "
        "no es experto: qué concluís, qué significa para nuestra plata y por qué. Sin jerga; si un "
        "término técnico no se puede evitar, explicalo entre paréntesis en el momento."
    ),
}

# Término -> (palabras que lo disparan en el texto, explicación en criollo).
TERMINOS: dict[str, tuple[tuple[str, ...], str]] = {
    "patrimonio": (
        ("patrimonio",),
        "lo que vale la cuenta hoy si cerráramos todo. Es el número que vale para saber cómo vamos.",
    ),
    "efectivo": (
        ("efectivo",),
        "la plata suelta. Incluye las primas cobradas, que todavía son deuda hasta cerrar el trade, "
        "así que siempre parece más de lo que realmente tenemos.",
    ),
    "prima / crédito": (
        ("crédito", "credito", "prima"),
        "la plata que nos pagan por vender la opción. Entra hoy, pero recién se gana cuando el trade "
        "se cierra o vence.",
    ),
    "débito": (
        ("débito", "debito"),
        "lo que hay que pagar para recomprar la posición y cerrarla. Si es menor que la prima que "
        "cobramos, el trade cierra en ganancia.",
    ),
    "vende / compra": (
        ("vende ", "compra ", "vendemos", "compramos"),
        "en cada pata, 'vende' es la opción por la que cobramos y 'compra' la que pagamos como "
        "seguro. En el bróker la vendida figura con cantidad -1 y la comprada con +1.",
    ),
    "put y call": (
        ("put ", "call ", "puts", "calls"),
        "un put gana valor cuando el precio baja y un call cuando sube. Vendiendo primas cobramos por "
        "el que creemos que no va a llegar hasta ahí.",
    ),
    "strike": (
        ("strike",),
        "el precio pactado de la opción. Mientras el activo no lo alcance, la opción vendida nos "
        "deja la prima.",
    ),
    "IV rank": (
        ("iv rank", "iv_rank"),
        "qué tan caras están las opciones contra su propio último año. 45 quiere decir más caras que "
        "en el 45% de los días. Vendiendo primas, cuanto más alto mejor.",
    ),
    "delta": (
        ("delta",),
        "la chance aproximada de que el precio llegue a ese strike. 0,15 es más o menos 15%.",
    ),
    "gamma": (
        ("gamma",),
        "qué tan rápido se acelera la posición en contra cuando el precio se mueve. Cerca del "
        "vencimiento es el riesgo principal.",
    ),
    "theta": (
        ("theta",),
        "lo que ganamos por día solo porque pasa el tiempo. Vendiendo primas, juega a favor.",
    ),
    "vega": (
        ("vega",),
        "cuánto nos afecta que suba o baje el miedo del mercado. Vendiendo primas, un susto grande "
        "juega en contra.",
    ),
    "equilibrio": (
        ("equilibrio",),
        "los dos precios entre los que el trade no pierde plata al vencimiento.",
    ),
    "ganancia máxima / pérdida máxima": (
        ("ganancia máx", "pérdida máx", "perdida máx"),
        "lo mejor y lo peor que puede pasar, en dólares, con esta posición. La pérdida máxima es la "
        "que importa: es lo que arriesgamos de verdad.",
    ),
    "retorno sobre riesgo": (
        ("retorno/riesgo", "retorno sobre riesgo"),
        "cuánto podemos ganar por cada peso que arriesgamos. 25% quiere decir 25 centavos por dólar "
        "en riesgo.",
    ),
    "natural y medio": (
        ("natural",),
        "'medio' es el precio teórico justo entre la punta compradora y la vendedora; 'natural' es el "
        "precio al que se llena seguro, peor para nosotros. Se intenta cobrar cerca del medio.",
    ),
    "skew": (
        ("skew",),
        "la diferencia de precio entre los puts y los calls. Puts más caros quiere decir que el "
        "mercado está pagando por cubrirse de una caída.",
    ),
    "colchón": (
        ("colchón", "colchon"),
        "cuánto puede moverse el precio en contra antes de que el trade empiece a sufrir.",
    ),
    "freno por drawdown": (
        ("drawdown",),
        "la caída desde el mejor momento de la cuenta. Si se pasa de lo permitido, el sistema no "
        "abre riesgo nuevo.",
    ),
    "iron condor": (
        ("iron condor", "condor"),
        "vendemos un put abajo y un call arriba, cada uno con su seguro más lejos. Se gana si el "
        "precio se queda en el medio.",
    ),
    "backtest": (
        ("backtest",),
        "la misma estrategia probada contra lo que pasó en el pasado. Es una referencia, no una "
        "promesa.",
    ),
}


# Frases donde la palabra del glosario quiere decir otra cosa: no cuentan como término.
# "prima de riesgo" no es la prima que cobramos y "gas natural" no es el precio natural.
FALSOS_AMIGOS = ("prima de riesgo", "gas natural", "recursos naturales", "delta del río")


def glosario_de(texto: str, maximo: int = 10) -> list[str]:
    """Las líneas del glosario para los términos que aparecen de verdad en ese texto."""
    bajo = texto.lower()
    for frase in FALSOS_AMIGOS:
        bajo = bajo.replace(frase, "")
    lineas = []
    for nombre, (disparadores, explicacion) in TERMINOS.items():
        if any(d in bajo for d in disparadores):
            lineas.append(f"  {nombre}: {explicacion}")
        if len(lineas) >= maximo:
            break
    return lineas


def con_glosario(texto: str, maximo: int = 10) -> str:
    """El informe con el pie que explica, en criollo, los términos que usó."""
    lineas = glosario_de(texto, maximo)
    if not lineas:
        return texto
    return texto + "\n\nQUÉ SIGNIFICA CADA COSA\n" + "\n".join(lineas)


def plata(valor: float | None, decimales: int = 2) -> str:
    """Un importe en dólares, con el signo cuando es un resultado."""
    return "-" if valor is None else f"{valor:,.{decimales}f} USD"


def envolver(texto: str, sangria: str = "  ", ancho: int = 100) -> list[str]:
    """Un párrafo cortado en líneas, para que se lea cómodo en la terminal."""
    if not texto or not texto.strip():
        return []
    return textwrap.wrap(texto.strip(), width=ancho, initial_indent=sangria, subsequent_indent=sangria)
