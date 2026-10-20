"""Pruebas de la fase 4 (consejo y jefe) sin red y sin modelo real."""

import asyncio
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import datos.iv as modulo_iv
from agentes import bitacora
from agentes.ciclo import correr_ciclo, registro_bitacora
from agentes.consejo import costos, dia_habil_anterior, escenarios, orden_por_defecto, plan_por_defecto
from agentes.formato import formatear_ciclo
from agentes.jefe import validar
from tests.test_fase3 import HOY, BrokerResearch, ModeloFalso


def datos_del_trade(pedido) -> dict:
    """Los datos comunes que el consejo y el jefe reciben en las instrucciones."""
    return json.loads(pedido.sistema.split("## Datos del trade (research, Risk y cálculos en código)\n", 1)[1])


class ModeloConsejo(ModeloFalso):
    """Además de research, responde por el consejo y el jefe."""

    def __init__(self, votos=None, jefe=None, **kw):
        super().__init__(**kw)
        self.votos = votos or {}
        self.jefe = jefe or (lambda mensaje, vuelta: {"decision": "RECHAZAR"})

    async def pedir(self, pedido):
        if pedido.agente.startswith(("Ejecutor", "Escéptico", "Gestor", "Liquidez")):
            self.pedidos.append(pedido)
            msg = datos_del_trade(pedido)
            ronda = 2 if "Ronda 2 de 2" in pedido.mensaje else 1
            nombre = pedido.agente.split(" (")[0]
            p = {
                "postura": "p",
                "respuesta_a_otros": "r" if ronda == 2 else "",
                "voto": self.votos.get(nombre, "A FAVOR"),
                "cambio_propuesto": "",
                "confianza": "media",
            }
            if nombre == "Ejecutor de primas":
                p["orden"] = msg["orden_por_defecto"]
            if nombre == "Gestor de posición":
                p["plan_salida"] = msg["plan_de_salida_por_defecto"]
                p["orden"] = {"id": "no-deberia-quedar", "contratos": 1, "precio_limite": 1}
            from agentes.modelo import Respuesta

            return Respuesta(datos=p, costo_usd=0.01)
        if pedido.agente == "Jefe":
            self.pedidos.append(pedido)
            msg = json.loads(pedido.mensaje.split("## Debate\n", 1)[1])
            msg["debate"] = datos_del_trade(pedido)
            base = {"razon_principal": "r", "objecion_que_mas_peso": "o", "cambios_pedidos": [], "que_revisar_proximo_ciclo": ""}
            base.update(self.jefe(msg, msg["vuelta"]))
            from agentes.modelo import Respuesta

            return Respuesta(datos=base, costo_usd=0.01)
        return await super().pedir(pedido)


def ciclo(modelo, archivo):
    with tempfile.TemporaryDirectory() as tmp:
        original = modulo_iv.CARPETA_CACHE
        modulo_iv.CARPETA_CACHE = Path(tmp)
        try:
            with mock.patch("agentes.research.cargar_eventos", return_value=[]), mock.patch(
                "agentes.ciclo.registrar_decision", lambda r: bitacora.registrar_decision(r, archivo)
            ):
                return asyncio.run(correr_ciclo(BrokerResearch(HOY), modelo, "SPY", hoy=HOY, trades=[]))
        finally:
            modulo_iv.CARPETA_CACHE = original


CANDIDATO = {
    "id": "spread_put-2026-10-26-P755",
    "estructura": "spread_put",
    "vencimiento": "2026-10-26",
    "dias": 11,
    "credito": 0.50,
    "credito_natural": 0.40,
    "perdida_maxima": 550.0,
    "patas": [
        {"tipo": "put", "lado": "compra", "strike": 750.0, "bid": 1.00, "ask": 1.10, "iv": 0.16},
        {"tipo": "put", "lado": "venta", "strike": 755.0, "bid": 1.45, "ask": 1.55, "iv": 0.15},
    ],
}


class TestCalculosConsejo(unittest.TestCase):
    def test_costos(self):
        c = costos(CANDIDATO)
        self.assertEqual(c["comisiones_usd"], 2.0)
        self.assertAlmostEqual(c["slippage_esperado_usd"], 10.0)
        self.assertAlmostEqual(c["costos_sobre_credito"], 12 / 50, places=3)
        self.assertAlmostEqual(c["spread_compra_venta_sobre_credito"], 0.4, places=3)

    def test_escenarios_pierden_si_baja(self):
        es = escenarios(CANDIDATO, 770.0, 6.0)
        self.assertEqual(len(es), 2)  # 2 ATR y 3%, solo hacia abajo (spread de puts)
        self.assertTrue(all("baja" in e["escenario"] for e in es))
        peor = min(e["resultado_por_contrato_usd"] for e in es)
        self.assertLess(peor, 0)
        self.assertGreaterEqual(peor, -550.0)

    def test_plan_y_orden(self):
        plan = plan_por_defecto(CANDIDATO)
        self.assertIn("0.25", plan["tomar_ganancia"])
        self.assertIn("1.50", plan["stop"])
        self.assertIn("2026-10-23", plan["cierre_por_tiempo"])  # el 26 es lunes
        self.assertEqual(dia_habil_anterior(date(2026, 10, 26)), date(2026, 10, 23))
        orden = orden_por_defecto(CANDIDATO, 3)
        self.assertEqual(orden["contratos"], 1)
        self.assertTrue(0.40 <= orden["precio_limite"] <= 0.50)


