# -*- coding: utf-8 -*-
"""
videos_luma.py — Videos de producto "vidriera blanca" (estilo Zara / Adidas)
============================================================================

Módulo STANDALONE de Studio Luma (mismo molde que imagenes_ia.py).

Qué hace
--------
De TU foto de la publicación (la que ya tiene a la modelo con la prenda puesta)
arma un video de catálogo: la MISMA modelo, la MISMA prenda, parada en un limbo
blanco infinito —sin paredes, sin esquinas, sin horizonte, sólo la sombra de
contacto en el piso— y la cámara va entrando hacia el detalle de la prenda.

Son dos etapas, y el orden importa:

1. CUADROS LLAVE (Nano Banana, el mismo motor de fotos de Luma). Cada toma del
   video se dibuja primero como FOTO: plano entero, 3/4, macro del detalle,
   espalda, hero. La primera toma es el ANCLA; las demás se generan mirando el
   ancla, así la cara, la luz y el blanco no cambian entre tomas.
2. MOVIMIENTO (Veo 3.1 o Wan/Seedance). Cada cuadro llave es el PRIMER FRAME
   literal del clip, y el prompt sólo describe el movimiento de cámara.

Por qué en dos etapas: los modelos de video no saben "poner" tu prenda sobre una
modelo. Si el clip arranca de un flat-lay, inventan la prenda y la modelo. Si
arranca de un cuadro llave que ya es exactamente lo que querés, sólo tienen que
moverlo — y eso lo hacen bien.

Cómo se engancha (main.py):

    from videos_luma import router as videos_router
    app.include_router(videos_router)

UI: GET /videos

Variables de entorno
--------------------
  GEMINI_API_KEY   (obligatoria)  -> cuadros llave + Veo + locución
  FAL_KEY          (opcional)     -> motores baratos Wan / Seedance
  VIDEOS_PREFIX    (opcional)     -> default "/videos"

Para unir los clips hace falta ffmpeg. En Railway alcanza con dejar
`imageio-ffmpeg` en requirements.txt (ya está); si además ponés la variable
NIXPACKS_PKGS = ffmpeg, usa el del sistema, que es más rápido.

Dependencias: fastapi, httpx, pillow, imageio-ffmpeg (+ lo que ya usa Luma)
"""

import asyncio
import base64
import json
import os
import re
import shutil
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse

