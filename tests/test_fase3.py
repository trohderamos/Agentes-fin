"""Pruebas de la fase 3 (agentes de research) sin red y sin modelo real."""

import asyncio
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentes import config
from agentes.backtest import correr_backtest, metricas, simular, strike_por_delta
from agentes.bitacora import BitacoraInvalida, TradeRegistrado, leer_trades
from agentes.estructuras import armar_candidatos
from agentes.formato import formatear_research
from agentes.freno import DETENIDO, ENFRIAMIENTO, PERMITIDO, evaluar_freno
from agentes.modelo import Pedido, Respuesta, cargar_skill, entorno_aislado
from agentes.research import Research, niveles
from agentes.riesgo import revisar
from broker.alpaca_paper import URL_DATOS, URL_TRADING_PAPER
from broker.base import Barra, Cuenta, Posicion, PuntoPatrimonio
from datos import calculos
from datos.cadena import Vencimiento
from datos.eventos import Evento
from tests.test_fase1 import CUENTA_PAPER, Respuesta as RespuestaHttp, crear
from tests.test_fase2 import BrokerFalso, contrato

HOY = date(2026, 10, 15)


def cuenta(patrimonio=100_000.0, anterior=100_000.0):
    return Cuenta("PA1", "ACTIVE", "USD", patrimonio, patrimonio, patrimonio, patrimonio, 3, True, anterior)


def cadena(venc, s=600.0, iv=0.15):
    """Cadena con deltas de Black-Scholes, strikes cada 1 dólar."""
    import math

    t = (venc - HOY).days / 365
    out = []
    for k in range(int(s) - 60, int(s) + 61):
        for tipo in ("put", "call"):
            p = calculos.precio_bs(tipo, s, k, t, iv)
            d1 = (math.log(s / k) + (iv * iv / 2) * t) / (iv * math.sqrt(t))
            nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
            delta = nd1 if tipo == "call" else nd1 - 1
            c = contrato(tipo, float(k), venc, max(p, 0.02), delta=delta, iv=iv)
            out.append(replace(c, gamma=0.01))
    return out


def venc(dias):
    f = HOY + timedelta(days=dias)
    return Vencimiento(fecha=f, dias=dias, contratos=cadena(f))


class TestEstructuras(unittest.TestCase):
    def test_candidatos_respetan_reglas(self):
        todos = armar_candidatos("SPY", [venc(9), venc(30)], 600.0, {HOY + timedelta(days=9): 10.0})
        cands = [c for c in todos if not c.id.endswith("-A8")]
        self.assertEqual({c.estructura for c in cands}, {"spread_put", "spread_call", "iron_condor"})
        for c in cands:
            self.assertEqual(c.dias, 9)  # el de 30 días queda afuera
            for p in c.vendidos:
                self.assertTrue(0.10 <= abs(p.delta) <= 0.20)
            self.assertGreater(c.credito, 0)
            self.assertAlmostEqual(c.perdida_maxima, (c.ancho - c.credito) * 100, places=2)
            self.assertAlmostEqual(c.ganancia_maxima, c.credito * 100, places=2)
        put = next(c for c in cands if c.estructura == "spread_put")
        call = next(c for c in cands if c.estructura == "spread_call")
        self.assertGreater(put.delta, 0)  # vender puts deja delta positivo
        self.assertLess(call.delta, 0)
        self.assertEqual(put.ancho, 4.0)  # 0.75 cada 100 con SPY a 600, redondeado
        self.assertAlmostEqual(put.equilibrio_bajo, put.vendidos[0].strike - put.credito, places=2)
        condor = next(c for c in cands if c.estructura == "iron_condor")
        self.assertEqual(len(condor.patas), 4)
        self.assertLess(condor.prob_ganancia, put.prob_ganancia)

    def test_tambien_alas_anchas(self):
        # Lección aprobada el 27 oct 2026: también alas de 1.4 cada 100 (8 con SPY a 600).
        cands = armar_candidatos("SPY", [venc(9)], 600.0)
        anchos = [c for c in cands if c.id.endswith("-A8")]
        self.assertEqual({c.estructura for c in anchos}, {"spread_put", "spread_call", "iron_condor"})
        for c in anchos:
            self.assertGreater(c.ancho, 4.0)
            self.assertEqual(c.clave_backtest, f"{c.estructura} alas 8")
        self.assertEqual(len({c.id for c in cands}), len(cands))

    def test_sin_cotizacion_no_hay_candidato(self):
        v = venc(9)
        sin_bid = Vencimiento(v.fecha, v.dias, [replace(c, bid=None) for c in v.contratos])
        self.assertEqual(armar_candidatos("SPY", [sin_bid], 600.0), [])


