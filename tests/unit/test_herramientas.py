"""Regresión de los hallazgos de la evaluación offline (requiere el stack de langchain instalado)."""
import re

import pytest

pytest.importorskip("langchain_chroma")
pytest.importorskip("langchain_huggingface")

from src.agent import tools as T  # noqa: E402


def test_fecha_futura_es_invalida(monkeypatch):
    monkeypatch.setenv("FECHA_REFERENCIA", "2026-10-20")
    r = T.evaluar_elegibilidad.invoke({"fecha_compra": "2026-12-01", "tipo_caso": "cambio"})
    assert r.startswith("FECHA_INVALIDA")


def test_limites_del_plazo(monkeypatch):
    monkeypatch.setenv("FECHA_REFERENCIA", "2026-10-20")
    assert T.evaluar_elegibilidad.invoke({"fecha_compra": "2026-10-10", "tipo_caso": "cambio"}).startswith("ELEGIBLE")
    assert T.evaluar_elegibilidad.invoke({"fecha_compra": "2026-10-09", "tipo_caso": "cambio"}).startswith("FUERA_DE_PLAZO")


def test_ids_de_ticket_unicos_y_solo_digitos(monkeypatch, tmp_path):
    monkeypatch.setattr(T, "TICKETS_PATH", tmp_path / "tickets.csv")
    ids = []
    for i in range(20):
        salida = T.crear_ticket.invoke({"cliente": "Prueba", "resumen": f"r{i}", "prioridad": "baja"})
        ids.append(re.search(r"TCK-\d+", salida).group())
    assert len(set(ids)) == 20
