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

os.environ.setdefault("IMAGENES_PREFIX", "")  # corre en la raíz "/"

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, RedirectResponse  # noqa: E402

from imagenes_ia import router as studio_router, VERSION, session_sub_from_request  # noqa: E402

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


app.include_router(studio_router)


@app.get("/health")
def health():
    return JSONResponse({"ok": True, "app": "Studio Luma", "version": VERSION})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"  Studio Luma  ->  http://localhost:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port)
