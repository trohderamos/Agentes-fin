"""Reglas y límites de los agentes de research.

Las reglas de estilo son las del usuario. Los límites de griegas son valores de
partida puestos por Claude y se pueden ajustar acá.
"""

from __future__ import annotations

import os
from datetime import date

# --- Estilo del usuario ---------------------------------------------------
DIAS_MIN = 7
DIAS_MAX = 21
DELTA_VENDIDO_MIN = 0.10
DELTA_VENDIDO_MAX = 0.20
DELTA_OBJETIVO = 0.15
# Eventos macro grandes: Fed, CPI y empleo. Antes descartaban cualquier
# vencimiento que los incluyera; desde el 27 oct 2026 se operan con criterio
# (ver MODO_EVENTOS más abajo).
TIPOS_EVENTO_GRANDE = ("fed", "inflacion", "empleo")
ESTRUCTURAS_PERMITIDAS = ("spread_put", "spread_call", "iron_condor")

# Semana de prueba en paper: el usuario pidió (20 oct 2026) que ningún evento
# dentro del plazo descarte el trade. Venció el 23 oct y quedó solo como
# excepción histórica: la regla que manda ahora es MODO_EVENTOS.
IGNORAR_EVENTOS_HASTA = date(2026, 10, 23)

# --- Regla de eventos (cambio pedido por el usuario el 27 oct 2026) --------
# "operar": el evento es un insumo, no un veto. El agente News arma la tesis
#   del evento (qué pasó la vez anterior, qué espera el mercado, su propio
#   pronóstico) y en código se mide cuánto se movió el activo en los eventos
#   parecidos del pasado (datos/reaccion_eventos.py). El trade se descarta solo
#   si el strike vendido queda más cerca que ese movimiento típico.
# "vetar": la regla vieja, cualquier evento grande dentro del plazo descarta.
MODO_EVENTOS = os.environ.get("AGENTES_MODO_EVENTOS", "operar")

# Percentil del movimiento histórico que se le exige de colchón al strike
# vendido. 50 es la mediana (el día típico del evento); 75 y 90 son cada vez
# más conservadores y dejan pasar menos trades.
PERCENTIL_COLCHON = float(os.environ.get("AGENTES_PERCENTIL_COLCHON", "50"))
# Percentil que se usa solo para avisar y para el escenario de estrés.
PERCENTIL_COLCHON_ESTRES = 90.0
# Earnings del propio subyacente (una acción suelta, no un ETF): sigue
# descartando. Un balance mueve a la acción mucho más que cualquier macro.
EARNINGS_PROPIO_VETA = True
# Movimiento supuesto de una acción el día de su balance, en porcentaje. Para
# un ETF se pondera por el peso de la empresa en el índice.
MOVIMIENTO_EARNINGS_PCT = 6.0
# Pesos aproximados (oct 2026) de las empresas de mayor peso, para estimar
# cuánto le pega al ETF el balance de una sola de ellas.
PESOS_MAYORES = {
    "SPY": {"NVDA": 0.075, "MSFT": 0.065, "AAPL": 0.060, "AMZN": 0.040, "GOOGL": 0.040, "META": 0.025},
    "QQQ": {"NVDA": 0.095, "MSFT": 0.080, "AAPL": 0.075, "AMZN": 0.055, "GOOGL": 0.050, "META": 0.030},
    "SMH": {"NVDA": 0.200, "TSM": 0.110, "AVGO": 0.090, "AMD": 0.050, "ASML": 0.045, "MU": 0.040},
}
# Con un evento macro dentro del plazo, el tope de contratos se multiplica por
# esto (1.0 lo desactiva). Las peores pérdidas de un evento son mucho peores
# que las de un día normal: el tamaño es la protección más barata.
FACTOR_CONTRATOS_EVENTO = float(os.environ.get("AGENTES_FACTOR_CONTRATOS_EVENTO", "0.5"))


def eventos_descartan(hoy: date) -> bool:
    """True si la regla vieja (evento = veto) está vigente."""
    return MODO_EVENTOS != "operar" and hoy > IGNORAR_EVENTOS_HASTA


def regla_eventos(hoy: date) -> str:
    """La regla de eventos vigente, para las instrucciones de los agentes."""
    if MODO_EVENTOS == "operar":
        return (
            "REGLA DE EVENTOS (cambiada por el usuario el 27 oct 2026): un evento dentro del plazo NO descarta "
            "el trade. Los eventos se operan: hay que entender qué puede hacer el evento y decidir con eso. "
            "Datos que te pasan en el mensaje: 'reaccion_historica' (cuánto se movió este activo en los Fed, CPI "
            "y empleo del pasado: mediana, percentil 90 y peor día) y 'colchon_eventos' de cada candidato (ese "
            "movimiento típico comparado con la distancia al strike vendido). Vender prima antes de un evento "
            "cobra más IV y después viene la caída de volatilidad, pero las peores pérdidas son mucho peores: "
            f"mirá el peor caso, no el promedio. Un candidato se descarta en código solo si el strike vendido "
            f"queda más cerca que el movimiento típico (percentil {PERCENTIL_COLCHON:g}) de los eventos del plazo. "
            "El balance de una empresa suelta mueve al índice en proporción a su peso (NVDA o MSFT pesan mucho "
            "más que el resto); un macro (Fed, CPI, empleo) mueve al índice entero."
        )
    if eventos_descartan(hoy):
        return (
            "Cualquier earnings (del activo o de las empresas de mayor peso de un ETF) o evento macro grande "
            "(Fed, CPI, empleo) dentro del plazo descarta el trade."
        )
    return (
        f"Semana de prueba hasta el {IGNORAR_EVENTOS_HASTA.isoformat()}: por decisión del usuario, ningún "
        "evento dentro del plazo descarta el trade, ni earnings ni Fed, CPI o empleo. Pesalos como riesgo "
        "(tamaño, distancia del strike, estructura, vencimiento antes o después del evento), no como veto."
    )

