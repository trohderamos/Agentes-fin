"""Interfaz común del bróker.

El resto del sistema (agentes, datos, orquestador) solo conoce esta interfaz.
Para pasar de Alpaca a tastytrade se escribe otro adaptador que la cumpla y
no se toca nada más.

En la fase 1 la interfaz es de solo lectura: no hay ningún método para enviar
órdenes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


class ErrorBroker(Exception):
    """Error al hablar con el bróker."""


class FaltanClaves(ErrorBroker):
    """No están las variables de entorno con las claves."""


class CuentaRealDetectada(ErrorBroker):
    """La conexión apunta a una cuenta real. Nunca se sigue adelante."""


@dataclass(frozen=True)
class Cuenta:
    numero: str
    estado: str
    moneda: str
    efectivo: float
    patrimonio: float
    poder_de_compra: float
    poder_de_compra_opciones: Optional[float]
    nivel_opciones: Optional[int]
    es_paper: bool


@dataclass(frozen=True)
class ContratoOpcion:
    simbolo: str
    subyacente: str
    vencimiento: date
    tipo: str  # "put" o "call"
    strike: float
    bid: Optional[float]
    ask: Optional[float]
    ultimo: Optional[float]
    volatilidad_implicita: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]

    @property
    def medio(self) -> Optional[float]:
        if self.bid is None or self.ask is None or self.ask <= 0:
            return None
        return round((self.bid + self.ask) / 2, 4)


class Broker(ABC):
    """Lo que el sistema necesita de un bróker en la fase 1 (solo lectura)."""

    nombre: str

    @abstractmethod
    def cuenta(self) -> Cuenta:
        """Datos de la cuenta. Debe frenar si la cuenta no es paper."""

    @abstractmethod
    def precio_subyacente(self, simbolo: str) -> Optional[float]:
        """Último precio del subyacente."""

    @abstractmethod
    def cadena_opciones(
        self, subyacente: str, vence_desde: date, vence_hasta: date
    ) -> list[ContratoOpcion]:
        """Contratos con vencimiento entre las dos fechas, inclusive."""