# Todo lo que ya sabe Studio Luma: motor de imagen, ajustes, presupuesto,
# inspector de prenda y el aislamiento de datos por usuaria.
from imagenes_ia import (
    _compress_ref,
    _current_api_key,
    _img_part,
    _pfx,
    _pricing,
    _strip_data_url,
    budget_check,
    budget_record,
    gemini_generate,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
    verificar_prenda,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("VIDEOS_PREFIX", "/videos").rstrip("/")
VERSION = "1.0.0"   # subí este número cada vez que cambiamos el archivo

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")
FAL_BASE = "https://queue.fal.run"

# Motores de video. Veo entiende mejor la tela fina; Wan/Seedance salen mucho
# más baratos y alcanzan de sobra para un push-in sobre un cuadro ya resuelto.
VEO_MODELS = {
    "veo_lite": "veo-3.1-lite-generate-preview",
    "veo_fast": "veo-3.1-fast-generate-preview",
    "veo_standard": "veo-3.1-generate-preview",
}
FAL_MODELS = {
    "wan": os.getenv("FAL_WAN_MODEL", "wan/v2.6/image-to-video/flash"),
    "seedance": os.getenv("FAL_SEEDANCE_MODEL",
                          "fal-ai/bytedance/seedance/v1/lite/image-to-video"),
}

# US$ por segundo de video generado (editables por env si cambian los precios)
PRECIO_SEG = {
    "veo_lite": float(os.getenv("VIDEOS_PRECIO_VEO_LITE", "0.08")),
    "veo_fast": float(os.getenv("VIDEOS_PRECIO_VEO_FAST", "0.15")),
    "veo_standard": float(os.getenv("VIDEOS_PRECIO_VEO_STD", "0.40")),
    "wan": float(os.getenv("VIDEOS_PRECIO_WAN", "0.05")),
    "seedance": float(os.getenv("VIDEOS_PRECIO_SEEDANCE", "0.09")),
}
MOTOR_LABEL = {
    "veo_lite": "Veo 3.1 Lite", "veo_fast": "Veo 3.1 Fast",
    "veo_standard": "Veo 3.1", "wan": "Wan 2.6", "seedance": "Seedance",
}

COSTO_GUION = 0.01   # Gemini texto para guion + subtítulos (estimado)
COSTO_TTS = 0.02     # locución (estimado)

TEXT_MODEL = os.getenv("VIDEOS_TEXT_MODEL", "gemini-2.5-flash")
TTS_MODEL = os.getenv("VIDEOS_TTS_MODEL", "gemini-2.5-flash-preview-tts")
TTS_VOCES = {"femenina": "Kore", "masculina": "Puck"}

# /data es el volumen persistente de Railway. Sin volumen, /tmp: los videos
# sobreviven hasta el próximo deploy (los cuadros llave, no: se rehacen).
WORK_DIR = (Path("/data/videos_luma") if Path("/data").exists()
            else Path("/tmp/videos_luma"))
WORK_DIR.mkdir(parents=True, exist_ok=True)


def _musica_path() -> Path:
    """La cortina musical es de CADA cuenta. Con un archivo único compartido,
    la música que sube una usuaria se le colaba en los videos de las demás."""
    import hashlib
    marca = hashlib.sha1(_pfx().encode()).hexdigest()[:12]
    return WORK_DIR / f"musica_{marca}.mp3"

POLL_INTERVAL = 10          # seg entre polls de la operación de Veo
POLL_TIMEOUT = 8 * 60       # máx 8 min por clip
JOB_TTL = 7 * 24 * 3600     # 7 días en el KV
JOBS_INDICE = 40            # cuántos trabajos guarda el historial

MAX_TOMAS = 6
DURACIONES_OK = (4, 6, 8)

# Tareas de fondo: sin la referencia fuerte, el GC de Python puede matar el job
# a mitad de camino y el estado queda congelado en "generando" para siempre.
_BG: set = set()


def _spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


# ─────────────────────────────────────────────────────────────────────────────
# GRAMÁTICA DE TOMAS
# Es el corazón del look: qué se ve en cada toma (para el cuadro llave) y cómo
# se mueve la cámara (para el clip). El orden por defecto es el de las marcas:
# entero -> 3/4 -> detalle -> hero.
# ─────────────────────────────────────────────────────────────────────────────

TOMAS: Dict[str, Dict[str, Any]] = {
    "entero": {
        "label": "Plano entero",
        "ayuda": "La prenda completa, de la cabeza a los pies. Es la que abre.",
        "encuadre": (
            "plano entero de cuerpo completo, la modelo centrada y de frente, "
            "de la cabeza a los pies, con los pies y su sombra de contacto "
            "DENTRO del cuadro, aire arriba de la cabeza y abajo de los pies"),
        "pose": (
            "pose de catálogo relajada: peso sobre una pierna, hombros "
            "distendidos, brazos sueltos al costado del cuerpo, mirada a cámara"),
        "motion": (
            "Locked-off full-body wide shot. The camera pushes in very slowly "
            "(slow dolly-in, a few centimetres) while the model holds her "
            "relaxed catalogue pose, breathing naturally and shifting her "
            "weight almost imperceptibly."),
    },
    "tres_cuartos": {
        "label": "Medio 3/4",
        "ayuda": "Plano medio con giro suave: se lee el calce y la caída.",
        "encuadre": (
            "plano medio, de la mitad del muslo para arriba, la modelo en "
            "tres cuartos de perfil, la prenda ocupando el centro del cuadro"),
        "pose": (
            "torso girado en tres cuartos, una mano acomodándose apenas el "
            "pelo o cayendo natural, mentón levemente hacia el hombro"),
        "motion": (
            "Medium shot. The camera arcs slowly around the model, about "
            "fifteen degrees, while she turns her torso towards the lens; the "
            "fabric follows the movement with realistic weight and drape."),
    },
    "detalle": {
        "label": "Macro del detalle",
        "ayuda": "El zoom que hacen Zara y Adidas: costura, encaje, textura.",
        "encuadre": (
            "primerísimo plano del detalle más lindo de la prenda sobre el "
            "cuerpo (la costura, el encaje, el elástico, la textura del tejido "
            "o la terminación), la tela ocupando todo el cuadro, profundidad "
            "de campo corta con el fondo blanco desenfocado"),
        "pose": (
            "sólo se ve el fragmento del cuerpo con la prenda; no hace falta "
            "la cara"),
        "motion": (
            "Macro detail shot. Very slow dolly-in on the garment detail with "
            "shallow depth of field; the fabric moves a couple of millimetres "
            "as the body breathes. Nothing else in frame."),
    },
    "espalda": {
        "label": "De espalda",
        "ayuda": "Muestra la parte de atrás: el cierre, el cruce, el escote.",
        "encuadre": (
            "plano entero desde atrás, la modelo de espaldas a la cámara, se "
            "ve toda la parte trasera de la prenda"),
        "pose": (
            "de espaldas, cabeza apenas girada hacia el hombro, brazos sueltos"),
        "motion": (
            "The model stands with her back to the camera. Slow tilt up from "
            "the hem of the garment to the shoulders, ending on the back "
            "neckline. The model stays still, only breathing."),
    },
    "caminata": {
        "label": "Caminata",
        "ayuda": "La modelo camina hacia cámara. Muy Adidas.",
        "encuadre": (
            "plano entero frontal con la modelo dando un paso hacia la cámara, "
            "cuerpo completo en el cuadro"),
        "pose": (
            "en pleno paso, una pierna adelante, brazos acompañando el "
            "movimiento, actitud segura"),
        "motion": (
            "The model walks straight towards the camera at a calm, confident "
            "pace while the camera dollies back slightly to keep her full body "
            "in frame. Natural, grounded walk, the garment moving with her."),
    },
    "hero": {
        "label": "Hero final",
        "ayuda": "El cierre limpio y centrado, listo para el logo o el precio.",
        "encuadre": (
            "plano entero centrado y simétrico, la modelo quieta de frente, "
            "composición limpia con el tercio inferior del cuadro despejado"),
        "pose": (
            "quieta, de frente, postura elegante y erguida, mirada a cámara"),
        "motion": (
            "Hero shot. The camera pulls back a few centimetres and settles "
            "into a clean, centred, symmetrical composition. The model holds a "
            "still, elegant pose."),
    },
}

TOMAS_DEFAULT = ["entero", "tres_cuartos", "detalle", "hero"]


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUES DE PROMPT DEL CUADRO LLAVE
# ─────────────────────────────────────────────────────────────────────────────

# El fondo es TODO el look. Hay que nombrar lo que no va, una por una: si no se
# lo prohíbe, el modelo dibuja la esquina del estudio o un degradé gris y el
# video deja de parecer de marca.
FONDO_BLANCO = (
    "FONDO (lo más importante de esta toma): limbo / ciclorama INFINITO BLANCO "
    "PURO. Blanco parejo y liso de borde a borde del cuadro. NO hay paredes, NO "
    "hay esquinas, NO hay zócalo, NO hay línea de horizonte, NO se ve la unión "
    "entre el piso y la pared, NO hay degradé gris ni viñeteo, NO hay textura "
    "de tela, papel ni pared. NO hay muebles, plantas, props, cortinas, "
    "reflectores, trípodes ni equipos de estudio. NO hay otra persona.\n"
    "La ÚNICA sombra permitida es la sombra de CONTACTO en el piso, justo "
    "debajo de los pies (o debajo de la prenda): corta, suave, difuminada, gris "
    "clarito. El fondo detrás de la modelo queda limpio, sin sombra proyectada."
)

LUZ_ESTUDIO = (
    "LUZ: estudio de moda profesional. Softbox grande al frente y rebote "
    "lateral: luz pareja y envolvente, sin luces duras, sin sombras marcadas en "
    "la cara, sin brillos quemados sobre la tela. Blancos limpios (el blanco es "
    "blanco, no gris ni celeste ni amarillo) y colores de la prenda fieles."
)

CALIDAD_FOTO = (
    "CALIDAD: fotografía real de campaña, cámara full-frame, lente 85 mm, piel "
    "con textura y poros reales, pelo con hebras sueltas, tela con su trama y "
    "su caída real. Nada de aspecto 3D, render, CGI, piel plástica ni "
    "sobre-nitidez.\n"
    "PROHIBIDO: texto, letras, números, logos, marcas de agua, collage, varias "
    "fotos dentro de la imagen, bordes, marcos, franjas de color."
)


def _bloque_identidad(sujeto: str) -> str:
    """Lo que NO se puede tocar de la foto original."""
    if sujeto == "prenda":
        return (
            "IDENTIDAD DE LA PRENDA (no negociable): la prenda de esta toma es "
            "EXACTAMENTE la de las fotos reales: mismo diseño, mismo color, "
            "misma tela, mismos breteles, mismas costuras, misma estampa, "
            "mismos apliques y terminaciones. No la rediseñes, no le agregues "
            "ni le saques detalles, no le cambies el tono. Va sin persona: "
            "sostenida por un maniquí fantasma invisible, con el volumen del "
            "cuerpo pero sin cuerpo a la vista."
        )
    return (
        "IDENTIDAD (no negociable): la persona de esta toma es LA MISMA de la "
        "IMAGEN 1. Misma cara y mismos rasgos, mismo tono de piel, mismo pelo "
        "(color, largo, peinado), mismo cuerpo, misma altura, misma edad. No la "
        "reemplaces por otra modelo, no la rejuvenezcas ni la adelgaces, no le "
        "cambies el maquillaje ni el peinado.\n"
        "PRENDA (no negociable): es EXACTAMENTE la misma prenda: mismo diseño, "
        "mismo color, misma tela, mismos breteles, mismas costuras, misma "
        "estampa, mismos apliques y terminaciones. No la rediseñes, no le "
        "agregues ni le saques detalles, no le cambies el tono. Tampoco le "
        "cambies el calzado ni le sumes accesorios que no estén en la foto.\n"
        "Lo ÚNICO que cambia respecto de la foto original es el FONDO, la LUZ y "
        "el ENCUADRE/POSE que se piden acá abajo."
    )


def _prompt_cuadro(toma: str, req: Dict[str, Any], con_ancla: bool,
                   n_refs: int, correcciones: str = "") -> str:
    """Prompt del cuadro llave. `con_ancla`: la IMAGEN 1 es el cuadro ya
    generado (manda la modelo, la luz y el blanco); si no, es la foto real."""
    t = TOMAS[toma]
    sujeto = req.get("sujeto", "modelo")
    formato = req.get("formato", "9:16")
    producto = (req.get("producto") or "").strip()
    notas = (req.get("notas") or "").strip()

    if con_ancla:
        refs = (
            "IMAGEN 1 = la TOMA YA APROBADA de esta misma sesión: de ahí salen "
            "la modelo (cara, pelo, cuerpo), el fondo blanco, la luz y el "
            "color exacto de la prenda. Esta toma tiene que parecer de la MISMA "
            "sesión de fotos, sacada un segundo después.\n"
            f"IMÁGENES 2 a {n_refs} = las fotos REALES del producto: son la "
            "verdad absoluta del diseño y las terminaciones de la prenda. Si "
            "algo de la prenda no se lee bien en la IMAGEN 1, se copia de acá."
        )
    else:
        refs = (
            "IMAGEN 1 = la foto REAL de tu publicación: de ahí salen la modelo "
            "y la prenda, tal cual son."
            + (f"\nIMÁGENES 2 a {n_refs} = más fotos reales del mismo producto "
               "(otros ángulos y detalles): úsalas para completar el diseño de "
               "la prenda." if n_refs > 1 else "")
        )

    encabezado = (
        "TAREA: generá UNA sola foto de campaña de moda para un video de "
        "e-commerce, estilo tienda internacional (catálogo de fondo blanco).\n"
        "Es la misma modelo y la misma prenda de las fotos que te paso, "
        "llevadas a un estudio de fondo blanco infinito.\n"
    )
    if sujeto == "prenda":
        encabezado = (
            "TAREA: generá UNA sola foto de producto para un video de "
            "e-commerce, estilo catálogo de fondo blanco: la prenda real de las "
            "fotos, sola, sin persona.\n"
        )

    partes = [
        encabezado,
        refs,
        _bloque_identidad(sujeto),
        f"ENCUADRE DE ESTA TOMA: {t['encuadre']}.",
        f"POSE / ACTITUD: {t['pose']}.",
        FONDO_BLANCO,
        LUZ_ESTUDIO,
        f"FORMATO: {formato}, la imagen llena todo el cuadro (sin franjas, sin "
        "márgenes blancos agregados, sin bordes).",
        CALIDAD_FOTO,
    ]
    if producto:
        partes.insert(3, f"PRODUCTO: {producto}.")
    if notas:
        partes.append(f"INDICACIONES DE LA MARCA: {notas}.")
    if correcciones:
        partes.append(
            "CORRECCIONES DEL INSPECTOR (la toma anterior salió con estas "
            f"diferencias contra la prenda real, corregilas): {correcciones}")
    return "\n\n".join(partes)


# ─────────────────────────────────────────────────────────────────────────────
# PROMPT DEL CLIP (en inglés: los modelos de video rinden bastante mejor)
# ─────────────────────────────────────────────────────────────────────────────

SUFIJO_VIDEO = (
    " Photorealistic fashion campaign footage, full-frame camera, soft even "
    "studio light, accurate fabric physics with realistic weight and drape, "
    "true-to-life colors, subtle film grain, very slight handheld "
    "micro-movement. The background stays PURE WHITE and completely EMPTY for "
    "the whole clip: no walls, no corners, no horizon line, no floor-to-wall "
    "seam, no gray gradient, no props, no furniture, no studio equipment, no "
    "other people. The only shadow is the soft contact shadow under the feet. "
    "The face, the hair, the body and the garment stay IDENTICAL from the first "
    "frame to the last — same design, same color, same details. Identical "
    "exposure and white balance throughout: never brighten, never blow out the "
    "whites, never shift color. No morphing, no warping, no dreamlike AI "
    "aesthetics, no extra limbs or fingers. Full-bleed composition filling the "
    "frame edge to edge — no black bars, no white borders, no letterboxing, no "
    "margins. ABSOLUTELY NO on-screen text, captions, subtitles, numbers, "
    "logos or watermarks of any kind."
)

NEGATIVO_VIDEO = (
    "walls, wall corner, room, horizon line, floor-wall seam, gray background, "
    "gradient background, colored background, shadow on the background, studio "
    "equipment, light stands, softbox in frame, props, furniture, plants, other "
    "people, text, captions, subtitles, letters, numbers, logos, watermarks, "
    "morphing, warping, face change, different model, different garment, "
    "redesigned product, extra fingers, distorted hands, overexposure, blown "
    "highlights, washed out colors, color shift, black bars, white bars, "
    "letterboxing, pillarboxing, borders, frame around video, margins"
)


def _prompt_clip(toma: str, req: Dict[str, Any]) -> str:
    """Movimiento de cámara sobre el cuadro llave, que ya es el primer frame."""
    t = TOMAS[toma]
    sujeto = req.get("sujeto", "modelo")
    quien = ("the garment" if sujeto == "prenda" else "the model")
    base = (
        "Fashion e-commerce studio video on a seamless pure white cyclorama. "
        f"The first frame is the reference image and {quien} is exactly as "
        f"shown there — do not restage it, do not change it. {t['motion']} "
    )
    if sujeto == "prenda":
        base += ("The garment is held by an invisible ghost mannequin: no "
                 "person, no hands, no mannequin visible. ")
    extra = (req.get("notas_video") or "").strip()
    if extra:
        base += extra + " "
    return base + SUFIJO_VIDEO


# ─────────────────────────────────────────────────────────────────────────────
# MOTOR DE VIDEO — Veo (Gemini) y Wan/Seedance (fal.ai)
# ─────────────────────────────────────────────────────────────────────────────

async def _veo_headers() -> Dict[str, str]:
    key = await _current_api_key()
    if not key:
        raise RuntimeError("Falta la API key de Google (ni propia ni global).")
    return {"x-goog-api-key": key, "Content-Type": "application/json"}


async def _lanzar_veo(prompt: str, frame_b64: str, formato: str, motor: str,
                      duracion: int) -> str:
    """Dispara la generación en Veo. Devuelve el nombre de la operación."""
    modelo = VEO_MODELS.get(motor, VEO_MODELS["veo_fast"])
    url = f"{GEMINI_BASE}/models/{modelo}:predictLongRunning"
    parametros: Dict[str, Any] = {"aspectRatio": formato,
                                  "negativePrompt": NEGATIVO_VIDEO}
    if motor != "veo_lite":
        parametros["resolution"] = "1080p"
        if duracion in (4, 6):
            # 4s y 6s piden 1080p o imagen de referencia; cumplimos las dos.
            parametros["durationSeconds"] = duracion
    body = {
        "instances": [{
            "prompt": prompt,
            "image": {"bytesBase64Encoded": frame_b64, "mimeType": "image/jpeg"},
        }],
        "parameters": parametros,
    }
    headers = await _veo_headers()
    async with httpx.AsyncClient(timeout=120) as cli:
        r = await cli.post(url, headers=headers, json=body)
        # Las restricciones cambian según la región del API (por ejemplo
        # "1080p is not supported for a duration of 6 seconds"): ante un 400,
        # sacamos los parámetros opcionales de a uno y reintentamos.
        if r.status_code == 400:
            for p in ("durationSeconds", "resolution"):
                if p in body["parameters"]:
                    body["parameters"].pop(p, None)
                    r = await cli.post(url, headers=headers, json=body)
                    if r.status_code == 200:
                        break

    if r.status_code != 200:
        txt = r.text[:400]
        if r.status_code in (403, 429) or "billing" in txt.lower() or "quota" in txt.lower():
            raise RuntimeError(
                f"Veo HTTP {r.status_code}: {txt} — Veo necesita una key de "
                "Google con facturación habilitada. Mientras tanto podés usar "
                "el motor Wan o Seedance (fal.ai), que salen mucho menos.")
        if r.status_code == 404:
            raise RuntimeError(
                f"Veo HTTP 404: el modelo '{modelo}' no existe para tu key. "
                f"Probá con otro motor. Detalle: {txt}")
        raise RuntimeError(f"Veo HTTP {r.status_code}: {txt}")
    op = r.json().get("name")
    if not op:
        raise RuntimeError(f"Veo no devolvió operación: {r.text[:300]}")
    return op


def _extraer_video_uri(op_data: Dict[str, Any]) -> Optional[str]:
    """Tolera las distintas formas de respuesta de la operación de Veo."""
    resp = op_data.get("response", {}) or {}
    gvr = resp.get("generateVideoResponse", {}) or {}
    for s in gvr.get("generatedSamples", []) or []:
        uri = (s.get("video") or {}).get("uri")
        if uri:
            return uri
    for v in resp.get("videos", []) or []:
        uri = v.get("uri") or (v.get("video") or {}).get("uri")
        if uri:
            return uri
    for s in resp.get("generatedVideos", []) or []:
        uri = (s.get("video") or {}).get("uri") or s.get("uri")
        if uri:
            return uri
    return None


async def _esperar_veo(op_name: str, destino: Path) -> None:
    """Polea la operación hasta que termine y baja el mp4."""
    headers = await _veo_headers()
    key = headers["x-goog-api-key"]
    url = f"{GEMINI_BASE}/{op_name}"
    inicio = time.time()
    async with httpx.AsyncClient(timeout=120) as cli:
        while True:
            if time.time() - inicio > POLL_TIMEOUT:
                raise RuntimeError("Timeout esperando la generación del clip")
            r = await cli.get(url, headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"Poll HTTP {r.status_code}: {r.text[:200]}")
            data = r.json()
            if data.get("error"):
                raise RuntimeError(f"Veo error: {json.dumps(data['error'])[:300]}")
            if data.get("done"):
                uri = _extraer_video_uri(data)
                if not uri:
                    resp = data.get("response", {}) or {}
                    gvr = resp.get("generateVideoResponse", {}) or {}
                    razones = (gvr.get("raiMediaFilteredReasons")
                               or resp.get("raiMediaFilteredReasons"))
                    if razones or gvr.get("raiMediaFilteredCount"):
                        raise RuntimeError(
                            "Veo filtró el clip por su política de contenido: "
                            f"{json.dumps(razones)[:250] if razones else 'sin detalle'}")
                    raise RuntimeError(
                        f"La operación terminó sin video: {json.dumps(data)[:300]}")
                dl = await cli.get(uri, headers=headers, follow_redirects=True)
                if dl.status_code != 200:
                    sep = "&" if "?" in uri else "?"
                    dl = await cli.get(f"{uri}{sep}key={key}", follow_redirects=True)
                if dl.status_code != 200:
                    raise RuntimeError(f"Descarga del clip HTTP {dl.status_code}")
                destino.write_bytes(dl.content)
                return
            await asyncio.sleep(POLL_INTERVAL)


async def _generar_fal(prompt: str, frame_b64: str, motor: str,
                       destino: Path, duracion: int) -> None:
    """Clip image-to-video por fal.ai (cola asíncrona: submit -> status -> get)."""
    settings = await get_settings()
    key = FAL_KEY or str(settings.get("fal_api_key") or "").strip()
    if not key:
        raise RuntimeError("Falta FAL_KEY (o la API key de fal en Ajustes) para "
                           "usar los motores Wan / Seedance.")
    modelo = FAL_MODELS.get(motor)
    if not modelo:
        raise RuntimeError(f"Motor desconocido: {motor}")
    payload = {
        "prompt": prompt,
        "image_url": f"data:image/jpeg;base64,{frame_b64}",
        "resolution": "1080p" if motor == "wan" else "720p",
        "duration": "5" if duracion <= 5 else "10",
    }
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    sub_url = f"{FAL_BASE}/{modelo}"
    async with httpx.AsyncClient(timeout=180) as cli:
        r = await cli.post(sub_url, headers=headers, json=payload)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"fal submit HTTP {r.status_code}: {r.text[:300]}")
        data = r.json()
        rid = data.get("request_id") or data.get("requestId")
        status_url = data.get("status_url") or (f"{sub_url}/requests/{rid}/status"
                                                if rid else None)
        result_url = data.get("response_url") or (f"{sub_url}/requests/{rid}"
                                                  if rid else None)
        if not status_url:
            raise RuntimeError(f"fal no devolvió status_url: {json.dumps(data)[:200]}")

        inicio = time.time()
        while True:
            if time.time() - inicio > POLL_TIMEOUT:
                raise RuntimeError("Timeout esperando a fal.ai")
            rs = await cli.get(status_url, headers=headers)
            st = rs.json().get("status", "") if rs.status_code == 200 else ""
            if st in ("COMPLETED", "Completed", "succeeded", "OK"):
                break
            if st in ("FAILED", "Error", "CANCELLED"):
                raise RuntimeError(f"fal falló: {rs.text[:300]}")
            await asyncio.sleep(5)

        rr = await cli.get(result_url, headers=headers)
        if rr.status_code != 200:
            raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
        res = rr.json()
        vurl = None
        if isinstance(res.get("video"), dict):
            vurl = res["video"].get("url")
        elif res.get("videos"):
            vurl = res["videos"][0].get("url")
        vurl = vurl or res.get("url")
        if not vurl:
            raise RuntimeError(f"fal no devolvió video: {json.dumps(res)[:300]}")
        dl = await cli.get(vurl, follow_redirects=True)
        if dl.status_code != 200:
            raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
        destino.write_bytes(dl.content)


