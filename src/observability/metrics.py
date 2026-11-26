"""Métricas de observabilidad (solo librería estándar, para poder usarlas en scripts y dashboard)."""
import json
import math
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path


def cargar_jsonl(ruta) -> list:
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    filas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if linea:
            try:
                filas.append(json.loads(linea))
            except json.JSONDecodeError:
                continue  # línea corrupta o a medio escribir
    return filas


def percentil(valores, p):
    v = sorted(valores)
    if not v:
        return None
    k = (len(v) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    return v[f] if f == c else v[f] + (v[c] - v[f]) * (k - f)


def resumen_numerico(valores) -> dict:
    v = [x for x in valores if x is not None]
    if not v:
        return {"n": 0, "media": None, "p50": None, "p95": None, "max": None}
    return {"n": len(v), "media": sum(v) / len(v), "p50": percentil(v, 50),
            "p95": percentil(v, 95), "max": max(v)}


def wilson(aciertos: int, n: int, z: float = 1.96):
    """Intervalo de confianza de Wilson para una proporción (útil con pocas ejecuciones)."""
    if n == 0:
        return (None, None)
    p = aciertos / n
    den = 1 + z * z / n
    centro = (p + z * z / (2 * n)) / den
    mitad = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centro - mitad), min(1.0, centro + mitad))


# ---------------- trazas: errores, latencia y recursos ----------------
def resumen_trazas(trazas) -> dict:
    n = len(trazas)
    por_estado = Counter(t.get("estado") for t in trazas)
    con_modelo = [t for t in trazas if t.get("estado") != "bloqueado"]
    return {
        "turnos": n,
        "tasa_error": por_estado["error"] / n if n else None,
        "tasa_degradado": por_estado["degradado"] / n if n else None,
        "tasa_bloqueado": por_estado["bloqueado"] / n if n else None,
        "latencia_s": resumen_numerico([t["latencia_total_s"] for t in con_modelo if t.get("estado") != "error"]),
        "llm_calls": resumen_numerico([t.get("llm_calls", 0) for t in con_modelo]),
        "tokens_total": resumen_numerico([t.get("tokens_total", 0) for t in con_modelo]),
        "cpu_s": resumen_numerico([t.get("cpu_s") for t in con_modelo]),
        "rss_mb_max": max([t["rss_mb"] for t in trazas if t.get("rss_mb")] or [None]),
    }


def errores_por_tipo(trazas) -> dict:
    return dict(Counter(e["tipo"] for t in trazas for e in t.get("errores", [])))


def latencia_por_nodo(trazas) -> dict:
    acc = defaultdict(list)
    for t in trazas:
        for n in t.get("nodos", []):
            acc[n["nodo"]].append(n["duracion_s"])
    return {k: resumen_numerico(v) for k, v in acc.items()}


def latencia_por_herramienta(trazas) -> dict:
    acc = defaultdict(list)
    for t in trazas:
        for h in t.get("herramientas", []):
            acc[h["nombre"]].append(h["duracion_s"])
    return {k: resumen_numerico(v) for k, v in acc.items()}


def fraccion_llm_en_latencia(trazas):
    """Qué parte del tiempo total de los turnos se va en esperar al modelo (cuello de botella)."""
    total = sum(t["latencia_total_s"] for t in trazas if t.get("estado") not in ("bloqueado", "error"))
    en_llm = sum(c["duracion_s"] for t in trazas if t.get("estado") not in ("bloqueado", "error")
                 for c in t.get("llm", []))
    return en_llm / total if total else None


# ---------------- evaluación: precisión y consistencia ----------------
def huella(traza) -> str:
    herr = sorted({h["nombre"] for h in traza.get("herramientas", [])})
    return "|".join(herr) + f"|ticket={int(bool(traza.get('ticket')))}|{traza.get('estado')}"


def _validas(filas, excluir_infra=True):
    return [f for f in filas if not (excluir_infra and f.get("error_infra"))]


def precision(filas, excluir_infra=True) -> dict:
    v = _validas(filas, excluir_infra)
    a = sum(1 for f in v if f.get("acierto"))
    lo, hi = wilson(a, len(v))
    return {"n": len(v), "aciertos": a, "precision": a / len(v) if v else None, "ic95": (lo, hi),
            "excluidas_por_infra": len(filas) - len(v)}


def precision_por_categoria(filas, excluir_infra=True) -> dict:
    por = defaultdict(list)
    for f in filas:
        por[f.get("categoria")].append(f)
    return {c: precision(v, excluir_infra) for c, v in por.items()}


def _norm_texto(t):
    return " ".join((t or "").lower().split())[:400]


def consistencia_por_caso(filas, trazas_por_run) -> dict:
    """Para cada caso con >= 2 repeticiones: acuerdo de la decisión (herramientas+ticket+estado),
    estabilidad del acierto y similitud promedio del texto de las respuestas."""
    por = defaultdict(list)
    for f in _validas(filas):
        por[f["caso_id"]].append(f)
    salida = {}
    for caso, reps in por.items():
        if len(reps) < 2:
            continue
        huellas = Counter(r.get("huella") for r in reps)
        aciertos = Counter(bool(r.get("acierto")) for r in reps)
        textos = [_norm_texto(trazas_por_run.get(r["run_id"], {}).get("respuesta")) for r in reps]
        pares = [SequenceMatcher(None, textos[i], textos[j]).ratio()
                 for i in range(len(textos)) for j in range(i + 1, len(textos))]
        salida[caso] = {
            "repeticiones": len(reps),
            "acuerdo_decision": huellas.most_common(1)[0][1] / len(reps),
            "estabilidad_acierto": aciertos.most_common(1)[0][1] / len(reps),
            "similitud_texto": sum(pares) / len(pares) if pares else None,
            "decisiones_distintas": len(huellas),
        }
    return salida


def consistencia_global(por_caso: dict) -> dict:
    if not por_caso:
        return {"casos": 0, "acuerdo_decision": None, "estabilidad_acierto": None, "similitud_texto": None}
    vals = list(por_caso.values())
    prom = lambda k: sum(v[k] for v in vals if v[k] is not None) / max(1, sum(1 for v in vals if v[k] is not None))  # noqa: E731
    return {"casos": len(vals), "acuerdo_decision": prom("acuerdo_decision"),
            "estabilidad_acierto": prom("estabilidad_acierto"), "similitud_texto": prom("similitud_texto")}
