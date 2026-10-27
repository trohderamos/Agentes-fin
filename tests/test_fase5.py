"""Pruebas de la fase 5 (órdenes en paper y gestión) sin red."""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from agentes import bitacora
from agentes.bitacora import TradeRegistrado, leer_trades
from agentes.ejecucion import _id_cliente, abrir, decision_pendiente, escalera_entrada, gestionar, motivo_de_cierre, trade_aprobado
from broker import OrdenesBloqueadas
from broker.alpaca_paper import URL_TRADING_PAPER, AlpacaPaper
from broker.base import ContratoOpcion, EstadoOrden, PataOrden, PuntoPatrimonio, Reloj
from tests.test_fase1 import CUENTA_PAPER, Respuesta, SesionFalsa
from tests.test_fase3 import BrokerResearch, cuenta

HOY = date(2026, 10, 20)
VENTA, COMPRA = "SPY261026P00750000", "SPY261026P00745000"


def contrato(simbolo, bid, ask, delta):
    return ContratoOpcion(simbolo, "SPY", date(2026, 10, 26), "put", float(simbolo[-8:]) / 1000, bid, ask, None, 0.15, delta, 0.01, -0.2, 0.3)


def decision(**extra):
    d = {
        "fecha": HOY.isoformat(),
        "decision": "APROBAR",
        "subyacente": "SPY",
        "vencimiento": "2026-10-26",
        "patas": [{"tipo": "put", "lado": "compra", "strike": 745.0}, {"tipo": "put", "lado": "venta", "strike": 750.0}],
        "perdida_maxima": 455.0,
        "orden_final": {"id": "SPY-spread_put-2026-10-26-P750", "contratos": 2, "precio_limite": 0.45},
        "plan_salida": {"tomar_ganancia": "x", "stop": "x", "cierre_por_tiempo": "x", "si_toca_el_strike": "x"},
    }
    d.update(extra)
    return d


class BrokerOrdenes(BrokerResearch):
    """Mercado fijo. Se llena si el crédito pedido es <= `llena_credito` (o el débito >= `llena_debito`)."""

    def __init__(self, cot, abierto=True, minutos=120, llena_credito=0.47, llena_debito=0.30, precio=770.0):
        super().__init__(HOY)
        self.cot, self._abierto, self._minutos = cot, abierto, minutos
        self.llena_credito, self.llena_debito, self._precio = llena_credito, llena_debito, precio
        self.ordenes, self.canceladas = [], []

    def cuenta(self):
        return cuenta()

    def precio_subyacente(self, simbolo):
        return self._precio

    def historial_patrimonio(self, desde):
        return [PuntoPatrimonio(HOY - timedelta(days=1), 100_000)]

    def reloj(self):
        ahora = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
        return Reloj(self._abierto, ahora, ahora + timedelta(minutes=self._minutos))

    def cotizaciones(self, simbolos):
        return {s: self.cot[s] for s in simbolos if s in self.cot}

    def enviar_orden_multipata(self, patas, contratos, limite, abrir, id_cliente):
        llena = limite <= self.llena_credito if abrir else limite >= self.llena_debito
        e = EstadoOrden(f"o{len(self.ordenes)}", "filled" if llena else "new", contratos, contratos if llena else 0,
                        limite if llena else None, limite)
        self.ordenes.append((patas, contratos, limite, abrir, e))
        return e

    def estado_orden(self, id_orden):
        return next(o[4] for o in self.ordenes if o[4].id == id_orden)

    def cancelar_orden(self, id_orden):
        self.canceladas.append(id_orden)


COT = {VENTA: contrato(VENTA, 0.95, 1.01, -0.15), COMPRA: contrato(COMPRA, 0.47, 0.51, -0.10)}  # crédito medio 0.49


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.trades, self.ejec = d / "trades.jsonl", d / "ejecuciones.jsonl"
        p1 = mock.patch("agentes.ejecucion.registrar_trade", lambda r: bitacora.registrar_trade(r, self.trades))
        p2 = mock.patch("agentes.ejecucion.registrar_ejecucion", lambda r: bitacora.registrar_ejecucion(r, self.ejec))
        p1.start(), p2.start()
        self.addCleanup(p1.stop), self.addCleanup(p2.stop), self.addCleanup(self.tmp.cleanup)


