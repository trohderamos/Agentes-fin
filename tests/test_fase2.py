"""Pruebas de la fase 2 (datos y cálculos) sin red."""

import math
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from broker.base import Barra, Broker, ContratoOpcion, Cuenta
from datos import calculos
from datos.eventos import cargar_eventos, cubre_hasta, eventos_entre
from datos.informe import formatear_informe, generar_informe
from datos.iv import calcular_puntos, iv_actual, simbolo_occ, vencimiento_objetivo


def barra(d, c, rango=2.0, v=100):
    return Barra(d, c, c + rango / 2, c - rango / 2, c, v)


class TestCalculos(unittest.TestCase):
    def test_bs_e_iv_ida_y_vuelta(self):
        for tipo in ("call", "put"):
            precio = calculos.precio_bs(tipo, 600, 590, 30 / 365, 0.18)
            iv = calculos.iv_implicita(tipo, precio, 600, 590, 30 / 365)
            self.assertAlmostEqual(iv, 0.18, places=4)

    def test_iv_sin_solucion(self):
        # Un call que vale menos que su valor intrínseco no tiene IV.
        self.assertIsNone(calculos.iv_implicita("call", 1.0, 600, 500, 30 / 365))

    def test_atr_rango_constante(self):
        d0 = date(2026, 1, 1)
        barras = [barra(d0 + timedelta(days=i), 100, rango=2.0) for i in range(30)]
        self.assertAlmostEqual(calculos.atr(barras, 14), 2.0)
        self.assertIsNone(calculos.atr(barras[:10], 14))

    def test_volatilidad_realizada(self):
        # Retornos que alternan +1% y -1%: desvío ~1% diario.
        cierres = [100.0]
        for i in range(20):
            cierres.append(cierres[-1] * (1.01 if i % 2 == 0 else 0.99))
        vol = calculos.volatilidad_realizada(cierres, 20)
        self.assertAlmostEqual(vol, 0.01 * math.sqrt(252), delta=0.01)

    def test_iv_rank_y_percentil(self):
        hist = [0.10, 0.15, 0.20, 0.30]
        self.assertAlmostEqual(calculos.iv_rank(0.20, hist), 50.0)
        self.assertAlmostEqual(calculos.iv_percentil(0.20, hist), 50.0)
        self.assertIsNone(calculos.iv_rank(0.2, []))

    def test_movimiento_esperado(self):
        self.assertAlmostEqual(calculos.movimiento_esperado(600, 0.2, 365), 120)


class TestIV(unittest.TestCase):
    def test_simbolo_occ(self):
        self.assertEqual(simbolo_occ("SPY", date(2026, 11, 13), "put", 772), "SPY261113P00772000")

    def test_vencimiento_objetivo_y_feriado(self):
        habiles = {date(2026, 3, 5) + timedelta(days=i) for i in range(60)}
        # 2026-03-05 + 30 = 2026-04-04 (sábado): el viernes más cercano es el 3 de abril.
        self.assertEqual(vencimiento_objetivo(date(2026, 3, 5), habiles, date(2026, 6, 1)), date(2026, 4, 3))
        # Si ese viernes no fue hábil (Viernes Santo), vence el jueves.
        habiles.discard(date(2026, 4, 3))
        self.assertEqual(vencimiento_objetivo(date(2026, 3, 5), habiles, date(2026, 6, 1)), date(2026, 4, 2))

    def test_calcular_puntos(self):
        d = date(2026, 9, 1)
        barras = [barra(d, 600.0)]
        venc = vencimiento_objetivo(d, {d}, d)
        t = (venc - d).days / 365
        op = {
            simbolo_occ("SPY", venc, tipo, 600): [
                barra(d, calculos.precio_bs(tipo, 600, 600, t, 0.15), rango=0)
            ]
            for tipo in ("call", "put")
        }
        puntos = calcular_puntos("SPY", barras, op, [d])
        self.assertAlmostEqual(puntos[d].iv, 0.15, places=3)

    def test_iv_actual(self):
        hoy = date(2026, 10, 15)
        venc = date(2026, 11, 13)
        t = (venc - hoy).days / 365
        contratos = []
        for tipo in ("call", "put"):
            for k in (770.0, 772.0, 774.0):
                p = calculos.precio_bs(tipo, 772, k, t, 0.13)
                contratos.append(contrato(tipo, k, venc, p))
        iv, v, k = iv_actual(772, contratos, hoy)
        self.assertEqual((v, k), (venc, 772.0))
        self.assertAlmostEqual(iv, 0.13, places=2)