async def _generar_clip(prompt: str, frame_b64: str, req: Dict[str, Any],
                        destino: Path, duracion: int) -> None:
    motor = req.get("motor", "veo_fast")
    if motor.startswith("veo"):
        op = await _lanzar_veo(prompt, frame_b64, req.get("formato", "9:16"),
                               motor, duracion)
        await _esperar_veo(op, destino)
    else:
        await _generar_fal(prompt, frame_b64, motor, destino, duracion)


# ─────────────────────────────────────────────────────────────────────────────
# FFMPEG — unir los clips, música, locución y subtítulos
# ─────────────────────────────────────────────────────────────────────────────

def _ffmpeg_bin() -> Optional[str]:
    """ffmpeg del sistema; si no está, el binario que trae imageio-ffmpeg."""
    sistema = shutil.which("ffmpeg")
    if sistema:
        return sistema
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _dims(formato: str) -> Tuple[int, int]:
    return (1080, 1920) if formato == "9:16" else (1920, 1080)


def _duracion_video(path: Path) -> float:
    """Duración en segundos leyendo el stderr de ffmpeg (no hay ffprobe)."""
    binario = _ffmpeg_bin()
    if not binario:
        return 0.0
    try:
        res = subprocess.run([binario, "-i", str(path)], capture_output=True,
                             text=True, timeout=30)
        mm = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", res.stderr)
        if mm:
            return (int(mm.group(1)) * 3600 + int(mm.group(2)) * 60
                    + float(mm.group(3)))
    except Exception:
        pass
    return 0.0


def _normalizar(clip: Path, salida: Path, formato: str, mudo: bool = True) -> bool:
    """Lleva el clip al tamaño exacto recortando (crop-to-fill).
    Jamás barras negras: un video con franjas se ve amateur en la ficha."""
    binario = _ffmpeg_bin()
    if not binario:
        return False
    w, h = _dims(formato)
    cmd = [binario, "-y", "-i", str(clip),
           "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                   f"crop={w}:{h},setsar=1,fps=24,format=yuv420p"),
           "-c:v", "libx264", "-preset", "fast", "-crf", "19"]
    # El audio de Veo (ambiente inventado) no sirve para nada acá: el video sale
    # mudo y la música/locución se suman después, si la usuaria las pidió.
    cmd += ["-an"] if mudo else ["-c:a", "aac", "-b:a", "128k"]
    cmd += [str(salida)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=300)
        return res.returncode == 0 and salida.exists()
    except Exception:
        return False


