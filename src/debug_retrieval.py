from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
vectorstore = Chroma(persist_directory="chroma_db", embedding_function=embeddings)

print("Total de fragmentos en la colección:", vectorstore._collection.count())

print("\n=== TODOS los fragmentos guardados ===")
todos = vectorstore._collection.get()
for i, doc in enumerate(todos["documents"]):
    print(f"\n--- Fragmento {i+1} ---")
    print(doc[:150], "...")

print("\n=== Búsqueda con score de similitud ===")
resultados = vectorstore.similarity_search_with_score("¿Cuánto dura la garantía de los relojes?", k=8)
for doc, score in resultados:
    print(f"\nScore: {score:.4f}")
    print(doc.page_content[:100], "...")