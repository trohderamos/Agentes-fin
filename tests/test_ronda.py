"""Ronda: ciclo y ejecución para cada subyacente del universo."""

import unittest
from unittest import mock

import orquestador
from agentes import config
from agentes.modelo import ErrorModelo
from broker import ErrorBroker


class TestRonda(unittest.TestCase):
    def test_parser_por_defecto(self):
        args = orquestador.armar_parser().parse_args(["ronda"])
        self.assertEqual(args.simbolos, list(config.UNIVERSO))
        self.assertFalse(args.enviar)
        self.assertIn("GLD", config.UNIVERSO)
        self.assertIn("USO", config.UNIVERSO)
        self.assertIn("IBIT", config.UNIVERSO)

    def test_aviso_de_fin_de_semana_para_cripto(self):
        from datetime import date
        from agentes.research import _sistema

        self.assertIn("24/7", _sistema("Risk", date(2026, 10, 20), [], "t", "IBIT"))
        self.assertNotIn("24/7", _sistema("Risk", date(2026, 10, 20), [], "t", "SPY"))

    def test_ejecuta_despues_de_cada_ciclo_y_sigue_si_uno_falla(self):
        llamadas = []

        def ciclo(broker, args):
            llamadas.append(("ciclo", args.simbolo))
            if args.simbolo == "GLD":
                raise ErrorModelo("falló")

        def ejecutar(broker, args):
            llamadas.append(("ejecutar", args.simbolo))

        args = orquestador.armar_parser().parse_args(["ronda", "--simbolos", "spy", "gld", "uso"])
        with mock.patch.object(orquestador, "cmd_ciclo", ciclo), mock.patch.object(orquestador, "cmd_ejecutar", ejecutar):
            with mock.patch("builtins.print"):
                with self.assertRaises(ErrorBroker):
                    orquestador.cmd_ronda(None, args)
        self.assertEqual(
            llamadas,
            [("ciclo", "SPY"), ("ejecutar", "SPY"), ("ciclo", "GLD"), ("ciclo", "USO"), ("ejecutar", "USO")],
        )


if __name__ == "__main__":
    unittest.main()
