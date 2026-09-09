from dotenv import load_dotenv
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain.chains import create_retrieval_chain
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

CHROMA_PATH = "chroma_db"

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
vectorstore = Chroma(persist_directory=CHROMA_PATH, embedding_function=embeddings)
retriever = vectorstore.as_retriever(search_kwargs={"k": 3})

llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash", temperature=0.3)

prompt = ChatPromptTemplate.from_messages([
    ("system", """Eres el asistente de atención al cliente de Prismatic Watch, una tienda de relojes.
Responde SOLO con base en el siguiente contexto recuperado de nuestros documentos de políticas y guías de cuidado.
Si la información no está en el contexto, dilo claramente y sugiere contactar a soporte@prismaticwatch.cl — no inventes datos.
Responde en español, de forma breve, clara y amable.

Contexto:
{context}"""),
    ("human", "{input}"),
])

document_chain = create_stuff_documents_chain(llm, prompt)
retrieval_chain = create_retrieval_chain(retriever, document_chain)


def preguntar(pregunta: str):
    respuesta = retrieval_chain.invoke({"input": pregunta})
    return respuesta["answer"]


if __name__ == "__main__":
    print("=== Prismatic Watch AI — Asistente de atención al cliente ===")
    print("Escribe tu pregunta (o 'salir' para terminar)\n")

    while True:
        pregunta = input("Tú: ").strip()
        if pregunta.lower() in ("salir", "exit", "quit"):
            print("¡Hasta luego!")
            break
        if not pregunta:
            continue

        respuesta = preguntar(pregunta)
        print(f"\nAsistente: {respuesta}\n")