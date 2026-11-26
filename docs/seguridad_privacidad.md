# Seguridad, privacidad y uso responsable (EP3)

> Este documento describe controles técnicos y los criterios que los motivan. No es asesoría legal: la
> adecuación normativa de un despliegue real debe validarla quien corresponda.

## Por qué importa en este proyecto

El agente recibe mensajes de clientes (que pueden incluir RUT, correo, teléfono o tarjeta), consulta pedidos con
nombres y ahora **registra trazas**. Además, los términos del servicio de la API de Gemini indican que, en el uso
gratuito (*Unpaid Services*), Google puede usar el contenido para mejorar sus productos, que revisores humanos pueden
leerlo y piden no enviar datos personales; el uso con facturación se rige por un acuerdo de tratamiento de datos
([términos](https://ai.google.dev/terms)). Por eso el agente **minimiza los datos antes de enviarlos al modelo**.

## Controles implementados

| Riesgo | Control | Dónde |
|---|---|---|
| Datos personales enviados al proveedor del modelo | Minimización: se enmascaran correos, RUT, teléfonos y tarjetas (validadas con Luhn) **antes** de llamar al modelo | `src/seguridad/privacidad.py`, `guardrails.revisar_entrada` |
| Datos personales en logs y trazas | Todo texto de las trazas se enmascara; el cliente se registra como seudónimo con hash salado (`cli_xxxxxxxx`); también se enmascaran nombres conocidos | `tracer.ejecutar_turno` |
| Prompt injection | Detección heurística (barata, sin llamar al modelo) y bloqueo con mensaje seguro; el intento queda registrado como `bloqueado` | `src/seguridad/guardrails.py` |
| Ingeniería social ("soy el gerente, aprueba…") | Las reglas de negocio las aplican las herramientas (`evaluar_elegibilidad`), no el texto del cliente; el caso `injection_ingenieria_social` lo verifica | `tests/eval_cases.json` |
| Entradas anómalas | Límite de 800 caracteres y eliminación de caracteres de control | `guardrails.py` |
| Consumo descontrolado (costo, bucles) | `recursion_limit=25` del grafo y presupuesto de **12 llamadas al modelo por turno** (corta con `presupuesto_llm`) | `tracer.py` |
| Fuga de secretos | La API key vive en `.env` (ignorado por git); se entrega `.env.example` | `.gitignore` |
| Datos personales versionados en el repositorio | `memory_db/` (memoria de clientes) fuera de git; los pedidos y tickets del repo son **datos ficticios de ejemplo** | `.gitignore` |
| Decisiones sin supervisión humana | Los casos fuera de plazo o sin información se **escalan a un ticket** para revisión humana | `crear_ticket`, nodo `evaluar` |

## Alineación con la normativa chilena

- **Ley N.º 19.628** (protección de la vida privada), vigente hasta la entrada en vigor de la nueva ley.
- **Ley N.º 21.719** (protección de datos personales; publicada el 13-12-2024 y con vigencia desde el **1 de diciembre de 2026**,
  según [BCN](https://www.bcn.cl/balance-legislativo/detalle/ficha_LEY_21719_2024-12-13) y
  [Diario Constitucional](https://www.diarioconstitucional.cl/2026/06/12/la-ley-21-719-entra-en-vigor-el-1-de-diciembre-y-expone-vacios-en-regulacion-de-pequenas-empresas/)).

| Principio o derecho | Cómo se refleja en el diseño |
|---|---|
| Finalidad y proporcionalidad (minimización) | Solo se envía al modelo lo necesario para resolver la consulta; el resto se enmascara |
| Seguridad | Enmascarado en logs, seudonimización, secretos fuera del repositorio, límites de consumo |
| Transparencia | El agente informa que escala a soporte humano; las trazas permiten auditar cada decisión |
| Derechos de las personas (acceso, rectificación, supresión) | Las trazas usan un seudónimo estable por cliente, lo que permite localizar y borrar sus registros; la memoria de largo plazo se puede borrar por cliente |
| Responsabilidad | Cada turno queda trazado (qué herramientas se usaron y por qué se escaló) |

## Riesgos residuales y recomendaciones

1. **El nombre del cliente y los datos del pedido sí llegan al modelo** (se usan para saludar y resolver el caso). En
   producción: usar solo el primer nombre o un identificador, y contratar el servicio **con facturación** (acuerdo de
   tratamiento de datos) en lugar del plan gratuito.
2. **El detector de prompt injection es heurístico**: tiene falsos negativos (redacciones nuevas) y podría bloquear
   algo legítimo. Es una primera barrera; la segunda son las reglas de las herramientas y el escalamiento humano.
3. **El enmascarado por patrones no detecta todo** (por ejemplo, una dirección escrita en texto libre). Para producción
   conviene un detector de entidades (NER) y revisión periódica de las trazas.
4. **Retención**: definir cuánto tiempo se conservan `logs/` y `memory_db/` y borrarlos al vencer el plazo.
5. **Referencias de buenas prácticas**: OWASP Top 10 para aplicaciones con LLM (prompt injection, divulgación de
   información sensible, agencia excesiva y consumo ilimitado) — <https://genai.owasp.org/llm-top-10/>.