class TestApertura(Base):
    def test_simulacion_no_envia(self):
        b = BrokerOrdenes(COT)
        r = abrir(b, decision(), HOY, enviar=False, trades=[], esperar=lambda s: None)
        self.assertTrue(all(ok for _, ok, _ in r.checklist), r.checklist)
        self.assertEqual(b.ordenes, [])
        self.assertIn("Simulación", r.motivo)
        self.assertFalse(self.ejec.exists())

    def test_envia_baja_y_registra(self):
        b = BrokerOrdenes(COT, llena_credito=0.46)
        r = abrir(b, decision(), HOY, enviar=True, trades=[], esperar=lambda s: None)
        self.assertTrue(r.ejecutado, r.motivo)
        self.assertEqual([o[2] for o in b.ordenes], [0.49, 0.48, 0.47, 0.46])
        self.assertEqual(len(b.canceladas), 3)
        for patas, contratos, _, abre, _ in b.ordenes:
            self.assertEqual(contratos, 2)
            self.assertTrue(abre)
            self.assertEqual({(p.simbolo, p.lado) for p in patas}, {(VENTA, "venta"), (COMPRA, "compra")})
        (t,) = leer_trades(self.trades)
        self.assertEqual((t.estado, t.credito, t.contratos), ("abierto", 0.46, 2))
        self.assertAlmostEqual(t.perdida_maxima, (5 - 0.46) * 100 * 2)
        self.assertEqual(dict(t.patas)[VENTA], "venta")

    def test_no_baja_del_minimo(self):
        b = BrokerOrdenes(COT, llena_credito=0.40)
        r = abrir(b, decision(), HOY, enviar=True, trades=[], esperar=lambda s: None)
        self.assertFalse(r.ejecutado)
        # Piso = 85% del crédito medio (0.49), no el precio_limite del debate (0.45).
        self.assertEqual([o[2] for o in b.ordenes], [0.49, 0.48, 0.47, 0.46, 0.45, 0.44, 0.43, 0.42])
        self.assertEqual(leer_trades(self.trades), [])
        self.assertTrue(self.ejec.exists())

    def test_piso_relativo_al_medio_no_al_debate(self):
        # Lección aprobada el 27 oct 2026: el jefe había escrito 0.60 y el medio es 0.49.
        d = decision(orden_final={"id": "SPY-spread_put-2026-10-26-P750", "contratos": 2, "precio_limite": 0.60})
        b = BrokerOrdenes(COT, llena_credito=0.44)
        r = abrir(b, d, HOY, enviar=True, trades=[], esperar=lambda s: None)
        self.assertTrue(r.ejecutado, r.motivo)
        self.assertEqual(b.ordenes[-1][2], 0.44)

    def test_escalera_entrada(self):
        self.assertEqual(escalera_entrada(1.23)[0], 1.23)
        self.assertEqual(escalera_entrada(1.23)[-1], 1.05)
        largo = escalera_entrada(3.0)  # no alcanza de a 0.01: el paso crece para llegar al piso
        self.assertLessEqual(len(largo), 30)
        self.assertEqual(largo[-1], 2.55)

    def test_id_con_ancho_extra(self):
        d = decision(orden_final={"id": "SPY-spread_put-2026-10-26-P750-A10", "contratos": 2, "precio_limite": 0.45})
        self.assertIsNotNone(trade_aprobado(d)[0])

    def test_checklist_frena(self):
        casos = [
            (BrokerOrdenes(COT, abierto=False), decision()),
            (BrokerOrdenes(COT, minutos=10), decision()),
            (BrokerOrdenes(COT), decision(perdida_maxima=1500.0)),  # 2 contratos = 3% del capital
            (BrokerOrdenes(COT, minutos=25), decision()),  # no entran los 15 minutos de negociación
            (BrokerOrdenes({VENTA: COT[VENTA]}), decision()),  # falta una cotización
            (BrokerOrdenes(COT), decision(patas=[{"tipo": "put", "lado": "venta", "strike": 750.0}])),  # sin protección
        ]
        for b, d in casos:
            r = abrir(b, d, HOY, enviar=True, trades=[], esperar=lambda s: None)
            self.assertFalse(r.enviado, r.checklist)
            self.assertEqual(b.ordenes, [])

    def test_orden_con_el_candidato_que_aprobo_el_jefe(self):
        # 21 oct 2026: el consejo debatió un condor al 4-nov y el jefe aprobó otro al 30-oct.
        # La orden tiene que llevar las patas del aprobado, no las del debatido.
        v, c = "SPY261026P00750000", "SPY261026P00745000"
        debatido = {"vencimiento": "2026-11-04", "patas": [{"tipo": "put", "lado": "venta", "strike": 740.0},
                                                         {"tipo": "put", "lado": "compra", "strike": 735.0}]}
        d = decision(
            trade_debatido="SPY-spread_put-2026-11-04-P740",
            **debatido,
            perdida_maxima=470.0,
            orden_final={
                "id": "SPY-spread_put-2026-10-26-P750", "contratos": 2, "precio_limite": 0.45,
                "vencimiento": "2026-10-26", "perdida_maxima": 455.0,
                "patas": [{"tipo": "put", "lado": "compra", "strike": 745.0}, {"tipo": "put", "lado": "venta", "strike": 750.0}],
            },
        )
        b = BrokerOrdenes(COT, llena_credito=0.49)
        r = abrir(b, d, HOY, enviar=True, trades=[], esperar=lambda s: None)
        self.assertTrue(r.ejecutado, r.motivo)
        for patas, *_ in b.ordenes:
            self.assertEqual({(p.simbolo, p.lado) for p in patas}, {(v, "venta"), (c, "compra")})
        (t,) = leer_trades(self.trades)
        self.assertEqual((t.id, t.vencimiento), ("SPY-spread_put-2026-10-26-P750", date(2026, 10, 26)))

    def test_no_envia_si_el_aprobado_no_se_identifica(self):
        casos = [
            # El jefe aprobó otro candidato y la orden no trae sus patas: solo están las del debatido.
            decision(trade_debatido="SPY-spread_put-2026-11-04-P740", vencimiento="2026-11-04",
                     patas=[{"tipo": "put", "lado": "venta", "strike": 740.0}, {"tipo": "put", "lado": "compra", "strike": 735.0}]),
            # Las patas de la orden no coinciden con el id aprobado.
            decision(orden_final={"id": "SPY-spread_put-2026-10-26-P750", "contratos": 2, "precio_limite": 0.45,
                                  "vencimiento": "2026-11-04", "patas": [{"tipo": "put", "lado": "venta", "strike": 750.0},
                                                                         {"tipo": "put", "lado": "compra", "strike": 745.0}]}),
            decision(patas=[{"tipo": "put", "lado": "venta", "strike": 755.0}, {"tipo": "put", "lado": "compra", "strike": 750.0}]),
        ]
        for d in casos:
            b = BrokerOrdenes(COT)
            r = abrir(b, d, HOY, enviar=True, trades=[], esperar=lambda s: None)
            self.assertFalse(r.enviado, r.motivo)
            self.assertEqual(b.ordenes, [])
            self.assertIn("Patas del candidato aprobado por el jefe", [n for n, ok, _ in r.checklist if not ok])
        self.assertTrue(self.ejec.exists())  # queda registrado

    def test_id_cliente_unico_en_reintentos(self):
        tid = "SPY-iron_condor-2026-10-30-P689-C748"
        ids = {_id_cliente("ab", tid, n) for n in range(3)} | {_id_cliente("ab", tid, 0) for _ in range(3)}
        self.assertEqual(len(ids), 6)
        self.assertTrue(all(len(i) <= 48 for i in ids))

    def test_decision_pendiente(self):
        d = decision()
        self.assertIs(decision_pendiente(HOY, [d], [], []), d)
        self.assertIsNone(decision_pendiente(HOY + timedelta(days=1), [d], [], []))
        hecho = TradeRegistrado(d["orden_final"]["id"], "abierto", "SPY", (), 1)
        self.assertIsNone(decision_pendiente(HOY, [d], [hecho], []))
        intento = {"accion": "apertura", "id": d["orden_final"]["id"], "enviado": True}
        self.assertIsNone(decision_pendiente(HOY, [d], [], [intento]))