class TestFreno(unittest.TestCase):
    def hist(self, *valores):
        return [PuntoPatrimonio(HOY - timedelta(days=len(valores) - i), v) for i, v in enumerate(valores)]

    def test_permitido(self):
        r = evaluar_freno(HOY, cuenta(), self.hist(100_000), [], [])
        self.assertEqual(r.estado, PERMITIDO)

    def test_falta_historial_detiene(self):
        self.assertEqual(evaluar_freno(HOY, cuenta(), [], [], []).estado, DETENIDO)
        self.assertEqual(evaluar_freno(HOY, cuenta(anterior=None), self.hist(1), [], []).estado, DETENIDO)

    def test_perdida_del_dia(self):
        r = evaluar_freno(HOY, cuenta(97_000, 100_000), self.hist(100_000), [], [])
        self.assertEqual(r.estado, DETENIDO)
        self.assertIn("día", r.motivos[0])

    def test_perdida_del_mes(self):
        hist = [PuntoPatrimonio(date(2026, 9, 30), 100_000), PuntoPatrimonio(date(2026, 10, 14), 92_000)]
        r = evaluar_freno(HOY, cuenta(91_500, 92_000), hist, [], [])
        self.assertEqual(r.estado, DETENIDO)
        self.assertEqual(r.reevaluar, "2026-11-01")

    def test_racha_enfria(self):
        trades = [
            TradeRegistrado("a", "cerrado", "SPY", (), 500, resultado=-100, cerrado_el=HOY - timedelta(days=3)),
            TradeRegistrado("b", "cerrado", "SPY", (), 500, resultado=-50, cerrado_el=HOY - timedelta(days=1)),
        ]
        self.assertEqual(evaluar_freno(HOY, cuenta(), self.hist(100_000), [], trades).estado, ENFRIAMIENTO)
        trades[1] = replace(trades[1], cerrado_el=HOY - timedelta(days=5))
        trades[0] = replace(trades[0], cerrado_el=HOY - timedelta(days=6))
        self.assertEqual(evaluar_freno(HOY, cuenta(), self.hist(100_000), [], trades).estado, PERMITIDO)

    def test_opcion_suelta_no_cuadra(self):
        pos = [Posicion("SPY261023P00590000", "us_option", -1, -50, 0)]
        self.assertEqual(evaluar_freno(HOY, cuenta(), self.hist(100_000), pos, []).estado, DETENIDO)


