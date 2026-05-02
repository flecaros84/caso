# Talent RAG - Evaluador por Competencias

Aplicación sencilla y modular para analizar anuncios laborales, deducir competencias requeridas, evaluar CV de candidatos y generar una terna recomendada con ranking explicable por evidencia documental.

## Enfoque

- Frontend web simple en HTML, CSS y JavaScript.
- Backend en FastAPI.
- Procesamiento de PDF e imágenes.
- Extracción dinámica de competencias desde cualquier anuncio laboral.
- RAG sobre CV mediante embeddings o fallback TF-IDF.
- Evaluación por evidencia con GitHub Models cuando está habilitado, o fallback local conservador.
- Ranking ponderado por competencias con umbrales configurables.
- Informe JSON explicable.

## Estructura

```text
app/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── services/
│   │       ├── competency_service.py
│   │       ├── evaluator_service.py
│   │       ├── file_service.py
│   │       ├── llm_client.py
│   │       ├── rag_service.py
│   │       ├── ranking_service.py
│   │       └── text_extractor.py
│   ├── requirements.txt
│   └── .env.example
└── frontend/
    ├── index.html
    ├── styles.css
    └── app.js
```

## Instalación

Desde la carpeta `caso/app/backend`:

```bash
python -m venv .venv
source .venv/bin/activate      # Linux/Mac
# .\.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements.txt
```

## Ejecución

```bash
uvicorn app.main:app --reload --port 8000
```

Luego abrir:

```text
http://localhost:8000
```

## Uso con modelos libres vía GitHub Models

El sistema funciona sin LLM externo usando extracción heurística. Para activar generación con GitHub Models, crea un archivo `.env` basado en `.env.example`:

```env
USE_LLM=true
GITHUB_TOKEN=tu_token
GITHUB_MODEL=openai/gpt-4o-mini
GITHUB_MODELS_ENDPOINT=https://models.inference.ai.azure.com/chat/completions
RECOMMENDED_THRESHOLD=75
CONSIDERABLE_THRESHOLD=55
```

Puedes cambiar `GITHUB_MODEL` por un modelo disponible en GitHub Models, por ejemplo modelos abiertos tipo Llama, Mistral o Phi cuando estén habilitados en tu cuenta.

## Nota ética

El sistema entrega una preselección documental. No debe reemplazar la decisión humana ni utilizar atributos sensibles como edad, género, fotografía, nacionalidad, estado civil o datos familiares.


## Indicador de modelo IA

El frontend consulta `GET /api/llm/status` para mostrar si la aplicación está usando GitHub Models o si está funcionando en modo local.

Variables relevantes en `app/backend/.env`:

```env
USE_LLM=true
GITHUB_TOKEN=tu_token
GITHUB_MODEL=openai/gpt-4o-mini
GITHUB_MODELS_ENDPOINT=https://models.inference.ai.azure.com/chat/completions
```

El token nunca se expone al frontend; solo se muestra si está configurado o no.

## Reportes locales

Cada análisis genera automáticamente dos archivos en:

```text
app/backend/outputs/reports/
```

Se guardan dos formatos:

- `.md`: reporte legible en Markdown, útil para entregar o revisar.
- `.json`: salida técnica completa, útil como evidencia de ejecución o trazabilidad.

Desde el frontend, al finalizar el análisis aparece la sección **Reporte local generado** con enlaces para abrir ambos archivos.


## Bitácora completa de ejecución

La pantalla de progreso muestra la trazabilidad completa del análisis en orden cronológico.
El backend conserva todos los eventos del trabajo en memoria mientras el servidor esté activo y expone el conteo mediante `event_count`.
Esto permite verificar qué pasos usaron LLM, fallback local o errores.
