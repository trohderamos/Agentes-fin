"""Bitácora: trades (bitacora/trades.jsonl) y decisiones (bitacora/decisiones.jsonl).

trades.jsonl tiene una línea JSON por trade abierto o cerrado en el bróker.
El freno por drawdown y Risk la leen; se escribe cuando haya órdenes (fase 5).
decisiones.jsonl tiene una línea por cada ciclo de research, consejo y jefe,
se opere o no (fase 4).

Campos de trades.jsonl que se usan acá:
    id, estado ("abierto" o "cerrado"), subyacente, simbolos (lista OCC),
    perdida_maxima (dólares, total del trade), delta, gamma, vega (de la
    posición, total), resultado (dólares, al cerrar), cerrado_el (AAAA-MM-DD).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

ARCHIVO = Path(__file__).parent.parent / "bitacora" / "trades.jsonl"


class BitacoraInvalida(ValueError):
    pass


@dataclass(frozen=True)
class TradeRegistrado:
    id: str
    estado: str
    subyacente: str
    simbolos: tuple[str, ...]
    perdida_maxima: float
    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    resultado: Optional[float] = None
    cerrado_el: Optional[date] = None


def leer_trades(archivo: Path = ARCHIVO) -> list[TradeRegistrado]:
    """Trades de la bitácora. Sin archivo, no hay trades registrados todavía."""
    if not archivo.exists():
        return []
    trades = []
    for n, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
        if not linea.strip():
            continue
        try:
            d = json.loads(linea)
            # Una línea posterior con el mismo id reemplaza a la anterior
            # (así se registra el cierre sin borrar la apertura).
            trades = [t for t in trades if t.id != str(d["id"])]
            trades.append(
                TradeRegistrado(
                    id=str(d["id"]),
                    estado=str(d["estado"]),
                    subyacente=str(d["subyacente"]),
                    simbolos=tuple(d.get("simbolos") or ()),
                    perdida_maxima=float(d["perdida_maxima"]),
                    delta=float(d.get("delta") or 0),
                    gamma=float(d.get("gamma") or 0),
                    vega=float(d.get("vega") or 0),
                    resultado=None if d.get("resultado") is None else float(d["resultado"]),
                    cerrado_el=None if not d.get("cerrado_el") else date.fromisoformat(d["cerrado_el"]),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise BitacoraInvalida(f"Línea {n} de {archivo.name} inválida: {e}") from None
    return trades


# --- Decisiones (fase 4) ---------------------------------------------------

ARCHIVO_DECISIONES = Path(__file__).parent.parent / "bitacora" / "decisiones.jsonl"


def registrar_decision(registro: dict, archivo: Path = ARCHIVO_DECISIONES) -> None:
    """Agrega una línea por ciclo, se opere o no. Nunca reescribe las anteriores."""
    archivo.parent.mkdir(parents=True, exist_ok=True)
    with archivo.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


def leer_decisiones(archivo: Path = ARCHIVO_DECISIONES) -> list[dict]:
    if not archivo.exists():
        return []
    return [json.loads(l) for l in archivo.read_text(encoding="utf-8").splitlines() if l.strip()]
