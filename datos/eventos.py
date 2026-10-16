"""Calendario de eventos macro que pueden mover a todo el mercado.

Las fechas están en datos/eventos.csv y se cargan a mano desde los calendarios
oficiales (Fed y BLS). Hay que agregar las nuevas a medida que se publican:
si el archivo no cubre el período pedido, el informe lo avisa.

Los earnings de empresas individuales no están acá: SPY no los tiene y el
agente News los busca cuando se sumen acciones.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

ARCHIVO = Path(__file__).parent / "eventos.csv"


@dataclass(frozen=True)
class Evento:
    fecha: date
    hora_et: str
    tipo: str
    evento: str
    fuente: str


def cargar_eventos(archivo: Path = ARCHIVO) -> list[Evento]:
    with archivo.open(encoding="utf-8") as f:
        return sorted(
            (
                Evento(
                    fecha=date.fromisoformat(fila["fecha"]),
                    hora_et=fila["hora_et"],
                    tipo=fila["tipo"],
                    evento=fila["evento"],
                    fuente=fila["fuente"],
                )
                for fila in csv.DictReader(f)
            ),
            key=lambda e: e.fecha,
        )


def eventos_entre(desde: date, hasta: date, eventos: Optional[list[Evento]] = None) -> list[Evento]:
    eventos = cargar_eventos() if eventos is None else eventos
    return [e for e in eventos if desde <= e.fecha <= hasta]


TIPOS_RECURRENTES = ("fed", "empleo", "inflacion")


def cubre_hasta(eventos: Optional[list[Evento]] = None) -> Optional[date]:
    """Hasta qué fecha están cargados todos los tipos recurrentes.

    Es la menor de las últimas fechas de cada tipo: más allá de ella puede
    faltar algún evento.
    """
    eventos = cargar_eventos() if eventos is None else eventos
    ultimas = [max((e.fecha for e in eventos if e.tipo == t), default=None) for t in TIPOS_RECURRENTES]
    if any(u is None for u in ultimas):
        return None
    return min(ultimas)
