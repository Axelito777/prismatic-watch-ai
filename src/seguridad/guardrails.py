"""Guardrails de entrada: validación, minimización de datos y detección de prompt injection.

Es una defensa heurística de primera línea (barata: no consume llamadas al modelo). No reemplaza
las reglas del prompt ni la revisión humana de tickets; sus límites se documentan en
docs/seguridad_privacidad.md.
"""
import re
import unicodedata
from dataclasses import dataclass
from typing import Optional

from src.seguridad.privacidad import enmascarar_texto

MAX_CARACTERES = 800

MENSAJE_BLOQUEO = (
    "No puedo procesar ese mensaje. Puedo ayudarte con cambios, devoluciones, garantía, "
    "despachos y cuidado de tu reloj. Si necesitas ayuda de una persona, escribe a "
    "soporte@prismaticwatch.cl."
)

# Patrones conservadores (sobre texto sin tildes y en minúsculas) para evitar falsos positivos:
# "¿cuáles son las instrucciones de cuidado del reloj?" NO debe bloquearse.
_INJECTION = [
    re.compile(r"ignor\w*\s+(todas?\s+)?(las\s+|tus\s+)?(instrucciones|reglas|indicaciones)"),
    re.compile(r"ignore\s+(all\s+|the\s+|your\s+)?(previous|prior|above)\s+(instructions|rules)"),
    re.compile(r"olvid\w*\s+(todo|lo)\s+(lo\s+)?anterior"),
    re.compile(r"(prompt|mensaje)\s+(del\s+)?sistema"),
    re.compile(r"system\s+prompt"),
    re.compile(r"tus\s+instrucciones\s+(internas|originales|iniciales|de\s+sistema)"),
    re.compile(r"(modo|mode)\s+(desarrollador|developer|dan)\b"),
    re.compile(r"you\s+are\s+now\b"),
    re.compile(r"a\s+partir\s+de\s+ahora\s+(eres|actuaras|debes\s+ignorar)"),
]


@dataclass
class ResultadoEntrada:
    texto: str                      # texto ya limpio, truncado y minimizado (lo que ve el modelo)
    bloqueado: bool = False
    razon: Optional[str] = None
    datos_enmascarados: bool = False
    truncado: bool = False


def _normalizar(texto: str) -> str:
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c)
    )
    return sin_tildes.lower()


def revisar_entrada(texto: str) -> ResultadoEntrada:
    limpio = "".join(ch for ch in (texto or "") if ch.isprintable() or ch in "\n\t").strip()
    truncado = len(limpio) > MAX_CARACTERES
    limpio = limpio[:MAX_CARACTERES]
    norm = _normalizar(limpio)
    for patron in _INJECTION:
        if patron.search(norm):
            return ResultadoEntrada(limpio, bloqueado=True, razon="posible_prompt_injection",
                                    truncado=truncado)
    minimizado = enmascarar_texto(limpio)
    return ResultadoEntrada(minimizado, datos_enmascarados=minimizado != limpio, truncado=truncado)
