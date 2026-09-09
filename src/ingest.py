from langchain_community.document_loaders import DirectoryLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma

DATA_PATH = "data/policies"
CHROMA_PATH = "chroma_db"


def cargar_documentos():
    loader = DirectoryLoader(DATA_PATH, glob="**/*.md", loader_cls=TextLoader)
    return loader.load()


def dividir_documentos(documentos):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )
    return splitter.split_documents(documentos)


def crear_vectorstore(fragmentos):
    embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    vectorstore = Chroma.from_documents(
        documents=fragmentos,
        embedding=embeddings,
        persist_directory=CHROMA_PATH,
    )
    return vectorstore


if __name__ == "__main__":
    documentos = cargar_documentos()
    print(f"Documentos cargados: {len(documentos)}")

    fragmentos = dividir_documentos(documentos)
    print(f"Fragmentos generados: {len(fragmentos)}")

    crear_vectorstore(fragmentos)
    print(f"Base vectorial guardada en '{CHROMA_PATH}'")