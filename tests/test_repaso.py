"""Pruebas del repaso semanal sin red y sin modelo real."""

import asyncio
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from agentes import modelo as modulo_modelo
from agentes.bitacora import TradeRegistrado
from agentes.modelo import Respuesta
from agentes.repaso import (
    clasificar_error,
    fichas,
    formatear_repaso,
    guardar_propuestas,
    repasar,
    resultado_hipotetico,
    semana_de,
    valor_al_vencimiento,
)
from broker.base import Posicion, PuntoPatrimonio
from tests.test_fase3 import HOY, BrokerResearch

VIERNES = date(2026, 10, 23)
PATAS = [
    {"tipo": "put", "lado": "compra", "strike": 740.0},
    {"tipo": "put", "lado": "venta", "strike": 745.0},
]


def decision(tid, decision="APROBAR", votos=None, **extra):
    d = {
        "fecha": "2026-10-19",
        "decision": decision,
        "razon": "r",
        "trade_debatido": tid,
        "patas": PATAS,
        "credito": 0.50,
        "vencimiento": "2026-10-21",
        "precio": 770.0,
        "lado_chart": "puts",
        "eventos": [],
        "peor_escenario_usd": -200.0,
        "tesis_strategy": "Tesis de prueba",
        "votos": votos or {"ejecutor": {"voto": "A FAVOR"}, "esceptico": {"voto": "EN CONTRA"}},
    }
    if decision == "APROBAR":
        d["orden_final"] = {"id": tid, "contratos": 1, "precio_limite": 0.5}
    d.update(extra)
    return d


class BrokerSemana(BrokerResearch):
    def __init__(self, cierres_raw=None, posiciones=None):
        super().__init__(VIERNES)
        self._raw = cierres_raw or {}
        self._pos = posiciones or []

    def historial_patrimonio(self, desde):
        return [
            PuntoPatrimonio(date(2026, 10, 16), 100_000),
            PuntoPatrimonio(date(2026, 10, 20), 99_000),
            PuntoPatrimonio(date(2026, 10, 22), 100_500),
        ]

    def posiciones(self):
        return self._pos

    def barras_diarias(self, simbolo, desde, hasta, ajustadas=True):
        barras = super().barras_diarias(simbolo, desde, hasta, ajustadas)
        if ajustadas:
            return barras
        from tests.test_fase2 import barra

        return [barra(d, self._raw.get(d, b.cierre)) for d, b in ((b.fecha, b) for b in barras)]


class TestCalculos(unittest.TestCase):
    def test_semana(self):
        self.assertEqual(semana_de(date(2026, 10, 23)), (date(2026, 10, 19), date(2026, 10, 23)))
        self.assertEqual(semana_de(date(2026, 10, 19)), (date(2026, 10, 19), date(2026, 10, 23)))

    def test_valor_y_hipotetico(self):
        self.assertEqual(valor_al_vencimiento(PATAS, 760), 0)
        self.assertEqual(valor_al_vencimiento(PATAS, 742), 3)
        self.assertEqual(valor_al_vencimiento(PATAS, 700), 5)
        d = decision("x")
        self.assertEqual(resultado_hipotetico(d, {date(2026, 10, 21): 760.0}), 50.0)
        self.assertEqual(resultado_hipotetico(d, {date(2026, 10, 21): 700.0}), -450.0)
        self.assertIsNone(resultado_hipotetico(d, {}))

    def test_clasificar_error(self):
        t = TradeRegistrado("x", "cerrado", "SPY", (), 450, resultado=-300, cerrado_el=date(2026, 10, 21), abierto_el=date(2026, 10, 19))
        e = clasificar_error(t, decision("x"), {date(2026, 10, 21): 740.0})
        texto = " ".join(e["motivos"])
        self.assertIn("Tesis de lado", texto)
        self.assertIn("Riesgo mal medido", texto)
        self.assertIn("No se encontró", clasificar_error(t, None, {})["motivos"][0])

    def test_fichas(self):
        ds = [
            decision("gano", votos={"ejecutor": {"voto": "A FAVOR"}, "esceptico": {"voto": "EN CONTRA"}, "gestor": {"voto": "AJUSTAR"}}),
            decision("perdio", "RECHAZAR", votos={"ejecutor": {"voto": "A FAVOR"}, "esceptico": {"voto": "EN CONTRA"}}),
            decision("sin_resultado"),
        ]
        f = fichas(ds, {"gano": 50.0, "perdio": -300.0})
        self.assertEqual((f["ejecutor"]["aciertos"], f["ejecutor"]["votos"]), (1, 2))
        self.assertEqual((f["esceptico"]["aciertos"], f["esceptico"]["votos"]), (1, 2))
        self.assertEqual(f["gestor"]["votos"], 0)  # AJUSTAR no cuenta
        self.assertEqual((f["jefe"]["aciertos"], f["jefe"]["votos"]), (2, 2))


