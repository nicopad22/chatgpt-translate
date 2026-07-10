import os
import uuid
import json
import time
import shutil
import threading
import tempfile
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import jwt
import httpx
import openai
from google.cloud import storage

# Unified OOXML translation module (copied into Docker image alongside this file)
import ooxml_translate

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "")
CLP_PER_WORD = 5  # ponytail: hard-coded rate, change here if pricing changes

# Supabase config
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://hbdgitevyptizksjewkz.supabase.co")
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("translate-api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI()

# Parse comma-separated origins, or fallback to wildcard
origins = [o.strip() for o in CORS_ORIGIN.split(",")] if CORS_ORIGIN else ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True if "*" not in origins else False,
    allow_methods=["*"],
    allow_headers=["*"],
)

gcs = storage.Client()
bucket = gcs.bucket(GCS_BUCKET) if GCS_BUCKET else None

ALLOWED_EXT = {".docx", ".xlsx", ".pptx"}
CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# ---------------------------------------------------------------------------
# HTTP clients for Supabase REST API (connection pooling)
# ---------------------------------------------------------------------------
_supabase_headers = {
    "apikey": SUPABASE_SERVICE_ROLE_KEY,
    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation",
}
_supabase_base_url = f"{SUPABASE_URL}/rest/v1"

# Async client for use in async endpoints
_async_http = httpx.AsyncClient(
    base_url=_supabase_base_url,
    headers=_supabase_headers,
    timeout=15.0,
)

# Sync client for use in background threads
_sync_http = httpx.Client(
    base_url=_supabase_base_url,
    headers=_supabase_headers,
    timeout=15.0,
)

