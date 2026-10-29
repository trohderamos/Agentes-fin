"""Cómo narran los agentes: la consigna de redacción, el campo en criollo y el glosario.

No prueba decisiones ni límites de riesgo: solo que lo que lee el usuario se entienda
y que nada de esto rompa los esquemas de salida.
"""

import unittest

from agentes import consejo, jefe, noticias, research
from agentes.glosario import COMO_ESCRIBIR, EN_CRIOLLO, con_glosario, envolver, glosario_de
from agentes.modelo import esquema_informe


class TestGlosario(unittest.TestCase):
    def test_solo_explica_lo_que_aparece(self):
        lineas = glosario_de("El IV rank es 45 y el delta del strike vendido es 0.15.")
        texto = "\n".join(lineas)
        self.assertIn("IV rank", texto)
        self.assertIn("delta", texto)
        self.assertNotIn("skew", texto)

    def test_sin_terminos_no_agrega_pie(self):
        self.assertEqual(con_glosario("Hoy no hubo novedades."), "Hoy no hubo novedades.")

    def test_el_pie_va_al_final(self):
        texto = con_glosario("Cobramos una prima de 205 USD.")
        self.assertTrue(texto.startswith("Cobramos"))
        self.assertIn("QUÉ SIGNIFICA CADA COSA", texto)

    def test_patrimonio_y_efectivo_se_explican_distinto(self):
        texto = con_glosario("Patrimonio 100.004 USD y efectivo 100.463 USD.")
        self.assertIn("si cerráramos todo", texto)
        self.assertIn("todavía son deuda", texto)

    def test_envolver_corta_lineas(self):
        lineas = envolver("palabra " * 60, ancho=40)
        self.assertTrue(all(len(l) <= 40 for l in lineas))
        self.assertEqual(envolver("   "), [])


class TestConsignaDeRedaccion(unittest.TestCase):
    def test_la_leen_research_el_consejo_y_el_repaso(self):
        import datetime

        hoy = datetime.date(2026, 10, 29)
        self.assertIn(COMO_ESCRIBIR, research._sistema("News", hoy, [], "tarea"))
        self.assertIn(COMO_ESCRIBIR, consejo.sistema_comun(hoy, {"trade": "x"}))

    def test_pide_conclusion_primero_y_terminos_explicados(self):
        self.assertIn("conclusión", COMO_ESCRIBIR)
        self.assertIn("IV rank", COMO_ESCRIBIR)
        self.assertIn("NO es ganancia", COMO_ESCRIBIR)


class TestEsquemas(unittest.TestCase):
    """El campo nuevo no puede ser obligatorio: una corrida no se cae por falta de narración."""

    def test_en_criollo_es_opcional(self):
        for esquema in (
            esquema_informe({}, []),
            research.ESQUEMA_NEWS,
            noticias.ESQUEMA_GUARDIA,
            consejo.ESQUEMA_POSTURA,
            jefe.ESQUEMA_DECISION,
        ):
            self.assertIn("en_criollo", esquema["properties"])
            self.assertNotIn("en_criollo", esquema["required"])

    def test_el_esquema_de_la_guardia_sigue_entero(self):
        self.assertIn("trades_abiertos", noticias.ESQUEMA_GUARDIA["required"])
        self.assertEqual(EN_CRIOLLO["type"], "string")


class TestInformeDeNoticias(unittest.TestCase):
    BASE = {
        "subyacente": "QQQ",
        "fecha": "2026-10-29",
        "hora": "15:23 ET",
        "sentimiento": "mixto",
        "veredicto": "ALERTA",
        "motivo_veredicto": "AMZN reporta esta noche.",
        "por_que_se_mueve": "El 10 años arriba de 5% presiona al Nasdaq.",
        "hechos": ["La Fed mantuvo la tasa (28-oct)."],
        "rumores": [],
        "eventos": [],
    }

    def test_arranca_por_la_conclusion_traducida(self):
        texto = noticias.formatear_noticias(dict(self.BASE, en_criollo="No cambió nada desde la mañana."))
        cabeza = texto.splitlines()[2]
        self.assertIn("ALERTA", cabeza)
        self.assertIn("se puede operar midiendo el golpe", cabeza)
        self.assertIn("No cambió nada desde la mañana.", texto)

    def test_explica_que_quiere_decir_cada_amenaza(self):
        texto = noticias.formatear_noticias(
            dict(self.BASE, trades_abiertos=[{"id": "QQQ-1", "amenaza": "cerrar", "motivo": "El put quedó cerca."}])
        )
        self.assertIn("CERRAR (la tesis con la que lo abrimos ya no vale)", texto)

    def test_anda_sin_los_campos_nuevos(self):
        texto = noticias.formatear_noticias(dict(self.BASE))
        self.assertIn("NOTICIAS QQQ", texto)


if __name__ == "__main__":
    unittest.main()
