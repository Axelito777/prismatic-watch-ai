from datetime import datetime
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.messages import trim_messages

from .tools import get_embeddings

ROOT = Path(__file__).resolve().parents[2]
MEMORY_PATH = str(ROOT / "memory_db")  # separada de chroma_db para que ingest.py no la borre
MAX_MENSAJES = 20


def _store() -> Chroma:
    return Chroma(
        collection_name="memoria_clientes",
        persist_directory=MEMORY_PATH,
        embedding_function=get_embeddings(),
    )


# ---------- Memoria de corto plazo ----------
# El historial de la sesión lo guarda el checkpointer de LangGraph (por thread_id).
# Acá solo se recorta para no saturar el contexto del modelo.
def recortar_historial(mensajes):
    return trim_messages(
        mensajes,
        strategy="last",
        token_counter=len,  # cuenta mensajes, no tokens
        max_tokens=MAX_MENSAJES,
        start_on="human",
        include_system=False,
    )


# ---------- Memoria de largo plazo (persistente y semántica) ----------
def guardar_recuerdo(cliente_id: str, texto: str) -> None:
    _store().add_texts(
        [texto],
        metadatas=[{"cliente_id": cliente_id.strip().lower(),
                    "fecha": datetime.now().isoformat(timespec="seconds")}],
    )


def recuperar_recuerdos(cliente_id: str, consulta: str, k: int = 3) -> str:
    docs = _store().similarity_search(
        consulta, k=k, filter={"cliente_id": cliente_id.strip().lower()}
    )
    if not docs:
        return "Sin interacciones previas."
    return "\n".join(f"- ({d.metadata.get('fecha', '?')[:10]}) {d.page_content}" for d in docs)