class TestValidarJefe(unittest.TestCase):
    research = {
        "candidatos": [
            {"candidato": CANDIDATO, "revision": {"contratos_max": 3, "vetado": False, "vetos": []}}
        ]
    }
    plan = plan_por_defecto(CANDIDATO)

    def aprobar(self, **orden):
        o = {"id": CANDIDATO["id"], "contratos": 1, "precio_limite": 0.48, **orden}
        return {"decision": "APROBAR", "orden_final": o, "plan_salida": dict(self.plan), "cambios_pedidos": []}

    def test_aprobacion_valida(self):
        self.assertEqual(validar(self.aprobar(), self.research, True)["decision"], "APROBAR")

    def test_reglas_fijas(self):
        casos = [
            (self.aprobar(id="otro"), True),
            (self.aprobar(contratos=4), True),
            (self.aprobar(precio_limite=0.60), True),
            (self.aprobar(), False),  # cuenta no paper
        ]
        for decision, paper in casos:
            d = validar(decision, self.research, paper)
            self.assertEqual(d["decision"], "AJUSTAR", d)
            self.assertNotIn("orden_final", d)
        sin_plan = self.aprobar()
        sin_plan["plan_salida"]["stop"] = ""
        self.assertEqual(validar(sin_plan, self.research, True)["decision"], "AJUSTAR")


class TestCiclo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archivo = Path(self.tmp.name) / "decisiones.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_aprueba_y_registra(self):
        def jefe(msg, vuelta):
            d = msg["debate"]
            return {"decision": "APROBAR", "orden_final": d["orden_por_defecto"], "plan_salida": d["plan_de_salida_por_defecto"]}

        modelo = ModeloConsejo(jefe=jefe)
        c = ciclo(modelo, self.archivo)
        agentes = [p.agente for p in modelo.pedidos]
        self.assertEqual(agentes[:5], ["Chart", "News", "Fundamentals", "Strategy", "Risk"])
        self.assertEqual(len([a for a in agentes if "ronda 1" in a]), 4)
        self.assertEqual(len([a for a in agentes if "ronda 2" in a]), 4)
        self.assertEqual(agentes[-1], "Jefe")
        # En la segunda ronda cada uno ve las posturas de la primera.
        ronda2 = next(p for p in modelo.pedidos if "ronda 2" in p.agente)
        self.assertIn("## Posturas de la ronda 1", ronda2.mensaje)
        # Los nueve llamados del consejo y el jefe comparten las instrucciones, para reusar el caché.
        sistemas = {p.sistema for p in modelo.pedidos if p.agente not in ("Chart", "News", "Fundamentals", "Strategy", "Risk")}
        self.assertEqual(len(sistemas), 1)
        # Solo el ejecutor deja orden.
        self.assertNotIn("orden", c.final.debate.rondas[0]["gestor"])
        self.assertEqual(c.final.decision["decision"], "APROBAR")
        (reg,) = bitacora.leer_decisiones(self.archivo)
        self.assertEqual(reg["decision"], "APROBAR")
        self.assertFalse(reg["orden_enviada"])
        self.assertEqual(set(reg["votos"]), {"ejecutor", "esceptico", "gestor", "liquidez"})
        self.assertIn("plan_salida", reg)
        self.assertIn("JEFE: APROBAR", formatear_ciclo(c))
        json.dumps(c.a_dict(), default=str)

    def test_ajustar_vuelve_a_research_y_corta_en_la_segunda(self):
        modelo = ModeloConsejo(jefe=lambda msg, vuelta: {"decision": "AJUSTAR", "cambios_pedidos": ["más lejos"]})
        c = ciclo(modelo, self.archivo)
        self.assertEqual(len(c.vueltas), 2)
        self.assertEqual(c.final.decision["decision"], "RECHAZAR")
        agentes = [p.agente for p in modelo.pedidos]
        self.assertEqual(agentes.count("Chart"), 1)  # Chart, News y Fundamentals se reusan
        self.assertEqual(agentes.count("Fundamentals"), 1)
        self.assertEqual(agentes.count("Strategy"), 2)
        strategy2 = [p for p in modelo.pedidos if p.agente == "Strategy"][1]
        self.assertEqual(json.loads(strategy2.mensaje)["ajustes_pedidos_por_el_jefe"], ["más lejos"])
        self.assertEqual(bitacora.leer_decisiones(self.archivo)[0]["vueltas"], 2)

    def test_aprobacion_invalida_no_pasa(self):
        modelo = ModeloConsejo(
            jefe=lambda msg, vuelta: {"decision": "APROBAR", "orden_final": {"id": "inventado", "contratos": 1, "precio_limite": 0.1}}
        )
        c = ciclo(modelo, self.archivo)
        self.assertEqual(c.final.decision["decision"], "RECHAZAR")
        self.assertTrue(c.vueltas[0].decision["correcciones"])

    def test_sin_candidatos_registra_igual(self):
        modelo = ModeloConsejo(strategy=lambda cs: [])
        c = ciclo(modelo, self.archivo)
        self.assertIsNone(c.final.debate)
        (reg,) = bitacora.leer_decisiones(self.archivo)
        self.assertEqual(reg["decision"], "RECHAZAR")
        self.assertIn("sin_trade", reg)

    def test_sin_modelo(self):
        c = ciclo(None, self.archivo)
        self.assertFalse(c.con_modelo)
        self.assertIn(c.final.decision["decision"], ("APROBAR", "RECHAZAR"))
        self.assertTrue(c.final.decision.get("sin_modelo"))
        self.assertTrue(registro_bitacora(c)["decision"])


if __name__ == "__main__":
    unittest.main()
