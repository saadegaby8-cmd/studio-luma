"""
Studio Luma — App standalone (corre en la raíz "/") con login por Google.

Railway:  Procfile ->  web: uvicorn main:app --host 0.0.0.0 --port $PORT

Variables de entorno en Railway (Settings → Variables):
  - REDIS_URL             -> Redis del proyecto
  - GEMINI_API_KEY        -> API key de Google para generar imágenes
  - GOOGLE_CLIENT_ID      -> del OAuth Client (login + Drive)
  - GOOGLE_CLIENT_SECRET  -> del OAuth Client
  - SESSION_SECRET        -> cualquier texto largo y secreto (firma las sesiones)
  - AUTH_REDIRECT_URI     -> (opcional) https://TU-DOMINIO/auth/callback
  - ALLOWED_EMAILS        -> (opcional) mails permitidos separados por coma. Vacío = abierto.

El login usa un SOLO permiso de Google que incluye identidad + Drive: cada
usuario guarda sus imágenes en SU propio Drive (carpeta "Studio Luma").
"""

import os
import time
import traceback

os.environ.setdefault("IMAGENES_PREFIX", "")  # corre en la raíz "/"

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402

from imagenes_ia import (router as studio_router, VERSION, session_sub_from_request, kv,  # noqa: E402
                         set_current_sub, _pfx)
from videos_luma import router as videos_router, VERSION as VERSION_VIDEOS  # noqa: E402
from personajes import router as personajes_router, VERSION as VERSION_PERSONAJES  # noqa: E402
from reels import router as reels_router, VERSION as VERSION_REELS  # noqa: E402
from comerciales import router as comerciales_router, VERSION as VERSION_COMERCIALES  # noqa: E402

app = FastAPI(title="Studio Luma", version=VERSION)

# Rutas que NO requieren login
_OPEN_PREFIXES = ("/auth", "/health", "/favicon.ico")


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    path = request.url.path
    if path == "/health" or any(path.startswith(p) for p in _OPEN_PREFIXES):
        return await call_next(request)
    sub = session_sub_from_request(request)
    if not sub:
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return RedirectResponse("/auth/login")
        return JSONResponse({"error": "login_requerido"}, status_code=401)
    return await call_next(request)


@app.exception_handler(Exception)
async def _error_interno(request: Request, exc: Exception):
    """Un error no previsto en un endpoint llegaba a la pantalla como un "500" pelado
    (FastAPI responde texto plano y el front no encuentra 'detail'). Acá se imprime el
    traceback en el log de Railway, se guarda para "Ver diagnóstico" y se responde con
    un detalle legible."""
    ruta = f"{request.method} {request.url.path}"
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print(f"[studio-luma] ERROR 500 en {ruta}: {type(exc).__name__}: {exc}\n{tb}", flush=True)
    try:
        set_current_sub(session_sub_from_request(request))
        await kv.set(_pfx() + "lasterror",
                     {"ruta": ruta, "tipo": type(exc).__name__, "mensaje": str(exc)[:500],
                      "traceback": tb[-3000:], "ts": int(time.time())}, ttl=7200)
    except Exception:
        pass
    return JSONResponse(
        {"detail": f"Error interno del servidor en {ruta}: {type(exc).__name__}: "
                   f"{str(exc)[:300]}. Abrí 'Ver diagnóstico' (sección error_servidor) y "
                   "pasame lo que dice."},
        status_code=500)


app.include_router(studio_router)
app.include_router(videos_router)   # videos de producto (vidriera blanca) en /videos
app.include_router(personajes_router)   # personajes digitales (tu persona digital) en /personajes
app.include_router(reels_router)        # reels de Instagram con un personaje en /reels
app.include_router(comerciales_router)  # videos comerciales estilo campaña (Kling / foto por foto) en /comerciales


@app.get("/health")
def health():
    # kv: "redis" = bien; "memoria" = SIN Redis -> las fotos de los trabajos viven en la
    # RAM del server y con varios sets seguidos se puede quedar sin memoria (se reinicia).
    return JSONResponse({"ok": True, "app": "Studio Luma", "version": VERSION,
                         "version_videos": VERSION_VIDEOS,
                         "version_personajes": VERSION_PERSONAJES, "version_reels": VERSION_REELS,
                         "version_comerciales": VERSION_COMERCIALES, "kv": kv.backend})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"  Studio Luma  ->  http://localhost:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port)
