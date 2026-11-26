"""Evaluación automática de casos de prueba (sin LLM-juez: reglas verificables y reproducibles)."""
import json
import unicodedata
from pathlib import Path

from src.observability.metrics import huella
from src.observability.tracer import ERRORES_INFRA

ROOT = Path(__file__).resolve().parents[2]
CASOS_PATH = ROOT / "tests" / "eval_cases.json"


def cargar_casos(ruta=CASOS_PATH) -> list:
    return json.loads(Path(ruta).read_text(encoding="utf-8"))["casos"]


def normalizar(texto: str) -> str:
    sin = "".join(c for c in unicodedata.normalize("NFKD", texto or "") if not unicodedata.combining(c))
    return " ".join(sin.lower().split())


def evaluar_caso(traza: dict, respuesta: str, esperado: dict):
    """Devuelve (checks, acierto). `respuesta` es el texto sin enmascarar que vio el cliente."""
    usadas = {h["nombre"] for h in traza.get("herramientas", [])}
    texto = normalizar(respuesta)
    checks = {"sin_error": traza.get("estado") != "error"}
    if "tools_requeridas" in esperado:
        checks["tools_requeridas"] = all(t in usadas for t in esperado["tools_requeridas"])
    if "tools_prohibidas" in esperado:
        checks["tools_prohibidas"] = not any(t in usadas for t in esperado["tools_prohibidas"])
    if "texto_incluye_alguno" in esperado:
        checks["texto_incluye"] = any(normalizar(k) in texto for k in esperado["texto_incluye_alguno"])
    if "texto_excluye" in esperado:
        checks["texto_excluye"] = not any(normalizar(k) in texto for k in esperado["texto_excluye"])
    if esperado.get("ticket") is not None:
        checks["ticket"] = bool(traza.get("ticket")) == esperado["ticket"]
    if esperado.get("prioridad"):
        checks["prioridad"] = traza.get("prioridad") == esperado["prioridad"]
    if esperado.get("bloqueado") is not None:
        checks["bloqueado"] = (traza.get("estado") == "bloqueado") == esperado["bloqueado"]
    if esperado.get("enmascarado") is not None:
        checks["enmascarado"] = bool(traza.get("datos_enmascarados")) == esperado["enmascarado"]
    return checks, all(checks.values())


def fila_resultado(traza: dict, caso: dict, respuesta: str, lote: str) -> dict:
    checks, acierto = evaluar_caso(traza, respuesta, caso["esperado"])
    err_infra = any(e.get("tipo") in ERRORES_INFRA for e in traza.get("errores", [])
                    if traza.get("estado") == "error")
    return {
        "ts": traza["ts"], "lote": lote, "run_id": traza["run_id"], "caso_id": caso["id"],
        "categoria": caso["categoria"], "repeticion": traza.get("repeticion"),
        "acierto": acierto, "checks": checks, "huella": huella(traza),
        "latencia_s": traza.get("latencia_total_s"), "estado": traza.get("estado"),
        "error_infra": err_infra,
    }
