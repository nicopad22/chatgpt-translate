# Traductor de Documentos

Traduce archivos `.docx`, `.xlsx` y `.pptx` entre inglés y español usando OpenAI (gpt-4o-mini).

## Uso como CLI (local)

1. Crea `TOKEN.txt` en la raíz del proyecto con tu API key de OpenAI.
2. Ejecuta: `python translate_openAI.py`
3. Selecciona idioma de salida (`es` / `en`).

Los archivos se procesan desde la ruta `root` configurada en `translate_openAI.py`.

## Uso como Web App

La app web se divide en dos servicios:

| Servicio | Plataforma | Directorio |
|----------|------------|------------|
| Frontend (HTML/CSS/JS estático) | Vercel | `frontend/` |
| Backend (FastAPI + Python) | Google Cloud Run | `api/` + `Dockerfile` |

### Requisitos

- Cuenta de Google Cloud con un proyecto activo
- `gcloud` CLI instalado
- Cuenta de Vercel
- API key de OpenAI

### 1. Crear bucket de GCS

```bash
gsutil mb -l us-central1 gs://translate-app-files-TUSUFIJO
gsutil lifecycle set -c '{"rule":[{"action":{"type":"Delete"},"condition":{"age":1}}]}' gs://translate-app-files-TUSUFIJO
```

### 2. Deploy del backend (Cloud Run)

Desde la raíz del repositorio:

```bash
gcloud run deploy translate-api \
  --project project-automation-498114 \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --no-cpu-throttling \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 3600 \
  --memory 512Mi \
  --set-env-vars "OPENAI_API_KEY=sk-...,APP_PASSWORD=tu-contraseña,APP_SECRET=$(openssl rand -hex 32),GCS_BUCKET=translate-app-files-TUSUFIJO,CORS_ORIGIN=https://tu-app.vercel.app"
```

### 3. Deploy del frontend (Vercel)

1. Edita `frontend/app.js` y establece `API_URL` con la URL de Cloud Run.
2. Desde el directorio `frontend/`:

```bash
cd frontend
npx vercel
```

### 4. Actualizar CORS

Después del primer deploy de Vercel, actualiza `CORS_ORIGIN` en Cloud Run con la URL de Vercel.

## Variables de entorno (Cloud Run)

| Variable | Descripción |
|----------|-------------|
| `OPENAI_API_KEY` | API key de OpenAI |
| `APP_PASSWORD` | Contraseña compartida para login |
| `APP_SECRET` | Clave secreta para firmar tokens (genera con `openssl rand -hex 32`) |
| `GCS_BUCKET` | Nombre del bucket de GCS |
| `CORS_ORIGIN` | URL del frontend en Vercel |

## Estado del proyecto

- [x] Traducción de Word (.docx)
- [x] Traducción de Excel (.xlsx)
- [x] Traducción de PowerPoint (.pptx)
- [ ] Traducción de PDF
- [x] CLI local
- [x] App web (Vercel + Cloud Run)