def abierto(credito=0.46, vencimiento=date(2026, 10, 26)):
    return TradeRegistrado(
        "spread_put-2026-10-26-P750", "abierto", "SPY", (VENTA, COMPRA), 908, credito=credito, contratos=2,
        patas=((VENTA, "venta"), (COMPRA, "compra")), vencimiento=vencimiento, abierto_el=HOY - timedelta(days=1),
    )


class TestGestion(Base):
    def test_reglas_de_salida(self):
        t = abierto(0.50)
        self.assertEqual(motivo_de_cierre(t, HOY, 0.24, 770, [("put", 750)]), "ganancia")
        self.assertEqual(motivo_de_cierre(t, HOY, 1.55, 770, [("put", 750)]), "stop")
        self.assertEqual(motivo_de_cierre(t, date(2026, 10, 23), 0.40, 770, [("put", 750)]), "vencimiento cercano")
        self.assertEqual(motivo_de_cierre(t, HOY, 0.40, 749, [("put", 750)]), "tocó el strike")
        self.assertIsNone(motivo_de_cierre(t, HOY, 0.40, 770, [("put", 750)]))

    def test_mantiene(self):
        b = BrokerOrdenes(COT)  # débito medio 0.49 contra crédito 0.46: nada dispara
        (r,) = gestionar(b, HOY, enviar=True, trades=[abierto()], esperar=lambda s: None)
        self.assertIn("Se mantiene", r.motivo)
        self.assertEqual(b.ordenes, [])

    def test_cierra_por_ganancia_y_registra(self):
        cot = {VENTA: contrato(VENTA, 0.30, 0.34, -0.08), COMPRA: contrato(COMPRA, 0.10, 0.12, -0.05)}  # débito medio 0.21
        b = BrokerOrdenes(cot, llena_debito=0.22)
        (r,) = gestionar(b, HOY, enviar=True, trades=[abierto(0.46)], esperar=lambda s: None)
        self.assertTrue(r.ejecutado, r.motivo)
        self.assertFalse(b.ordenes[0][3])  # orden de cierre
        self.assertEqual([o[2] for o in b.ordenes], [0.21, 0.22])
        (t,) = leer_trades(self.trades)
        self.assertEqual(t.estado, "cerrado")
        self.assertAlmostEqual(t.resultado, (0.46 - 0.22) * 200 - 4)

    def test_stop_sube_hasta_el_natural(self):
        cot = {VENTA: contrato(VENTA, 2.40, 2.60, -0.40), COMPRA: contrato(COMPRA, 1.00, 1.10, -0.25)}  # medio 1.45, natural 1.60
        b = BrokerOrdenes(cot, llena_debito=9.0)
        (r,) = gestionar(b, HOY, enviar=True, trades=[abierto(0.46)], esperar=lambda s: None)
        self.assertFalse(r.ejecutado)
        precios = [o[2] for o in b.ordenes]
        self.assertGreater(len(precios), 3)  # sin el tope de 3 intentos
        self.assertLessEqual(max(precios), 1.60)

    def test_simulacion_no_cierra(self):
        cot = {VENTA: contrato(VENTA, 0.30, 0.34, -0.08), COMPRA: contrato(COMPRA, 0.10, 0.12, -0.05)}
        b = BrokerOrdenes(cot)
        (r,) = gestionar(b, HOY, enviar=False, trades=[abierto()], esperar=lambda s: None)
        self.assertIn("ganancia", r.motivo)
        self.assertEqual(b.ordenes, [])


