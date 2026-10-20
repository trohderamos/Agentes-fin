"""Llamado al modelo con el Claude Agent SDK.

Cada agente es un llamado con su skill en las instrucciones y una salida JSON
con esquema fijo. Los agentes no tienen herramientas de Claude Code (ni
archivos ni terminal); News puede usar además la búsqueda web.

La credencial se lee de ANTHROPIC_API_KEY o CLAUDE_CODE_OAUTH_TOKEN; sin
ninguna, se usa la sesión de Claude Code que corre el SDK. Nunca se imprime.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

from agentes import config

CARPETA_SKILLS = Path(__file__).parent.parent / ".claude" / "skills"


class ErrorModelo(Exception):
    pass


ARCHIVO_LECCIONES = Path(__file__).parent / "lecciones.md"


def lecciones_aprobadas() -> str:
    """Lecciones que el usuario aprobó en los repasos semanales. Todos los agentes las leen."""
    if not ARCHIVO_LECCIONES.exists():
        return ""
    texto = ARCHIVO_LECCIONES.read_text(encoding="utf-8").strip()
    lineas = [l for l in texto.splitlines() if l.strip().startswith("- ")]
    if not lineas:
        return ""
    return "## Lecciones aprobadas por el usuario (respetalas)\n" + "\n".join(lineas)


def cargar_skill(nombre: str) -> str:
    """Texto del skill, sin el encabezado YAML."""
    ruta = CARPETA_SKILLS / nombre / "SKILL.md"
    if not ruta.exists():
        raise ErrorModelo(f"No está el skill {nombre} en {ruta.parent}")
    texto = ruta.read_text(encoding="utf-8")
    return re.sub(r"\A---\n.*?\n---\n", "", texto, flags=re.S).strip()


@dataclass
class Pedido:
    agente: str
    sistema: str
    mensaje: str
    esquema: dict
    busqueda_web: bool = False
    max_turnos: int = 6


@dataclass
class Respuesta:
    datos: dict
    costo_usd: Optional[float] = None
    turnos: int = 0
    avisos: list[str] = field(default_factory=list)


_CONFIG_AISLADA = Path(tempfile.gettempdir()) / "agentes-fin-claude"


def entorno_aislado() -> dict[str, str]:
    """Variables para el proceso del SDK, según la credencial disponible.

    Con ANTHROPIC_API_KEY, o si no con CLAUDE_CODE_OAUTH_TOKEN (de
    `claude setup-token`), se vacían las demás variables CLAUDE_* y
    ANTHROPIC_* heredadas y se usa una carpeta de configuración propia, para
    que se use solo esa credencial. Sin ninguna de las dos no se aísla nada:
    el SDK usa la sesión de Claude Code que lo corre (la de la rutina), que va
    contra el plan del usuario. Lo eligió él el 19 oct 2026.
    """
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")):
        return {}
    entorno = {k: "" for k in os.environ if k.startswith("CLAUDE") or k.startswith("ANTHROPIC_")}
    _CONFIG_AISLADA.mkdir(parents=True, exist_ok=True)
    entorno["CLAUDE_CONFIG_DIR"] = str(_CONFIG_AISLADA)
    if os.environ.get("ANTHROPIC_API_KEY"):
        entorno["ANTHROPIC_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
        entorno["ANTHROPIC_BASE_URL"] = "https://api.anthropic.com"
    else:
        entorno["CLAUDE_CODE_OAUTH_TOKEN"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    return entorno


class Modelo(Protocol):
    async def pedir(self, pedido: Pedido) -> Respuesta: ...


class ModeloClaude:
    """Modelo real con el Claude Agent SDK."""

    def __init__(self, modelo: str = config.MODELO, presupuesto_usd: float = config.PRESUPUESTO_AGENTE_USD):
        self.modelo = modelo
        self.presupuesto_usd = presupuesto_usd

    async def pedir(self, pedido: Pedido) -> Respuesta:
        from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

        herramientas = ["WebSearch"] if pedido.busqueda_web else []
        opciones = ClaudeAgentOptions(
            system_prompt=pedido.sistema,
            model=self.modelo,
            tools=herramientas,
            allowed_tools=herramientas,
            permission_mode="dontAsk",
            setting_sources=[],
            strict_mcp_config=True,
            max_turns=pedido.max_turnos,
            max_budget_usd=self.presupuesto_usd,
            output_format={"type": "json_schema", "schema": pedido.esquema},
            # Con API key u OAuth token, aislado; sin ninguno, con la sesión que lo corre.
            env=entorno_aislado(),
        )
        resultado: Optional[ResultMessage] = None
        try:
            async for mensaje in query(prompt=pedido.mensaje, options=opciones):
                if isinstance(mensaje, ResultMessage):
                    resultado = mensaje
        except Exception as e:
            raise ErrorModelo(f"{pedido.agente}: falló el llamado al modelo: {str(e)[:300]}") from None
        if resultado is None:
            raise ErrorModelo(f"{pedido.agente}: el modelo no devolvió resultado.")
        if resultado.is_error or resultado.structured_output is None:
            detalle = resultado.subtype
            if resultado.errors:
                detalle += ": " + "; ".join(resultado.errors)[:300]
            raise ErrorModelo(f"{pedido.agente}: el modelo terminó con error ({detalle}).")
        return Respuesta(
            datos=resultado.structured_output,
            costo_usd=resultado.total_cost_usd,
            turnos=resultado.num_turns,
        )


def esquema_informe(extra: dict[str, Any], requeridos_extra: list[str]) -> dict:
    """Formato común de research: tesis, datos, confianza y qué la invalidaría."""
    propiedades = {
        "tesis": {"type": "string", "description": "Una frase."},
        "datos": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Cifras con fecha y fuente. Nada inventado.",
        },
        "confianza": {"type": "string", "enum": ["baja", "media", "alta"]},
        "motivo_confianza": {"type": "string"},
        "invalidaria": {"type": "array", "items": {"type": "string"}},
        "faltantes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Datos que harían falta y no están.",
        },
    }
    propiedades.update(extra)
    return {
        "type": "object",
        "properties": propiedades,
        "required": ["tesis", "datos", "confianza", "motivo_confianza", "invalidaria", "faltantes"]
        + requeridos_extra,
        "additionalProperties": False,
    }