def _concatenar(clips: List[Path], salida: Path) -> bool:
    """Cortes secos, uno atrás del otro. Sin transiciones: las marcas cortan
    en seco, y un fundido entre tomas de fondo blanco se ve barato."""
    binario = _ffmpeg_bin()
    if not binario or not clips:
        return False
    lista = salida.parent / "lista.txt"
    lista.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips))
    cmd = [binario, "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
           "-c", "copy", "-movflags", "+faststart", str(salida)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=300)
        if res.returncode == 0 and salida.exists():
            return True
        # Si el copy directo no cierra (parámetros distintos entre clips),
        # reencodeamos: más lento, pero no falla.
        cmd = [binario, "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
               "-c:v", "libx264", "-preset", "fast", "-crf", "19",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
        res = subprocess.run(cmd, capture_output=True, timeout=600)
        return res.returncode == 0 and salida.exists()
    except Exception:
        return False


def _font_path() -> Optional[str]:
    """Fuente para los subtítulos; si el sistema no trae ninguna, la baja."""
    import glob as _glob
    for pat in ("/usr/share/fonts/**/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/**/*Bold*.ttf",
                "/usr/share/fonts/**/*.ttf"):
        hits = _glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    cache = WORK_DIR / "fonts" / "DejaVuSans-Bold.ttf"
    if cache.exists():
        return str(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    for u in ("https://cdn.jsdelivr.net/npm/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf",
              "https://unpkg.com/dejavu-fonts-ttf@2.37.3/ttf/DejaVuSans-Bold.ttf"):
        try:
            urllib.request.urlretrieve(u, str(cache))
            if cache.exists() and cache.stat().st_size > 100_000:
                return str(cache)
        except Exception as e:
            print(f"[videos_luma] espejo de fuente falló ({u}): {e}")
    return None


def _png_subtitulo(texto: str, w: int, fuente: str, destino: Path) -> bool:
    """Subtítulo sobrio (blanco, mayúsculas, espaciado) sobre PNG transparente.
    Nada de karaoke amarillo: el look de vidriera es limpio."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    try:
        texto = " ".join(texto.upper().split())
        medidor = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        fs = max(int(w * 0.045), 22)
        for _ in range(10):
            font = ImageFont.truetype(fuente, fs)
            bbox = medidor.textbbox((0, 0), texto, font=font, stroke_width=2)
            if bbox[2] - bbox[0] <= int(w * 0.86) or fs <= 18:
                break
            fs = int(fs * 0.9)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = 14
        img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # Borde gris muy suave: sobre fondo blanco, el texto blanco puro
        # desaparece; con el contorno se lee siempre.
        d.text((pad - bbox[0], pad - bbox[1]), texto, font=font,
               fill=(255, 255, 255, 255), stroke_width=2,
               stroke_fill=(0, 0, 0, 120))
        img.save(destino)
        return destino.exists()
    except Exception as e:
        print(f"[videos_luma] PNG subtítulo error: {e}")
        return False


def _sub_limpio(t: str) -> str:
    """Saca los caracteres que rompen los filtros de ffmpeg."""
    return re.sub(r"[:;'\"\\%{}|]", "", t or "").strip()[:42]


async def _guion_y_subtitulos(req: Dict[str, Any], tomas: List[str],
                              segundos: float) -> Dict[str, Any]:
    """Gemini escribe la locución argentina y una frase corta por toma."""
    key = await _current_api_key()
    if not key:
        return {}
    palabras = max(int(segundos * 2.0), 8)
    pedido = (
        "Sos redactor publicitario argentino para e-commerce de moda.\n"
        f"Producto: {req.get('producto') or 'prenda de la foto'}\n"
        f"Notas de la marca: {req.get('notas') or 'ninguna'}\n"
        f"El video son {len(tomas)} tomas de catálogo sobre fondo blanco "
        f"({', '.join(TOMAS[t]['label'] for t in tomas)}), {segundos:.0f} "
        "segundos en total.\n\n"
        "Devolvé SOLO un JSON, sin markdown, con esta forma:\n"
        '{"guion": "...", "subtitulos": ["...", "..."]}\n\n'
        f"- guion: locución en español rioplatense (voseo: tenés, mirá, "
        f"llevátelo). Tono sobrio y elegante, de marca, NO gritado. MÁXIMO "
        f"{palabras} palabras: si se pasa, la voz queda cortada.\n"
        f"- subtitulos: exactamente {len(tomas)} frases, una por toma, de "
        "MÁXIMO 4 palabras cada una, sin emojis, sin comillas y sin dos puntos."
    )
    url = f"{GEMINI_BASE}/models/{TEXT_MODEL}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(url, headers={"x-goog-api-key": key,
                                             "Content-Type": "application/json"},
                               json={"contents": [{"parts": [{"text": pedido}]}]})
        if r.status_code != 200:
            print(f"[videos_luma] guion HTTP {r.status_code}: {r.text[:200]}")
            return {}
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"[videos_luma] guion error: {e}")
        return {}


async def _tts_argentino(texto: str, voz: str, destino: Path) -> bool:
    """Locución con Gemini TTS, con acento rioplatense pedido por estilo."""
    key = await _current_api_key()
    if not key:
        return False
    url = f"{GEMINI_BASE}/models/{TTS_MODEL}:generateContent"
    instruccion = ("Leé este texto publicitario con acento argentino "
                   "rioplatense, tono sobrio y elegante de marca de moda, "
                   "ritmo tranquilo y claro: ")
    body = {
        "contents": [{"parts": [{"text": instruccion + texto}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {
                "voiceName": TTS_VOCES.get(voz, "Kore")}}},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=120) as cli:
            r = await cli.post(url, headers={"x-goog-api-key": key,
                                             "Content-Type": "application/json"},
                               json=body)
        if r.status_code != 200:
            print(f"[videos_luma] TTS HTTP {r.status_code}: {r.text[:200]}")
            return False
        b64 = (r.json()["candidates"][0]["content"]["parts"][0]
               ["inlineData"]["data"])
        pcm = destino.with_suffix(".pcm")
        pcm.write_bytes(base64.b64decode(b64))
        binario = _ffmpeg_bin()
        if not binario:
            return False
        res = subprocess.run(
            [binario, "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", str(pcm), str(destino)], capture_output=True, timeout=60)
        return res.returncode == 0 and destino.exists()
    except Exception as e:
        print(f"[videos_luma] TTS error: {e}")
        return False


def _terminar(video: Path, salida: Path, formato: str,
              subtitulos: List[Tuple[str, float, float]],
              voz: Optional[Path], musica: Optional[Path]) -> Tuple[bool, str]:
    """Segunda pasada sobre el video ya unido: subtítulos y audio.
    Va aparte del concat a propósito: si esta pasada falla, el video mudo ya
    está hecho y se entrega igual."""
    binario = _ffmpeg_bin()
    if not binario:
        return False, "ffmpeg no disponible"
    if not subtitulos and not voz and not musica:
        shutil.copy(video, salida)
        return True, ""

    w, h = _dims(formato)
    dur = max(_duracion_video(video), 1.0)
    # NADA de entradas infinitas: ni el PNG en loop ni la música en bucle.
    # Con `-shortest` y el video saliendo de un filter_complex, ffmpeg se
    # cuelga y escribe para siempre (nos comimos 62 MB de un video de 6s).
    # Acotando cada entrada con `-t`, termina solo y no hace falta -shortest.
    tope = f"{dur:.2f}"
    cmd = [binario, "-y", "-i", str(video)]
    filtros: List[str] = []
    etiqueta = "[0:v]"
    idx = 1

    y_sub = h - int(h * 0.17)
    for k, (png, ini, fin) in enumerate(subtitulos):
        cmd += ["-loop", "1", "-framerate", "24", "-t", tope, "-i", str(png)]
        filtros.append(f"{etiqueta}[{idx}:v]overlay=(W-w)/2:{y_sub}"
                       f":enable='between(t,{ini:.2f},{fin:.2f})'[s{k}]")
        etiqueta = f"[s{k}]"
        idx += 1
    filtros.append(f"{etiqueta}format=yuv420p[vout]")

    pistas: List[str] = []
    if voz and voz.exists():
        cmd += ["-i", str(voz)]
        # apad con whole_dur: rellena con silencio HASTA el final del video y
        # ahí corta (apad pelado nunca termina).
        filtros.append(f"[{idx}:a]apad=whole_dur={tope}[vz]")
        pistas.append("[vz]")
        idx += 1
    if musica and musica.exists():
        # La música se repite si es corta, pero acotada al largo del video.
        cmd += ["-stream_loop", "-1", "-t", tope, "-i", str(musica)]
        filtros.append(f"[{idx}:a]volume={'0.18' if pistas else '0.42'},"
                       f"afade=t=out:st={max(dur - 1.2, 0):.2f}:d=1.2[mz]")
        pistas.append("[mz]")
        idx += 1

    maps = ["-map", "[vout]"]
    if pistas:
        if len(pistas) == 1:
            filtros.append(f"{pistas[0]}acopy[aout]")
        else:
            filtros.append(f"{''.join(pistas)}amix=inputs={len(pistas)}"
                           ":duration=longest:normalize=0[aout]")
        maps += ["-map", "[aout]"]

    cmd += ["-filter_complex", ";".join(filtros)] + maps
    cmd += ["-t", tope, "-c:v", "libx264", "-preset", "fast",
            "-crf", "19", "-pix_fmt", "yuv420p"]
    if pistas:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd += ["-movflags", "+faststart", str(salida)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=600)
        if res.returncode == 0 and salida.exists():
            return True, ""
        err = (res.stderr or b"").decode(errors="replace")[-300:]
        print(f"[videos_luma] terminación falló: {err}")
        return False, f"ffmpeg: …{err}"
    except Exception as e:
        return False, str(e)[:200]


# ─────────────────────────────────────────────────────────────────────────────
# JOBS (estado en el KV de Luma, archivos en disco)
# ─────────────────────────────────────────────────────────────────────────────

def _k_job(jid: str) -> str:
    return _pfx() + "vidjob:" + jid


def _k_indice() -> str:
    return _pfx() + "vidjobs"


async def _job_set(jid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    job = (await kv.get(_k_job(jid))) or {"job_id": jid}
    job.update(patch)
    await kv.set(_k_job(jid), job, ttl=JOB_TTL)
    return job


async def _job_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(_k_job(jid))


async def _indice_agregar(jid: str) -> None:
    lst = (await kv.get(_k_indice())) or []
    lst = [jid] + [x for x in lst if x != jid]
    await kv.set(_k_indice(), lst[:JOBS_INDICE])


def _dir(jid: str) -> Path:
    d = WORK_DIR / jid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _purgar_viejos() -> None:
    """Los trabajos de más de 7 días se borran del disco (el KV ya expira)."""
    limite = time.time() - JOB_TTL
    try:
        for d in WORK_DIR.iterdir():
            if d.is_dir() and d.name != "fonts" and d.stat().st_mtime < limite:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# COSTOS
# ─────────────────────────────────────────────────────────────────────────────

def _precio_cuadro(settings: Dict[str, Any], size: str) -> float:
    return _pricing(settings).get(size, _pricing(settings)["2K"])


def _estimar(req: Dict[str, Any], settings: Dict[str, Any]) -> Dict[str, Any]:
    """Lo que va a salir el video ANTES de gastar un centavo."""
    tomas = req.get("tomas") or TOMAS_DEFAULT
    n = len(tomas)
    size = req.get("calidad_cuadro", "2K")
    p_img = _precio_cuadro(settings, size)
    # El ancla puede necesitar una segunda pasada si el inspector la rechaza.
    imgs = round(p_img * n, 4)
    segundos = int(req.get("segundos", 6))
    p_seg = PRECIO_SEG.get(req.get("motor", "veo_fast"), PRECIO_SEG["veo_fast"])
    video = 0.0 if req.get("solo_cuadros") else round(p_seg * segundos * n, 4)
    extras = 0.0
    if not req.get("solo_cuadros") and req.get("audio") == "voz":
        extras = COSTO_GUION + COSTO_TTS
    return {
        "cuadros": n, "usd_cuadros": imgs,
        "segundos_video": 0 if req.get("solo_cuadros") else segundos * n,
        "usd_video": video, "usd_extras": round(extras, 4),
        "usd_total": round(imgs + video + extras, 4),
        "precio_seg": p_seg, "precio_cuadro": p_img,
    }


def _recortar_por_tope(req: Dict[str, Any], settings: Dict[str, Any]) -> Optional[str]:
    """Si el video no entra en el tope por video, saca tomas del final hasta
    que entre (mínimo 2). Devuelve el aviso para mostrar, o None."""
    tope = float(req.get("tope_usd") or 0)
    if tope <= 0:
        return None
    tomas = list(req.get("tomas") or TOMAS_DEFAULT)
    sacadas: List[str] = []
    while len(tomas) > 2 and _estimar({**req, "tomas": tomas}, settings)["usd_total"] > tope:
        sacadas.append(tomas.pop())
    req["tomas"] = tomas
    if not sacadas:
        return None
    nombres = ", ".join(TOMAS[t]["label"] for t in reversed(sacadas))
    return (f"Para no pasar el tope de US${tope:.2f} dejé el video en "
            f"{len(tomas)} tomas (saqué: {nombres}).")


# ─────────────────────────────────────────────────────────────────────────────
# WORKER
# ─────────────────────────────────────────────────────────────────────────────

async def _cuadro_llave(req: Dict[str, Any], toma: str, refs: List[str],
                        con_ancla: bool, settings: Dict[str, Any],
                        correcciones: str = "") -> bytes:
    """Genera UNA toma como foto. `refs` = imágenes b64 en orden (la 1 manda)."""
    prompt = _prompt_cuadro(toma, req, con_ancla, len(refs), correcciones)
    parts: List[Dict[str, Any]] = [{"text": prompt}]
    for i, b64 in enumerate(refs):
        etiqueta = ("IMAGEN 1 (la toma ya aprobada de esta sesión):"
                    if (i == 0 and con_ancla) else
                    f"IMAGEN {i + 1} (foto real del producto):")
        parts.append({"text": etiqueta})
        parts.append(_img_part(b64))
    return await gemini_generate(parts, settings, req.get("formato", "9:16"),
                                 req.get("calidad_cuadro", "2K"))


async def _procesar(jid: str, req: Dict[str, Any]) -> None:
    d = _dir(jid)
    settings = await get_settings()
    tomas: List[str] = req.get("tomas") or TOMAS_DEFAULT
    fotos_b64: List[str] = req["fotos"]
    gastado_img, gastado_video = 0.0, 0.0
    try:
        # ── 1) Cuadros llave ────────────────────────────────────────────────
        cuadros: Dict[int, Path] = {}
        ancla_b64: Optional[str] = None
        p_img = _precio_cuadro(settings, req.get("calidad_cuadro", "2K"))
        estados = [{"n": i + 1, "toma": t, "label": TOMAS[t]["label"],
                    "estado": "pendiente"} for i, t in enumerate(tomas)]
        await _job_set(jid, {"estado": "cuadros", "tomas": estados,
                             "detalle": "Armando el primer cuadro en fondo blanco…"})

        for i, toma in enumerate(tomas):
            n = i + 1
            estados[i]["estado"] = "generando"
            await _job_set(jid, {"tomas": estados,
                                 "detalle": f"Cuadro {n}/{len(tomas)} — "
                                            f"{TOMAS[toma]['label']}…"})
            # El ancla se dibuja mirando tus fotos; el resto mira el ancla
            # primero, que es lo que mantiene la cara, la luz y el blanco.
            refs = ([ancla_b64] + fotos_b64[:3]) if ancla_b64 else fotos_b64[:4]
            try:
                img = await _cuadro_llave(req, toma, refs, ancla_b64 is not None,
                                          settings)
                gastado_img += p_img

                # Inspector de prenda: sólo sobre el ancla. Es la toma que
                # define todas las demás, así que si ahí se coló un cambio de
                # diseño se arrastra a todo el video. Una sola pasada de QC.
                if (n == 1 and str(settings.get("qc_prenda", "si")) == "si"
                        and str(req.get("qc", "si")) == "si"):
                    await _job_set(jid, {"detalle": "Revisando que la prenda "
                                                    "sea igual a la real…"})
                    qc = await verificar_prenda(img, fotos_b64[:3])
                    umbral = int(settings.get("qc_umbral", 9) or 9)
                    if qc and int(qc.get("puntaje", 10)) < umbral:
                        difs = "; ".join(qc.get("diferencias", []))[:500]
                        await _job_set(jid, {"detalle": "La prenda salió con "
                                                        "diferencias; rehago el cuadro…"})
                        img = await _cuadro_llave(req, toma, refs, False,
                                                  settings, correcciones=difs)
                        gastado_img += p_img
                        estados[i]["qc"] = f"corregido ({qc.get('puntaje')}/10)"

                p = d / f"cuadro_{n}.jpg"
                p.write_bytes(img)
                cuadros[n] = p
                if ancla_b64 is None:
                    ancla_b64 = _compress_ref(img, max_dim=1280, q=92)
                estados[i]["estado"] = "listo"
            except Exception as e:
                estados[i]["estado"] = "error"
                estados[i]["error"] = str(e)[:300]
            await _job_set(jid, {"tomas": estados})

        if not cuadros:
            raise RuntimeError("No salió ningún cuadro llave. Revisá que la "
                               "foto muestre bien la prenda y probá de nuevo.")

        if req.get("solo_cuadros"):
            await budget_record("video_cuadros", req.get("calidad_cuadro", "2K"),
                                gastado_img, len(cuadros),
                                note=f"cuadros llave · {req.get('producto', '')[:40]}")
            await _job_set(jid, {
                "estado": "listo", "detalle": "Cuadros listos ✅ Mirálos y, si "
                                              "te gustan, generá el video.",
                "solo_cuadros": True, "terminado": time.time(),
                "costo": {"usd_cuadros": round(gastado_img, 4),
                          "usd_video": 0.0,
                          "usd_total": round(gastado_img, 4)}})
            return

        # ── 2) Movimiento ───────────────────────────────────────────────────
        dur = int(req.get("segundos", 6))
        if dur not in DURACIONES_OK:
            dur = 6
        p_seg = PRECIO_SEG.get(req.get("motor", "veo_fast"), PRECIO_SEG["veo_fast"])
        clips: List[Path] = []
        for i, toma in enumerate(tomas):
            n = i + 1
            if n not in cuadros:
                continue
            estados[i]["estado"] = "animando"
            await _job_set(jid, {"estado": "animando", "tomas": estados,
                                 "detalle": f"Moviendo la toma {n}/{len(tomas)} "
                                            f"con {MOTOR_LABEL.get(req.get('motor'), '')}…"})
            destino = d / f"clip_{n}.mp4"
            frame = _compress_ref(cuadros[n].read_bytes(), max_dim=1536, q=94)
            try:
                try:
                    await _generar_clip(_prompt_clip(toma, req), frame, req,
                                        destino, dur)
                except Exception as e1:
                    if "filtró" in str(e1) or "política" in str(e1):
                        raise
                    await asyncio.sleep(3)
                    await _generar_clip(_prompt_clip(toma, req), frame, req,
                                        destino, dur)
                gastado_video += p_seg * dur
                norm = d / f"norm_{n}.mp4"
                if _normalizar(destino, norm, req.get("formato", "9:16")):
                    clips.append(norm)
                    estados[i]["estado"] = "listo"
                else:
                    estados[i]["estado"] = "error"
                    estados[i]["error"] = ("El clip se generó pero no se pudo "
                                           "normalizar (¿falta ffmpeg?)")
            except Exception as e:
                estados[i]["estado"] = "error"
                estados[i]["error"] = str(e)[:300]
            await _job_set(jid, {"tomas": estados})

        if not clips:
            raise RuntimeError("Ningún clip se generó. Los cuadros llave "
                               "quedaron guardados: podés bajarlos igual.")

        # ── 3) Montaje ──────────────────────────────────────────────────────
        await _job_set(jid, {"estado": "montando",
                             "detalle": "Uniendo las tomas…"})
        crudo = d / "crudo.mp4"
        if not _concatenar(clips, crudo):
            raise RuntimeError("No pude unir los clips (revisá que ffmpeg esté "
                               "disponible en el servidor).")

        subs: List[Tuple[str, float, float]] = []
        voz_path: Optional[Path] = None
        aviso = ""
        if req.get("audio") == "voz" or req.get("subtitulos"):
            total = sum(_duracion_video(c) for c in clips) or (dur * len(clips))
            plan = await _guion_y_subtitulos(req, tomas[:len(clips)], total)
            if req.get("subtitulos") and plan.get("subtitulos"):
                fuente = _font_path()
                if fuente:
                    w, _h = _dims(req.get("formato", "9:16"))
                    t0 = 0.0
                    for k, c in enumerate(clips):
                        dc = _duracion_video(c) or dur
                        txt = _sub_limpio((plan["subtitulos"] or [""] * len(clips))[k]
                                          if k < len(plan["subtitulos"]) else "")
                        if txt:
                            png = d / f"sub_{k}.png"
                            if _png_subtitulo(txt, w, fuente, png):
                                subs.append((str(png), t0 + 0.25, t0 + dc - 0.15))
                        t0 += dc
                else:
                    aviso = "No encontré una fuente para los subtítulos; el video salió sin ellos."
            if req.get("audio") == "voz" and plan.get("guion"):
                wav = d / "voz.wav"
                if await _tts_argentino(plan["guion"], req.get("voz", "femenina"), wav):
                    voz_path = wav
                    await _job_set(jid, {"guion": plan.get("guion", "")})
                else:
                    aviso = (aviso + " " if aviso else "") + "La locución falló; el video salió sin voz."

        final = d / "final.mp4"
        mus = _musica_path()
        musica = mus if (req.get("musica") and mus.exists()) else None
        ok, err = _terminar(crudo, final, req.get("formato", "9:16"), subs,
                            voz_path, musica)
        if not ok:
            # El video mudo ya está: se entrega igual en vez de perder todo.
            shutil.copy(crudo, final)
            aviso = (aviso + " " if aviso else "") + f"No pude sumar audio/subtítulos ({err})."

        costo_total = gastado_img + gastado_video
        if req.get("audio") == "voz":
            costo_total += COSTO_GUION + COSTO_TTS
        await budget_record("video", req.get("calidad_cuadro", "2K"),
                            costo_total, len(clips),
                            note=f"video vidriera blanca · {req.get('producto', '')[:40]}")
        await _job_set(jid, {
            "estado": "listo", "detalle": "Video listo ✅",
            "aviso": aviso, "clips": len(clips), "final": True,
            "terminado": time.time(),
            "costo": {"usd_cuadros": round(gastado_img, 4),
                      "usd_video": round(gastado_video, 4),
                      "usd_total": round(costo_total, 4)}})
    except Exception as e:
        # Aunque falle, lo gastado se anota: si no, el tope mensual miente.
        if gastado_img or gastado_video:
            try:
                await budget_record("video_fallido", req.get("calidad_cuadro", "2K"),
                                    gastado_img + gastado_video, 0,
                                    note=str(e)[:80])
            except Exception:
                pass
        await _job_set(jid, {"estado": "error", "detalle": str(e)[:500],
                             "terminado": time.time()})


# ─────────────────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

async def _bind(request: Request) -> None:
    """Aísla los datos por cuenta, igual que el router de fotos."""
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind)])


def _normalizar_pedido(payload: Dict[str, Any]) -> Dict[str, Any]:
    req = dict(payload)
    fotos = [_strip_data_url(f) for f in (payload.get("fotos") or []) if f]
    if not fotos:
        raise HTTPException(400, "Subí al menos una foto: es de ahí de donde "
                                 "salen la modelo y la prenda.")
    # Las fotos del celular vienen enormes y, si fueron sacadas en vertical,
    # ACOSTADAS (la rotación vive en el EXIF, que la API no mira). Comprimirlas
    # acá arregla las dos cosas: llegan derechas y el pedido no se hace de 30 MB.
    livianas = []
    for f in fotos:
        try:
            livianas.append(_compress_ref(base64.b64decode(f), max_dim=1600, q=92))
        except Exception:
            livianas.append(f)   # si no la puedo abrir, va tal cual
    fotos = livianas
    # La foto marcada con la modelo va primera: es el ancla de todo el video.
    principal = int(payload.get("foto_principal") or 1)
    if 1 <= principal <= len(fotos):
        fotos = [fotos[principal - 1]] + [f for i, f in enumerate(fotos)
                                          if i != principal - 1]
    req["fotos"] = fotos[:6]

    tomas = [t for t in (payload.get("tomas") or TOMAS_DEFAULT) if t in TOMAS]
    if not tomas:
        tomas = list(TOMAS_DEFAULT)
    req["tomas"] = tomas[:MAX_TOMAS]

    if req.get("formato") not in ("9:16", "16:9"):
        req["formato"] = "9:16"
    if req.get("motor") not in PRECIO_SEG:
        req["motor"] = "veo_fast"
    if req.get("calidad_cuadro") not in ("1K", "2K", "4K"):
        req["calidad_cuadro"] = "2K"
    if req.get("sujeto") not in ("modelo", "prenda"):
        req["sujeto"] = "modelo"
    if req.get("audio") not in ("mudo", "voz"):
        req["audio"] = "mudo"
    if req.get("voz") not in ("femenina", "masculina"):
        req["voz"] = "femenina"
    try:
        req["segundos"] = int(req.get("segundos", 6))
    except (TypeError, ValueError):
        req["segundos"] = 6
    if req["segundos"] not in DURACIONES_OK:
        req["segundos"] = 6
    req["subtitulos"] = bool(req.get("subtitulos"))
    req["musica"] = bool(req.get("musica"))
    req["solo_cuadros"] = bool(req.get("solo_cuadros"))
    return req


@router.post(ROUTE_PREFIX + "/api/estimar")
async def api_estimar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Cuánto saldría, sin generar nada. No gasta un centavo."""
    req = dict(payload)
    req.setdefault("fotos", ["x"])   # el presupuesto no mira las fotos
    req = _normalizar_pedido(req)
    settings = await get_settings()
    est = _estimar(req, settings)
    _permitido, motivo, total, cap = await budget_check(est["usd_total"])
    est.update({"mes_gastado": total, "tope_mensual": cap, "aviso_tope": motivo})
    return est


@router.post(ROUTE_PREFIX + "/api/generar")
async def api_generar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    if not await _current_api_key():
        raise HTTPException(500, "Falta la API key de Google (cargala en "
                                 "Ajustes o en las variables del servidor).")
    settings = await get_settings()
    aviso = _recortar_por_tope(req, settings)
    est = _estimar(req, settings)
    permitido, motivo, _total, _cap = await budget_check(est["usd_total"])
    if not permitido:
        raise HTTPException(402, motivo)

    _purgar_viejos()
    jid = _uuid.uuid4().hex[:12]
    await _job_set(jid, {
        "job_id": jid, "estado": "encolado", "detalle": "En cola…",
        "producto": req.get("producto", ""), "formato": req["formato"],
        "motor": req["motor"], "motor_label": MOTOR_LABEL.get(req["motor"], ""),
        "sujeto": req["sujeto"], "segundos": req["segundos"],
        "solo_cuadros": req["solo_cuadros"], "estimado": est,
        "aviso": aviso or "", "creado": time.time(),
        "tomas": [{"n": i + 1, "toma": t, "label": TOMAS[t]["label"],
                   "estado": "pendiente"} for i, t in enumerate(req["tomas"])],
    })
    await _indice_agregar(jid)
    _spawn(_procesar(jid, req))
    return {"job_id": jid, "estado": "encolado", "estimado": est,
            "aviso": aviso or ""}


@router.get(ROUTE_PREFIX + "/api/estado/{jid}")
async def api_estado(jid: str) -> Dict[str, Any]:
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "No encontré ese trabajo")
    d = WORK_DIR / jid
    job = dict(job)
    job["final_disponible"] = (d / "final.mp4").exists()
    job["cuadros_disponibles"] = sorted(
        int(p.stem.split("_")[1]) for p in d.glob("cuadro_*.jpg")) if d.exists() else []
    job["clips_disponibles"] = sorted(
        int(p.stem.split("_")[1]) for p in d.glob("norm_*.mp4")) if d.exists() else []
    return job


@router.get(ROUTE_PREFIX + "/api/jobs")
async def api_jobs() -> Dict[str, Any]:
    ids = (await kv.get(_k_indice())) or []
    out = []
    for jid in ids[:20]:
        job = await _job_get(jid)
        if job:
            out.append({k: job.get(k) for k in
                        ("job_id", "estado", "detalle", "producto", "creado",
                         "terminado", "costo", "solo_cuadros", "formato")})
    return {"jobs": out}


async def _archivo_del_trabajo(jid: str, nombre: str, falta: str) -> Path:
    """Devuelve un archivo del trabajo, pero SÓLO si es de esta cuenta: el
    estado se guarda con el prefijo de la usuaria, así que si no lo encuentra
    ahí, el trabajo no es suyo. De paso corta cualquier jid inventado."""
    if not await _job_get(jid):
        raise HTTPException(404, "No encontré ese trabajo")
    f = WORK_DIR / jid / nombre
    if not f.exists():
        raise HTTPException(404, falta)
    return f


@router.get(ROUTE_PREFIX + "/api/cuadro/{jid}/{n}")
async def api_cuadro(jid: str, n: int):
    f = await _archivo_del_trabajo(jid, f"cuadro_{n}.jpg", "Cuadro no disponible")
    return FileResponse(f, media_type="image/jpeg",
                        filename=f"cuadro_{jid}_{n}.jpg")


@router.get(ROUTE_PREFIX + "/api/clip/{jid}/{n}")
async def api_clip(jid: str, n: int):
    f = await _archivo_del_trabajo(jid, f"norm_{n}.mp4", "Clip no disponible")
    return FileResponse(f, media_type="video/mp4",
                        filename=f"clip_{jid}_{n}.mp4")


@router.get(ROUTE_PREFIX + "/api/final/{jid}")
async def api_final(jid: str):
    f = await _archivo_del_trabajo(jid, "final.mp4",
                                   "El video final todavía no está")
    return FileResponse(f, media_type="video/mp4",
                        filename=f"video_{jid}.mp4")


@router.post(ROUTE_PREFIX + "/api/musica")
async def api_musica(archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube la cortina musical que se usa en todos los videos."""
    contenido = await archivo.read()
    if len(contenido) > 20_000_000:
        raise HTTPException(400, "La música tiene que pesar menos de 20 MB")
    mus = _musica_path()
    mus.write_bytes(contenido)
    return {"ok": True, "nombre": archivo.filename,
            "duracion": round(_duracion_video(mus), 1)}


@router.delete(ROUTE_PREFIX + "/api/musica")
async def api_musica_borrar() -> Dict[str, Any]:
    _musica_path().unlink(missing_ok=True)
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/health")
async def api_health() -> Dict[str, Any]:
    return {
        "ok": True, "version": VERSION,
        "gemini_key": bool(GEMINI_API_KEY),
        "fal_key": bool(FAL_KEY),
        "ffmpeg": _ffmpeg_bin() is not None,
        "musica": _musica_path().exists(),
        "kv": kv.backend,
        "almacenamiento": str(WORK_DIR) + (
            " (volumen persistente)" if str(WORK_DIR).startswith("/data")
            else " (temporal: se borra en cada deploy)"),
        "tomas": {k: v["label"] for k, v in TOMAS.items()},
    }


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE, headers={
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
    })


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Videos de producto</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600&family=Jost:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{
    --ink:#ecebf1; --ink-soft:#96919f; --line:#2c2a34;
    --ivory:#131218; --card:#1b1a21; --card-2:#232128;
    --rose:#c9a86b; --rose-deep:#d8b878; --ok:#5fae86; --bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);
    font-family:Jost,system-ui,sans-serif;font-size:17px;line-height:1.6;
    -webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:20px 18px 14px;border-bottom:1px solid var(--line);
    background:rgba(19,18,24,.86);backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:12px}
  .mono{width:42px;height:42px;border-radius:11px;border:1px solid var(--rose);
    display:flex;align-items:center;justify-content:center;flex:none;
    background:linear-gradient(150deg,#221f27,#161419);
    font-family:'Bodoni Moda',serif;font-weight:600;font-size:20px;color:var(--rose-deep);
    box-shadow:inset 0 0 12px rgba(201,168,107,.12)}
  .brand{font-family:'Bodoni Moda',serif;font-size:25px;font-weight:600;line-height:1}
  .brand small{font-family:Jost;font-weight:400;font-size:11px;color:var(--ink-soft);
    letter-spacing:.22em;text-transform:uppercase;display:block;margin-top:5px}
  .volver{margin-left:auto;font-size:13px;border:1px solid var(--line);
    border-radius:99px;padding:7px 14px;background:var(--card)}
  main{max-width:900px;margin:0 auto;padding:20px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;
    padding:22px;margin-bottom:18px;box-shadow:var(--shadow)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:24px;margin:0 0 6px}
  h3{font-family:'Bodoni Moda',serif;font-weight:500;font-size:19px;margin:0 0 4px}
  .hint{color:var(--ink-soft);font-size:14.5px;margin:0 0 15px}
  label{display:block;font-size:14px;font-weight:500;color:var(--ink-soft);margin:14px 0 6px}
  input,select,textarea{width:100%;padding:13px 14px;border:1px solid var(--line);
    border-radius:11px;background:var(--card-2);color:var(--ink);
    font-family:Jost,sans-serif;font-size:16px}
  textarea{min-height:74px;resize:vertical}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:14px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  .chip{padding:9px 15px;border-radius:999px;border:1px solid var(--line);
    background:var(--card-2);cursor:pointer;font-size:14.5px;color:var(--ink-soft);
    transition:.15s;user-select:none}
  .chip:hover{border-color:var(--rose)}
  .chip.on{background:var(--rose);color:#17140d;border-color:var(--rose);font-weight:500}
  .chip .num{display:inline-block;min-width:18px;font-weight:600}
  .btn{display:block;width:100%;padding:15px;border-radius:12px;border:none;
    background:var(--rose);color:#17140d;font-family:Jost,sans-serif;font-size:17px;
    font-weight:500;cursor:pointer;margin-top:16px}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .btn.sec{background:var(--card-2);color:var(--ink);border:1px solid var(--line)}
  .fotos{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:10px;margin-top:10px}
  .foto{position:relative;aspect-ratio:3/4;border-radius:10px;overflow:hidden;
    border:2px solid var(--line);cursor:pointer;background:var(--card-2)}
  .foto img{width:100%;height:100%;object-fit:cover}
  .foto.principal{border-color:var(--rose)}
  .foto .tag{position:absolute;left:0;right:0;bottom:0;background:rgba(201,168,107,.92);
    color:#17140d;font-size:11px;text-align:center;padding:2px;font-weight:600}
  .foto .x{position:absolute;top:4px;right:4px;width:22px;height:22px;border-radius:50%;
    background:rgba(0,0,0,.65);color:#fff;font-size:13px;display:flex;
    align-items:center;justify-content:center}
  .mas{display:flex;align-items:center;justify-content:center;font-size:28px;
    color:var(--ink-soft);border:1px dashed var(--line);border-radius:10px;
    aspect-ratio:3/4;cursor:pointer}
  .note{background:rgba(201,168,107,.08);border:1px solid var(--line);border-radius:10px;
    padding:11px 13px;font-size:13.5px;color:var(--ink-soft);margin-top:12px}
  .precio{display:flex;align-items:baseline;gap:8px;margin-top:14px}
  .precio b{font-family:'Bodoni Moda',serif;font-size:27px;color:var(--rose-deep)}
  .oculto{display:none}
  .estado{display:flex;align-items:center;gap:10px;font-size:15px}
  .punto{width:10px;height:10px;border-radius:50%;background:var(--rose);
    animation:lat 1.1s infinite}
  @keyframes lat{0%,100%{opacity:.25}50%{opacity:1}}
  .lista{margin-top:14px;display:flex;flex-direction:column;gap:9px}
  .item{display:flex;align-items:center;gap:10px;padding:11px 13px;border-radius:11px;
    background:var(--card-2);border:1px solid var(--line);font-size:14.5px}
  .item .est{margin-left:auto;font-size:12.5px;color:var(--ink-soft)}
  .item.listo{border-color:rgba(95,174,134,.5)}
  .item.error{border-color:rgba(224,115,111,.5)}
  .grid-cuadros{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));
    gap:10px;margin-top:14px}
  .grid-cuadros a{display:block;border-radius:10px;overflow:hidden;border:1px solid var(--line)}
  .grid-cuadros img{width:100%;display:block}
  video{width:100%;border-radius:12px;margin-top:14px;background:#000}
  .hist{display:flex;flex-direction:column;gap:8px;margin-top:10px}
  .hist div{display:flex;gap:10px;font-size:13.5px;color:var(--ink-soft);
    padding:9px 12px;border:1px solid var(--line);border-radius:10px;cursor:pointer}
  .err{background:rgba(224,115,111,.1);border:1px solid rgba(224,115,111,.45);
    border-radius:10px;padding:11px 13px;font-size:14px;margin-top:12px}
  @media(max-width:560px){.row,.row3{grid-template-columns:1fr}main{padding:12px}.card{padding:16px}}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Videos<small>Vidriera blanca · v%%VERSION%%</small></div>
    <a class="volver" href="%%HOME%%">← Fotos</a>
  </div>
</header>

<main>
<div class="card" id="formCard">
  <h2>Video de vidriera blanca</h2>
  <p class="hint">Subí la foto de tu publicación —la que ya tiene a tu modelo con
  la prenda puesta—. Luma la lleva a un fondo blanco infinito, arma una toma por
  encuadre y después las mueve: plano entero, giro, y el zoom al detalle.</p>

  <label>Fotos del producto <span style="color:var(--rose-deep)">· tocá una para marcarla como la principal</span></label>
  <div class="fotos" id="fotos">
    <div class="mas" id="btnFoto">+</div>
  </div>
  <input type="file" id="inputFoto" accept="image/*" multiple class="oculto">
  <div class="note">La <b>principal</b> es la que manda: de ahí salen la cara, el
  cuerpo y el color de la prenda. Las otras se usan para copiar bien los detalles
  (costuras, espalda, terminaciones).</div>

  <label>Producto (opcional)</label>
  <input id="producto" placeholder="Ej: Conjunto seamless línea Aura, negro">

  <label>Indicaciones para la marca (opcional)</label>
  <textarea id="notas" placeholder="Ej: que se vea el detalle del encaje del escote, sin calzado"></textarea>

  <label>Qué se ve</label>
  <div class="chips" id="sujeto">
    <div class="chip on" data-v="modelo">Mi modelo con la prenda</div>
    <div class="chip" data-v="prenda">La prenda sola (maniquí fantasma)</div>
  </div>

  <label>Tomas del video <span style="color:var(--ink-soft)">· en este orden</span></label>
  <div class="chips" id="tomas"></div>
  <div class="hint" id="tomasAyuda" style="margin-top:8px"></div>

  <div class="row">
    <div>
      <label>Formato</label>
      <div class="chips" id="formato">
        <div class="chip on" data-v="9:16">9:16 vertical</div>
        <div class="chip" data-v="16:9">16:9</div>
      </div>
    </div>
    <div>
      <label>Segundos por toma</label>
      <div class="chips" id="segundos">
        <div class="chip" data-v="4">4s</div>
        <div class="chip on" data-v="6">6s</div>
        <div class="chip" data-v="8">8s</div>
      </div>
    </div>
  </div>

  <label>Motor de video</label>
  <select id="motor">
    <option value="veo_fast">Veo 3.1 Fast — el que mejor respeta la tela (US$0,15/s)</option>
    <option value="veo_standard">Veo 3.1 — máxima calidad (US$0,40/s)</option>
    <option value="veo_lite">Veo 3.1 Lite — más barato (US$0,08/s)</option>
    <option value="wan">Wan 2.6 — económico, necesita FAL_KEY (US$0,05/s)</option>
    <option value="seedance">Seedance — económico, necesita FAL_KEY (US$0,09/s)</option>
  </select>

  <div class="row">
    <div>
      <label>Calidad de los cuadros</label>
      <select id="calidad">
        <option value="1K">1K — la más barata</option>
        <option value="2K" selected>2K — alcanza para 1080p</option>
        <option value="4K">4K — si además querés las fotos</option>
      </select>
    </div>
    <div>
      <label>Tope de gasto para este video (US$)</label>
      <input id="tope" type="number" step="0.5" min="0" placeholder="0 = sin tope">
    </div>
  </div>

  <label>Audio</label>
  <div class="chips" id="audio">
    <div class="chip on" data-v="mudo">Mudo (estilo Zara)</div>
    <div class="chip" data-v="voz">Con locución argentina</div>
  </div>
  <div class="chips" style="margin-top:8px">
    <div class="chip" id="chipSubs">Subtítulos</div>
    <div class="chip" id="chipMusica">Música de fondo</div>
  </div>
  <div class="hint" id="musicaEstado" style="margin-top:8px"></div>
  <input type="file" id="inputMusica" accept="audio/*" class="oculto">

  <div class="precio">
    <span class="hint" style="margin:0">Va a salir</span>
    <b id="precioTxt">—</b>
    <span class="hint" id="precioDet" style="margin:0"></span>
  </div>

  <button class="btn sec" id="btnCuadros">Ver los cuadros primero (sin video)</button>
  <button class="btn" id="btnGenerar">Generar el video</button>
  <div class="err oculto" id="errorBox"></div>
</div>

<div class="card oculto" id="jobCard">
  <div class="estado"><div class="punto" id="punto"></div><div id="detalle">…</div></div>
  <div class="lista" id="listaTomas"></div>
  <div id="avisoBox"></div>
  <div class="grid-cuadros" id="gridCuadros"></div>
  <div id="resultado"></div>
  <button class="btn sec oculto" id="btnVolver">Hacer otro</button>
</div>

<div class="card">
  <h3>Videos anteriores</h3>
  <div class="hist" id="historial"><span class="hint">Todavía no hay videos.</span></div>
</div>
</main>

<script>
const API = "%%PREFIX%%/api";
const TOMAS = %%TOMAS_JSON%%;
const $ = s => document.querySelector(s);
let FOTOS = [], PRINCIPAL = 1, ORDEN = %%DEFAULT_JSON%%, JOB = null, TIMER = null;

/* ---------- chips ---------- */
function grupo(id, cb){
  const c = document.getElementById(id);
  c.addEventListener('click', e => {
    const chip = e.target.closest('.chip'); if(!chip) return;
    c.querySelectorAll('.chip').forEach(x => x.classList.remove('on'));
    chip.classList.add('on');
    if(cb) cb(chip.dataset.v);
  });
}
const valor = id => (document.querySelector('#'+id+' .chip.on')||{dataset:{}}).dataset.v;

/* ---------- tomas (multi, con orden) ---------- */
function pintarTomas(){
  const c = $("#tomas"); c.innerHTML = "";
  Object.keys(TOMAS).forEach(k => {
    const i = ORDEN.indexOf(k);
    const d = document.createElement('div');
    d.className = 'chip' + (i >= 0 ? ' on' : '');
    d.innerHTML = (i >= 0 ? '<span class="num">'+(i+1)+'.</span> ' : '') + TOMAS[k].label;
    d.onclick = () => {
      const j = ORDEN.indexOf(k);
      if(j >= 0) ORDEN.splice(j, 1); else ORDEN.push(k);
      if(!ORDEN.length) ORDEN = [k];
      pintarTomas(); estimar();
    };
    d.onmouseenter = () => { $("#tomasAyuda").textContent = TOMAS[k].ayuda; };
    c.appendChild(d);
  });
  $("#tomasAyuda").textContent = ORDEN.map(k => TOMAS[k].label).join(" → ");
}

/* ---------- fotos ---------- */
function pintarFotos(){
  const c = $("#fotos");
  c.querySelectorAll('.foto').forEach(n => n.remove());
  FOTOS.forEach((f, i) => {
    const d = document.createElement('div');
    d.className = 'foto' + (i + 1 === PRINCIPAL ? ' principal' : '');
    d.innerHTML = '<img src="'+f+'">' + (i + 1 === PRINCIPAL ? '<div class="tag">PRINCIPAL</div>' : '')
      + '<div class="x">×</div>';
    d.onclick = e => {
      if(e.target.classList.contains('x')){
        FOTOS.splice(i, 1);
        if(PRINCIPAL > FOTOS.length) PRINCIPAL = 1;
      } else { PRINCIPAL = i + 1; }
      pintarFotos();
    };
    c.insertBefore(d, $("#btnFoto"));
  });
}
$("#btnFoto").onclick = () => $("#inputFoto").click();
$("#inputFoto").onchange = e => {
  [...e.target.files].slice(0, 6).forEach(file => {
    const r = new FileReader();
    r.onload = ev => { if(FOTOS.length < 6){ FOTOS.push(ev.target.result); pintarFotos(); } };
    r.readAsDataURL(file);
  });
  e.target.value = "";
};

/* ---------- música ---------- */
$("#chipMusica").onclick = async () => {
  const on = $("#chipMusica").classList.contains('on');
  if(on){ $("#chipMusica").classList.remove('on'); return; }
  const h = await (await fetch(API + "/health")).json();
  if(h.musica){ $("#chipMusica").classList.add('on'); pintarMusica(true); }
  else $("#inputMusica").click();
};
$("#chipSubs").onclick = () => $("#chipSubs").classList.toggle('on');
$("#inputMusica").onchange = async e => {
  const f = e.target.files[0]; if(!f) return;
  const fd = new FormData(); fd.append("archivo", f);
  $("#musicaEstado").textContent = "Subiendo la música…";
  const r = await fetch(API + "/musica", {method:"POST", body: fd});
  if(r.ok){ $("#chipMusica").classList.add('on'); pintarMusica(true, f.name); }
  else $("#musicaEstado").textContent = "No pude subir la música.";
  e.target.value = "";
};
function pintarMusica(hay, nombre){
  $("#musicaEstado").innerHTML = hay
    ? "Música cargada" + (nombre ? " (" + nombre + ")" : "")
      + ' · <a href="#" id="cambiarMus">cambiar</a>'
    : "";
  const a = $("#cambiarMus");
  if(a) a.onclick = ev => { ev.preventDefault(); $("#inputMusica").click(); };
}

/* ---------- pedido ---------- */
function pedido(solo){
  return {
    fotos: FOTOS, foto_principal: PRINCIPAL,
    producto: $("#producto").value, notas: $("#notas").value,
    sujeto: valor('sujeto'), tomas: ORDEN, formato: valor('formato'),
    segundos: parseInt(valor('segundos')), motor: $("#motor").value,
    calidad_cuadro: $("#calidad").value,
    tope_usd: parseFloat($("#tope").value || 0),
    audio: valor('audio'),
    subtitulos: $("#chipSubs").classList.contains('on'),
    musica: $("#chipMusica").classList.contains('on'),
    solo_cuadros: !!solo,
  };
}

async function estimar(){
  try{
    const r = await fetch(API + "/estimar", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({...pedido(false), fotos:["x"]})});
    const e = await r.json();
    if(!r.ok) return;
    $("#precioTxt").textContent = "US$" + e.usd_total.toFixed(2);
    $("#precioDet").textContent = e.cuadros + " cuadros (US$" + e.usd_cuadros.toFixed(2)
      + ") + " + e.segundos_video + "s de video (US$" + e.usd_video.toFixed(2) + ")"
      + (e.tope_mensual ? " · este mes llevás US$" + (e.mes_gastado||0).toFixed(2)
         + " de US$" + e.tope_mensual.toFixed(2) : "");
  }catch(err){}
}

function error(msg){
  const b = $("#errorBox");
  if(!msg){ b.classList.add('oculto'); return; }
  b.textContent = msg; b.classList.remove('oculto');
}

async function lanzar(solo){
  if(!FOTOS.length){ error("Subí al menos una foto del producto."); return; }
  error("");
  $("#btnGenerar").disabled = $("#btnCuadros").disabled = true;
  try{
    const r = await fetch(API + "/generar", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify(pedido(solo))});
    const j = await r.json();
    if(!r.ok){ error(j.detail || "No pude arrancar el trabajo."); return; }
    JOB = j.job_id;
    $("#formCard").classList.add('oculto');
    $("#jobCard").classList.remove('oculto');
    if(j.aviso) $("#avisoBox").innerHTML = '<div class="note">' + j.aviso + '</div>';
    seguir();
  }catch(e){ error(String(e)); }
  finally{ $("#btnGenerar").disabled = $("#btnCuadros").disabled = false; }
}
$("#btnGenerar").onclick = () => lanzar(false);
$("#btnCuadros").onclick = () => lanzar(true);
$("#btnVolver").onclick = () => {
  clearInterval(TIMER);
  $("#jobCard").classList.add('oculto');
  $("#formCard").classList.remove('oculto');
  $("#gridCuadros").innerHTML = ""; $("#resultado").innerHTML = "";
  $("#avisoBox").innerHTML = ""; historial();
};

const ICONO = {pendiente:"·", generando:"⏳", animando:"🎬", listo:"✓", error:"✕"};

async function seguir(){
  clearInterval(TIMER);
  const tick = async () => {
    const r = await fetch(API + "/estado/" + JOB);
    if(!r.ok) return;
    const j = await r.json();
    $("#detalle").textContent = j.detalle || j.estado;
    $("#listaTomas").innerHTML = (j.tomas||[]).map(t =>
      '<div class="item ' + (t.estado==='listo'?'listo':(t.estado==='error'?'error':'')) + '">'
      + '<span>' + (ICONO[t.estado]||"·") + '</span><span>' + t.n + '. ' + t.label + '</span>'
      + '<span class="est">' + (t.error ? t.error : (t.qc || t.estado)) + '</span></div>').join("");
    $("#gridCuadros").innerHTML = (j.cuadros_disponibles||[]).map(n =>
      '<a href="' + API + '/cuadro/' + JOB + '/' + n + '" target="_blank">'
      + '<img src="' + API + '/cuadro/' + JOB + '/' + n + '"></a>').join("");
    if(j.aviso) $("#avisoBox").innerHTML = '<div class="note">' + j.aviso + '</div>';

    if(j.estado === 'listo' || j.estado === 'error'){
      clearInterval(TIMER);
      $("#punto").style.animation = "none";
      $("#btnVolver").classList.remove('oculto');
      let html = "";
      if(j.estado === 'error') html += '<div class="err">' + (j.detalle||"") + '</div>';
      if(j.final_disponible){
        html += '<video controls playsinline src="' + API + '/final/' + JOB + '"></video>'
             + '<a class="btn" style="text-align:center;text-decoration:none" download '
             + 'href="' + API + '/final/' + JOB + '">Descargar el video</a>';
      }
      if(j.costo) html += '<div class="note">Gastaste US$' + (j.costo.usd_total||0).toFixed(2)
        + ' (cuadros US$' + (j.costo.usd_cuadros||0).toFixed(2)
        + ' · video US$' + (j.costo.usd_video||0).toFixed(2) + ')</div>';
      if(j.guion) html += '<div class="note">Locución: “' + j.guion + '”</div>';
      $("#resultado").innerHTML = html;
      historial();
    }
  };
  await tick();
  TIMER = setInterval(tick, 5000);
}

async function historial(){
  try{
    const j = await (await fetch(API + "/jobs")).json();
    const box = $("#historial");
    if(!j.jobs || !j.jobs.length){ box.innerHTML = '<span class="hint">Todavía no hay videos.</span>'; return; }
    box.innerHTML = j.jobs.map(x =>
      '<div data-j="' + x.job_id + '"><span>' + (x.estado==='listo'?'✓':(x.estado==='error'?'✕':'⏳')) + '</span>'
      + '<span>' + (x.producto || 'Sin nombre') + (x.solo_cuadros ? ' · sólo cuadros' : '') + '</span>'
      + '<span style="margin-left:auto">' + (x.costo ? 'US$' + (x.costo.usd_total||0).toFixed(2) : '') + '</span></div>').join("");
    box.querySelectorAll('div[data-j]').forEach(n => n.onclick = () => {
      JOB = n.dataset.j;
      $("#formCard").classList.add('oculto');
      $("#jobCard").classList.remove('oculto');
      $("#punto").style.animation = "";
      seguir();
    });
  }catch(e){}
}

/* ---------- arranque ---------- */
grupo('sujeto'); grupo('formato', estimar); grupo('segundos', estimar); grupo('audio', estimar);
$("#motor").onchange = estimar; $("#calidad").onchange = estimar;
pintarTomas(); estimar(); historial();
fetch(API + "/health").then(r => r.json()).then(h => {
  if(h.musica) pintarMusica(true);
  if(!h.ffmpeg) error("Ojo: el servidor no tiene ffmpeg, así que no voy a poder "
    + "unir las tomas. Los clips sueltos sí se generan.");
});
</script>
</body>
</html>"""

HTML_PAGE = (HTML_PAGE
             .replace("%%PREFIX%%", ROUTE_PREFIX)
             .replace("%%HOME%%", os.environ.get("IMAGENES_PREFIX", "/imagenes") or "/")
             .replace("%%VERSION%%", VERSION)
             .replace("%%TOMAS_JSON%%", json.dumps(
                 {k: {"label": v["label"], "ayuda": v["ayuda"]}
                  for k, v in TOMAS.items()}, ensure_ascii=False))
             .replace("%%DEFAULT_JSON%%", json.dumps(TOMAS_DEFAULT)))
