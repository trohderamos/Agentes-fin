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
# Eventos macro que descartan cualquier vencimiento que los incluya.
TIPOS_EVENTO_GRANDE = ("fed", "inflacion", "empleo")
ESTRUCTURAS_PERMITIDAS = ("spread_put", "spread_call", "iron_condor")

# Semana de prueba en paper: el usuario pidió (20 oct 2026) que ningún evento
# dentro del plazo descarte el trade, ni earnings ni Fed, CPI o empleo, para
# ver a los agentes operar. Hasta esta fecha inclusive los eventos son una
# alerta que pesan el consejo y el jefe; después vuelven a descartar.
IGNORAR_EVENTOS_HASTA = date(2026, 10, 23)


def eventos_descartan(hoy: date) -> bool:
    return hoy > IGNORAR_EVENTOS_HASTA


def regla_eventos(hoy: date) -> str:
    """La regla de eventos vigente, para las instrucciones de los agentes."""
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
MODELO = os.environ.get("AGENTES_MODELO", "claude-opus-5-5")
MODELO_RESEARCH = os.environ.get("AGENTES_MODELO_RESEARCH", "claude-sonnet-5-5")
# Agentes que usan MODELO_RESEARCH; el resto (consejo, jefe, repaso) usa MODELO.
AGENTES_RESEARCH = ("Chart", "News", "Fundamentals", "Strategy", "Risk")


def modelo_de_agente(agente: str) -> str:
    return MODELO_RESEARCH if agente in AGENTES_RESEARCH else MODELO
# Tope de gasto por agente y por corrida, en dólares.
PRESUPUESTO_AGENTE_USD = float(os.environ.get("AGENTES_PRESUPUESTO_USD", "1.0"))