class TestEventos(unittest.TestCase):
    def test_archivo_y_filtro(self):
        eventos = cargar_eventos()
        self.assertTrue(eventos)
        fomc = eventos_entre(date(2026, 10, 15), date(2026, 11, 5), eventos)
        self.assertEqual([e.tipo for e in fomc], ["fed"])

    def test_cubre_hasta(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = Path(tmp) / "e.csv"
            ruta.write_text(
                "fecha,hora_et,tipo,evento,fuente\n"
                "2026-12-09,14:00,fed,FOMC,x\n2026-11-06,08:30,empleo,NFP,x\n2026-11-10,08:30,inflacion,CPI,x\n",
                encoding="utf-8",
            )
            self.assertEqual(cubre_hasta(cargar_eventos(ruta)), date(2026, 11, 6))


def contrato(tipo, k, venc, medio, delta=None, iv=None):
    return ContratoOpcion(
        simbolo=simbolo_occ("SPY", venc, tipo, k),
        subyacente="SPY",
        vencimiento=venc,
        tipo=tipo,
        strike=k,
        bid=round(medio - 0.01, 4),
        ask=round(medio + 0.01, 4),
        ultimo=medio,
        volatilidad_implicita=iv,
        delta=delta,
        gamma=None,
        theta=-0.1,
        vega=0.2,
    )


class BrokerFalso(Broker):
    """Mercado sintético: SPY sube de a poco y la IV es 15% en todos lados."""

    nombre = "falso"

    def __init__(self, hoy):
        self.hoy = hoy
        self.dias = [hoy - timedelta(days=i) for i in range(400, -1, -1) if (hoy - timedelta(days=i)).weekday() < 5]
        self.cierres = {d: 500 + i * 0.5 for i, d in enumerate(self.dias)}

    def cuenta(self):
        return Cuenta("PA1", "ACTIVE", "USD", 1, 1, 1, 1, 3, True)

    def precio_subyacente(self, simbolo):
        return self.cierres[self.hoy]

    def posiciones(self):
        return []

    def historial_patrimonio(self, desde):
        return []

    def barras_diarias(self, simbolo, desde, hasta, ajustadas=True):
        return [barra(d, self.cierres[d], rango=4) for d in self.dias if desde <= d <= hasta]

    def barras_opciones(self, simbolos, desde, hasta):
        from broker.alpaca_paper import parsear_simbolo_occ

        res = {}
        for s in simbolos:
            _, venc, tipo, k = parsear_simbolo_occ(s)
            res[s] = [
                barra(d, calculos.precio_bs(tipo, self.cierres[d], k, (venc - d).days / 365, 0.15), rango=0)
                for d in self.dias
                if desde <= d <= hasta and d < venc
            ]
        return res

    def cadena_opciones(self, subyacente, vence_desde, vence_hasta):
        s = self.cierres[self.hoy]
        out = []
        d = vence_desde
        while d <= vence_hasta:
            if d.weekday() == 4:
                t = (d - self.hoy).days / 365
                for k in [round(s) + x for x in range(-30, 31, 5)]:
                    for tipo in ("call", "put"):
                        p = calculos.precio_bs(tipo, s, k, t, 0.15)
                        dist = (k - s) / s
                        delta = max(0.01, min(0.99, 0.5 - dist * 10)) if tipo == "call" else -max(0.01, min(0.99, 0.5 + dist * 10))
                        out.append(contrato(tipo, float(k), d, p, delta=delta, iv=0.15))
            d += timedelta(days=1)
        return out


class TestInforme(unittest.TestCase):
    def test_informe_completo(self):
        import datos.iv as modulo_iv

        hoy = date(2026, 10, 15)
        with tempfile.TemporaryDirectory() as tmp:
            original = modulo_iv.CARPETA_CACHE
            modulo_iv.CARPETA_CACHE = Path(tmp)
            try:
                inf = generar_informe(BrokerFalso(hoy), "SPY", hoy=hoy)
            finally:
                modulo_iv.CARPETA_CACHE = original

        self.assertAlmostEqual(inf.iv.actual, 0.15, places=2)
        self.assertGreater(inf.iv.dias_historial, 200)
        self.assertAlmostEqual(inf.precio.atr_14, 4.0, places=1)
        self.assertTrue(inf.vencimientos)
        for v in inf.vencimientos:
            self.assertTrue(7 <= v.dias <= 21)
            self.assertIsNotNone(v.put_objetivo)
            self.assertLess(v.put_objetivo.strike, inf.precio.actual)
        self.assertIn("FOMC", " ".join(e.evento for e in inf.eventos))
        texto = formatear_informe(inf)
        self.assertIn("IV rank", texto)
        self.assertIsInstance(inf.a_dict()["fecha"], str)


if __name__ == "__main__":
    unittest.main()
