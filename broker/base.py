"""Interfaz común del bróker.

El resto del sistema (agentes, datos, orquestador) solo conoce esta interfaz.
Para pasar de Alpaca a tastytrade se escribe otro adaptador que la cumpla y
no se toca nada más.

Las lecturas son abstractas. Las órdenes (fase 5) son opcionales: un bróker
que no las implementa lanza ErrorBroker. El adaptador de Alpaca las tiene
bloqueadas salvo que se lo cree con permitir_ordenes=True.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


class ErrorBroker(Exception):
    """Error al hablar con el bróker."""


class FaltanClaves(ErrorBroker):
    """No están las variables de entorno con las claves."""


class OrdenesBloqueadas(ErrorBroker):
    """Se intentó enviar o cancelar una orden con el bróker en modo solo lectura."""


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
    patrimonio_cierre_anterior: Optional[float] = None


@dataclass(frozen=True)
class Barra:
    """Barra diaria (OHLC y volumen)."""

    fecha: date
    apertura: float
    maximo: float
    minimo: float
    cierre: float
    volumen: float


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


@dataclass(frozen=True)
class Posicion:
    simbolo: str
    clase: str  # "us_option", "us_equity", ...
    cantidad: float
    valor_mercado: Optional[float]
    resultado_no_realizado: Optional[float]


@dataclass(frozen=True)
class PuntoPatrimonio:
    """Patrimonio de la cuenta al cierre de un día."""

    fecha: date
    patrimonio: float


@dataclass(frozen=True)
class Noticia:
    fecha: str  # ISO con hora, UTC
    titular: str
    resumen: str
    fuente: str
    url: str
    simbolos: tuple[str, ...]


@dataclass(frozen=True)
class PataOrden:
    simbolo: str  # OCC
    lado: str  # "venta" o "compra"
    ratio: int = 1


@dataclass(frozen=True)
class EstadoOrden:
    id: str
    estado: str  # new, accepted, filled, partially_filled, canceled, expired, rejected...
    contratos: int
    contratos_llenados: int
    precio_promedio: Optional[float]  # por acción, con el signo del bróker
    limite: Optional[float]

    @property
    def llena(self) -> bool:
        return self.estado == "filled"

    @property
    def terminada(self) -> bool:
        return self.estado in ("filled", "canceled", "expired", "rejected", "done_for_day")


@dataclass(frozen=True)
class Reloj:
    abierto: bool
    ahora: datetime
    proximo_cierre: datetime


class Broker(ABC):
    """Lo que el sistema necesita de un bróker (solo lectura)."""

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

    @abstractmethod
    def barras_diarias(
        self, simbolo: str, desde: date, hasta: date, ajustadas: bool = True
    ) -> list[Barra]:
        """Barras diarias del subyacente.

        Ajustadas por splits y dividendos para indicadores; sin ajustar cuando
        hay que compararlas con strikes de opciones.
        """

    @abstractmethod
    def barras_opciones(
        self, simbolos: list[str], desde: date, hasta: date
    ) -> dict[str, list[Barra]]:
        """Barras diarias de contratos de opciones (también vencidos), por símbolo OCC."""

    @abstractmethod
    def posiciones(self) -> list[Posicion]:
        """Posiciones abiertas de la cuenta."""

    @abstractmethod
    def historial_patrimonio(self, desde: date) -> list[PuntoPatrimonio]:
        """Patrimonio al cierre de cada día desde la fecha pedida.

        Los días en que la cuenta todavía no existía no se devuelven.
        """

    def noticias(self, simbolos: list[str], desde: date, limite: int = 50) -> list[Noticia]:
        """Noticias recientes de los símbolos, de la más nueva a la más vieja.

        Opcional: un bróker sin noticias devuelve una lista vacía y el agente
        News trabaja con la búsqueda web.
        """
        return []

    # --- Órdenes (fase 5) ---------------------------------------------------

    def reloj(self) -> Reloj:
        """Si el mercado está abierto y cuándo cierra."""
        raise ErrorBroker(f"{self.nombre} no informa el horario del mercado.")

    def cotizaciones(self, simbolos: list[str]) -> dict[str, ContratoOpcion]:
        """Cotización y griegas actuales de contratos puntuales, por símbolo OCC."""
        raise ErrorBroker(f"{self.nombre} no da cotizaciones por símbolo.")

    def enviar_orden_multipata(
        self, patas: list[PataOrden], contratos: int, limite: float, abrir: bool, id_cliente: str
    ) -> EstadoOrden:
        """Una sola orden con todas las patas, límite y vigencia del día.

        `limite` es positivo: al abrir (abrir=True) es el crédito mínimo a cobrar
        por acción; al cerrar, el débito máximo a pagar.
        """
        raise ErrorBroker(f"{self.nombre} no envía órdenes.")

    def estado_orden(self, id_orden: str) -> EstadoOrden:
        raise ErrorBroker(f"{self.nombre} no envía órdenes.")

    def cancelar_orden(self, id_orden: str) -> None:
        raise ErrorBroker(f"{self.nombre} no envía órdenes.")
