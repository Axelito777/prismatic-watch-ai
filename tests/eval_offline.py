"""Evaluación SIN llamadas al modelo (no gasta cuota): herramientas y recuperación semántica.

Mide: exactitud de las herramientas, hit@3 de la búsqueda, rechazo correcto de consultas fuera de
dominio (umbral de distancia) y latencia de la recuperación. Escribe logs/eval_offline.jsonl.

Uso:  python tests/eval_offline.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

logs = Path(os.getenv("OBS_LOGS_DIR", ROOT / "logs"))
logs.mkdir(parents=True, exist_ok=True)
os.environ["FECHA_REFERENCIA"] = "2026-10-20"
_tmp_tickets = Path(tempfile.mkdtemp(prefix="eval_tickets_")) / "tickets.csv"
os.environ["TICKETS_PATH"] = str(_tmp_tickets)

from src.agent import tools as T  # noqa: E402
from src.observability.evaluacion import normalizar  # noqa: E402
from src.observability.metrics import percentil  # noqa: E402
from src.observability.tracer import escribir_jsonl  # noqa: E402

LOTE = time.strftime("offline-%Y%m%d-%H%M")
SALIDA = logs / "eval_offline.jsonl"
filas = []


def registrar(tipo, caso_id, categoria, esperado, obtenido, acierto, latencia, **extra):
    fila = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "lote": LOTE, "tipo": tipo, "caso_id": caso_id,
            "categoria": categoria, "esperado": esperado, "obtenido": str(obtenido)[:200],
            "acierto": bool(acierto), "latencia_s": round(latencia, 5), **extra}
    filas.append(fila)
    escribir_jsonl(SALIDA, fila)


def medir(fn, *a, **kw):
    t0 = time.perf_counter()
    r = fn(*a, **kw)
    return r, time.perf_counter() - t0


# ---------- A) herramientas deterministas (fecha de referencia 2026-10-20) ----------
def elig(fecha, tipo):
    return T.evaluar_elegibilidad.invoke({"fecha_compra": fecha, "tipo_caso": tipo})


CASOS_HERRAMIENTAS = [
    ("pedido_valido", "consultar_pedido", lambda: T.consultar_pedido.invoke("ORD-1001"), lambda r: "Camila" in r, "devuelve el pedido"),
    ("pedido_minusculas_espacios", "consultar_pedido", lambda: T.consultar_pedido.invoke(" ord-1002 "), lambda r: "Diego" in r, "normaliza el ID"),
    ("pedido_inexistente", "consultar_pedido", lambda: T.consultar_pedido.invoke("ORD-9999"), lambda r: r == "PEDIDO_NO_ENCONTRADO", "PEDIDO_NO_ENCONTRADO"),
    ("pedido_vacio", "consultar_pedido", lambda: T.consultar_pedido.invoke(""), lambda r: r == "PEDIDO_NO_ENCONTRADO", "PEDIDO_NO_ENCONTRADO"),
    ("elegible_8_dias", "evaluar_elegibilidad", lambda: elig("2026-10-12", "cambio"), lambda r: r.startswith("ELEGIBLE"), "ELEGIBLE"),
    ("fuera_plazo_46_dias", "evaluar_elegibilidad", lambda: elig("2026-09-04", "devolucion"), lambda r: r.startswith("FUERA_DE_PLAZO"), "FUERA_DE_PLAZO"),
    ("limite_10_dias", "evaluar_elegibilidad", lambda: elig("2026-10-10", "cambio"), lambda r: r.startswith("ELEGIBLE"), "ELEGIBLE (10 días exactos)"),
    ("limite_11_dias", "evaluar_elegibilidad", lambda: elig("2026-10-09", "cambio"), lambda r: r.startswith("FUERA_DE_PLAZO"), "FUERA_DE_PLAZO (11 días)"),
    ("garantia_vigente", "evaluar_elegibilidad", lambda: elig("2026-10-05", "garantia"), lambda r: r.startswith("ELEGIBLE"), "ELEGIBLE"),
    ("garantia_vencida_366", "evaluar_elegibilidad", lambda: elig("2025-10-19", "garantia"), lambda r: r.startswith("FUERA_DE_PLAZO"), "FUERA_DE_PLAZO"),
    ("fecha_formato_invalido", "evaluar_elegibilidad", lambda: elig("12-10-2026", "cambio"), lambda r: r.startswith("FECHA_INVALIDA"), "FECHA_INVALIDA"),
    ("fecha_futura", "evaluar_elegibilidad", lambda: elig("2026-12-01", "cambio"), lambda r: r.startswith("FECHA_INVALIDA"), "FECHA_INVALIDA (fecha futura)"),
]


def correr_herramientas():
    for caso_id, herramienta, fn, ok, esperado in CASOS_HERRAMIENTAS:
        r, dt = medir(fn)
        registrar("herramienta", caso_id, herramienta, esperado, r, ok(r), dt)

    def crear(n):
        return T.crear_ticket.invoke({"cliente": "Prueba", "resumen": f"prueba {n}", "prioridad": "baja"})

    (a, dt1), (b, dt2) = medir(crear, 1), medir(crear, 2)
    registrar("herramienta", "ticket_creado", "crear_ticket", "TICKET_CREADO", a, a.startswith("TICKET_CREADO"), dt1)
    registrar("herramienta", "ticket_ids_unicos", "crear_ticket", "dos tickets seguidos con ID distinto",
              f"{a} / {b}", a.split()[1] != b.split()[1], dt2)


# ---------- B) recuperación semántica ----------
EN_DOMINIO = [
    ("¿cuánto demora el despacho?", ["3 a 5"]),
    ("cuanto demora el desppacho", ["3 a 5"]),
    ("plazo para cambiar un reloj", ["10 días"]),
    ("cómo limpio la correa de cuero", ["paño seco"]),
    ("cuánto dura la garantía", ["12 meses"]),
    ("cuánta agua resiste mi reloj", ["ATM"]),
    ("mi reloj se mojó", ["agua"]),
    ("se acabó la pila del reloj", ["pila"]),
    ("puedo retirar en tienda", ["Retiro en tienda"]),
    ("cómo guardo mi reloj", ["lugar seco"]),
    ("a qué correo escribo para soporte", ["soporte@"]),
]
FUERA_DOMINIO = [
    ("receta de pizza", "fuera_de_tema"),
    ("quién ganó el mundial de fútbol", "fuera_de_tema"),
    ("cómo programo en python", "fuera_de_tema"),
    ("qué clima hace en Santiago", "fuera_de_tema"),
    ("venden relojes de oro", "borde"),
]


def correr_recuperacion():
    vs = T.get_vectorstore()
    T.buscar_politicas.invoke("precalentamiento")  # excluye la carga del modelo de la latencia
    for consulta, claves in EN_DOMINIO:
        r, dt = medir(T.buscar_politicas.invoke, consulta)
        dist = [round(s, 2) for _, s in vs.similarity_search_with_score(consulta, k=3)]
        hit = r != "SIN_RESULTADOS" and any(normalizar(k) in normalizar(r) for k in claves)
        registrar("retrieval", consulta, "en_dominio", f"contiene {claves}", r[:120].replace("\n", " "),
                  hit, dt, dist_min=dist[0] if dist else None)
    for consulta, cat in FUERA_DOMINIO:
        r, dt = medir(T.buscar_politicas.invoke, consulta)
        dist = [round(s, 2) for _, s in vs.similarity_search_with_score(consulta, k=3)]
        registrar("retrieval", consulta, f"fuera_dominio_{cat}", "SIN_RESULTADOS", r[:120].replace("\n", " "),
                  r == "SIN_RESULTADOS", dt, dist_min=dist[0] if dist else None)


def resumen():
    def tasa(f):
        return f"{sum(x['acierto'] for x in f)}/{len(f)}" if f else "-"
    her = [f for f in filas if f["tipo"] == "herramienta"]
    ind = [f for f in filas if f["categoria"] == "en_dominio"]
    fuera = [f for f in filas if f["categoria"].startswith("fuera_dominio")]
    lat = [f["latencia_s"] for f in filas if f["tipo"] == "retrieval"]
    print(f"\n=== Evaluación offline (lote {LOTE}) ===")
    print(f"Herramientas correctas:           {tasa(her)}")
    print(f"Búsqueda: hit@3 en dominio:       {tasa(ind)}")
    print(f"Búsqueda: rechazo fuera dominio:  {tasa(fuera)}")
    if lat:
        print(f"Latencia de búsqueda:             p50={percentil(lat, 50) * 1000:.0f} ms  p95={percentil(lat, 95) * 1000:.0f} ms")
    fallos = [f for f in filas if not f["acierto"]]
    if fallos:
        print("\nCasos que fallaron (candidatos a hallazgo en el informe):")
        for f in fallos:
            print(f"  - [{f['tipo']}] {f['caso_id']}: esperado {f['esperado']!r}, obtenido {f['obtenido'][:80]!r}")


if __name__ == "__main__":
    correr_herramientas()
    try:
        correr_recuperacion()
    except Exception as e:  # noqa: BLE001
        print(f"\n[aviso] No se pudo evaluar la recuperación ({type(e).__name__}: {e}).\n"
              "¿Existe chroma_db? Genera la base con: python src/ingest.py")
    resumen()