class TestRiesgo(unittest.TestCase):
    def setUp(self):
        self.put = next(c for c in armar_candidatos("SPY", [venc(9)], 600.0) if c.estructura == "spread_put")
        self.freno = evaluar_freno(HOY, cuenta(), [PuntoPatrimonio(HOY, 100_000)], [], [])

    def test_pasa(self):
        r = revisar(self.put, HOY, 100_000, 600, self.freno, [], [])
        self.assertFalse(r.vetado, r.vetos)
        self.assertEqual(r.contratos_max, int(2000 // self.put.perdida_maxima))

    @mock.patch.object(config, "MODO_EVENTOS", "vetar")
    @mock.patch.object(config, "IGNORAR_EVENTOS_HASTA", date(2000, 1, 1))
    def test_evento_grande_veta(self):
        fed = Evento(HOY + timedelta(days=5), "14:00", "fed", "FOMC", "Fed")
        r = revisar(self.put, HOY, 100_000, 600, self.freno, [], [fed])
        self.assertTrue(r.vetado)
        despues = Evento(HOY + timedelta(days=20), "14:00", "fed", "FOMC", "Fed")
        self.assertFalse(revisar(self.put, HOY, 100_000, 600, self.freno, [], [despues]).vetado)

    @mock.patch.object(config, "MODO_EVENTOS", "vetar")
    @mock.patch.object(config, "IGNORAR_EVENTOS_HASTA", date(2000, 1, 1))
    def test_evento_de_news_veta(self):
        ev = [{"fecha": (HOY + timedelta(days=3)).isoformat(), "evento": "Earnings NVDA", "impacto": "alto"}]
        self.assertTrue(revisar(self.put, HOY, 100_000, 600, self.freno, [], [], ev).vetado)
        ev[0]["impacto"] = "medio"
        self.assertFalse(revisar(self.put, HOY, 100_000, 600, self.freno, [], [], ev).vetado)

    @mock.patch.object(config, "MODO_EVENTOS", "vetar")
    def test_eventos_no_vetan_en_la_semana_de_prueba(self):
        ev = [{"fecha": (HOY + timedelta(days=3)).isoformat(), "evento": "Earnings NVDA", "tipo": "earnings", "impacto": "alto"}]
        fed = Evento(HOY + timedelta(days=5), "14:00", "fed", "FOMC", "Fed")
        with mock.patch.object(config, "IGNORAR_EVENTOS_HASTA", HOY):
            r = revisar(self.put, HOY, 100_000, 600, self.freno, [], [fed], ev)
            self.assertFalse(r.vetado)
            self.assertEqual(len(r.alertas), 2)
            self.assertIn("ningún evento", config.regla_eventos(HOY))
        with mock.patch.object(config, "IGNORAR_EVENTOS_HASTA", HOY - timedelta(days=1)):
            self.assertTrue(revisar(self.put, HOY, 100_000, 600, self.freno, [], [], ev).vetado)
            self.assertTrue(revisar(self.put, HOY, 100_000, 600, self.freno, [], [fed]).vetado)

    def test_capital_chico_veta(self):
        r = revisar(self.put, HOY, 10_000, 600, self.freno, [], [])
        self.assertTrue(any("por trade" in v for v in r.vetos))

    def test_riesgo_abierto_y_subyacente(self):
        abiertos = [TradeRegistrado("x", "abierto", "SPY", ("S",), 1_800)]
        r = revisar(self.put, HOY, 100_000, 600, self.freno, abiertos, [])
        self.assertTrue(any("SPY quedaría" in v for v in r.vetos))
        otros = [TradeRegistrado(str(i), "abierto", "QQQ", ("S",), 1_990) for i in range(5)]
        r = revisar(self.put, HOY, 100_000, 600, self.freno, otros, [])
        self.assertTrue(any("riesgo abierto" in v for v in r.vetos))

    def test_freno_veta(self):
        detenido = evaluar_freno(HOY, cuenta(), [], [], [])
        self.assertTrue(revisar(self.put, HOY, 100_000, 600, detenido, [], []).vetado)

    def test_delta_fuera_de_rango_veta(self):
        p = self.put.patas[1]
        malo = replace(self.put, patas=(self.put.patas[0], replace(p, delta=-0.35)))
        self.assertTrue(revisar(malo, HOY, 100_000, 600, self.freno, [], []).vetado)


def barras_sinteticas(n=900, s0=300.0, paso=0.0004, osc=0.01):
    d = date(2019, 1, 2)
    out = []
    s = s0
    for i in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        s *= 1 + paso + (osc if i % 2 else -osc)
        out.append(Barra(d, s, s * 1.005, s * 0.995, s, 1))
        d += timedelta(days=1)
    return out


class TestBacktest(unittest.TestCase):
    def test_strike_por_delta(self):
        import math

        s, t, iv = 600, 10 / 365, 0.2
        for tipo in ("put", "call"):
            k = strike_por_delta(tipo, s, t, iv, 0.15)
            d1 = (math.log(s / k) + iv * iv / 2 * t) / (iv * math.sqrt(t))
            nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
            self.assertAlmostEqual(abs(nd1 if tipo == "call" else nd1 - 1), 0.15, places=3)

    def test_sin_solapamiento_y_metricas(self):
        barras = barras_sinteticas()
        trades = simular(barras, "spread_put", 10, 0.15, 0.0075)
        self.assertGreater(len(trades), 50)
        for a, b in zip(trades, trades[1:]):
            self.assertGreater(b.entrada, a.salida)
        for t in trades:
            self.assertGreaterEqual(t.resultado, -t.perdida_maxima - 1e-6)
        m = metricas(trades)
        self.assertEqual(m.trades, len(trades))
        self.assertLessEqual(m.peor_drawdown, 0)

    def test_resultado_completo(self):
        r = correr_backtest(barras_sinteticas(), "iron_condor", 10)
        self.assertIn(r.veredicto, ("DESPLEGAR", "REFINAR", "ABANDONAR"))
        self.assertEqual(set(r.estres), {"slippage doble", "delta +20%", "delta -20%"})
        self.assertIn("COVID (feb-abr 2020)", r.crisis)
        self.assertEqual(r.ajuste.trades + r.prueba.trades, r.total.trades)
        json.dumps(r.a_dict())


class TestBitacora(unittest.TestCase):
    def test_lectura_y_reemplazo(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "trades.jsonl"
            self.assertEqual(leer_trades(ruta), [])
            ruta.write_text(
                '{"id": "a", "estado": "abierto", "subyacente": "SPY", "perdida_maxima": 500}\n'
                '{"id": "a", "estado": "cerrado", "subyacente": "SPY", "perdida_maxima": 500, "resultado": -20, "cerrado_el": "2026-10-10"}\n',
                encoding="utf-8",
            )
            (t,) = leer_trades(ruta)
            self.assertEqual((t.estado, t.resultado, t.cerrado_el), ("cerrado", -20.0, date(2026, 10, 10)))
            ruta.write_text('{"id": "b"}\n', encoding="utf-8")
            with self.assertRaises(BitacoraInvalida):
                leer_trades(ruta)


class TestModelo(unittest.TestCase):
    def test_skills_en_el_repo(self):
        for s in ("research-opciones-primas", "analisis-opciones-primas", "calendario-catalizadores", "freno-drawdown-opciones"):
            texto = cargar_skill(s)
            self.assertFalse(texto.startswith("---"))
            self.assertGreater(len(texto), 200)

    def test_entorno_aislado(self):
        env = {"ANTHROPIC_API_KEY": "clave", "CLAUDE_CODE_OAUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "http://otro", "PATH": "/bin"}
        with mock.patch.dict(os.environ, env, clear=True):
            e = entorno_aislado()
        self.assertEqual(e["ANTHROPIC_API_KEY"], "clave")
        self.assertEqual(e["CLAUDE_CODE_OAUTH_TOKEN"], "")
        self.assertEqual(e["ANTHROPIC_BASE_URL"], "https://api.anthropic.com")
        self.assertIn("CLAUDE_CONFIG_DIR", e)
        self.assertNotIn("PATH", e)

    def test_entorno_con_oauth(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": "tok", "ANTHROPIC_BASE_URL": "http://otro", "CLAUDE_X": "y"}
        with mock.patch.dict(os.environ, env, clear=True):
            e = entorno_aislado()
        self.assertEqual(e["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
        self.assertEqual(e["ANTHROPIC_BASE_URL"], "")
        self.assertEqual(e["CLAUDE_X"], "")
        self.assertIn("CLAUDE_CONFIG_DIR", e)


class ModeloFalso:
    """Responde según el agente y guarda los pedidos."""

    def __init__(self, strategy=None, risk=None, news_eventos=None):
        self.pedidos: list[Pedido] = []
        self.strategy = strategy
        self.risk = risk or {"vetos": [], "comentarios": []}
        self.news_eventos = news_eventos or []

    async def pedir(self, pedido):
        self.pedidos.append(pedido)
        base = {"tesis": "t", "datos": [], "confianza": "media", "motivo_confianza": "", "invalidaria": [], "faltantes": []}
        if pedido.agente == "Chart":
            base.update(tendencia="alcista", lado_a_vender="puts", iv_favorable=True, soportes=[], resistencias=[])
        elif pedido.agente == "Fundamentals":
            base.update(valuacion="justa", estimaciones="estables", lado_a_vender="puts", se_querria_tener=True,
                        motivo_tenencia="", riesgos_fundamentales=[])
        elif pedido.agente == "News":
            base.update(sentimiento="mixto", por_que_se_mueve="", hechos=[], rumores=[], eventos=self.news_eventos,
                        veredicto="SIN EVENTOS", motivo_veredicto="")
        elif pedido.agente == "Strategy":
            candidatos = json.loads(pedido.mensaje)["candidatos"]
            elegidos = self.strategy(candidatos) if self.strategy else [candidatos[0]["id"]]
            base.update(elegidos=[{"id": i, "motivo": "m", "riesgo_principal": "r"} for i in elegidos],
                        preferido=elegidos[0] if elegidos else "", no_hay_trade_motivo="" if elegidos else "IV baja")
        elif pedido.agente == "Risk":
            base.update(self.risk)
        return Respuesta(datos=base, costo_usd=0.01)


class BrokerResearch(BrokerFalso):
    def __init__(self, hoy, historial=True):
        super().__init__(hoy)
        self._historial = historial

    def cuenta(self):
        return cuenta()

    def historial_patrimonio(self, desde):
        return [PuntoPatrimonio(self.hoy - timedelta(days=1), 100_000)] if self._historial else []

    def cadena_opciones(self, subyacente, vence_desde, vence_hasta):
        s = self.cierres[self.hoy]
        out, d = [], vence_desde
        while d <= vence_hasta:
            if d.weekday() == 4:
                out += [replace(c, subyacente="SPY") for c in cadena_en(d, s)]
            d += timedelta(days=1)
        return out


def cadena_en(venc_fecha, s):
    import math

    t = (venc_fecha - HOY).days / 365
    out = []
    for k in range(int(s) - 60, int(s) + 61):
        for tipo in ("put", "call"):
            p = calculos.precio_bs(tipo, s, k, t, 0.15)
            d1 = (math.log(s / k) + 0.15 * 0.15 / 2 * t) / (0.15 * math.sqrt(t))
            nd1 = 0.5 * (1 + math.erf(d1 / math.sqrt(2)))
            out.append(replace(contrato(tipo, float(k), venc_fecha, max(p, 0.02), delta=nd1 if tipo == "call" else nd1 - 1, iv=0.15), gamma=0.005))
    return out


def correr(broker, modelo, eventos=None):
    import datos.iv as modulo_iv

    eventos = eventos if eventos is not None else []
    with tempfile.TemporaryDirectory() as tmp:
        original = modulo_iv.CARPETA_CACHE
        modulo_iv.CARPETA_CACHE = Path(tmp)
        try:
            with mock.patch("agentes.research.cargar_eventos", return_value=eventos):
                return asyncio.run(Research(broker, modelo, "SPY", hoy=HOY).correr(trades=[]))
        finally:
            modulo_iv.CARPETA_CACHE = original


class TestResearch(unittest.TestCase):
    def test_circuito_completo(self):
        modelo = ModeloFalso()
        r = correr(BrokerResearch(HOY), modelo)
        self.assertEqual([p.agente for p in modelo.pedidos][:3], ["Chart", "News", "Fundamentals"])
        self.assertEqual([p.agente for p in modelo.pedidos][3:], ["Strategy", "Risk"])
        fundamentals = modelo.pedidos[2]
        self.assertTrue(fundamentals.busqueda_web)
        self.assertIn("Skill fundamentals-opciones-primas", fundamentals.sistema)
        self.assertIn("informe_fundamentals", json.loads(modelo.pedidos[3].mensaje))
        self.assertEqual(r.fundamentals["valuacion"], "justa")
        self.assertTrue(modelo.pedidos[1].busqueda_web)
        self.assertIn("calendario-catalizadores", modelo.pedidos[1].sistema.lower().replace("skill ", ""))
        self.assertFalse(r.no_hay_trade, r.motivo_no_trade)
        self.assertEqual(len(r.candidatos), 1)
        self.assertTrue(r.candidatos[0]["preferido"])
        self.assertAlmostEqual(r.costo_usd, 0.05)
        self.assertIn("CANDIDATOS PARA EL CONSEJO", formatear_research(r))
        json.dumps(r.a_dict(), default=str)

    def test_freno_no_llama_al_modelo(self):
        modelo = ModeloFalso()
        r = correr(BrokerResearch(HOY, historial=False), modelo)
        self.assertTrue(r.no_hay_trade)
        self.assertEqual(modelo.pedidos, [])
        self.assertIn("NO HAY TRADE HOY", formatear_research(r))

    def test_strategy_no_puede_inventar(self):
        modelo = ModeloFalso(strategy=lambda cs: ["iron_condor-inventado"])
        r = correr(BrokerResearch(HOY), modelo)
        self.assertTrue(r.no_hay_trade)
        self.assertTrue(any("inventado" in a for a in r.avisos))

    def test_risk_veta_y_no_levanta(self):
        modelo = ModeloFalso(
            strategy=lambda cs: [c["id"] for c in cs[:2]],
            risk={"vetos": [{"id": "x", "motivo": "m"}], "comentarios": []},
        )
        r = correr(BrokerResearch(HOY), modelo)
        self.assertEqual(len(r.candidatos), 2)
        primero = r.candidatos[0]["candidato"]["id"]
        modelo = ModeloFalso(strategy=lambda cs: [primero], risk={"vetos": [{"id": primero, "motivo": "gap"}], "comentarios": []})
        r = correr(BrokerResearch(HOY), modelo)
        self.assertTrue(r.no_hay_trade)
        self.assertIn("Risk: gap", r.descartados[primero])

    @mock.patch.object(config, "MODO_EVENTOS", "vetar")
    @mock.patch.object(config, "IGNORAR_EVENTOS_HASTA", date(2000, 1, 1))
    def test_eventos_descartan_vencimientos(self):
        # Fed dentro de todos los vencimientos: no llega nada a Strategy.
        fed = Evento(HOY + timedelta(days=2), "14:00", "fed", "FOMC", "Fed")
        modelo = ModeloFalso()
        r = correr(BrokerResearch(HOY), modelo, [fed])
        self.assertTrue(r.no_hay_trade)
        self.assertEqual([p.agente for p in modelo.pedidos], ["Chart", "News", "Fundamentals"])
        # Un evento alto que encontró News descarta solo los vencimientos que lo incluyen.
        ev = [{"fecha": (HOY + timedelta(days=12)).isoformat(), "evento": "Earnings", "impacto": "alto"}]
        modelo = ModeloFalso(strategy=lambda cs: [c["id"] for c in cs][:3], news_eventos=ev)
        r = correr(BrokerResearch(HOY), modelo)
        for x in r.candidatos:
            self.assertLess(date.fromisoformat(x["candidato"]["vencimiento"]), HOY + timedelta(days=12))

    def test_sin_modelo(self):
        r = correr(BrokerResearch(HOY), None)
        self.assertFalse(r.con_modelo)
        self.assertTrue(r.chart["sin_modelo"])
        self.assertIn("SIN MODELO", formatear_research(r))

    def test_niveles(self):
        barras = barras_sinteticas(60, osc=0.0)
        barras[30] = replace(barras[30], minimo=1.0)
        sop, res = niveles(barras, 1000.0)
        self.assertIn(1.0, sop)


class TestAlpacaFase3(unittest.TestCase):
    def test_historial_saltea_dias_sin_cuenta(self):
        ts = lambda d: int(datetime(d.year, d.month, d.day, 4, tzinfo=timezone.utc).timestamp())
        hist = {"timestamp": [ts(date(2026, 10, 13)), ts(date(2026, 10, 14))], "equity": [0.0, 100000.0]}
        b = crear({
            URL_TRADING_PAPER + "/v2/account": RespuestaHttp({**CUENTA_PAPER, "last_equity": "99000"}),
            URL_TRADING_PAPER + "/v2/account/portfolio/history": RespuestaHttp(hist),
            URL_TRADING_PAPER + "/v2/positions": RespuestaHttp([{"symbol": "SPY", "asset_class": "us_equity", "qty": "10", "market_value": "6000", "unrealized_pl": "5"}]),
            URL_DATOS + "/v1beta1/news": RespuestaHttp({"news": [{"created_at": "2026-10-15T10:00:00Z", "headline": " Hola ", "summary": "<p>uno <b>dos</b></p>", "source": "benzinga", "url": "u", "symbols": ["SPY"]}]}),
        })
        self.assertEqual(b.cuenta().patrimonio_cierre_anterior, 99000.0)
        self.assertEqual(b.historial_patrimonio(date(2026, 9, 1)), [PuntoPatrimonio(date(2026, 10, 14), 100000.0)])
        self.assertEqual(b.posiciones()[0].cantidad, 10.0)
        (n,) = b.noticias(["SPY"], date(2026, 10, 12))
        self.assertEqual((n.titular, n.resumen), ("Hola", "uno dos"))


if __name__ == "__main__":
    unittest.main()
