"""Pruebas de la fase 1 sin red: se simulan las respuestas de Alpaca."""

import os
import unittest
from datetime import date
from unittest import mock

from broker import CuentaRealDetectada, FaltanClaves
from broker.alpaca_paper import URL_DATOS, URL_TRADING_PAPER, AlpacaPaper, parsear_simbolo_occ
from datos.cadena import cadena_en_ventana, filtrar_por_delta
from orquestador import mostrar_cadena, mostrar_cuenta

CUENTA_PAPER = {
    "account_number": "PA1234567",
    "status": "ACTIVE",
    "currency": "USD",
    "cash": "100000",
    "equity": "100000",
    "buying_power": "200000",
    "options_buying_power": "100000",
    "options_trading_level": 3,
}


class Respuesta:
    def __init__(self, datos, status=200):
        self._datos = datos
        self.status_code = status
        self.text = str(datos)

    def json(self):
        return self._datos


class SesionFalsa:
    """Imita requests.Session. No tiene post/put/delete a propósito."""

    def __init__(self, rutas):
        self.rutas = rutas
        self.headers = {}
        self.pedidos = []

    def get(self, url, params=None, timeout=None):
        self.pedidos.append((url, dict(params or {})))
        respuesta = self.rutas[url]
        if callable(respuesta):
            return respuesta(params or {})
        return respuesta


def snapshot(delta, bid=1.0, ask=1.1, iv=0.15):
    return {
        "latestQuote": {"bp": bid, "ap": ask},
        "latestTrade": {"p": (bid + ask) / 2},
        "greeks": {"delta": delta, "gamma": 0.01, "theta": -0.05, "vega": 0.1},
        "impliedVolatility": iv,
    }


def crear(rutas):
    with mock.patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}, clear=False):
        os.environ.pop("ALPACA_BASE_URL", None)
        return AlpacaPaper(sesion=SesionFalsa(rutas))


class TestSeguridad(unittest.TestCase):
    def test_faltan_claves(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(FaltanClaves):
                AlpacaPaper(sesion=SesionFalsa({}))

    def test_url_no_paper_se_rechaza(self):
        env = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "ALPACA_BASE_URL": "https://api.alpaca.markets"}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(CuentaRealDetectada):
                AlpacaPaper(sesion=SesionFalsa({}))

    def test_url_paper_por_entorno_se_acepta(self):
        env = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "ALPACA_BASE_URL": "https://paper-api.alpaca.markets/v2"}
        with mock.patch.dict(os.environ, env, clear=True):
            AlpacaPaper(sesion=SesionFalsa({}))

    def test_cuenta_real_frena(self):
        real = dict(CUENTA_PAPER, account_number="123456789")
        b = crear({URL_TRADING_PAPER + "/v2/account": Respuesta(real)})
        with self.assertRaises(CuentaRealDetectada):
            b.cuenta()

    def test_cuenta_real_frena_antes_de_pedir_la_cadena(self):
        real = dict(CUENTA_PAPER, account_number="123456789")
        sesion_rutas = {URL_TRADING_PAPER + "/v2/account": Respuesta(real)}
        b = crear(sesion_rutas)
        with self.assertRaises(CuentaRealDetectada):
            b.cadena_opciones("SPY", date(2026, 10, 23), date(2026, 11, 6))
        self.assertEqual(len(b._sesion.pedidos), 1)

    def test_no_hay_metodos_de_ordenes(self):
        for nombre in dir(AlpacaPaper):
            self.assertNotIn("orden", nombre.lower())
            self.assertNotIn("order", nombre.lower())


class TestCadena(unittest.TestCase):
    def test_parsear_occ(self):
        self.assertEqual(
            parsear_simbolo_occ("SPY261030P00550000"), ("SPY", date(2026, 10, 30), "put", 550.0)
        )
        self.assertEqual(
            parsear_simbolo_occ("SPY261106C00612500"), ("SPY", date(2026, 11, 6), "call", 612.5)
        )

    def test_cadena_paginada_y_ventana(self):
        paginas = {
            None: {
                "snapshots": {
                    "SPY261030P00550000": snapshot(-0.15),
                    "SPY261030C00620000": snapshot(0.12),
                },
                "next_page_token": "p2",
            },
            "p2": {
                "snapshots": {
                    "SPY261106P00540000": snapshot(-0.08),
                    "SPY261106P00560000": snapshot(-0.18),
                },
                "next_page_token": None,
            },
        }
        rutas = {
            URL_TRADING_PAPER + "/v2/account": Respuesta(CUENTA_PAPER),
            URL_DATOS + "/v1beta1/options/snapshots/SPY": lambda p: Respuesta(paginas[p.get("page_token")]),
        }
        b = crear(rutas)
        hoy = date(2026, 10, 16)
        vencs = cadena_en_ventana(b, "SPY", hoy=hoy)

        self.assertEqual([v.fecha for v in vencs], [date(2026, 10, 30), date(2026, 11, 6)])
        self.assertEqual([v.dias for v in vencs], [14, 21])
        self.assertEqual(sum(len(v.contratos) for v in vencs), 4)

        pedido = [p for u, p in b._sesion.pedidos if "snapshots" in u][0]
        self.assertEqual(pedido["expiration_date_gte"], "2026-10-23")
        self.assertEqual(pedido["expiration_date_lte"], "2026-11-06")
        self.assertEqual(pedido["feed"], "indicative")

        filtrados = filtrar_por_delta(vencs[1].contratos, 0.10, 0.20)
        self.assertEqual([c.strike for c in filtrados], [560.0])
        self.assertAlmostEqual(filtrados[0].medio, 1.05)

        texto = mostrar_cadena("SPY", 600.0, vencs, 0.10, 0.20)
        self.assertIn("SPY261106P00560000", texto)
        self.assertNotIn("SPY261106P00540000", texto)

    def test_mostrar_cuenta(self):
        b = crear({URL_TRADING_PAPER + "/v2/account": Respuesta(CUENTA_PAPER)})
        texto = mostrar_cuenta(b.cuenta())
        self.assertIn("PA1234567", texto)
        self.assertIn("100,000.00", texto)


if __name__ == "__main__":
    unittest.main()
