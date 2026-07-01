"""
Studio Luma — App standalone (corre en la raíz "/").

Este main.py es el "cascarón" que levanta la app de generación de imágenes
(imagenes_ia.py) como un sitio propio, separado de ML×TN Sync.

Cómo corre en Railway:
  - Procfile:  web: uvicorn main:app --host 0.0.0.0 --port $PORT

Variables de entorno a cargar en Railway (Settings → Variables):
  - REDIS_URL        -> la conexión del Redis NUEVO de este proyecto
  - GEMINI_API_KEY   -> tu API key de Google (idealmente una NUEVA, separada)
  - (opcionales de Drive, si vas a usar la galería) las mismas que ya conocés.

Nota: acá NO hay login todavía. Cualquiera con el link puede usarlo.
Perfecto para probar con tus papás. El login/cobro es la etapa siguiente.
"""

import os

# Studio Luma corre en la raíz "/". Debe setearse ANTES de importar el módulo,
# porque el prefijo se lee al importar imagenes_ia.
os.environ.setdefault("IMAGENES_PREFIX", "")

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from imagenes_ia import router as studio_router, VERSION  # noqa: E402

app = FastAPI(title="Studio Luma", version=VERSION)
app.include_router(studio_router)


@app.get("/health")
def health():
    return JSONResponse({"ok": True, "app": "Studio Luma", "version": VERSION})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    print(f"  Studio Luma  ->  http://localhost:{port}/")
    uvicorn.run(app, host="0.0.0.0", port=port)
