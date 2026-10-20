from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI

load_dotenv()

llm = ChatGoogleGenerativeAI(model="gemini-3.6-flash")

respuesta = llm.invoke("Responde solo con: 'Prismatic Watch AI conectado correctamente'")
print(respuesta.content)