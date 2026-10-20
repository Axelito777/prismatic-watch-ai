import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage

from src.agent.graph import _texto, build_agent

LOGS = ROOT / "tests" / "logs"
LOGS.mkdir(parents=True, exist_ok=True)

ESCENARIOS = {
    1: ("Matias", "¿Cuánto demora un despacho a regiones?", "Consulta simple (solo buscar_politicas)"),
    2: ("Camila", "Quiero cambiar mi reloj, pedido ORD-1001", "Varias etapas: pedido -> elegibilidad -> elegible"),
    3: ("Diego", "Quiero devolver el pedido ORD-1002", "Fuera de plazo -> el evaluador fuerza ticket"),
    4: ("Matias", "Quiero revisar mi pedido ORD-9999", "Pedido inexistente -> pide verificar el número"),
    5: ("Matias", "¿Quién ganó el mundial de fútbol?", "Sin resultados -> reformula -> ticket"),
    6: ("Diego", "¿Qué pasó con mi caso anterior?", "Memoria larga entre sesiones (correr después del escenario 3)"),
}


def main(n: int):
    cliente, pregunta, objetivo = ESCENARIOS[n]
    lineas = [f"ESCENARIO {n}: {objetivo}", f"Cliente: {cliente}", f"Tú: {pregunta}"]
    agent = build_agent()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 25}
    entrada = {"messages": [HumanMessage(content=pregunta)], "cliente_id": cliente}
    respuesta = ""
    try:
        for update in agent.stream(entrada, config, stream_mode="updates"):
            for nodo, data in update.items():
                if not data:
                    continue
                if nodo == "recuperar_memoria":
                    lineas.append(f"  [memoria] {data['recuerdos']}")
                elif nodo == "planificar":
                    lineas.append(f"  [plan | prioridad {data['prioridad']}]\n    " + data["plan"].replace("\n", "\n    "))
                elif nodo == "agente":
                    msg = data["messages"][-1]
                    if msg.tool_calls:
                        for tc in msg.tool_calls:
                            lineas.append(f"  [tool] {tc['name']}({tc['args']})")
                    else:
                        respuesta = _texto(msg)
                elif nodo == "evaluar" and data.get("alerta"):
                    lineas.append(f"  [evaluador] {data['alerta']}")
        lineas.append(f"\nAsistente: {respuesta}")
    except Exception as e:
        lineas.append(f"\n[error] {e}")

    salida = "\n".join(lineas)
    print(salida)
    (LOGS / f"escenario{n}.txt").write_text(salida, encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or int(sys.argv[1]) not in ESCENARIOS:
        print("Uso: python tests/run_scenarios.py <1-6>")
        sys.exit(1)
    main(int(sys.argv[1]))