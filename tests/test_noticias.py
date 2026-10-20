"""Guardia de noticias: News fuera del ciclo."""

import asyncio
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from agentes import noticias
from agentes.bitacora import TradeRegistrado
from agentes.modelo import Respuesta
from tests.test_fase3 import BrokerResearch

HOY = date(2026, 10, 20)


class ModeloNews:
    def __init__(self):
        self.pedidos = []

    async def pedir(self, pedido):
        self.pedidos.append(pedido)
        return Respuesta(
            datos={
                "tesis": "t", "datos": [], "confianza": "media", "motivo_confianza": "", "invalidaria": [], "faltantes": [],
                "sentimiento": "mixto", "por_que_se_mueve": "Fed", "hechos": ["h"], "rumores": [],
                "eventos": [{"fecha": "2026-10-28", "hora": "14:00", "evento": "FOMC", "tipo": "macro", "fuente": "Fed", "confirmado": True, "impacto": "alto"}],
                "veredicto": "ALERTA", "motivo_veredicto": "Fed",
                "trades_abiertos": [{"id": "GLD-x", "amenaza": "vigilar", "motivo": "m"}],
            },
            costo_usd=0.1,
        )


class TestNoticias(unittest.TestCase):
    def test_revision_registro_y_notas_del_dia(self):
        modelo = ModeloNews()
        trades = [
            TradeRegistrado(id="GLD-x", estado="abierto", subyacente="GLD", simbolos=("A",), perdida_maxima=100),
            TradeRegistrado(id="SPY-y", estado="abierto", subyacente="SPY", simbolos=("B",), perdida_maxima=100),
        ]
        with tempfile.TemporaryDirectory() as d:
            archivo = Path(d) / "noticias.jsonl"
            with mock.patch.object(noticias, "ARCHIVO_NOTICIAS", archivo):
                r = asyncio.run(noticias.revisar_noticias(BrokerResearch(HOY), modelo, "GLD", HOY, trades=trades, previas=[]))
                noticias.registrar_noticias(r, archivo)
                notas = noticias.notas_de_hoy("GLD", HOY, archivo)
                self.assertEqual(noticias.notas_de_hoy("SPY", HOY, archivo), [])
        mensaje = modelo.pedidos[0].mensaje
        self.assertIn("GLD-x", mensaje)
        self.assertNotIn("SPY-y", mensaje)
        self.assertTrue(modelo.pedidos[0].busqueda_web)
        self.assertEqual(len(notas), 1)
        self.assertEqual(notas[0]["trades_abiertos"][0]["amenaza"], "vigilar")
        self.assertIn("VIGILAR", noticias.formatear_noticias(r))
        self.assertIn("trades_abiertos", noticias.ESQUEMA_GUARDIA["required"])
        self.assertNotIn("trades_abiertos", noticias.ESQUEMA_NEWS["properties"])


if __name__ == "__main__":
    unittest.main()
