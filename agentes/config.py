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

# Semana de prueba en paper: el usuario pidió (20 oct 2026) que los earnings
# dentro del plazo no descarten el trade, para ver a los agentes operar. Hasta
# esta fecha inclusive los earnings son una alerta que pesan el consejo y el
# jefe; después vuelven a descartar. Fed, CPI y empleo descartan siempre.
IGNORAR_EARNINGS_HASTA = date(2026, 10, 23)


def earnings_descartan(hoy: date) -> bool:
    return hoy > IGNORAR_EARNINGS_HASTA


def regla_earnings(hoy: date) -> str:
    """La regla de earnings vigente, para las instrucciones de los agentes."""
    if earnings_descartan(hoy):
        return "Cualquier earnings dentro del plazo descarta el trade."
    return (
        f"Semana de prueba hasta el {IGNORAR_EARNINGS_HASTA.isoformat()}: por decisión del usuario, los "
        "earnings dentro del plazo (del activo o de las empresas de mayor peso de un ETF) NO descartan el "
        "trade. Pesalos como riesgo (tamaño, distancia del strike, estructura), no como veto. "
        "Fed, CPI y empleo siguen descartando."
    )

# Subyacentes que recorre `orquestador.py ronda`. GLD (oro) y USO (petróleo)
# no tienen balances, así que siguen operables en temporada de earnings.
# SMH (semiconductores) sí tiene balances: News mira los de sus mayores
# empresas. Se eligió sobre SOXX porque tiene más vencimientos semanales.
# SLV quedó afuera: la prima a delta 0.15 es tan chica que los costos se
# comen más de la mitad del crédito.
UNIVERSO = ("SPY", "GLD", "USO", "SMH")

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
MODELO = os.environ.get("AGENTES_MODELO", "claude-opus-5-5")
# Tope de gasto por agente y por corrida, en dólares.
PRESUPUESTO_AGENTE_USD = float(os.environ.get("AGENTES_PRESUPUESTO_USD", "1.0"))
