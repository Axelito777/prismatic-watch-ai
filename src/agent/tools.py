import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from langchain_chroma import Chroma
from langchain_core.tools import tool
from langchain_huggingface import HuggingFaceEmbeddings

ROOT = Path(__file__).resolve().parents[2]
CHROMA_PATH = str(ROOT / "chroma_db")
ORDERS_PATH = ROOT / "data" / "orders.json"
TICKETS_PATH = ROOT / "data" / "tickets.csv"

EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# AJUSTA ESTOS PLAZOS A LO QUE DICEN TUS DOCUMENTOS EN data/policies/
PLAZOS_DIAS = {
    "cambio": 10,
    "devolucion": 10,
    "garantia": 365,
}

_vectorstore = None
_embeddings = None

# Distancia máxima aceptable (menor = más parecido). CALIBRAR con tests/calibrar_umbral.py
UMBRAL_DISTANCIA = 20.0


def get_embeddings() -> HuggingFaceEmbeddings:
    """Carga el modelo de embeddings una sola vez (lo comparten políticas y memoria)."""
    global _embeddings
    if _embeddings is None:
        _embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
    return _embeddings


def get_vectorstore() -> Chroma:
    global _vectorstore
    if _vectorstore is None:
        _vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=get_embeddings())
    return _vectorstore


@tool
def buscar_politicas(consulta: str) -> str:
    """Busca en las políticas de Prismatic Watch (garantía, cambios, envíos)
    y en la guía de cuidado de relojes. Úsala para cualquier pregunta sobre
    reglas, plazos, condiciones o cuidado de relojes."""
    resultados = get_vectorstore().similarity_search_with_score(consulta, k=3)
    docs = [d for d, dist in resultados if dist <= UMBRAL_DISTANCIA]
    if not docs:
        return "SIN_RESULTADOS"
    return "\n\n".join(
        f"[{d.metadata.get('source', 'desconocido')}]\n{d.page_content}" for d in docs
    )


@tool
def consultar_pedido(id_pedido: str) -> str:
    """Consulta un pedido por su ID (ej: ORD-1001). Devuelve cliente,
    producto, fecha de compra y estado. Úsala cuando el cliente mencione
    un número de pedido."""
    pedidos = json.loads(ORDERS_PATH.read_text(encoding="utf-8"))
    pedido = pedidos.get(id_pedido.strip().upper())
    if pedido is None:
        return "PEDIDO_NO_ENCONTRADO"
    return json.dumps(pedido, ensure_ascii=False)


@tool
def evaluar_elegibilidad(
    fecha_compra: str, tipo_caso: Literal["cambio", "devolucion", "garantia"]
) -> str:
    """Calcula si un caso está dentro del plazo. Para cambio y devolucion usa la fecha de RECEPCIÓN del pedido; para garantia, la fecha de compra. Formato YYYY-MM-DD."""
    try:
        compra = datetime.strptime(fecha_compra, "%Y-%m-%d").date()
    except ValueError:
        return "FECHA_INVALIDA: usa el formato YYYY-MM-DD"
    dias = (date.today() - compra).days
    plazo = PLAZOS_DIAS[tipo_caso]
    if dias <= plazo:
        return f"ELEGIBLE: han pasado {dias} días y el plazo de {tipo_caso} es de {plazo} días."
    return f"FUERA_DE_PLAZO: han pasado {dias} días y el plazo de {tipo_caso} es de {plazo} días."


@tool
def crear_ticket(
    cliente: str, resumen: str, prioridad: Literal["alta", "media", "baja"]
) -> str:
    """Crea un ticket de soporte humano cuando el caso no se puede resolver
    automáticamente (fuera de plazo, ambiguo, reclamo). Prioridad: alta si hay
    daño o cliente molesto, media si es duda de política, baja si es consulta."""
    TICKETS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existe = TICKETS_PATH.exists()
    ticket_id = f"TCK-{datetime.now():%Y%m%d%H%M%S}"
    with TICKETS_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not existe:
            w.writerow(["id", "fecha", "cliente", "resumen", "prioridad"])
        w.writerow([ticket_id, datetime.now().isoformat(timespec="seconds"), cliente, resumen, prioridad])
    return f"TICKET_CREADO: {ticket_id} (prioridad {prioridad})"


TOOLS = [buscar_politicas, consultar_pedido, evaluar_elegibilidad, crear_ticket]