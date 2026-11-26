from src.observability.evaluacion import evaluar_caso, normalizar
from src.observability.metrics import (consistencia_por_caso, errores_por_tipo, huella, percentil,
                                       precision, resumen_trazas, wilson)


def traza(**kw):
    base = {"run_id": "r", "estado": "ok", "herramientas": [], "ticket": None, "latencia_total_s": 1.0,
            "llm_calls": 2, "tokens_total": 100, "cpu_s": 0.1, "rss_mb": 200, "errores": [], "nodos": [],
            "llm": [], "respuesta": "", "prioridad": "baja", "datos_enmascarados": False}
    base.update(kw)
    return base


def test_percentil_interpola():
    assert percentil([1, 2, 3, 4], 50) == 2.5
    assert percentil([10], 95) == 10
    assert percentil([], 50) is None


def test_wilson_contiene_la_proporcion():
    lo, hi = wilson(8, 10)
    assert lo < 0.8 < hi and 0 <= lo and hi <= 1
    assert wilson(0, 0) == (None, None)


def test_resumen_trazas_tasas():
    ts = [traza(), traza(estado="error", errores=[{"tipo": "cuota_diaria"}]), traza(estado="degradado"),
          traza(estado="bloqueado", llm_calls=0)]
    r = resumen_trazas(ts)
    assert r["turnos"] == 4 and r["tasa_error"] == 0.25 and r["tasa_bloqueado"] == 0.25
    assert errores_por_tipo(ts) == {"cuota_diaria": 1}


def test_evaluar_caso_checks():
    t = traza(herramientas=[{"nombre": "consultar_pedido"}, {"nombre": "crear_ticket"}], ticket="TCK-1")
    esperado = {"tools_requeridas": ["consultar_pedido"], "tools_prohibidas": ["buscar_politicas"],
                "texto_incluye_alguno": ["fuera de plazo"], "texto_excluye": ["aprobado"], "ticket": True}
    checks, ok = evaluar_caso(t, "Tu solicitud está FUERA DE PLAZO", esperado)
    assert ok and all(checks.values())
    checks, ok = evaluar_caso(t, "Quedó aprobado", esperado)
    assert not ok and not checks["texto_incluye"] and not checks["texto_excluye"]


def test_normalizar_quita_tildes():
    assert normalizar("Paño   SECO") == "pano seco"


def test_consistencia_detecta_decisiones_distintas():
    a = traza(herramientas=[{"nombre": "buscar_politicas"}])
    b = traza(herramientas=[{"nombre": "buscar_politicas"}, {"nombre": "crear_ticket"}], ticket="TCK-1")
    filas = [{"caso_id": "c", "run_id": "1", "acierto": True, "huella": huella(a)},
             {"caso_id": "c", "run_id": "2", "acierto": True, "huella": huella(a)},
             {"caso_id": "c", "run_id": "3", "acierto": False, "huella": huella(b)}]
    runs = {"1": {"respuesta": "hola mundo"}, "2": {"respuesta": "hola mundo"}, "3": {"respuesta": "otra cosa"}}
    r = consistencia_por_caso(filas, runs)["c"]
    assert abs(r["acuerdo_decision"] - 2 / 3) < 1e-9 and r["decisiones_distintas"] == 2
    assert 0 < r["similitud_texto"] < 1


def test_precision_excluye_errores_de_infraestructura():
    filas = [{"acierto": True}, {"acierto": False}, {"acierto": False, "error_infra": True}]
    p = precision(filas)
    assert p["n"] == 2 and p["precision"] == 0.5 and p["excluidas_por_infra"] == 1
