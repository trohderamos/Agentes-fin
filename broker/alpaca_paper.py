"""Adaptador contra la cuenta paper de Alpaca.

Reglas de seguridad de este módulo:
- La URL de trading está fija en el entorno paper y no se puede cambiar.
- Antes de devolver cualquier dato se verifica que la cuenta sea paper; si no
  lo es, se lanza CuentaRealDetectada y no se hace nada más.
- Las órdenes están bloqueadas salvo que el adaptador se cree con
  permitir_ordenes=True. Aun así, los únicos pedidos que no son GET van a
  /v2/orders del entorno paper: enviar o cancelar una orden.
- Las claves se leen de ALPACA_API_KEY y ALPACA_SECRET_KEY y nunca se imprimen.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from broker.base import (
    Barra,
    Broker,
    ContratoOpcion,
    Cuenta,
    CuentaRealDetectada,
    ErrorBroker,
    EstadoOrden,
    FaltanClaves,
    Noticia,
    OrdenesBloqueadas,
    PataOrden,
    Reloj,
    Posicion,
    PuntoPatrimonio,
)

URL_TRADING_PAPER = "https://paper-api.alpaca.markets"
URL_DATOS = "https://data.alpaca.markets"

# Las cuentas paper de Alpaca tienen número que empieza con "PA".
PREFIJO_CUENTA_PAPER = "PA"

# Signo del límite en órdenes de varias patas: según la referencia de Alpaca,
# un valor negativo es un crédito (se cobra). Se confirma con la primera orden
# en paper; si resultara al revés, se cambia solo esta constante.
SIGNO_CREDITO = -1

# Alpaca acepta hasta 100 símbolos por pedido de barras de opciones.
MAX_SIMBOLOS_POR_PEDIDO = 100

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


def _fin_sip(hasta: date) -> str:
    """Fin del pedido de barras con feed SIP.

    El plan gratuito de Alpaca no da datos SIP de los últimos 15 minutos, así
    que el fin se recorta a 16 minutos atrás.
    """
    tope = datetime.now(timezone.utc) - timedelta(minutes=16)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), timezone.utc)
    return min(fin, tope).strftime("%Y-%m-%dT%H:%M:%SZ")


def _float(valor: Any) -> Optional[float]:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _texto_plano(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class AlpacaPaper(Broker):
    nombre = "alpaca-paper"

    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        feed_opciones: str = "indicative",
        sesion: Optional[requests.Session] = None,
        timeout: float = 15.0,
        permitir_ordenes: bool = False,
    ) -> None:
        self._permitir_ordenes = permitir_ordenes
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
        if resp.status_code == 401:
            raise ErrorBroker(
                f"Alpaca rechazó las claves (401) en {ruta}. Revisá que sean las de la cuenta paper."
            )
        if resp.status_code == 403:
            raise ErrorBroker(f"Alpaca no permite este pedido (403) en {ruta}: {resp.text[:200]}")
        if resp.status_code >= 400:
            raise ErrorBroker(f"Alpaca respondió {resp.status_code} en {ruta}: {resp.text[:200]}")
        return resp.json()

    def _escribir(self, metodo: str, ruta: str, cuerpo: Optional[dict] = None) -> Any:
        """POST o DELETE, solo a /v2/orders del entorno paper y solo si se permitió."""
        if not self._permitir_ordenes:
            raise OrdenesBloqueadas("El adaptador está en solo lectura: no se envían ni cancelan órdenes.")
        if not ruta.startswith("/v2/orders"):
            raise OrdenesBloqueadas(f"Ruta no permitida para escribir: {ruta}")
        self.cuenta()  # vuelve a verificar que la cuenta sea paper antes de cada escritura
        url = URL_TRADING_PAPER + ruta
        try:
            resp = self._sesion.request(metodo, url, json=cuerpo, timeout=self._timeout)
        except requests.RequestException as e:
            raise ErrorBroker(f"No se pudo conectar con Alpaca: {type(e).__name__}") from None
        if resp.status_code >= 400:
            raise ErrorBroker(f"Alpaca respondió {resp.status_code} en {ruta}: {resp.text[:300]}")
        return resp.json() if resp.text else None

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
            patrimonio_cierre_anterior=_float(datos.get("last_equity")),
        )

    def posiciones(self) -> list[Posicion]:
        self._verificar_paper()
        return [
            Posicion(
                simbolo=str(p.get("symbol", "")),
                clase=str(p.get("asset_class", "")),
                cantidad=_float(p.get("qty")) or 0.0,
                valor_mercado=_float(p.get("market_value")),
                resultado_no_realizado=_float(p.get("unrealized_pl")),
            )
            for p in self._get(URL_TRADING_PAPER, "/v2/positions") or []
        ]

    def historial_patrimonio(self, desde: date) -> list[PuntoPatrimonio]:
        self._verificar_paper()
        datos = self._get(
            URL_TRADING_PAPER,
            "/v2/account/portfolio/history",
            {"period": f"{max((date.today() - desde).days + 1, 1)}D", "timeframe": "1D"},
        )
        puntos = []
        for ts, patrimonio in zip(datos.get("timestamp") or [], datos.get("equity") or []):
            valor = _float(patrimonio)
            # Antes de que existiera la cuenta Alpaca devuelve 0 o null.
            if not valor:
                continue
            fecha = datetime.fromtimestamp(int(ts), timezone.utc).date()
            if fecha < desde:
                continue
            puntos.append(PuntoPatrimonio(fecha=fecha, patrimonio=valor))
        return puntos

    def noticias(self, simbolos: list[str], desde: date, limite: int = 50) -> list[Noticia]:
        self._verificar_paper()
        datos = self._get(
            URL_DATOS,
            "/v1beta1/news",
            {
                "symbols": ",".join(simbolos),
                "start": desde.isoformat(),
                "limit": min(limite, 50),
                "sort": "desc",
            },
        )
        return [
            Noticia(
                fecha=str(n.get("created_at", "")),
                titular=str(n.get("headline", "")).strip(),
                resumen=_texto_plano(n.get("summary") or "")[:500],
                fuente=str(n.get("source", "")),
                url=str(n.get("url", "")),
                simbolos=tuple(n.get("symbols") or ()),
            )
            for n in datos.get("news") or []
        ]

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

    def barras_diarias(
        self, simbolo: str, desde: date, hasta: date, ajustadas: bool = True
    ) -> list[Barra]:
        self._verificar_paper()
        params: dict[str, Any] = {
            "timeframe": "1Day",
            "start": desde.isoformat(),
            "end": _fin_sip(hasta),
            "adjustment": "all" if ajustadas else "raw",
            "feed": "sip",
            "limit": 10000,
        }
        barras: list[Barra] = []
        while True:
            datos = self._get(URL_DATOS, f"/v2/stocks/{simbolo}/bars", params)
            barras.extend(self._barra(b) for b in datos.get("bars") or [])
            token = datos.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return barras

    def barras_opciones(
        self, simbolos: list[str], desde: date, hasta: date
    ) -> dict[str, list[Barra]]:
        self._verificar_paper()
        resultado: dict[str, list[Barra]] = {}
        unicos = sorted(set(simbolos))
        for i in range(0, len(unicos), MAX_SIMBOLOS_POR_PEDIDO):
            params: dict[str, Any] = {
                "symbols": ",".join(unicos[i : i + MAX_SIMBOLOS_POR_PEDIDO]),
                "timeframe": "1Day",
                "start": desde.isoformat(),
                "end": hasta.isoformat(),
                "limit": 10000,
            }
            while True:
                datos = self._get(URL_DATOS, "/v1beta1/options/bars", params)
                for simbolo, barras in (datos.get("bars") or {}).items():
                    resultado.setdefault(simbolo, []).extend(self._barra(b) for b in barras)
                token = datos.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        return resultado

    # --- Órdenes (fase 5) ---------------------------------------------------

    def reloj(self) -> Reloj:
        self._verificar_paper()
        d = self._get(URL_TRADING_PAPER, "/v2/clock")
        return Reloj(
            abierto=bool(d.get("is_open")),
            ahora=datetime.fromisoformat(str(d["timestamp"]).replace("Z", "+00:00")),
            proximo_cierre=datetime.fromisoformat(str(d["next_close"]).replace("Z", "+00:00")),
        )

    def cotizaciones(self, simbolos: list[str]) -> dict[str, ContratoOpcion]:
        self._verificar_paper()
        out: dict[str, ContratoOpcion] = {}
        unicos = sorted(set(simbolos))
        for i in range(0, len(unicos), MAX_SIMBOLOS_POR_PEDIDO):
            datos = self._get(
                URL_DATOS,
                "/v1beta1/options/snapshots",
                {"symbols": ",".join(unicos[i : i + MAX_SIMBOLOS_POR_PEDIDO]), "feed": self._feed_opciones},
            )
            for simbolo, snap in (datos.get("snapshots") or {}).items():
                out[simbolo] = self._contrato(simbolo, snap or {})
        return out

    def enviar_orden_multipata(
        self, patas: list[PataOrden], contratos: int, limite: float, abrir: bool, id_cliente: str
    ) -> EstadoOrden:
        if contratos < 1 or limite <= 0 or not patas:
            raise ErrorBroker("Orden inválida: hacen falta patas, contratos >= 1 y límite positivo.")
        intencion = "open" if abrir else "close"
        legs = []
        for p in patas:
            parsear_simbolo_occ(p.simbolo)  # valida el símbolo
            # Al abrir se vende lo que se vende; al cerrar se invierte cada pata.
            vende = (p.lado == "venta") == abrir
            legs.append(
                {
                    "symbol": p.simbolo,
                    "ratio_qty": str(p.ratio),
                    "side": "sell" if vende else "buy",
                    "position_intent": f"{'sell' if vende else 'buy'}_to_{intencion}",
                }
            )
        # Al abrir una estructura de crédito se cobra; al cerrarla se paga.
        precio = SIGNO_CREDITO * limite if abrir else -SIGNO_CREDITO * limite
        cuerpo = {
            "order_class": "mleg",
            "qty": str(contratos),
            "type": "limit",
            "limit_price": f"{precio:.2f}",
            "time_in_force": "day",
            "client_order_id": id_cliente,
            "legs": legs,
        }
        return self._estado(self._escribir("POST", "/v2/orders", cuerpo))

    def estado_orden(self, id_orden: str) -> EstadoOrden:
        self._verificar_paper()
        return self._estado(self._get(URL_TRADING_PAPER, f"/v2/orders/{id_orden}"))

    def cancelar_orden(self, id_orden: str) -> None:
        self._escribir("DELETE", f"/v2/orders/{id_orden}")

    @staticmethod
    def _estado(d: dict) -> EstadoOrden:
        return EstadoOrden(
            id=str(d.get("id", "")),
            estado=str(d.get("status", "")),
            contratos=int(float(d.get("qty") or 0)),
            contratos_llenados=int(float(d.get("filled_qty") or 0)),
            precio_promedio=_float(d.get("filled_avg_price")),
            limite=_float(d.get("limit_price")),
        )

    @staticmethod
    def _barra(b: dict) -> Barra:
        return Barra(
            fecha=date.fromisoformat(str(b["t"])[:10]),
            apertura=float(b["o"]),
            maximo=float(b["h"]),
            minimo=float(b["l"]),
            cierre=float(b["c"]),
            volumen=float(b.get("v") or 0),
        )

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
