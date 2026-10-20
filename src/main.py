import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import HumanMessage

from src.agent.graph import _texto, build_agent


def main():
    print("=== Prismatic Watch AI — Agente de atención al cliente ===")
    cliente = input("Tu nombre: ").strip() or "invitado"
    agent = build_agent()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 25}
    print(f"\nHola {cliente}. Escribe tu consulta ('salir' para terminar).\n")

    while True:
        pregunta = input("Tú: ").strip()
        if pregunta.lower() in ("salir", "exit", "quit"):
            print("¡Hasta luego!")
            break
        if not pregunta:
            continue

        entrada = {"messages": [HumanMessage(content=pregunta)], "cliente_id": cliente}
        respuesta = ""
        try:
            for update in agent.stream(entrada, config, stream_mode="updates"):
                for nodo, data in update.items():
                    if not data:
                        continue
                    if nodo == "recuperar_memoria":
                        print(f"  [memoria] {data['recuerdos']}")
                    elif nodo == "planificar":
                        print(f"  [plan | prioridad {data['prioridad']}]\n    " + data["plan"].replace("\n", "\n    "))
                    elif nodo == "agente":
                        msg = data["messages"][-1]
                        if msg.tool_calls:
                            for tc in msg.tool_calls:
                                print(f"  [tool] {tc['name']}({tc['args']})")
                        else:
                            respuesta = _texto(msg)
                    elif nodo == "evaluar" and data.get("alerta"):
                        print(f"  [evaluador] {data['alerta']}")
        except Exception as e:
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                print("\n[error] Límite de la API alcanzado (cuota o RPM). Espera un minuto o cambia MODEL_NAME en .env.\n")
            else:
                print(f"\n[error] {e}\n")
            continue
        print(f"\nAsistente: {respuesta}\n")
        


if __name__ == "__main__":
    main()