import os
import uuid
import hmac
import hashlib
import json
import time
import shutil
import threading
import tempfile
import logging
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import openai
from google.cloud import storage

# Unified OOXML translation module (copied into Docker image alongside this file)
import ooxml_translate

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
APP_PASSWORD = os.environ["APP_PASSWORD"]
APP_SECRET = os.environ["APP_SECRET"]
CORS_ORIGIN = os.environ.get("CORS_ORIGIN", "*")
GCS_BUCKET = os.environ["GCS_BUCKET"]
CLP_PER_WORD = 5  # ponytail: hard-coded rate, change here if pricing changes

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
bucket = gcs.bucket(GCS_BUCKET)

ALLOWED_EXT = {".docx", ".xlsx", ".pptx"}
CONTENT_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# ---------------------------------------------------------------------------
# Auth — HMAC tokens from stdlib, no PyJWT
# ---------------------------------------------------------------------------
TOKEN_TTL = 86400  # 24 hours


def _make_token(username: str) -> str:
    expiry = str(int(time.time()) + TOKEN_TTL)
    payload = f"{username}:{expiry}"
    sig = hmac.new(APP_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def _verify_token(token: str) -> str:
    """Returns username or raises 401."""
    parts = token.rsplit(":", 2)
    if len(parts) != 3:
        raise HTTPException(401, "Token inválido")
    username, expiry, sig = parts
    expected = hmac.new(
        APP_SECRET.encode(), f"{username}:{expiry}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise HTTPException(401, "Token inválido")
    if int(expiry) < time.time():
        raise HTTPException(401, "Sesión expirada")
    return username


def _current_user(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "No autorizado")
    return _verify_token(auth[7:])


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


@app.post("/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    if password != APP_PASSWORD:
        raise HTTPException(401, "Contraseña incorrecta")
    name = username.strip()
    if not name:
        raise HTTPException(400, "Ingresa un nombre de usuario")
    return {"token": _make_token(name), "username": name}


@app.post("/jobs")
async def create_job(
    language: str = Form(...),
    files: list[UploadFile] = File(...),
    user: str = Depends(_current_user),
):
    if language not in ("es", "en"):
        raise HTTPException(400, "Idioma debe ser 'es' o 'en'")

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

    status = {
        "status": "procesando",
        "language": language,
        "username": user,
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
        target=_translate_job, args=(job_id, file_infos, language, user), daemon=True
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
# Background translation
# ---------------------------------------------------------------------------
def _translate_job(job_id: str, file_infos: list, language: str, username: str):
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
        "username": username,
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
            bucket.blob(f"jobs/{job_id}/output/{out_name}").upload_from_filename(
                out_path
            )
            translated_files.append(out_name)

        # Final status
        cost = total_words * CLP_PER_WORD
        final = {
            "status": "listo",
            "language": language,
            "username": username,
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

        log.info(
            "[TRADUCCIÓN] usuario: %s | archivos: %d | idioma: %s | "
            "palabras: %s | costo: $%s CLP",
            username,
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