# Subyacentes en los que se buscan trades nuevos (`orquestador.py ronda` y la
# revisión de noticias). El 20 oct 2026 el usuario lo achicó a SPY y QQQ para
# gastar menos plan: cada activo son diez agentes por ronda. Quedaron afuera
# GLD (oro), USO (petróleo), SMH (semis) e IBIT (bitcoin); se pueden volver a
# sumar acá o pasarlos a mano con `--simbolos`.
# Las posiciones abiertas se gestionan igual, sea el activo del universo o no:
# `gestionar` sale de la bitácora, y la revisión de noticias suma los activos
# con trades abiertos (ver simbolos_a_revisar).
UNIVERSO = ("SPY", "QQQ")
def simbolos_a_revisar(trades) -> tuple[str, ...]:
    """El universo más los activos con un trade abierto, sin repetir."""
    abiertos = [t.subyacente for t in trades if t.estado == "abierto"]
    return tuple(dict.fromkeys([*UNIVERSO, *abiertos]))


# Activos cuyo subyacente cotiza 24/7: los agentes tienen en cuenta el salto
# del fin de semana.
ACTIVOS_24_7 = ("IBIT", "ETHA")

# Ancho de las alas de los spreads, en dólares por cada 100 de precio del
# subyacente (con SPY a 770, 0.75 da alas de 6 dólares).
ANCHO_CADA_100 = 0.75
# Anchos con los que se arman candidatos. El primero es el de siempre; los
# demás se suman con "-A<ancho>" al final del id. Lección del repaso del 23 oct
# 2026, aprobada el 27: el jefe pidió alas de 10 dos veces y no había candidato.
ANCHOS_CADA_100 = (ANCHO_CADA_100, 1.4)

# --- Entrada (lección del repaso del 23 oct 2026, aprobada el 27) ----------
# El piso de la orden de apertura es una fracción del crédito medio del momento,
# no la cifra que se escribió en el debate. Se arranca al medio y se baja de a
# 0.01 (o un poco más si no alcanza el tiempo) hasta el piso, en 15 minutos.
FRACCION_PISO_ENTRADA = 0.85
MINUTOS_NEGOCIAR_ENTRADA = 15

# --- Risk (skill research-opciones-primas) --------------------------------
PERDIDA_MAX_TRADE = 0.02  # del capital
RIESGO_TOTAL_MAX = 0.10  # del capital
RIESGO_SUBYACENTE_MAX = 0.20  # del riesgo total permitido

# Límites de griegas del portafolio (valores de partida, ajustables).
# Pérdida estimada con delta y gamma si el subyacente se mueve 3% en contra
# (hacia cualquiera de los dos lados), como fracción del capital. Con plazos
# cortos el gamma es el riesgo principal y este límite lo incluye.
MOVIMIENTO_ESTRES = 0.03
PERDIDA_ESTRES_MAX = 0.015
# Pérdida si la IV sube 1 punto (vega de la posición), como fracción del capital.
VEGA_PUNTO_MAX = 0.005

# --- Freno por drawdown (skill freno-drawdown-opciones) -------------------
FRENO_DIA = 0.02
FRENO_SEMANA = 0.05
FRENO_MES = 0.08
FRENO_RACHA = 2

# --- Backtest -------------------------------------------------------------
BACKTEST_DESDE_ANIOS = 8
# Comisión por pata y por contrato al abrir (tastytrade cobra 1 dólar).
COMISION_PATA = 1.00
# Slippage por pata, en dólares de prima (0.02 = 2 dólares por contrato).
SLIPPAGE_PATA = 0.02
# La IV histórica se aproxima como vol. realizada de 20 días por este factor.
FACTOR_IV_REALIZADA = 1.15

# --- Modelo ---------------------------------------------------------------
# El usuario eligió (20 oct 2026) Sonnet para los cinco agentes de research y
# Opus para el consejo y el jefe, que son los que deciden. Research junta datos
# y los resume con esquema fijo; el juicio fino queda en el consejo y el jefe.
MODELO = os.environ.get("AGENTES_MODELO", "claude-opus-5")
MODELO_RESEARCH = os.environ.get("AGENTES_MODELO_RESEARCH", "claude-sonnet-5")
# Agentes que usan MODELO_RESEARCH; el resto (consejo, jefe, repaso) usa MODELO.
AGENTES_RESEARCH = ("Chart", "News", "Fundamentals", "Strategy", "Risk")


def modelo_de_agente(agente: str) -> str:
    return MODELO_RESEARCH if agente in AGENTES_RESEARCH else MODELO
# Tope de gasto por agente y por corrida, en dólares.
PRESUPUESTO_AGENTE_USD = float(os.environ.get("AGENTES_PRESUPUESTO_USD", "1.0"))

# Tiempo máximo de cada llamado a un agente. Si se pasa, el activo se saltea.
TIMEOUT_AGENTE_SEGUNDOS = float(os.environ.get("AGENTES_TIMEOUT_SEGUNDOS", "600"))
