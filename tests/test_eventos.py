"""Regla de eventos nueva (27 oct 2026): el evento se mide, no se veta.

Cubre la medición del movimiento histórico, el colchón, el veto por distancia
al strike y la comparación de la regla nueva contra la vieja.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from agentes import config
from agentes.comparacion_eventos import comparar, formatear, freno_por_evento, modo_de
from agentes.estructuras import armar_candidatos
from agentes.eventos import (
    EventoDelPlazo,
    calcular_colchon,
    distancia_al_strike_pct,
    empresa_de,
    eventos_del_plazo,
    tipo_macro,
)
from agentes.freno import evaluar_freno
from agentes.riesgo import revisar
from broker.base import Barra, PuntoPatrimonio
from datos.eventos import Evento
from datos.reaccion_eventos import (
    EventoPasado,
    cargar_historicos,
    movimiento_del_evento,
    percentil,
    reaccion_de_tipo,
    reacciones,
)
from tests.test_fase3 import HOY, cuenta, venc


def barras(precios: dict[date, float]) -> list[Barra]:
    return [Barra(f, p, p, p, p, 1_000) for f, p in sorted(precios.items())]


def serie(desde: date, valores: list[float]) -> list[Barra]:
    """Una barra por día hábil, en orden."""
    out, d = {}, desde
    for v in valores:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out[d] = v
        d += timedelta(days=1)
    return barras(out)


class TestMedicion(unittest.TestCase):
    def test_percentil(self):
        self.assertEqual(percentil([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(percentil([5], 90), 5)
        self.assertIsNone(percentil([], 50))

    def test_movimiento_usa_el_peor_entre_el_dia_y_el_siguiente(self):
        b = serie(date(2026, 1, 5), [100, 101, 99])  # lun, mar, mié
        cierres = sorted(((x.fecha, x.cierre) for x in b), key=lambda x: x[0])
        m = movimiento_del_evento(cierres, date(2026, 1, 6))
        self.assertEqual(m["dia_pct"], 1.0)
        self.assertEqual(m["dos_dias_pct"], -1.0)
        self.assertEqual(m["movimiento_pct"], 1.0)

    def test_sin_barra_previa_no_hay_movimiento(self):
        b = serie(date(2026, 1, 5), [100, 101])
        cierres = sorted(((x.fecha, x.cierre) for x in b), key=lambda x: x[0])
        self.assertIsNone(movimiento_del_evento(cierres, date(2026, 1, 5)))
        self.assertIsNone(movimiento_del_evento(cierres, date(2026, 1, 20)))

    def test_reaccion_de_tipo(self):
        b = serie(date(2026, 1, 5), [100, 102, 102, 104, 104])
        eventos = [
            EventoPasado(date(2026, 1, 6), "fed", "FOMC", "f"),
            EventoPasado(date(2026, 1, 8), "fed", "FOMC", "f"),
            EventoPasado(date(2026, 1, 8), "inflacion", "CPI", "f"),
        ]
        r = reaccion_de_tipo(b, eventos, "fed")
        self.assertEqual(r.eventos, 2)
        self.assertAlmostEqual(r.mediana_pct, 1.98, places=1)
        self.assertEqual(r.peor_fecha, "2026-01-06")
        self.assertEqual(len(r.ultimos), 2)
        # Un evento posterior a 'hasta' no se cuenta.
        self.assertEqual(reaccion_de_tipo(b, eventos, "fed", hasta=date(2026, 1, 7)).eventos, 1)
        self.assertEqual(reaccion_de_tipo(b, eventos, "empleo").eventos, 0)

    def test_el_csv_historico_tiene_los_tres_tipos(self):
        historicos = cargar_historicos()
        self.assertGreater(len(historicos), 200)
        for tipo in ("fed", "inflacion", "empleo"):
            del_tipo = [e for e in historicos if e.tipo == tipo]
            self.assertGreater(len(del_tipo), 30, tipo)
            self.assertLess(del_tipo[0].fecha, date(2019, 1, 1), tipo)

    def test_reacciones_devuelve_los_tres_tipos(self):
        r = reacciones(serie(date(2026, 1, 5), [100] * 5))
        self.assertEqual(sorted(r), ["empleo", "fed", "inflacion"])


class TestColchon(unittest.TestCase):
    def test_reconoce_macro_y_empresa(self):
        self.assertEqual(tipo_macro("Decisión de tasas de la Fed (FOMC)"), "fed")
        self.assertEqual(tipo_macro("CPI de octubre"), "inflacion")
        self.assertEqual(tipo_macro("Informe de empleo (nóminas)"), "empleo")
        self.assertIsNone(tipo_macro("Balance de Microsoft"))
        self.assertEqual(empresa_de("Balance de Microsoft (MSFT) después del cierre"), "MSFT")
        self.assertIsNone(empresa_de("Reunión de la OPEP"))

    def test_eventos_del_plazo_no_repite_el_macro_del_calendario(self):
        fed = Evento(HOY + timedelta(days=3), "14:00", "fed", "Decisión de tasas de la Fed", "Fed")
        news = [
            {"fecha": (HOY + timedelta(days=3)).isoformat(), "evento": "FOMC: decisión de tasas", "tipo": "macro", "impacto": "alto"},
            {"fecha": (HOY + timedelta(days=4)).isoformat(), "evento": "Balance de NVDA", "tipo": "earnings", "impacto": "alto"},
            {"fecha": (HOY + timedelta(days=40)).isoformat(), "evento": "CPI", "tipo": "macro", "impacto": "alto"},
        ]
        plazo = eventos_del_plazo([fed], news, HOY, HOY + timedelta(days=14), "QQQ")
        self.assertEqual([e.tipo for e in plazo], ["fed", "earnings"])
        self.assertEqual(plazo[1].empresa, "NVDA")

    def test_colchon_suma_en_cuadratura_y_pondera_el_balance(self):
        reaccion = {
            "fed": {"mediana_pct": 3.0, "p90_pct": 6.0, "eventos": 10, "desde": "a", "hasta": "b"},
            "inflacion": {"mediana_pct": 4.0, "p90_pct": 8.0, "eventos": 10, "desde": "a", "hasta": "b"},
        }
        plazo = [
            EventoDelPlazo("2026-11-01", "FOMC", "fed", "calendario"),
            EventoDelPlazo("2026-11-05", "CPI", "inflacion", "calendario"),
        ]
        c = calcular_colchon("QQQ", plazo, reaccion, distancia_strike_pct=6.0, percentil=50)
        self.assertEqual(c.colchon_pct, 5.0)  # raíz de 3² + 4²
        self.assertEqual(c.colchon_estres_pct, 10.0)
        self.assertTrue(c.alcanza)
        self.assertTrue(c.hay_macro)
        self.assertEqual(c.sobra_pct, 1.0)
        # Un balance de NVDA en QQQ aporta su peso por el movimiento supuesto.
        solo_nvda = calcular_colchon(
            "QQQ", [EventoDelPlazo("2026-11-01", "Balance de NVDA", "earnings", "news", empresa="NVDA")], reaccion
        )
        self.assertAlmostEqual(solo_nvda.colchon_pct, round(config.PESOS_MAYORES["QQQ"]["NVDA"] * config.MOVIMIENTO_EARNINGS_PCT, 2))
        self.assertFalse(solo_nvda.hay_macro)

    def test_sin_eventos_no_hay_colchon(self):
        c = calcular_colchon("SPY", [], {}, distancia_strike_pct=3.0)
        self.assertEqual(c.colchon_pct, 0.0)
        self.assertEqual(c.aportes, [])

    def test_sin_datos_historicos_el_aporte_queda_anotado(self):
        c = calcular_colchon("SPY", [EventoDelPlazo("2026-11-01", "FOMC", "fed", "calendario")], {}, distancia_strike_pct=3.0)
        self.assertEqual(c.colchon_pct, 0.0)
        self.assertIsNone(c.aportes[0]["aporte_pct"])


def reaccion_falsa(mediana: float, p90: float) -> dict:
    return {
        t: {"mediana_pct": mediana, "p75_pct": (mediana + p90) / 2, "p90_pct": p90, "eventos": 50, "desde": "2018", "hasta": "2026"}
        for t in ("fed", "inflacion", "empleo")
    }


class TestRiesgoConEventos(unittest.TestCase):
    def setUp(self):
        self.put = next(c for c in armar_candidatos("SPY", [venc(9)], 600.0) if c.estructura == "spread_put")
        self.freno = evaluar_freno(HOY, cuenta(), [PuntoPatrimonio(HOY, 100_000)], [], [])
        self.fed = Evento(HOY + timedelta(days=5), "14:00", "fed", "Decisión de tasas de la Fed", "Fed")

    def revisar(self, eventos=(), news=None, reaccion=None):
        return revisar(
            self.put, HOY, 100_000, 600, self.freno, [], list(eventos), news, reaccion or reaccion_falsa(1.0, 3.0)
        )

    def test_el_strike_lejos_opera_con_alerta(self):
        distancia = distancia_al_strike_pct(self.put, 600.0)
        self.assertGreater(distancia, 1.0)
        r = self.revisar([self.fed])
        self.assertFalse(r.vetado, r.vetos)
        self.assertTrue(any("Eventos en el plazo" in a for a in r.alertas), r.alertas)
        self.assertEqual(r.colchon["colchon_pct"], 1.0)
        self.assertTrue(r.colchon["hay_macro"])

    def test_el_strike_cerca_descarta_por_la_cuenta(self):
        r = self.revisar([self.fed], reaccion=reaccion_falsa(20.0, 30.0))
        self.assertTrue(r.vetado)
        self.assertTrue(any("mueven típicamente" in v for v in r.vetos), r.vetos)

    def test_con_macro_se_recorta_el_tamano(self):
        sin_evento = self.revisar()
        con_evento = self.revisar([self.fed])
        self.assertIsNone(sin_evento.contratos_max_sin_evento)
        self.assertEqual(con_evento.contratos_max_sin_evento, sin_evento.contratos_max)
        self.assertLessEqual(con_evento.contratos_max, sin_evento.contratos_max)
        with mock.patch.object(config, "FACTOR_CONTRATOS_EVENTO", 1.0):
            self.assertEqual(self.revisar([self.fed]).contratos_max, sin_evento.contratos_max)

    def test_el_balance_del_propio_subyacente_sigue_vetando(self):
        news = [{"fecha": (HOY + timedelta(days=3)).isoformat(), "evento": "Balance de SPY", "tipo": "earnings", "impacto": "alto"}]
        r = revisar(self.put, HOY, 100_000, 600, self.freno, [], [], news, reaccion_falsa(1.0, 3.0), es_etf=False)
        self.assertTrue(r.vetado)
        self.assertTrue(any("el propio subyacente" in v for v in r.vetos), r.vetos)
        # Como ETF, el mismo evento no veta.
        self.assertFalse(revisar(self.put, HOY, 100_000, 600, self.freno, [], [], news, reaccion_falsa(1.0, 3.0)).vetado)

    def test_la_regla_vieja_sigue_disponible(self):
        with mock.patch.object(config, "MODO_EVENTOS", "vetar"), mock.patch.object(
            config, "IGNORAR_EVENTOS_HASTA", date(2000, 1, 1)
        ):
            r = self.revisar([self.fed])
            self.assertTrue(r.vetado)
            self.assertIsNone(r.colchon)

    def test_el_texto_de_la_regla_cambia_con_el_modo(self):
        self.assertIn("NO descarta", config.regla_eventos(HOY))
        with mock.patch.object(config, "MODO_EVENTOS", "vetar"), mock.patch.object(
            config, "IGNORAR_EVENTOS_HASTA", date(2000, 1, 1)
        ):
            self.assertIn("descarta el trade", config.regla_eventos(HOY))


DECISION_VIEJA = {
    "fecha": "2026-10-23",
    "subyacente": "SPY",
    "decision": "RECHAZAR",
    "sin_trade": "Todos los candidatos superan algún límite: Evento grande dentro del plazo",
}
DECISION_NUEVA = {
    "fecha": "2026-10-28",
    "subyacente": "QQQ",
    "decision": "APROBAR",
    "razon": "La prima paga el riesgo de la Fed.",
    "estructura": "iron_condor",
    "trade_debatido": "QQQ-iron_condor-2026-11-13-P690-C760",
    "orden_final": {"id": "QQQ-iron_condor-2026-11-13-P690-C760", "contratos": 1},
    "regla_eventos": {"modo": "operar", "percentil_colchon": 50},
    "colchon_eventos": {
        "colchon_pct": 2.1,
        "colchon_estres_pct": 5.2,
        "distancia_strike_pct": 4.3,
        "eventos": [{"fecha": "2026-10-28", "evento": "Decisión de tasas de la Fed"}],
    },
}


class TestComparacion(unittest.TestCase):
    def test_modo_y_freno(self):
        self.assertEqual(modo_de(DECISION_VIEJA), "vetar")
        self.assertEqual(modo_de(DECISION_NUEVA), "operar")
        self.assertTrue(freno_por_evento(DECISION_VIEJA))
        self.assertFalse(freno_por_evento(DECISION_NUEVA))

    def test_compara_los_dos_grupos(self):
        from agentes.bitacora import TradeRegistrado

        trades = [
            TradeRegistrado(
                DECISION_NUEVA["trade_debatido"], "cerrado", "QQQ", ("S",), 379, resultado=91.0, cerrado_el=date(2026, 11, 3)
            )
        ]
        c = comparar([DECISION_VIEJA, DECISION_NUEVA], trades)
        viejo, nuevo = c["grupos"]["vetar"], c["grupos"]["operar"]
        self.assertEqual((viejo["ciclos"], viejo["aprobados"], viejo["frenados_por_evento"]), (1, 0, 1))
        self.assertEqual((nuevo["ciclos"], nuevo["aprobados"], nuevo["cerrados"]), (1, 1, 1))
        self.assertEqual(nuevo["resultado_usd"], 91.0)
        self.assertEqual(nuevo["trades"][0]["colchon_pct"], 2.1)
        texto = formatear(c)
        self.assertIn("cambiaste la regla de eventos", texto)
        self.assertIn("+91.00 USD", texto)
        self.assertIn("Decisión de tasas de la Fed", texto)

    def test_el_informe_se_escribe(self):
        import tempfile

        from agentes.comparacion_eventos import escribir

        with tempfile.TemporaryDirectory() as tmp:
            ruta = escribir(Path(tmp) / "sub" / "regla-eventos.md", comparar([], []))
            self.assertIn("Regla de eventos", ruta.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
