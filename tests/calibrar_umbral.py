import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.agent.tools import get_vectorstore

vs = get_vectorstore()
consultas = [
    "plazo para cambiar un reloj",
    "cómo limpio la correa de cuero",
    "cuánto demora el envío",
    "receta de pizza",
    "quién ganó el mundial de fútbol",
]
for q in consultas:
    scores = [round(s, 2) for _, s in vs.similarity_search_with_score(q, k=3)]
    print(f"{q!r:45} -> {scores}")