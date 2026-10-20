"""Conexión con el bróker, aislada en este paquete.

Todo el resto del código pide el bróker con `crear_broker()` y usa la interfaz
de `broker.base`. Para migrar a tastytrade se agrega un adaptador nuevo acá y
se cambia esta función.
"""

from broker.base import (
    Barra,
    Broker,
    ContratoOpcion,
    Cuenta,
    CuentaRealDetectada,
    ErrorBroker,
    EstadoOrden,
    FaltanClaves,
    Noticia,
    OrdenesBloqueadas,
    PataOrden,
    Reloj,
    Posicion,
    PuntoPatrimonio,
)


def crear_broker(nombre: str = "alpaca-paper", **opciones) -> Broker:
    if nombre == "alpaca-paper":
        from broker.alpaca_paper import AlpacaPaper

        return AlpacaPaper(**opciones)
    raise ErrorBroker(f"Bróker desconocido: {nombre}")


__all__ = [
    "Barra",
    "Broker",
    "ContratoOpcion",
    "Cuenta",
    "CuentaRealDetectada",
    "ErrorBroker",
    "EstadoOrden",
    "FaltanClaves",
    "OrdenesBloqueadas",
    "PataOrden",
    "Reloj",
    "Noticia",
    "Posicion",
    "PuntoPatrimonio",
    "crear_broker",
]
