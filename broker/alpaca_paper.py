"""Adaptador contra la cuenta paper de Alpaca.

Reglas de seguridad de este módulo:
- La URL de trading está fija en el entorno paper y no se puede cambiar.
- Antes de devolver cualquier dato se verifica que la cuenta sea paper; si no
  lo es, se lanza CuentaRealDetectada y no se hace nada más.
- Solo se hacen pedidos GET. En la fase 1 no existe forma de enviar órdenes.
- Las claves se leen de ALPACA_API_KEY y ALPACA_SECRET_KEY y nunca se imprimen.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime
from typing import Any, Optional

import requests

from broker.base import (
    Broker,
    ContratoOpcion,
    Cuenta,
    CuentaRealDetectada,
    ErrorBroker,
    FaltanClaves,
)

URL_TRADING_PAPER = "https://paper-api.alpaca.markets"
URL_DATOS = "https://data.alpaca.markets"

# Las cuentas paper de Alpaca tienen número que empieza con "PA".
PREFIJO_CUENTA_PAPER = "PA"

# Símbolo OCC: raíz + AAMMDD + C/P + strike * 1000 en 8 dígitos.
_OCC = re.compile(r"^(?P<raiz>[A-Z.]{1,6})(?P<fecha>\d{6})(?P<tipo>[CP])(?P<strike>\d{8})$")


def parsear_simbolo_occ(simbolo: str) -> tuple[str, date, str, float]:
    m = _OCC.match(simbolo)
    if not m:
        raise ErrorBroker(f"Símbolo de opción no reconocido: {simbolo}")
    vencimiento = datetime.strptime(m["fecha"], "%y%m%d").date()
    tipo = "call" if m["tipo"] == "C" else "put"
    strike = int(m["strike"]) / 1000
    return m["raiz"], vencimiento, tipo, strike


def _float(valor: Any) -> Optional[float]:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


class AlpacaPaper(Broker):
    nombre = "alpaca-paper"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        feed_opciones: str = "indicative",
        sesion: Optional[requests.Session] = None,
        timeout: float = 15.0,
    ) -> None:
        url_env = os.environ.get("ALPACA_BASE_URL")
        if url_env and url_env.rstrip("/").removesuffix("/v2") != URL_TRADING_PAPER:
            raise CuentaRealDetectada(
                "ALPACA_BASE_URL apunta a algo que no es el entorno paper. "
                "Este adaptador solo opera contra paper-api.alpaca.markets."
            )

        api_key = api_key or os.environ.get("ALPACA_API_KEY")
        secret_key = secret_key or os.environ.get("ALPACA_SECRET_KEY")
        faltan = [
            nombre
            for nombre, valor in (("ALPACA_API_KEY", api_key), ("ALPACA_SECRET_KEY", secret_key))
            if not valor
        ]
        if faltan:
            raise FaltanClaves("Faltan las variables de entorno: " + ", ".join(faltan))

        self._sesion = sesion or requests.Session()
        self._sesion.headers.update(
            {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        )
        self._feed_opciones = feed_opciones
        self._timeout = timeout
        self._cuenta_verificada = False

    # --- HTTP (solo GET) -------------------------------------------------

    def _get(self, base: str, ruta: str, params: Optional[dict] = None) -> Any:
        url = base + ruta
        try:
            resp = self._sesion.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as e:
            raise ErrorBroker(f"No se pudo conectar con {base}: {type(e).__name__}") from None
        if resp.status_code in (401, 403):
            raise ErrorBroker(
                f"Alpaca rechazó las claves ({resp.status_code}) en {ruta}. "
                "Revisá que sean las de la cuenta paper."
            )
        if resp.status_code >= 400:
            raise ErrorBroker(f"Alpaca respondió {resp.status_code} en {ruta}: {resp.text[:200]}")
        return resp.json()

    def _verificar_paper(self) -> None:
        if not self._cuenta_verificada:
            self.cuenta()

    # --- Interfaz Broker -------------------------------------------------

    def cuenta(self) -> Cuenta:
        datos = self._get(URL_TRADING_PAPER, "/v2/account")
        numero = str(datos.get("account_number", ""))
        if not numero.startswith(PREFIJO_CUENTA_PAPER):
            raise CuentaRealDetectada(
                "La cuenta no tiene número de cuenta paper. Me detengo sin hacer nada más."
            )
        self._cuenta_verificada = True
        nivel = datos.get("options_trading_level", datos.get("options_approved_level"))
        return Cuenta(
            numero=numero,
            estado=str(datos.get("status", "")),
            moneda=str(datos.get("currency", "")),
            efectivo=_float(datos.get("cash")) or 0.0,
            patrimonio=_float(datos.get("equity")) or 0.0,
            poder_de_compra=_float(datos.get("buying_power")) or 0.0,
            poder_de_compra_opciones=_float(datos.get("options_buying_power")),
            nivel_opciones=int(nivel) if nivel is not None else None,
            es_paper=True,
        )

    def precio_subyacente(self, simbolo: str) -> Optional[float]:
        self._verificar_paper()
        datos = self._get(URL_DATOS, f"/v2/stocks/{simbolo}/snapshot", {"feed": "iex"})
        trade = datos.get("latestTrade") or {}
        precio = _float(trade.get("p"))
        if precio is None:
            barra = datos.get("dailyBar") or {}
            precio = _float(barra.get("c"))
        return precio

    def cadena_opciones(
        self, subyacente: str, vence_desde: date, vence_hasta: date
    ) -> list[ContratoOpcion]:
        self._verificar_paper()
        params: dict[str, Any] = {
            "feed": self._feed_opciones,
            "expiration_date_gte": vence_desde.isoformat(),
            "expiration_date_lte": vence_hasta.isoformat(),
            "limit": 1000,
        }
        contratos: list[ContratoOpcion] = []
        while True:
            datos = self._get(URL_DATOS, f"/v1beta1/options/snapshots/{subyacente}", params)
            for simbolo, snap in (datos.get("snapshots") or {}).items():
                contratos.append(self._contrato(simbolo, snap or {}))
            token = datos.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        contratos.sort(key=lambda c: (c.vencimiento, c.tipo, c.strike))
        return contratos

    @staticmethod
    def _contrato(simbolo: str, snap: dict) -> ContratoOpcion:
        raiz, vencimiento, tipo, strike = parsear_simbolo_occ(simbolo)
        cot = snap.get("latestQuote") or {}
        trade = snap.get("latestTrade") or {}
        griegas = snap.get("greeks") or {}
        return ContratoOpcion(
            simbolo=simbolo,
            subyacente=raiz,
            vencimiento=vencimiento,
            tipo=tipo,
            strike=strike,
            bid=_float(cot.get("bp")),
            ask=_float(cot.get("ap")),
            ultimo=_float(trade.get("p")),
            volatilidad_implicita=_float(snap.get("impliedVolatility")),
            delta=_float(griegas.get("delta")),
            gamma=_float(griegas.get("gamma")),
            theta=_float(griegas.get("theta")),
            vega=_float(griegas.get("vega")),
        )
