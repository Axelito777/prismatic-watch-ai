"""Trazabilidad: captura latencia por nodo, llamadas al LLM, tokens, herramientas y errores.

Se engancha al agente mediante callbacks de LangChain (no modifica el grafo) y escribe un registro
JSONL por turno en logs/traces.jsonl, con los datos personales ya enmascarados.
"""
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage

from src.seguridad.guardrails import MENSAJE_BLOQUEO, revisar_entrada
from src.seguridad.privacidad import enmascarar_texto, seudonimo

ROOT = Path(__file__).resolve().parents[2]
ERRORES_INFRA = {"cuota_diaria", "cuota_rpm", "modelo_no_disponible", "presupuesto_llm", "timeout"}


def logs_dir() -> Path:
    return Path(os.getenv("OBS_LOGS_DIR", ROOT / "logs"))


def traces_path() -> Path:
    return logs_dir() / "traces.jsonl"


class PresupuestoLLMExcedido(RuntimeError):
    """Se superó el máximo de llamadas al modelo permitido en un turno (control de costo y cuota)."""


def clasificar_error(exc) -> str:
    s = f"{type(exc).__name__} {exc}"
    if isinstance(exc, PresupuestoLLMExcedido):
        return "presupuesto_llm"
    if "RESOURCE_EXHAUSTED" in s or "429" in s:
        return "cuota_diaria" if ("PerDay" in s or "per day" in s.lower()) else "cuota_rpm"
    if "NOT_FOUND" in s or "404" in s:
        return "modelo_no_disponible"
    if "GraphRecursionError" in s:
        return "recursion"
    if "timeout" in s.lower() or "timed out" in s.lower():
        return "timeout"
    return "otro"


def _contenido(x) -> str:
    c = getattr(x, "content", x)
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)
    return str(c)


def _codigo_herramienta(salida: str) -> str:
    m = re.match(r"\s*([A-Z][A-Z_]{3,})", salida or "")
    return m.group(1) if m else "OK"


