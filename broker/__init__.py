"""Conexión con el bróker, aislada en este paquete.

Todo el resto del código pide el bróker con `crear_broker()` y usa la interfaz
de `broker.base`. Para migrar a tastytrade se agrega un adaptador nuevo acá y
se cambia esta función.
"""

from broker.base import (
    Broker,
    ContratoOpcion,
    Cuenta,
    CuentaRealDetectada,
    ErrorBroker,
    FaltanClaves,
)


def crear_broker(nombre: str = "alpaca-paper", **opciones) -> Broker:
    if nombre == "alpaca-paper":
        from broker.alpaca_paper import AlpacaPaper

        return AlpacaPaper(**opciones)
    raise ErrorBroker(f"Bróker desconocido: {nombre}")


__all__ = [
    "Broker",
    "ContratoOpcion",
    "Cuenta",
    "CuentaRealDetectada",
    "ErrorBroker",
    "FaltanClaves",
    "crear_broker",
]