class ModeloRepaso:
    def __init__(self):
        self.pedidos = []

    async def pedir(self, pedido):
        self.pedidos.append(pedido)
        return Respuesta(
            datos={
                "resumen_semana": "Semana corta.",
                "cambios": [
                    {"leccion": "Vender con delta 0.12", "motivo": "m", "evidencia": "e", "parametro": "delta_objetivo", "valor_nuevo": 0.12},
                    {"leccion": "Delta 0.30", "motivo": "m", "evidencia": "e", "parametro": "delta_objetivo", "valor_nuevo": 0.30},
                    {"leccion": "Mirar el VIX", "motivo": "m", "evidencia": "e", "parametro": "ninguno", "valor_nuevo": 0},
                ],
            },
            costo_usd=0.02,
        )


class TestRepaso(unittest.TestCase):
    def correr(self, modelo=None, posiciones=None):
        trades = [
            TradeRegistrado("perdedor", "cerrado", "SPY", ("A",), 450, resultado=-300, cerrado_el=date(2026, 10, 21),
                            abierto_el=date(2026, 10, 19), credito=0.5),
            TradeRegistrado("abierto", "abierto", "SPY", ("B",), 450, abierto_el=date(2026, 10, 20), credito=0.4),
        ]
        decisiones = [decision("perdedor"), decision("abierto"), decision("rechazado", "RECHAZAR"),
                      {"fecha": "2026-10-22", "decision": "RECHAZAR", "razon": "No hay trade hoy", "sin_trade": "IV baja"}]
        broker = BrokerSemana({date(2026, 10, 21): 760.0, VIERNES: 760.0}, posiciones)
        return asyncio.run(repasar(broker, modelo, VIERNES, "SPY", trades=trades, decisiones=decisiones))

    def test_informe_sin_modelo(self):
        r = self.correr(posiciones=[Posicion("B", "us_option", -1, -20, 15.0), Posicion("Z", "us_option", 1, 5, 1.0)])
        self.assertEqual(r.resultado, 0.0)  # BrokerResearch: patrimonio 100.000
        self.assertAlmostEqual(r.drawdown_semana, -0.01)
        self.assertEqual([t["id"] for t in r.trades_cerrados], ["perdedor"])
        self.assertEqual(r.trades_cerrados[0]["tesis"], "Tesis de prueba")
        self.assertEqual(r.posiciones_abiertas[0]["resultado_no_realizado"], 15.0)
        self.assertTrue(any("Z" in a for a in r.avisos))
        self.assertEqual(len(r.errores), 1)
        self.assertEqual(r.rechazados, [{"id": "rechazado", "decision": "RECHAZAR", "resultado_hipotetico": 50.0}])
        self.assertEqual(r.fichas["jefe"]["votos"], 2)  # perdedor (aprobado) y rechazado
        self.assertEqual(len(r.decisiones), 4)
        texto = formatear_repaso(r)
        for parte in ("## Resultado", "## Posiciones abiertas", "## 1. Errores", "## 2. Fichas", "## 3. Lecciones"):
            self.assertIn(parte, texto)
        json.dumps(r.a_dict(), default=str)

    def test_lecciones_y_prueba(self):
        modelo = ModeloRepaso()
        r = self.correr(modelo)
        self.assertEqual(len(modelo.pedidos), 1)
        self.assertIn("Skill bitacora-trades", modelo.pedidos[0].sistema)
        self.assertEqual(len(r.pruebas), 2)  # el de "ninguno" no se prueba
        self.assertIn("comparacion", r.pruebas[0])
        self.assertIn("fuera de 0.10 a 0.20", r.pruebas[1]["error"])
        self.assertIn("## 4. Prueba con el backtest", formatear_repaso(r))
        with tempfile.TemporaryDirectory() as tmp:
            archivo = Path(tmp) / "p.jsonl"
            guardar_propuestas(r, archivo)
            (linea,) = archivo.read_text(encoding="utf-8").splitlines()
            self.assertIsNone(json.loads(linea)["aprobado"])


class TestLecciones(unittest.TestCase):
    def test_solo_lineas_con_guion(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "lecciones.md"
            with mock.patch.object(modulo_modelo, "ARCHIVO_LECCIONES", ruta):
                self.assertEqual(modulo_modelo.lecciones_aprobadas(), "")
                ruta.write_text("# Lecciones\n\nTexto.\n", encoding="utf-8")
                self.assertEqual(modulo_modelo.lecciones_aprobadas(), "")
                ruta.write_text("# Lecciones\n\n- 2026-10-23: no vender calls con tendencia alcista fuerte.\n", encoding="utf-8")
                self.assertIn("no vender calls", modulo_modelo.lecciones_aprobadas())

    def test_el_repo_no_trae_lecciones_todavia(self):
        self.assertEqual(modulo_modelo.lecciones_aprobadas(), "")


if __name__ == "__main__":
    unittest.main()