class TraceCollector(BaseCallbackHandler):
    """Acumula eventos de un turno. raise_error=True solo para que el presupuesto corte la ejecución."""

    raise_error = True

    def __init__(self, max_llm_calls=None):
        self.inicio = time.perf_counter()
        self.max_llm_calls = max_llm_calls
        self.nodos, self.llm, self.herramientas, self.errores = [], [], [], []
        self._activos = {}
        self._llm_iniciadas = 0

    def _t(self):
        return round(time.perf_counter() - self.inicio, 4)

    # ---- nodos del grafo ----
    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, tags=None,
                       metadata=None, **kwargs):
        try:
            nodo = (metadata or {}).get("langgraph_node")
            if nodo and kwargs.get("name") == nodo:
                self._activos[run_id] = ("nodo", nodo, time.perf_counter(), self._t())
        except Exception:
            pass

    def _cerrar_nodo(self, run_id, estado):
        try:
            info = self._activos.pop(run_id, None)
            if info and info[0] == "nodo":
                self.nodos.append({"nodo": info[1], "inicio_s": info[3],
                                   "duracion_s": round(time.perf_counter() - info[2], 4),
                                   "estado": estado})
        except Exception:
            pass

    def on_chain_end(self, outputs, *, run_id, parent_run_id=None, **kwargs):
        self._cerrar_nodo(run_id, "ok")

    def on_chain_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        self._cerrar_nodo(run_id, "error")

    # ---- llamadas al modelo ----
    def _llm_inicio(self, run_id, metadata):
        self._llm_iniciadas += 1
        if self.max_llm_calls is not None and self._llm_iniciadas > self.max_llm_calls:
            raise PresupuestoLLMExcedido(
                f"Más de {self.max_llm_calls} llamadas al modelo en un solo turno")
        self._activos[run_id] = ("llm", (metadata or {}).get("langgraph_node"),
                                 time.perf_counter(), self._t())

    def on_chat_model_start(self, serialized, messages, *, run_id, parent_run_id=None, tags=None,
                            metadata=None, **kwargs):
        self._llm_inicio(run_id, metadata)

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, tags=None,
                     metadata=None, **kwargs):
        self._llm_inicio(run_id, metadata)

    def on_llm_end(self, response, *, run_id, parent_run_id=None, **kwargs):
        try:
            info = self._activos.pop(run_id, None)
            if not info:
                return
            t_in = t_out = t_tot = 0
            try:
                um = getattr(response.generations[0][0].message, "usage_metadata", None) or {}
                t_in, t_out = um.get("input_tokens", 0), um.get("output_tokens", 0)
                t_tot = um.get("total_tokens", t_in + t_out)
            except Exception:
                uso = (response.llm_output or {}).get("token_usage", {})
                t_in, t_out = uso.get("prompt_tokens", 0), uso.get("completion_tokens", 0)
                t_tot = uso.get("total_tokens", t_in + t_out)
            self.llm.append({"nodo": info[1], "inicio_s": info[3],
                             "duracion_s": round(time.perf_counter() - info[2], 4),
                             "tokens_in": t_in, "tokens_out": t_out, "tokens_total": t_tot,
                             "estado": "ok"})
        except Exception:
            pass

    def on_llm_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            info = self._activos.pop(run_id, None)
            tipo = clasificar_error(error)
            if info:
                self.llm.append({"nodo": info[1], "inicio_s": info[3],
                                 "duracion_s": round(time.perf_counter() - info[2], 4),
                                 "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
                                 "estado": "error", "error": tipo})
            self.errores.append({"origen": "llm", "nodo": info[1] if info else None, "tipo": tipo})
        except Exception:
            pass

    # ---- herramientas ----
    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, tags=None,
                      metadata=None, inputs=None, **kwargs):
        try:
            nombre = (serialized or {}).get("name") or kwargs.get("name") or "desconocida"
            self._activos[run_id] = ("tool", nombre, time.perf_counter(), self._t(),
                                     inputs if inputs is not None else input_str)
        except Exception:
            pass

    def on_tool_end(self, output, *, run_id, parent_run_id=None, **kwargs):
        try:
            info = self._activos.pop(run_id, None)
            if not info:
                return
            salida = _contenido(output)
            self.herramientas.append({
                "nombre": info[1], "inicio_s": info[3],
                "duracion_s": round(time.perf_counter() - info[2], 4),
                "estado": "ok", "codigo": _codigo_herramienta(salida),
                "args": info[4], "_salida": salida})
        except Exception:
            pass

    def on_tool_error(self, error, *, run_id, parent_run_id=None, **kwargs):
        try:
            info = self._activos.pop(run_id, None)
            if info:
                self.herramientas.append({
                    "nombre": info[1], "inicio_s": info[3],
                    "duracion_s": round(time.perf_counter() - info[2], 4),
                    "estado": "error", "codigo": type(error).__name__,
                    "args": info[4], "_salida": str(error)})
            self.errores.append({"origen": "herramienta", "nodo": info[1] if info else None,
                                 "tipo": "herramienta_fallida"})
        except Exception:
            pass


_ALERTAS = [
    ("reformular_busqueda", "no devolvió resultados"),
    ("escalar_sin_informacion", "tras reintentar"),
    ("pedido_inexistente", "el pedido no existe"),
    ("fuera_de_plazo", "fuera de plazo"),
    ("fecha_invalida", "fecha era inválida"),
]


def _codigo_alerta(texto: str) -> str:
    t = texto.lower()
    for codigo, frag in _ALERTAS:
        if frag in t:
            return codigo
    return "otra"


def _rss_mb():
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(kb / (1024 * 1024) if sys.platform == "darwin" else kb / 1024, 1)
    except Exception:
        return None


