import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent.graph import build_agent
from src.observability.tracer import ejecutar_turno


def mostrar(nodo, data):
    """Imprime en consola lo que hace el agente en cada nodo (útil en la demo en vivo)."""
    if nodo == "recuperar_memoria":
        print(f"  [memoria] {data['recuerdos']}")
    elif nodo == "planificar":
        print(f"  [plan | prioridad {data['prioridad']}]\n    " + data["plan"].replace("\n", "\n    "))
    elif nodo == "agente":
        msg = data["messages"][-1]
        for tc in getattr(msg, "tool_calls", None) or []:
            print(f"  [tool] {tc['name']}({tc['args']})")
    elif nodo == "evaluar" and data.get("alerta"):
        print(f"  [evaluador] {data['alerta']}")


def main():
    print("=== Prismatic Watch AI — Agente de atención al cliente ===")
    cliente = input("Tu nombre: ").strip() or "invitado"
    agent = build_agent()
    thread_id = str(uuid.uuid4())  # misma conversación = misma memoria de corto plazo
    print(f"\nHola {cliente}. Escribe tu consulta ('salir' para terminar).\n")

    while True:
        pregunta = input("Tú: ").strip()
        if pregunta.lower() in ("salir", "exit", "quit"):
            print("¡Hasta luego!")
            break
        if not pregunta:
            continue

        respuesta, traza = ejecutar_turno(
            agent, pregunta, cliente, thread_id=thread_id, on_update=mostrar)

        if traza["estado"] == "error":
            tipos = {e.get("tipo") for e in traza["errores"]}
            if tipos & {"cuota_diaria", "cuota_rpm"}:
                print("\n[error] Límite de la API alcanzado (cuota o RPM). Espera un minuto o cambia MODEL_NAME en .env.\n")
            else:
                print(f"\n[error] {traza['errores'][-1].get('mensaje') or tipos}\n")
            continue
        print(f"\nAsistente: {respuesta}\n")
        print(f"  [obs] {traza['latencia_total_s']:.1f}s · {traza['llm_calls']} llamadas al modelo · "
              f"{traza['tokens_total']} tokens · estado: {traza['estado']}\n")


if __name__ == "__main__":
    main()
