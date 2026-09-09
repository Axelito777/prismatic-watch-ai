# Prismatic Watch AI

Chatbot de atención al cliente con RAG (Retrieval-Augmented Generation) para Prismatic Watch, una tienda de relojes en línea. Responde preguntas sobre garantía, cambios, envíos y cuidado de relojes, basándose exclusivamente en documentos reales — no inventa información.

Proyecto desarrollado para la Evaluación Parcial N°1 de ISY0101 - Ingeniería de Soluciones con IA.

## Arquitectura

RAG con dos fuentes de datos (interna: políticas de la tienda; externa: guía de cuidado de relojes), embeddings locales multilingües, base vectorial Chroma, y generación con Gemini 3.6 Flash vía LangChain.

## Requisitos

- Python 3.10+
- Una API key gratuita de Google Gemini: https://aistudio.google.com/apikey

## Instalación

```fish
git clone https://github.com/Axelito777/prismatic-watch-ai.git
cd prismatic-watch-ai
python3 -m venv venv
source venv/bin/activate.fish
pip install -r requirements.txt
```

## Configuración

Crea un archivo `.env` en la raíz con tu API key:

GOOGLE_API_KEY=tu_api_key_aqui

## Ejecución

1. Generar la base vectorial (solo la primera vez, o si cambian los documentos en `data/policies/`):

```fish
python src/ingest.py
```

2. Ejecutar el chatbot en modo interactivo:

```fish
python src/chatbot.py
```

Escribe tus preguntas en la consola. Escribe `salir` para terminar.

## Estructura del proyecto

prismatic-watch-ai/
├── data/
│ └── policies/ # Documentos fuente (políticas internas + guía externa de cuidado)
├── src/
│ ├── ingest.py # Carga documentos, genera embeddings, crea la base vectorial
│ ├── chatbot.py # Chatbot interactivo con RAG
│ └── debug_retrieval.py # Script de diagnóstico de recuperación semántica
├── requirements.txt
├── .env # No incluido en el repo (contiene la API key)
└── README.md

## Integrantes

- Axel Subiabre
- Matias Molina