def escribir_jsonl(ruta: Path, registro: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def ejecutar_turno(agent, pregunta, cliente, *, thread_id=None, lote=None, caso_id=None,
                   categoria=None, repeticion=None, on_update=None, max_llm_calls=12,
                   nombres_extra=(), escribir=True):
    """Ejecuta un turno del agente con guardrails y trazabilidad.

    Devuelve (respuesta_sin_enmascarar, registro). El registro (enmascarado) se agrega a
    logs/traces.jsonl. Las llamadas al modelo por turno se limitan a `max_llm_calls`.
    """
    run_id = uuid.uuid4().hex[:12]
    thread_id = thread_id or uuid.uuid4().hex
    nombres = [cliente, *nombres_extra]
    mask = lambda t: enmascarar_texto(t, nombres)  # noqa: E731
    revision = revisar_entrada(pregunta)
    registro = {
        "ts": datetime.now().isoformat(timespec="seconds"), "run_id": run_id, "thread_id": thread_id,
        "lote": lote, "caso_id": caso_id, "categoria": categoria, "repeticion": repeticion,
        "modelo": os.getenv("MODEL_NAME"), "cliente": seudonimo(cliente),
        "pregunta": mask(revision.texto), "datos_enmascarados": revision.datos_enmascarados,
        "truncado": revision.truncado,
    }

    if revision.bloqueado:
        registro.update({
            "estado": "bloqueado", "razon_bloqueo": revision.razon, "respuesta": MENSAJE_BLOQUEO,
            "prioridad": None, "plan_pasos": 0, "latencia_total_s": 0.0, "nodos": [], "llm": [],
            "herramientas": [], "llm_calls": 0, "tokens_in": 0, "tokens_out": 0, "tokens_total": 0,
            "cpu_s": 0.0, "rss_mb": _rss_mb(), "alertas": [], "ticket": None,
            "reintentos_busqueda": 0, "errores": [],
        })
        if escribir:
            escribir_jsonl(traces_path(), registro)
        return MENSAJE_BLOQUEO, registro

    col = TraceCollector(max_llm_calls=max_llm_calls)
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 25, "callbacks": [col]}
    entrada = {"messages": [HumanMessage(content=revision.texto)], "cliente_id": cliente}
    respuesta, prioridad, plan_pasos, alertas, error = "", None, 0, [], None

    cpu0, t0 = time.process_time(), time.perf_counter()
    try:
        for update in agent.stream(entrada, config, stream_mode="updates"):
            for nodo, data in update.items():
                if not data:
                    continue
                if nodo == "planificar":
                    prioridad = data.get("prioridad")
                    plan_pasos = len((data.get("plan") or "").splitlines())
                elif nodo == "agente":
                    msg = data["messages"][-1]
                    if not getattr(msg, "tool_calls", None):
                        respuesta = _contenido(msg)
                elif nodo == "evaluar" and data.get("alerta"):
                    alertas.append(_codigo_alerta(data["alerta"]))
                if on_update:
                    on_update(nodo, data)
    except Exception as e:  # noqa: BLE001 - se clasifica y se registra
        error = {"origen": "turno", "nodo": None, "tipo": clasificar_error(e),
                 "mensaje": mask(str(e))[:300]}
    latencia = round(time.perf_counter() - t0, 4)
    cpu = round(time.process_time() - cpu0, 4)

    salidas = " ".join(h.get("_salida", "") for h in col.herramientas)
    ticket = (re.findall(r"TCK-\d+", salidas) or [None])[-1]
    herramientas = []
    for h in col.herramientas:
        h = dict(h)
        h.pop("_salida", None)
        args = h.get("args")
        h["args"] = mask(json.dumps(args, ensure_ascii=False, default=str)) if args is not None else None
        herramientas.append(h)

    errores = col.errores + ([error] if error else [])
    estado = "error" if error else ("degradado" if col.errores else "ok")
    registro.update({
        "estado": estado, "respuesta": mask(respuesta), "prioridad": prioridad,
        "plan_pasos": plan_pasos, "latencia_total_s": latencia, "nodos": col.nodos, "llm": col.llm,
        "herramientas": herramientas, "llm_calls": len(col.llm),
        "tokens_in": sum(c["tokens_in"] for c in col.llm),
        "tokens_out": sum(c["tokens_out"] for c in col.llm),
        "tokens_total": sum(c["tokens_total"] for c in col.llm),
        "cpu_s": cpu, "rss_mb": _rss_mb(), "alertas": alertas, "ticket": ticket,
        "reintentos_busqueda": sum(1 for h in herramientas if h["codigo"] == "SIN_RESULTADOS"),
        "errores": errores,
    })
    if escribir:
        escribir_jsonl(traces_path(), registro)
    return respuesta, registro