class SesionConEscritura(SesionFalsa):
    def __init__(self, rutas):
        super().__init__(rutas)
        self.escrituras = []

    def request(self, metodo, url, json=None, timeout=None):
        self.escrituras.append((metodo, url, json))
        return Respuesta({"id": "abc", "status": "accepted", "qty": json["qty"] if json else "0", "filled_qty": "0", "limit_price": json["limit_price"] if json else None})


class TestAdaptador(unittest.TestCase):
    def crear(self, permitir):
        sesion = SesionConEscritura({URL_TRADING_PAPER + "/v2/account": Respuesta(CUENTA_PAPER)})
        with mock.patch.dict(os.environ, {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}, clear=False):
            os.environ.pop("ALPACA_BASE_URL", None)
            return AlpacaPaper(sesion=sesion, permitir_ordenes=permitir), sesion

    def test_orden_multipata(self):
        b, sesion = self.crear(True)
        patas = [PataOrden(VENTA, "venta"), PataOrden(COMPRA, "compra")]
        e = b.enviar_orden_multipata(patas, 2, 0.46, True, "id1")
        self.assertEqual(e.estado, "accepted")
        metodo, url, cuerpo = sesion.escrituras[0]
        self.assertEqual((metodo, url), ("POST", URL_TRADING_PAPER + "/v2/orders"))
        self.assertEqual((cuerpo["order_class"], cuerpo["type"], cuerpo["time_in_force"], cuerpo["qty"]), ("mleg", "limit", "day", "2"))
        self.assertEqual(cuerpo["limit_price"], "-0.46")  # crédito
        lados = {l["symbol"]: (l["side"], l["position_intent"]) for l in cuerpo["legs"]}
        self.assertEqual(lados[VENTA], ("sell", "sell_to_open"))
        self.assertEqual(lados[COMPRA], ("buy", "buy_to_open"))
        # Antes de escribir se verificó otra vez la cuenta.
        self.assertTrue(any(u.endswith("/v2/account") for u, _ in sesion.pedidos))

        b.enviar_orden_multipata(patas, 2, 0.22, False, "id2")
        cuerpo = sesion.escrituras[1][2]
        self.assertEqual(cuerpo["limit_price"], "0.22")  # débito
        lados = {l["symbol"]: (l["side"], l["position_intent"]) for l in cuerpo["legs"]}
        self.assertEqual(lados[VENTA], ("buy", "buy_to_close"))
        self.assertEqual(lados[COMPRA], ("sell", "sell_to_close"))

    def test_bloqueado_y_rutas(self):
        b, sesion = self.crear(False)
        with self.assertRaises(OrdenesBloqueadas):
            b.enviar_orden_multipata([PataOrden(VENTA, "venta")], 1, 0.5, True, "x")
        self.assertEqual(sesion.escrituras, [])
        b, sesion = self.crear(True)
        with self.assertRaises(OrdenesBloqueadas):
            b._escribir("POST", "/v2/account/configurations", {})
        with self.assertRaises(Exception):
            b.enviar_orden_multipata([PataOrden("MAL", "venta")], 1, 0.5, True, "x")
        self.assertEqual(sesion.escrituras, [])


if __name__ == "__main__":
    unittest.main()
