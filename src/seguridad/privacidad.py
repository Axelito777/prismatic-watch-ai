"""Privacidad: minimización y enmascarado de datos personales.

Se usa en dos puntos:
1. Antes de enviar el mensaje del cliente al modelo (principio de minimización).
2. Antes de escribir cualquier traza o log (los logs no deben contener datos personales).
"""
import hashlib
import os
import re

SAL = os.getenv("LOG_SALT", "prismatic-watch")

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_RUT = re.compile(r"(?<![\w-])\d{1,2}\.?\d{3}\.?\d{3}-[\dkK](?![\w])")
_TARJETA = re.compile(r"(?<![\w-])(?:\d[ -]?){13,19}(?!\d)")
_TELEFONO = re.compile(r"(?<![\w-])(?:\+?56[\s-]?)?9[\s-]?\d{4}[\s-]?\d{4}(?!\d)")


def _luhn_valido(digitos: str) -> bool:
    total = 0
    for i, d in enumerate(reversed(digitos)):
        n = int(d)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _reemplazar_tarjeta(m: re.Match) -> str:
    bruto = m.group()
    limpio = bruto.rstrip(" -")
    digitos = re.sub(r"\D", "", limpio)
    if 13 <= len(digitos) <= 19 and _luhn_valido(digitos):
        return "[TARJETA]" + bruto[len(limpio):]
    return bruto  # no es una tarjeta válida (p. ej. un ID de ticket): se deja intacto


def enmascarar_texto(texto: str, nombres=()) -> str:
    """Reemplaza correos, RUT, tarjetas (validadas con Luhn), teléfonos y nombres conocidos."""
    if not texto:
        return texto
    texto = _EMAIL.sub("[EMAIL]", texto)
    texto = _RUT.sub("[RUT]", texto)
    texto = _TARJETA.sub(_reemplazar_tarjeta, texto)
    texto = _TELEFONO.sub("[TELEFONO]", texto)
    partes = set()
    for nombre in nombres:
        if not nombre:
            continue
        partes.add(nombre.strip())
        partes.update(p for p in nombre.split() if len(p) >= 3)
    for parte in sorted(partes, key=len, reverse=True):
        if len(parte) >= 3:
            texto = re.sub(rf"\b{re.escape(parte)}\b", "[CLIENTE]", texto, flags=re.IGNORECASE)
    return texto


def seudonimo(cliente: str) -> str:
    """Identificador estable pero no reversible del cliente para los logs."""
    h = hashlib.sha256((SAL + (cliente or "").strip().lower()).encode("utf-8")).hexdigest()
    return f"cli_{h[:8]}"