# ---------------------------------------------------------------------------
# Auth — Supabase JWT verification
# ---------------------------------------------------------------------------
def _current_user(request: Request) -> str:
    """Extract and verify Supabase JWT from Authorization header.

    Returns the user UUID (``sub`` claim) or raises 401.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "No autorizado")
    token = auth[7:]
    try:
        payload = jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "exp"]},
        )
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Sesión expirada")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Token inválido")


# ---------------------------------------------------------------------------
# Supabase DB helpers — async (for endpoints)
# ---------------------------------------------------------------------------
async def _get_usuario(user_uuid: str) -> dict | None:
    """Lookup full usuario row by user_uuid. Returns dict or None."""
    resp = await _async_http.get(
        "/usuarios",
        params={"user_uuid": f"eq.{user_uuid}", "select": "*"},
    )
    resp.raise_for_status()
    rows = resp.json()
    return rows[0] if rows else None


async def _check_rate_limit(usuario_id: int, account_type: int) -> bool:
    """Return True if the user is allowed to translate.

    Free-tier users (account_type == 0) are limited to 1 translation job
    per calendar day (UTC).
    """
    if account_type != 0:
        return True
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()
    resp = await _async_http.get(
        "/traducciones",
        params={
            "id_usuario": f"eq.{usuario_id}",
            "created_at": f"gte.{today_start}",
            "select": "id",
        },
        headers={**_supabase_headers, "Prefer": "count=exact"},
    )
    resp.raise_for_status()
    count = int(resp.headers.get("content-range", "*/0").split("/")[-1])
    return count < 1


async def _insert_traduccion(usuario_id: int, num_archivos: int, idioma: str) -> int:
    """Insert a row into traducciones and return its id."""
    resp = await _async_http.post(
        "/traducciones",
        json={
            "id_usuario": usuario_id,
            "numero_archivos": num_archivos,
            "idioma": idioma,
            "costo": 0,
            "moneda_costo": "CLP",
        },
    )
    resp.raise_for_status()
    return resp.json()[0]["id"]


async def _insert_archivos(
    traduccion_id: int, usuario_id: int, file_infos: list, job_id: str
) -> None:
    """Insert archivo rows with GCS original paths; nuevo is null."""
    rows = [
        {
            "id_traduccion": traduccion_id,
            "id_usuario": usuario_id,
            "original": f"jobs/{job_id}/input/{fi['name']}",
            "nuevo": None,
        }
        for fi in file_infos
    ]
    resp = await _async_http.post("/archivos", json=rows)
    resp.raise_for_status()


async def _update_traduccion_cost(
    traduccion_id: int, costo: float, moneda: str
) -> None:
    """Update cost columns on a traduccion row."""
    resp = await _async_http.patch(
        "/traducciones",
        params={"id": f"eq.{traduccion_id}"},
        json={"costo": costo, "moneda_costo": moneda},
    )
    resp.raise_for_status()


async def _update_archivo_nuevo(
    traduccion_id: int, original_name: str, nuevo_path: str
) -> None:
    """Update the nuevo column for a specific archivo after translation."""
    resp = await _async_http.patch(
        "/archivos",
        params={
            "id_traduccion": f"eq.{traduccion_id}",
            "original": f"like.*/{original_name}",
        },
        json={"nuevo": nuevo_path},
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# Supabase DB helpers — sync (for background thread)
# ---------------------------------------------------------------------------
def _sync_update_archivo_nuevo(
    traduccion_id: int, original_name: str, nuevo_path: str
) -> None:
    """Sync version: update the nuevo column after translation."""
    resp = _sync_http.patch(
        "/archivos",
        params={
            "id_traduccion": f"eq.{traduccion_id}",
            "original": f"like.*/{original_name}",
        },
        json={"nuevo": nuevo_path},
    )
    resp.raise_for_status()


def _sync_update_traduccion_cost(
    traduccion_id: int, costo: float, moneda: str
) -> None:
    """Sync version: update cost columns on a traduccion row."""
    resp = _sync_http.patch(
        "/traducciones",
        params={"id": f"eq.{traduccion_id}"},
        json={"costo": costo, "moneda_costo": moneda},
    )
    resp.raise_for_status()


# ---------------------------------------------------------------------------
# GCS helpers
# ---------------------------------------------------------------------------
def _save_status(job_id: str, status: dict):
    # Serialize to string synchronously to prevent dict mutation issues in the background thread
    status_str = json.dumps(status)
    def upload():
        try:
            blob = bucket.blob(f"jobs/{job_id}/status.json")
            blob.upload_from_string(status_str, content_type="application/json")
        except Exception as e:
            log.error(f"Error saving status to GCS: {e}")
    threading.Thread(target=upload, daemon=True).start()


def _load_status(job_id: str) -> dict | None:
    blob = bucket.blob(f"jobs/{job_id}/status.json")
    try:
        return json.loads(blob.download_as_text())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/jobs")
async def create_job(
    language: str = Form(...),
    files: list[UploadFile] = File(...),
    user_uuid: str = Depends(_current_user),
):
    if language not in ("es", "en"):
        raise HTTPException(400, "Idioma debe ser 'es' o 'en'")

    # --- Supabase user lookup ---
    usuario = await _get_usuario(user_uuid)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")
    if not usuario.get("is_active", False):
        raise HTTPException(403, "Cuenta desactivada")

    usuario_id = usuario["id"]
    account_type = usuario["account_type"]

    # --- Rate limit for free tier ---
    if not await _check_rate_limit(usuario_id, account_type):
        raise HTTPException(
            429,
            "Has alcanzado el límite diario de traducciones. "
            "Actualiza tu plan para continuar.",
        )

    job_id = uuid.uuid4().hex[:12]

    # Validate extensions
    file_infos = []
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXT:
            raise HTTPException(400, f"Tipo de archivo no soportado: {ext}")
        file_infos.append({"name": f.filename, "ext": ext})

    # Upload originals to GCS
    for f in files:
        blob = bucket.blob(f"jobs/{job_id}/input/{f.filename}")
        content = await f.read()
        blob.upload_from_string(content, content_type=f.content_type)

    # --- Insert DB records ---
    traduccion_id = await _insert_traduccion(usuario_id, len(file_infos), language)
    await _insert_archivos(traduccion_id, usuario_id, file_infos, job_id)

    status = {
        "status": "procesando",
        "language": language,
        "user_uuid": user_uuid,
        "files": [fi["name"] for fi in file_infos],
        "translated_files": [],
        "word_count": 0,
        "cost_clp": 0,
        "current_file": "",
        "files_done": 0,
        "files_total": len(file_infos),
        "words_translated": 0,
        "words_total": 0,
    }
    _save_status(job_id, status)

    t = threading.Thread(
        target=_translate_job,
        args=(job_id, file_infos, language, user_uuid, traduccion_id),
        daemon=True,
    )
    t.start()

    return {"job_id": job_id, "status": "procesando"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str, user: str = Depends(_current_user)):
    status = _load_status(job_id)
    if not status:
        raise HTTPException(404, "Trabajo no encontrado")
    return status


@app.get("/jobs/{job_id}/download/{filename:path}")
async def download_file(job_id: str, filename: str, user: str = Depends(_current_user)):
    blob = bucket.blob(f"jobs/{job_id}/output/{filename}")
    if not blob.exists():
        raise HTTPException(404, "Archivo no encontrado")
    content = blob.download_as_bytes()
    ext = os.path.splitext(filename)[1].lower()
    # ponytail: streams entire file through memory. Ceiling: ~32 MB
    # (Cloud Run request-body limit). Upgrade path: GCS signed URLs.
    return Response(
        content=content,
        media_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Account & History endpoints
# ---------------------------------------------------------------------------
@app.get("/account")
async def get_account(user_uuid: str = Depends(_current_user)):
    """Return account info and aggregate translation stats."""
    usuario = await _get_usuario(user_uuid)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")

    usuario_id = usuario["id"]

    # Fetch aggregate stats from traducciones
    resp = await _async_http.get(
        "/traducciones",
        params={
            "id_usuario": f"eq.{usuario_id}",
            "select": "id,costo",
        },
    )
    resp.raise_for_status()
    traducciones = resp.json()

    translations_count = len(traducciones)
    total_cost = sum(t.get("costo", 0) or 0 for t in traducciones)

    return {
        "account_type": usuario["account_type"],
        "created_at": usuario["created_at"],
        "is_active": usuario["is_active"],
        "translations_count": translations_count,
        "total_cost": total_cost,
    }


@app.get("/history")
async def get_history(user_uuid: str = Depends(_current_user)):
    """Return the user's translation history (last 50) with associated files."""
    usuario = await _get_usuario(user_uuid)
    if not usuario:
        raise HTTPException(404, "Usuario no encontrado")

    usuario_id = usuario["id"]

    # Fetch last 50 traducciones with embedded archivos via PostgREST resource embedding
    resp = await _async_http.get(
        "/traducciones",
        params={
            "id_usuario": f"eq.{usuario_id}",
            "select": "id,created_at,numero_archivos,costo,moneda_costo,idioma,archivos(id,original,nuevo,created_at)",
            "order": "created_at.desc",
            "limit": "50",
        },
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Background translation
# ---------------------------------------------------------------------------
def _translate_job(
    job_id: str,
    file_infos: list,
    language: str,
    user_uuid: str,
    traduccion_id: int,
):
    tmp_dir = os.path.join(tempfile.gettempdir(), job_id)
    os.makedirs(tmp_dir, exist_ok=True)
    root = tmp_dir + "/"

    total_words = 0
    words_translated = 0
    words_total = 0
    translated_files = []
    client = openai.OpenAI(api_key=OPENAI_API_KEY)

    # Initialize the local status dictionary
    status = {
        "status": "procesando",
        "language": language,
        "user_uuid": user_uuid,
        "files": [fi["name"] for fi in file_infos],
        "translated_files": [],
        "word_count": 0,
        "cost_clp": 0,
        "current_file": "",
        "files_done": 0,
        "files_total": len(file_infos),
        "words_translated": 0,
        "words_total": 0,
    }

    last_gcs_save = time.time()

    def llm_call(system_prompt, user_prompt):
        """LLM call with retry, used by ooxml_translate internally."""
        nonlocal total_words
        last_err = None
        for attempt in range(2):
            try:
                result = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.7,
                )
                translated = result.choices[0].message.content
                total_words += len(translated.split())
                return translated
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(2)
        raise last_err

    def on_progress(words):
        """Called by ooxml_translate after each translated element."""
        nonlocal words_translated, last_gcs_save
        words_translated += words
        status["words_translated"] = words_translated
        current_time = time.time()
        if current_time - last_gcs_save > 5:
            _save_status(job_id, status)
            last_gcs_save = current_time

    try:
        # 1. Download all files to tmp_dir and pre-calculate words_total
        for fi in file_infos:
            name, ext = fi["name"], fi["ext"]
            in_path = os.path.join(tmp_dir, name)
            try:
                bucket.blob(f"jobs/{job_id}/input/{name}").download_to_filename(in_path)
                words_total += ooxml_translate.get_word_count(in_path)
            except Exception as ex:
                log.error(f"Error downloading or counting words for {name}: {ex}", exc_info=True)

        status["words_total"] = words_total
        _save_status(job_id, status)

        # 2. Iterate and translate files (they are already downloaded)
        for i, fi in enumerate(file_infos):
            name, ext = fi["name"], fi["ext"]
            in_path = os.path.join(tmp_dir, name)

            # Update progress metadata
            status["current_file"] = name
            status["files_done"] = i
            _save_status(job_id, status)

            # Translate (reads input zip, writes new output zip)
            out_name = os.path.splitext(name)[0] + " [TRADUCIDO]" + ext
            out_path = os.path.join(tmp_dir, out_name)
            ooxml_translate.translate_file(in_path, out_path, language, llm_call, on_progress)

            # Upload result to GCS
            gcs_output_path = f"jobs/{job_id}/output/{out_name}"
            bucket.blob(gcs_output_path).upload_from_filename(out_path)
            translated_files.append(out_name)

            # Update archivo's nuevo column in Supabase
            try:
                _sync_update_archivo_nuevo(traduccion_id, name, gcs_output_path)
            except Exception as db_err:
                log.error(f"Error updating archivo nuevo for {name}: {db_err}")

        # Final status
        cost = total_words * CLP_PER_WORD
        final = {
            "status": "listo",
            "language": language,
            "user_uuid": user_uuid,
            "files": [fi["name"] for fi in file_infos],
            "translated_files": translated_files,
            "word_count": total_words,
            "cost_clp": cost,
            "files_done": len(file_infos),
            "files_total": len(file_infos),
            "current_file": "",
            "words_translated": words_total,
            "words_total": words_total,
        }
        _save_status(job_id, final)

        # Update translation cost in Supabase
        try:
            _sync_update_traduccion_cost(traduccion_id, cost, "CLP")
        except Exception as db_err:
            log.error(f"Error updating traduccion cost: {db_err}")

        log.info(
            "[TRADUCCIÓN] usuario: %s | archivos: %d | idioma: %s | "
            "palabras: %s | costo: $%s CLP",
            user_uuid,
            len(file_infos),
            language,
            f"{total_words:,}".replace(",", "."),
            f"{cost:,}".replace(",", "."),
        )

    except Exception as e:
        status["status"] = "error"
        status["error"] = str(e)
        _save_status(job_id, status)
        log.error("[ERROR] job %s: %s", job_id, e, exc_info=True)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
