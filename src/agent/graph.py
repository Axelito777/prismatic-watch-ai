import os
from typing import Annotated, Literal

from dotenv import load_dotenv
from langchain_core.messages import AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from .memory import guardar_recuerdo, recortar_historial, recuperar_recuerdos
from .prompts import MEMORY_PROMPT, PLANNER_PROMPT, SYSTEM_PROMPT
from .tools import TOOLS

load_dotenv(override=True)

MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.6-flash")
print(f"[config] modelo: {MODEL_NAME}")
MAX_REINTENTOS = 2


class Plan(BaseModel):
    prioridad: Literal["alta", "media", "baja"] = Field(description="Prioridad del caso")
    pasos: list[str] = Field(description="Pasos ordenados, máximo 5")


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    cliente_id: str
    recuerdos: str
    prioridad: str
    plan: str
    alerta: str
    reintentos: int


def _texto(msg) -> str:
    c = msg.content
    if isinstance(c, str):
        return c
    return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in c)


def _turno(messages):
    """Mensajes desde el último mensaje humano (el turno actual)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return messages[i:]
    return messages


def build_agent():
    llm = ChatGoogleGenerativeAI(model=MODEL_NAME)
    llm_tools = llm.bind_tools(TOOLS)
    planner = llm.with_structured_output(Plan)

    # 1) Memoria larga: recupera recuerdos semánticamente relevantes del cliente
    def recuperar_memoria(state: AgentState):
        consulta = _texto(_turno(state["messages"])[0])
        return {
            "recuerdos": recuperar_recuerdos(state["cliente_id"], consulta),
            "alerta": "",
            "reintentos": 0,
        }

    # 2) Planificación: prioridad + pasos ordenados (se replanifica en cada turno)
    def planificar(state: AgentState):
        mensaje = _texto(_turno(state["messages"])[0])
        try:
            plan = planner.invoke(
                PLANNER_PROMPT.format(recuerdos=state["recuerdos"], mensaje=mensaje)
            )
            prioridad, pasos = plan.prioridad, plan.pasos
        except Exception:
            prioridad, pasos = "media", ["Resolver la consulta con las herramientas disponibles"]
        return {
            "prioridad": prioridad,
            "plan": "\n".join(f"{i}. {p}" for i, p in enumerate(pasos, 1)),
        }

    # 3) Agente: el LLM decide qué herramienta usar (o responde)
    def agente(state: AgentState):
        sistema = SYSTEM_PROMPT.format(
            cliente=state["cliente_id"],
            recuerdos=state["recuerdos"],
            prioridad=state["prioridad"],
            pasos=state["plan"],
            alerta=f"\nALERTA DEL SISTEMA: {state['alerta']}" if state["alerta"] else "",
        )
        historial = recortar_historial(state["messages"])
        return {"messages": [llm_tools.invoke([SystemMessage(content=sistema)] + historial)]}

    # 4) Evaluador: inspecciona resultados de herramientas y ajusta el comportamiento
    def evaluar(state: AgentState):
        resultados = []
        for m in reversed(_turno(state["messages"])):
            if isinstance(m, ToolMessage):
                resultados.append(_texto(m))
            elif resultados:
                break
        texto = " ".join(resultados)
        alerta, reintentos = "", state.get("reintentos", 0)

        if "SIN_RESULTADOS" in texto:
            if reintentos < MAX_REINTENTOS:
                alerta = ("La búsqueda no devolvió resultados. Reformula la consulta con otras "
                          "palabras clave y busca de nuevo.")
                reintentos += 1
            else:
                alerta = ("Tras reintentar no hay información en los documentos. No inventes: "
                          "crea un ticket de prioridad media y sugiere soporte@prismaticwatch.cl.")
        elif "PEDIDO_NO_ENCONTRADO" in texto:
            alerta = ("El pedido no existe. Pide al cliente que verifique el número (formato ORD-XXXX). "
                      "No crees ticket todavía.")
        elif "FUERA_DE_PLAZO" in texto:
            alerta = ("El caso está fuera de plazo. Explícalo con claridad y crea un ticket para que "
                      "soporte humano evalúe una excepción.")
        elif "FECHA_INVALIDA" in texto:
            alerta = "La fecha era inválida. Corrígela al formato YYYY-MM-DD y reintenta."
        return {"alerta": alerta, "reintentos": reintentos}

    # 5) Memoria larga: guarda un resumen del turno si se usaron herramientas
    def guardar_memoria(state: AgentState):
        turno = _turno(state["messages"])
        herramientas = sorted({
            tc["name"] for m in turno if getattr(m, "tool_calls", None) for tc in m.tool_calls
        })
        if herramientas:
            guardar_recuerdo(
                state["cliente_id"],
                f"Consultó: {_texto(turno[0])[:150]}. "
                f"Herramientas usadas: {', '.join(herramientas)}. "
                f"Resultado: {_texto(turno[-1])[:200]}",
            )
        return {"alerta": ""}

    g = StateGraph(AgentState)
    g.add_node("recuperar_memoria", recuperar_memoria)
    g.add_node("planificar", planificar)
    g.add_node("agente", agente)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_node("evaluar", evaluar)
    g.add_node("guardar_memoria", guardar_memoria)

    g.add_edge(START, "recuperar_memoria")
    g.add_edge("recuperar_memoria", "planificar")
    g.add_edge("planificar", "agente")
    g.add_conditional_edges("agente", tools_condition, {"tools": "tools", END: "guardar_memoria"})
    g.add_edge("tools", "evaluar")
    g.add_edge("evaluar", "agente")
    g.add_edge("guardar_memoria", END)

    # Memoria corta: historial por thread_id durante la sesión
    return g.compile(checkpointer=MemorySaver())