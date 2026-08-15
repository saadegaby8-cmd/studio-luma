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
   video se dibuja primero como FOTO: plano entero, 3/4, espalda, los macros
   (frente, espalda, short/bombacha), hero — o la que escriba la usuaria. La
   primera toma de cada LOOK es su ANCLA; las demás de ese look se generan
   mirándola, así la cara, la luz y el blanco no cambian entre tomas. Un look
   es una modelo con su color: con varios, cada uno tiene sus fotos, su ancla
   y su revisión de prenda, y las tomas se reparten entre ellos.
2. MOVIMIENTO, y se elige TOMA POR TOMA porque es el 90% de lo que sale un
   video:
   - "ia" (Veo 3.1 o Wan/Seedance): el cuadro llave es el PRIMER FRAME literal
     del clip y el prompt sólo describe el movimiento. Mueve a la modelo de
     verdad, y es lo único que sirve donde el cuerpo se mueve.
   - "camara": el movimiento lo hace ffmpeg recortando el cuadro, como en una
     mesa de edición. GRATIS y en segundos. Mueve la cámara, no a la modelo:
     en un macro no se nota, en un plano entero sí.

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
    CURRENT_SUB,
    _compress_ref,
    _current_api_key,
    _drive_connected_for,
    _img_part,
    _pfx,
    _pricing,
    _strip_data_url,
    budget_check,
    budget_record,
    drive_upload,
    ficha_to_text,
    gemini_analyze,
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
VERSION = "2.0.0"   # subí este número cada vez que cambiamos el archivo

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
    # El mismo Seedance pero en su versión Pro, que sale a 1080p en vez de 720p.
    # El Lite es el que salió mejor en la prueba real, así que el Pro es el
    # candidato más obvio a mejorarlo: misma familia, más resolución.
    "seedance_pro": os.getenv("FAL_SEEDANCE_PRO_MODEL",
                              "fal-ai/bytedance/seedance/v1/pro/image-to-video"),
    # MiniMax H3 (el que fal llama también Hailuo 03): saca hasta 2K con audio.
    # Si fal le cambia la ruta al modelo, se corrige con FAL_MINIMAX_MODEL sin
    # tocar el código — es lo que ya hacemos con Wan y Seedance.
    "minimax_h3": os.getenv("FAL_MINIMAX_MODEL", "minimax/h3/image-to-video"),
    # LTX de Lightricks (pesos abiertos). El Pro es el de más calidad; el Fast
    # sale bastante menos y para un push-in sobre un cuadro ya resuelto alcanza.
    "ltx25_pro": os.getenv("FAL_LTX_PRO_MODEL",
                           "lightricks/ltx-2.5/image-to-video/pro"),
    "ltx25_fast": os.getenv("FAL_LTX_FAST_MODEL",
                            "lightricks/ltx-2.5/image-to-video/fast"),
    "ltx23_fast": os.getenv("FAL_LTX23_MODEL",
                            "fal-ai/ltx-2.3/image-to-video/fast"),
}

# US$ por segundo de video generado (editables por env si cambian los precios)
PRECIO_SEG = {
    "veo_lite": float(os.getenv("VIDEOS_PRECIO_VEO_LITE", "0.08")),
    "veo_fast": float(os.getenv("VIDEOS_PRECIO_VEO_FAST", "0.15")),
    "veo_standard": float(os.getenv("VIDEOS_PRECIO_VEO_STD", "0.40")),
    "wan": float(os.getenv("VIDEOS_PRECIO_WAN", "0.05")),
    # Seedance Lite: US$0,18 el video de 5s a 720p = US$0,036/s. Estaba cargado
    # en 0,09 —dos veces y media de más—, así que el estimador venía cobrando de
    # más justo al motor más barato de todos.
    "seedance": float(os.getenv("VIDEOS_PRECIO_SEEDANCE", "0.036")),
    "seedance_pro": float(os.getenv("VIDEOS_PRECIO_SEEDANCE_PRO", "0.148")),
    "minimax_h3": float(os.getenv("VIDEOS_PRECIO_MINIMAX", "0.26")),
    # Precios a 1080p, que es lo que pedimos: el video se entrega en 1080x1920.
    # A 720p el LTX sale menos (Pro 0,12 · Fast 0,09) pero habría que agrandarlo
    # y las tomas de detalle salen blandas.
    "ltx25_pro": float(os.getenv("VIDEOS_PRECIO_LTX_PRO", "0.17")),
    "ltx25_fast": float(os.getenv("VIDEOS_PRECIO_LTX_FAST", "0.13")),
    "ltx23_fast": float(os.getenv("VIDEOS_PRECIO_LTX23", "0.04")),
}
# El motor que viene puesto. Era Veo 3.1 Fast por ser el que mejor entiende la
# tela fina, pero en la prueba real Seedance salió mejor que Veo y que Wan — y
# sale la cuarta parte. Manda lo que se vio, no lo que decía la ficha técnica.
MOTOR_DEFAULT = os.getenv("VIDEOS_MOTOR_DEFAULT", "seedance")

MOTOR_LABEL = {
    "veo_lite": "Veo 3.1 Lite", "veo_fast": "Veo 3.1 Fast",
    "veo_standard": "Veo 3.1", "wan": "Wan 2.6",
    "seedance": "Seedance Lite", "seedance_pro": "Seedance Pro",
    "minimax_h3": "MiniMax H3", "ltx25_pro": "LTX 2.5 Pro",
    "ltx25_fast": "LTX 2.5 Fast", "ltx23_fast": "LTX 2.3 Fast",
}

# La resolución que se le pide a cada modelo de fal. MiniMax H3 llega a 2K, pero
# el video se entrega en 1080x1920: pedirle 2K sería pagar píxeles que el
# montaje recorta.
RESOLUCION_FAL = {
    "wan": "1080p", "seedance": "720p", "seedance_pro": "1080p",
    "minimax_h3": "1080P",
    "ltx25_pro": "1080p", "ltx25_fast": "1080p", "ltx23_fast": "1080p",
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

MAX_TOMAS = 8
DURACIONES_OK = (4, 6, 8)

# Un LOOK es una modelo con su color: sus fotos, su ancla y su revisión de
# prenda. Sin looks el video entero sale de una sola foto —el ancla es una y
# todas las tomas la miran—, así que cuatro modelos con cuatro colores salían
# las cuatro con la cara de la principal y los colores mezclados: las fotos que
# no son la principal entran al prompt como "la verdad del diseño", y pasarle
# tres colores distintos es pedirle la prenda de tres colores a la vez.
MAX_LOOKS = 4
MAX_FOTOS = 12

# De qué lado está sacada cada foto. No es un adorno: el análisis de la prenda y
# el inspector necesitan saber cuál es la espalda para no comparar el frente
# contra la espalda y reportar diferencias que no existen — y, sobre todo, para
# no INVENTAR la espalda cuando sí hay foto de la espalda.
VISTAS = ("frente", "perfil", "espalda")

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
        "intencion": "presentar la prenda entera: se para como quien sabe que la están mirando de arriba a abajo",
        "camara": {"modo": "push", "z": 1.28, "ax": .5, "ay": .42},
        "vivo": True,
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
            "Full-body wide shot. The camera pushes in slowly and continuously "
            "through the whole clip, starting on the complete head-to-toe "
            "silhouette and ending on a medium shot framed from mid-thigh up, "
            "while the model holds her relaxed catalogue pose, breathing "
            "naturally and shifting her weight."),
    },
    "tres_cuartos": {
        "intencion": "mostrar cómo cae y cómo calza de costado, girando para que la tela se mueva",
        "camara": {"modo": "push", "z": 1.30, "ax": .5, "ay": .38},
        "vivo": True,
        "label": "Medio 3/4",
        "ayuda": "Plano medio con giro suave: se lee el calce y la caída.",
        "encuadre": (
            "plano medio, de la mitad del muslo para arriba, la modelo en "
            "tres cuartos de perfil, la prenda ocupando el centro del cuadro"),
        "pose": (
            "torso girado en tres cuartos, una mano acomodándose apenas el "
            "pelo o cayendo natural, mentón levemente hacia el hombro"),
        "motion": (
            "The model starts in a three-quarter pose and turns calmly towards "
            "the camera until she faces it, settling into a still frontal "
            "pose; the fabric follows the movement with realistic weight and "
            "drape. The camera pushes in gently at the same time, from a full "
            "shot to a waist-up medium shot."),
    },
    "detalle": {
        "intencion": "mostrar el detalle del frente: acomoda apenas la tela para que se lea la terminación",
        "camara": {"modo": "push", "z": 1.30, "ax": .5, "ay": .5},
        "label": "Macro del frente",
        "ayuda": "El zoom que hacen Zara y Adidas: escote, costura, encaje.",
        "encuadre": (
            "primerísimo plano del detalle más lindo del FRENTE de la prenda "
            "sobre el cuerpo (el escote, el drapeado, la costura, el encaje, "
            "el elástico o la textura del tejido), la tela ocupando todo el "
            "cuadro, profundidad de campo corta con el fondo blanco "
            "desenfocado"),
        "pose": (
            "sólo se ve el fragmento del cuerpo con la prenda; no hace falta "
            "la cara"),
        "motion": (
            "Macro detail shot. The camera pushes in slowly and continuously "
            "on the garment detail with shallow depth of field, ending on a "
            "TIGHT but READABLE close-up where the detail is still recognisable "
            "— never so close that the frame turns into a flat wall of fabric "
            "with nothing to look at; the fabric "
            "moves a couple of millimetres as the body breathes. Nothing else "
            "in frame, no face."),
    },
    "detalle_espalda": {
        "intencion": "mostrar la terminación de atrás: los breteles, el cierre, cómo cierra la espalda",
        "camara": {"modo": "push", "z": 1.30, "ax": .5, "ay": .32},
        "label": "Macro de la espalda",
        "ayuda": "El cierre, el cruce de los breteles y la terminación de atrás.",
        "encuadre": (
            "primerísimo plano de la ESPALDA de la prenda sobre el cuerpo: los "
            "breteles con sus reguladores, el cruce, el cierre o el nudo y la "
            "costura de la terminación trasera, ocupando todo el cuadro, "
            "profundidad de campo corta con el fondo blanco desenfocado"),
        "pose": (
            "de espaldas a la cámara y quieta; se ve sólo el fragmento de la "
            "espalda con la prenda, no hace falta la cara"),
        "motion": (
            "Macro detail shot of the BACK of the garment. The camera pushes in "
            "slowly and continuously on the straps, the closure and the back "
            "finishing with shallow depth of field, ending on a tight but "
            "READABLE close-up where the straps and the closure are still "
            "recognisable, never a flat wall of fabric. "
            "The model keeps her back to the camera "
            "and stays still, only breathing; the straps shift a millimetre "
            "with the breath. No face in frame."),
    },
    "detalle_abajo": {
        "intencion": "mostrar cómo calza abajo: la cintura, el ruedo, cómo se apoya en la cadera",
        "camara": {"modo": "push", "z": 1.30, "ax": .5, "ay": .62},
        "label": "Macro de abajo (short / bombacha)",
        "ayuda": "La parte de abajo: cintura, ruedo, elástico y cómo calza.",
        "encuadre": (
            "primerísimo plano de la PARTE DE ABAJO de la prenda sobre el "
            "cuerpo (el short, la bombacha, la bikini o la pollera), de la "
            "cintura a la mitad del muslo: la cintura y su elástico, el ruedo, "
            "la costura del costado y cómo calza sobre la cadera; profundidad "
            "de campo corta con el fondo blanco desenfocado"),
        "pose": (
            "de frente o en tres cuartos, el peso sobre una pierna para que se "
            "lea el calce; se ve sólo ese fragmento del cuerpo, sin la cara"),
        "motion": (
            "Macro detail shot of the LOWER half of the garment (the shorts, "
            "the briefs or the skirt). The camera pushes in slowly and "
            "continuously on the waistband, the hem and the side seam with "
            "shallow depth of field, ending on a tight but READABLE close-up "
            "where the waistband and the hem are still recognisable, never a "
            "flat wall of fabric; the fabric moves a couple of millimetres as the body "
            "breathes and shifts its weight. No face in frame."),
    },
    "espalda": {
        "intencion": "mostrar la prenda de atrás completa, que es la vista que nadie ve al comprar online",
        "camara": {"modo": "push", "z": 1.45, "ax": .5, "ay": .22},
        "label": "De espalda",
        "ayuda": "Muestra la parte de atrás: el cierre, el cruce, el escote.",
        "encuadre": (
            "plano entero desde atrás, la modelo de espaldas a la cámara, se "
            "ve toda la parte trasera de la prenda"),
        "pose": (
            "de espaldas, cabeza apenas girada hacia el hombro, brazos sueltos"),
        "motion": (
            "The model stands with her back to the camera. The camera starts "
            "on her full body from behind and pushes in continuously and "
            "decisively towards her upper back, ending in a tight close-up of "
            "the straps, the back neckline and the finishing of the garment, "
            "filling the frame. The model stays still, only breathing."),
    },
    "caminata": {
        "intencion": "mostrar cómo se mueve la prenda al caminar: la tela acompaña el paso",
        "camara": {"modo": "push", "z": 1.35, "ax": .5, "ay": .45},
        "vivo": True,
        "label": "Caminata",
        "ayuda": "La modelo camina hacia cámara. Muy Adidas.",
        "encuadre": (
            "plano entero frontal LEJANO: la modelo entera y con bastante aire "
            "arriba y abajo, chiquita en el cuadro, para que la cámara tenga a "
            "dónde acercarse"),
        "pose": (
            "en pleno paso, una pierna adelante, brazos acompañando el "
            "movimiento, actitud segura, descalza"),
        "motion": (
            "The model walks straight towards the camera at a calm, confident "
            "pace. THE CAMERA IS LOCKED ON A TRIPOD AND DOES NOT MOVE, DOES NOT "
            "ZOOM AND DOES NOT DOLLY BACK. She physically covers ground and "
            "therefore GETS VISIBLY BIGGER in the frame: she starts small, with "
            "her whole body and plenty of empty space around her, and ends "
            "framed from the knees up, much closer than she started. If her "
            "size in the frame does not change, the shot is WRONG — it looks "
            "like she is walking on a treadmill. Natural, grounded, barefoot "
            "walk; the garment moves and sways with each step."),
    },
    "hero": {
        "intencion": "el cierre: la prenda entera, quieta y clara, como la foto de tapa",
        "camara": {"modo": "pull", "z": 1.14, "ax": .5, "ay": .45},
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

# El orden por defecto sale de medir un video de catálogo real: 6 clips de 5s,
# cortes secos, sin audio. Caminata para entrar, giro, la espalda que termina
# en el detalle de los breteles, el macro del frente y el cierre.
TOMAS_DEFAULT = ["caminata", "tres_cuartos", "espalda", "detalle", "hero"]

# Las tomas que escribe la usuaria ("toma mía"): el catálogo de arriba cubre lo
# que se repite en todas las marcas, pero cada prenda tiene SU detalle —el ruedo
# del short de una tankini, el moño de atrás, la etiqueta tejida— y esa toma no
# se puede anticipar desde acá. Van con clave `libre_N` y su texto viaja aparte,
# en `libres`, así el catálogo sigue siendo fijo y validable.
LIBRE_RE = re.compile(r"^libre_\d+$")
MAX_LIBRES = 4


def _label_libre(texto: str) -> str:
    t = " ".join((texto or "").split())
    if not t:
        return "Toma mía"
    return "Toma mía: " + (t[:28] + "…" if len(t) > 28 else t)


# El motor de CADA toma, que es de lejos lo que más mueve el precio:
#
#   "ia"     -> Veo / Wan mueven de verdad a la modelo. Es el 90% de lo que sale
#              un video, y es lo ÚNICO que sirve donde el cuerpo se mueve: la
#              caminata, el giro, la tela sacudiéndose.
#   "camara" -> el movimiento lo hace ffmpeg recortando la foto, como en una
#              mesa de edición. Sale GRATIS, tarda segundos y el detalle es
#              pixel por pixel el de la foto, sin riesgo de que invente una
#              costura. Mueve la cámara, no a la modelo: en un macro no se
#              nota, en un plano entero sí (por eso las tomas con "vivo": True
#              avisan en el panel).
MOTORES_TOMA = ("ia", "camara")


def _motor_toma(req: Dict[str, Any], toma: str) -> str:
    m = (req.get("toma_motor") or {}).get(toma, "ia")
    return m if m in MOTORES_TOMA else "ia"


def _camara_de(toma: str) -> Dict[str, Any]:
    """La ficha de recorte de una toma. Las libres no tienen: entran suave y
    centradas, que es lo que no puede quedar mal sin saber qué se ve."""
    base = {"modo": "push", "z": 1.35, "ax": .5, "ay": .5}
    base.update(TOMAS.get(toma, {}).get("camara") or {})
    return base


def _nombre_look(req: Dict[str, Any], look: int) -> str:
    """El nombre que le puso la usuaria a ese look ("Coral"), o "" si no le
    puso ninguno. Vacío es distinto de "Look 2": el nombre viaja al prompt y
    "Look 2" no le dice nada al motor."""
    return str((req.get("looks_nombre") or {}).get(str(look), "")).strip()


def _etiqueta_look(req: Dict[str, Any], look: int) -> str:
    """El nombre para mostrar en el panel, que siempre tiene que decir algo."""
    return _nombre_look(req, look) or f"Look {look}"


def _fotos_por_look(req: Dict[str, Any]) -> Tuple[Dict[int, List[str]], int]:
    """Agrupa las fotos por look SIN alterar el orden: la primera de cada grupo
    es la que manda en ese look. Devuelve también el look base, que es el de la
    primera foto: es donde caen las tomas que apuntan a un look sin fotos."""
    fotos: List[str] = req.get("fotos") or []
    looks: List[int] = req.get("foto_look") or []
    por_look: Dict[int, List[str]] = {}
    for i, f in enumerate(fotos):
        L = looks[i] if i < len(looks) else 1
        por_look.setdefault(L, []).append(f)
    base = (looks[0] if looks else 1)
    return por_look, base


def _vista_de(req: Dict[str, Any], look: int, vista: str) -> List[str]:
    """Las fotos de ESE look sacadas de ESE lado."""
    fotos = req.get("fotos") or []
    looks = req.get("foto_look") or []
    vistas = req.get("foto_vista") or []
    out = []
    for i, f in enumerate(fotos):
        if (looks[i] if i < len(looks) else 1) != look:
            continue
        if (vistas[i] if i < len(vistas) else "frente") == vista:
            out.append(f)
    return out


def _toma_def(toma: str, req: Dict[str, Any]) -> Dict[str, Any]:
    """La ficha de una toma: encuadre, pose y movimiento.

    Las del catálogo salen de TOMAS tal cual. Las libres se arman con el texto
    que escribió la usuaria: ese texto ES el encuadre del cuadro llave, y para
    el clip se usa su traducción al inglés (`libres_en`) —si la traducción
    falló, va el castellano, que Veo entiende peor pero entiende—."""
    if toma in TOMAS:
        return TOMAS[toma]
    texto = ((req.get("libres") or {}).get(toma) or "").strip()
    ingles = ((req.get("libres_en") or {}).get(toma) or "").strip() or texto
    return {
        "label": _label_libre(texto),
        "ayuda": texto,
        "encuadre": texto,
        "pose": ("la que pida el encuadre de arriba: natural, de catálogo, sin "
                 "forzar"),
        "motion": (
            f"The shot the brand asked for: {ingles}. The camera moves slowly "
            "and continuously, pushing in a little towards exactly what that "
            "description asks to show, and ends framed on it. The model holds "
            "her pose, breathing and micro-adjusting naturally."),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BLOQUES DE PROMPT DEL CUADRO LLAVE
# ─────────────────────────────────────────────────────────────────────────────

# El fondo es TODO el look. Hay que nombrar lo que no va, una por una: si no se
# lo prohíbe, el modelo dibuja la esquina del estudio o un degradé gris y el
# video deja de parecer de marca.
FONDO_BLANCO = (
    "FONDO (lo más importante de esta toma): limbo / ciclorama INFINITO BLANCO. "
    "Blanco parejo de borde a borde del cuadro. NO hay paredes, NO hay "
    "esquinas, NO hay zócalo, NO hay línea de horizonte, NO se ve la unión "
    "entre el piso y la pared, NO hay textura de tela, papel ni pared. NO hay "
    "muebles, plantas, props, cortinas, reflectores, trípodes ni equipos de "
    "estudio. NO hay otra persona.\n"
    "El piso se insinúa apenas, como un blanco un puntito más cálido que el "
    "fondo, sin que se vea dónde empieza. Se permite una caída de luz muy "
    "suave hacia los bordes de arriba, nada más.\n"
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
    "CALIDAD: fotografía real de campaña, cámara full-frame, lente 85 mm a f/4, "
    "grano fino de sensor real, foco natural. Nada de aspecto 3D, render, CGI "
    "ni sobre-nitidez.\n"
    "PROHIBIDO: texto, letras, números, logos, marcas de agua, collage, varias "
    "fotos dentro de la imagen, bordes, marcos, franjas de color."
)

# Lo que separa una foto de campaña de una "imagen de IA" no es la resolución:
# es que la IA EMBELLECE sola. Alisa la piel, empareja la cara, afina el cuerpo
# y plancha la tela — y ahí la modelo deja de parecer una persona. No alcanza
# con pedir "realista": hay que pedir cada defecto por su nombre.
REALISMO = (
    "REALISMO (lo que hace que parezca una foto y no una imagen de IA):\n"
    "- PIEL de persona real: poros, lunares, pecas, alguna vena, vellito, "
    "líneas de expresión, brillo natural desparejo. NADA de retoque de belleza, "
    "ni piel alisada, ni porcelana, ni plástico, ni aerógrafo.\n"
    "- CARA de persona real: levemente asimétrica, mirada viva, boca relajada. "
    "No la conviertas en una cara de modelo genérica ni de muñeca.\n"
    "- CUERPO tal cual está en la foto: mismo peso, misma silueta, mismas "
    "proporciones, mismas marcas de la piel. No lo afines ni lo estilices.\n"
    "- TELA de verdad: arrugas, pliegues donde el cuerpo la dobla, la costura "
    "marcándose, la caída con su peso. No la planches.\n"
    "- MANOS Y PIES bien formados, con la cantidad de dedos que corresponde."
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
                   n_refs: int, correcciones: str = "", look: int = 0) -> str:
    """Prompt del cuadro llave. `con_ancla`: la IMAGEN 1 es el cuadro ya
    generado (manda la modelo, la luz y el blanco); si no, es la foto real.
    `look`: de qué modelo/color sale esta toma, para nombrarle el color."""
    t = _toma_def(toma, req)
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

    ficha = (req.get("ficha_prenda") or "").strip()
    partes = [
        encabezado,
        refs,
        _bloque_identidad(sujeto),
    ]
    if ficha:
        # En TODAS las tomas, no sólo en la primera: el texto no se desvía, una
        # foto de referencia sí se interpreta — y en la toma de espalda, si las
        # fotos no muestran la espalda, es lo único que ancla el color.
        partes.append(
            "LA PRENDA, DESCRITA (esto manda sobre cualquier interpretación de "
            f"las fotos): {ficha}\n"
            "Si lo que estás por dibujar no coincide con esta descripción "
            "—sobre todo el COLOR y la TELA—, está mal.")
    partes += [
        f"ENCUADRE DE ESTA TOMA: {t['encuadre']}.",
        f"POSE / ACTITUD: {t['pose']}.",
        # Lo que separa a una modelo de catálogo de alguien parado con una
        # prenda puesta: sabe qué está vendiendo en ESTA toma y lo muestra. Sin
        # esto salían poses lindas pero mudas, que no señalan nada.
        ("INTENCIÓN DE ESTA TOMA (actuá como modelo profesional de catálogo, "
         "estilo Zara o Adidas): en esta toma la modelo está tratando de "
         f"{t.get('intencion', 'presentar la prenda')}. La pose, hacia dónde "
         "mira y qué hace con las manos tienen que servir a eso: la prenda es "
         "la protagonista, no ella. Actitud segura y natural, nada de pose de "
         "revista ni de gesto forzado."),
        FONDO_BLANCO,
        LUZ_ESTUDIO,
        REALISMO,
        f"FORMATO: {formato}, la imagen llena todo el cuadro (sin franjas, sin "
        "márgenes blancos agregados, sin bordes).",
        CALIDAD_FOTO,
    ]
    # El nombre del look ("Coral") va al prompt como el color de ESTA toma: con
    # varias modelos y varios colores en el mismo video, decirlo con todas las
    # letras es lo que evita que se le escape el color de la toma de al lado.
    nombre_look = _nombre_look(req, look) if look else ""
    if producto or nombre_look:
        linea = "PRODUCTO: " + (producto or "la prenda de las fotos")
        if nombre_look:
            linea += f" — la versión/color de ESTA toma es: {nombre_look}"
        partes.insert(3, linea + ".")
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
    "true-to-life colors, subtle film grain. "
    # Un video de vidriera se filma sobre slider o trípode, no a pulso: el
    # "micro-movimiento de mano" que pedíamos antes es justo lo que los modelos
    # de video traducen en tembleque y deformación de la cara.
    "The camera runs on a smooth motorized slider: the move is steady and "
    "continuous, no shake, no jitter, no sudden changes of speed. "
    # Sin esto sale todo en cámara lenta, que es el sello de "video hecho con
    # IA", y la modelo se queda dura como un maniquí.
    "REAL TIME: normal playback speed at 24 fps, the pace of a real person "
    "filmed on set — never slow motion, never sped up. The model is ALIVE, not "
    "a mannequin: she blinks, she breathes, her weight shifts, and her hair and "
    "the fabric follow her body a fraction of a second late. Her skin keeps its "
    "real texture, its pores and its marks, and her body keeps the exact weight "
    "and proportions of the first frame — never slimmed, never smoothed, never "
    "beautified. If she walks, her feet make real contact with the floor with "
    "visible heel-to-toe weight transfer: no sliding, no floating, no gliding. "
    "The background stays PURE WHITE and completely EMPTY for "
    "the whole clip: no walls, no corners, no horizon line, no floor-to-wall "
    "seam, no gray gradient, no props, no furniture, no studio equipment, no "
    "other people. The only shadow is the soft contact shadow under the feet. "
    "IDENTITY LOCK (the single most important rule): the face in the first frame "
    "is a REAL, SPECIFIC person. Her bone structure, the shape and spacing of "
    "her eyes, her nose, her mouth, her jawline, her eyebrows, her skin tone and "
    "her hairline stay EXACTLY the same in every frame. When she smiles, turns "
    "her head or moves, only the MUSCLES move: the face underneath is the same "
    "face. Do NOT re-generate, re-draw, beautify, symmetrise or smooth her face "
    "at any point \u2014 a smile must not change who she is. If the person at the "
    "end is not immediately recognisable as the person at the start, the clip is "
    "WRONG. "
    "The hair, the body and the garment stay IDENTICAL from the first "
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
    "letterboxing, pillarboxing, borders, frame around video, margins, "
    "slow motion, time lapse, speed ramp, stuttering, flickering, camera shake, "
    "waxy skin, plastic skin, airbrushed skin, retouched skin, doll face, "
    "mannequin, uncanny valley, 3d render, cgi, video game character, "
    "beautified face, slimmed body, reshaped body, frozen face, no blinking, "
    "stiff robotic movement, rubbery limbs, sliding feet, floating, gliding walk"
)


# Los NO, escritos en el prompt y no sólo en el campo `negativePrompt`: ese
# campo lo lee Veo, pero los motores de fal —Wan, Seedance, LTX, MiniMax— ni lo
# reciben. Un "no" que viaja en un campo que la mitad de los motores ignora no
# es una regla, es una intención.
NO_VIDEO = (
    " HARD NOS \u2014 breaking any of these ruins the clip: NO changing the face or "
    "the identity of the person, not even for a frame. NO morphing, warping or "
    "melting of the face, the hands or the garment. NO changing the color, the "
    "fabric, the print or the details of the garment. NO extra or missing "
    "fingers, arms or legs. NO walking in place like on a treadmill. NO slow "
    "motion. NO camera shake. NO ending on a flat, unreadable wall of fabric. "
    "NO walls, corners, horizon line or floor-to-wall seam. NO text, captions, "
    "logos or watermarks. NO black bars or borders."
)


def _prompt_clip(toma: str, req: Dict[str, Any]) -> str:
    """Movimiento de cámara sobre el cuadro llave, que ya es el primer frame."""
    t = _toma_def(toma, req)
    sujeto = req.get("sujeto", "modelo")
    quien = ("the garment" if sujeto == "prenda" else "the model")
    base = (
        "Fashion e-commerce studio video on a seamless pure white cyclorama. "
        f"The first frame is the reference image and {quien} is exactly as "
        f"shown there — do not restage it, do not change it. {t['motion']} "
    )
    if sujeto != "prenda" and t.get("intencion"):
        # La misma intención que el cuadro llave, para que el movimiento no la
        # contradiga: si la toma es para mostrar la espalda, que no se dé vuelta.
        base += ("She is a professional catalogue model and she KNOWS what this "
                 "shot is selling: her movement, where she looks and what she "
                 "does with her hands all serve showing the garment. She is "
                 "confident and natural, never posing for the sake of posing. ")
    if sujeto == "prenda":
        base += ("The garment is held by an invisible ghost mannequin: no "
                 "person, no hands, no mannequin visible. ")
    extra = (req.get("notas_video") or "").strip()
    if extra:
        base += extra + " "
    return base + SUFIJO_VIDEO + NO_VIDEO


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
                           "usar los motores de fal (Wan, Seedance, MiniMax).")
    modelo = FAL_MODELS.get(motor)
    if not modelo:
        raise RuntimeError(f"Motor desconocido: {motor}")
    payload = {
        "prompt": prompt,
        "image_url": f"data:image/jpeg;base64,{frame_b64}",
        "resolution": RESOLUCION_FAL.get(motor, "720p"),
        "duration": "5" if duracion <= 5 else "10",
    }
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    sub_url = f"{FAL_BASE}/{modelo}"
    async with httpx.AsyncClient(timeout=180) as cli:
        r = await cli.post(sub_url, headers=headers, json=payload)
        # Cada modelo de fal acepta lo suyo, y las escrituras cambian entre uno
        # y otro ("720p" contra "720P", duration en texto o en número). Ante un
        # rechazo por los datos, se sacan los opcionales de a uno y se reintenta:
        # sin resolución ni duración, el modelo usa sus valores por defecto y el
        # clip sale igual. Es lo mismo que ya hacemos con Veo.
        if r.status_code in (400, 422):
            for p in ("resolution", "duration"):
                if p in payload:
                    print(f"[videos_luma] fal {motor} rechazó '{p}' "
                          f"({r.text[:120]}); reintento sin ese campo")
                    payload.pop(p, None)
                    r = await cli.post(sub_url, headers=headers, json=payload)
                    if r.status_code in (200, 201):
                        break
        if r.status_code == 404:
            raise RuntimeError(
                f"fal HTTP 404: el modelo '{modelo}' no existe con esa ruta. "
                "Si fal se la cambió, cargá la nueva en la variable de entorno "
                f"{'FAL_MINIMAX_MODEL' if motor == 'minimax_h3' else 'FAL_WAN_MODEL / FAL_SEEDANCE_MODEL'} "
                "y no hace falta tocar el código.")
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
    motor = req.get("motor", MOTOR_DEFAULT)
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


def _clip_camara(foto: Path, salida: Path, formato: str, dur: int,
                 toma: str) -> bool:
    """El movimiento hecho en la mesa de edición: un recorte que entra (o sale)
    sobre la foto quieta. Cero costo, unos segundos de CPU.

    Dos cosas que hay que hacer sí o sí, y que no son obvias:

    - `zoompan` saca `d` cuadros POR CADA cuadro de entrada. Con una entrada en
      loop de 5 segundos son 125 x 120 = 15.000 cuadros: se cuelga. Va UNA sola
      imagen de entrada y el largo se corta con `-frames:v`.
    - La foto se agranda ANTES de recortar. Si no, en el momento de máximo zoom
      el recorte tiene menos píxeles que la salida y la toma sale blanda.

    `z` se calcula con `on` (el número de cuadro de salida) y no con el
    acumulador `zoom`: así el recorrido no depende de por dónde venía."""
    binario = _ffmpeg_bin()
    if not binario:
        return False
    w, h = _dims(formato)
    c = _camara_de(toma)
    zmax = max(float(c["z"]), 1.01)
    cuadros = max(int(dur * 24), 24)
    # Al zoom máximo el recorte tiene que medir al menos lo que la salida.
    w2 = min(int(w * zmax) // 2 * 2, 4096)
    h2 = min(int(h * zmax) // 2 * 2, 4096)
    paso = (zmax - 1.0) / max(cuadros - 1, 1)
    if c["modo"] == "pull":
        z = f"max({zmax:.4f}-on*{paso:.6f},1.0)"
    else:
        z = f"min(1.0+on*{paso:.6f},{zmax:.4f})"
    # x/y son la esquina del recorte: con zoom=1 el término se anula (se ve todo)
    # y a medida que entra, la ventana se corre hacia el ancla (ax, ay).
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase,"
          f"crop={w2}:{h2},"
          f"zoompan=z='{z}':d={cuadros}"
          f":x='(iw-iw/zoom)*{float(c['ax']):.3f}'"
          f":y='(ih-ih/zoom)*{float(c['ay']):.3f}'"
          f":s={w}x{h}:fps=24,format=yuv420p")
    cmd = [binario, "-y", "-loop", "1", "-i", str(foto), "-vf", vf,
           "-frames:v", str(cuadros), "-an",
           "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(salida)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=300)
        if res.returncode == 0 and salida.exists():
            return True
        print("[videos_luma] cámara falló: "
              + (res.stderr or b"").decode(errors="replace")[-300:])
        return False
    except Exception as e:
        print(f"[videos_luma] cámara error: {e}")
        return False


TRANSICIONES = {
    "corte": "Cortes secos (como las marcas)",
    "blanco": "Fundido a blanco, cortito",
    "fundido": "Fundido cruzado, cortito",
}
TRANSICION_SEG = 0.35   # más largo que esto ya se siente lento y amateur


def _concatenar_con_transicion(clips: List[Path], salida: Path, modo: str,
                               formato: str) -> bool:
    """Une los clips con un fundido corto entre toma y toma.

    El corte seco es lo que hacen las marcas y sigue siendo el default, pero
    entre tomas de escalas MUY distintas —un plano entero seguido de un macro—
    el salto pega feo. Un fundido de 0,35s lo suaviza sin que se note.

    `blanco` va a blanco y vuelve, que sobre un ciclorama blanco es casi
    invisible y es lo que mejor tapa el salto de escala. `fundido` es el cruce
    clásico entre las dos imágenes."""
    binario = _ffmpeg_bin()
    if not binario or len(clips) < 2:
        return False
    w, h = _dims(formato)
    d = TRANSICION_SEG
    duraciones = [max(_duracion_video(c), d + 0.1) for c in clips]
    cmd = [binario, "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    filtros: List[str] = []
    # Cada clip se pasa por fps/formato: xfade exige que las dos entradas
    # coincidan exacto, y un clip de otro motor puede venir distinto.
    for i in range(len(clips)):
        filtros.append(f"[{i}:v]scale={w}:{h},setsar=1,fps=24,format=yuv420p[v{i}]")
    trans = "fade" if modo == "fundido" else "fadewhite"
    prev, reloj = "[v0]", duraciones[0]
    for i in range(1, len(clips)):
        # offset = dónde ARRANCA el fundido en la línea de tiempo ya armada.
        # Con el reloj mal, xfade corta el clip anterior o deja un congelado.
        offset = max(reloj - d, 0)
        etq = f"[x{i}]"
        filtros.append(f"{prev}[v{i}]xfade=transition={trans}:duration={d}"
                       f":offset={offset:.3f}{etq}")
        prev = etq
        reloj = offset + d + duraciones[i] - d   # el fundido se come `d` de cada lado
    filtros.append(f"{prev}format=yuv420p[vout]")
    cmd += ["-filter_complex", ";".join(filtros), "-map", "[vout]", "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "19",
            "-movflags", "+faststart", str(salida)]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=600)
        if res.returncode == 0 and salida.exists():
            return True
        print("[videos_luma] transición falló: "
              + (res.stderr or b"").decode(errors="replace")[-300:])
        return False
    except Exception as e:
        print(f"[videos_luma] transición error: {e}")
        return False


def _concatenar(clips: List[Path], salida: Path) -> bool:
    """Cortes secos, uno atrás del otro. Es lo que hacen las marcas."""
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
        f"({', '.join(_toma_def(t, req)['label'] for t in tomas)}), "
        f"{segundos:.0f} segundos en total.\n\n"
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


async def _guardar_en_drive(jid: str, req: Dict[str, Any], final: Optional[Path],
                            cuadros: Dict[int, Path]) -> Dict[str, Any]:
    """Manda el video (y los cuadros) al Drive de la usuaria.

    Los videos viven en el disco del server, que sin un volumen montado en
    /data se borra en CADA deploy. Un video son varios dólares: perderlo es
    pagarlo dos veces. Por eso se sube apenas está listo.

    Devuelve QUÉ PASÓ de verdad, no lo que se intentó. El panel de fotos
    anunciaba "✅ guardándose en tu Drive" apenas largaba la tarea de fondo, así
    que una subida que fallaba se veía igual que una que funcionaba."""
    sub = req.get("user_sub") or CURRENT_SUB.get()
    if not await _drive_connected_for(sub):
        return {"estado": "sin_drive",
                "detalle": "Google Drive no está conectado: los archivos quedan "
                           "sólo en el server y se borran en el próximo deploy. "
                           "Conectalo desde Ajustes → Google Drive."}
    nombre = re.sub(r"[^\w\-. ]", "", (req.get("producto") or "video").strip())[:40] or "video"
    marca = time.strftime("%Y%m%d_%H%M%S")
    subidos: List[str] = []
    fallados: List[str] = []
    link_final = ""
    try:
        if final and final.exists():
            link = await drive_upload(f"luma_{nombre}_{marca}.mp4",
                                      final.read_bytes(), "video/mp4", user_sub=sub)
            (subidos if link else fallados).append("el video")
            link_final = link or ""
        # Los cuadros van SIEMPRE, haya video o no. Antes se subían sólo cuando
        # no había video, con la idea de que "el video ya los lleva adentro":
        # está mal. Un cuadro es una foto de campaña en 2K o 4K, con la modelo
        # en fondo blanco, que se paga aparte y sirve sola para la publicación.
        # Un frame arrancado de un video de 1080p no es lo mismo ni de cerca, y
        # encima el video se comprime. Eran las fotos que se generaban y se
        # perdían en cada deploy.
        for n in sorted(cuadros):
            link = await drive_upload(f"luma_{nombre}_{marca}_cuadro{n}.jpg",
                                      cuadros[n].read_bytes(), "image/jpeg",
                                      user_sub=sub)
            (subidos if link else fallados).append(f"el cuadro {n}")
    except Exception as e:
        print(f"[videos_luma][drive] {jid}: {e}")
        return {"estado": "error", "detalle": f"Drive falló: {str(e)[:160]}",
                "link": link_final}
    if fallados and not subidos:
        return {"estado": "error", "link": link_final,
                "detalle": "No pude subir nada a Drive (mirá el log del server). "
                           "El archivo sigue acá: bajalo antes del próximo deploy."}
    if fallados:
        return {"estado": "parcial", "link": link_final,
                "detalle": f"Subí {len(subidos)} a Drive, pero falló "
                           f"{', '.join(fallados)}."}
    cuenta = (f"el video y {len(subidos) - 1} cuadro(s)"
              if link_final and len(subidos) > 1
              else f"{len(subidos)} cuadro(s)" if len(subidos) > 2
              else ", ".join(subidos))
    return {"estado": "ok", "link": link_final,
            "detalle": f"Guardado en tu Google Drive ({cuenta})."}


async def _ficha_prenda(req: Dict[str, Any]) -> str:
    """La ficha de la prenda, con el MISMO análisis que usa la app de fotos.

    No se reescribe acá: `gemini_analyze` ya sabe pedir el color, la tela, la
    estampa con su escala, las terminaciones, CÓMO ES LA ESPALDA —y si no hay
    foto de la espalda avisa "(deducida, sin foto)" y elige la versión más
    conservadora en vez de inventar— y una lista de errores típicos a evitar
    para esta prenda en particular.

    Esos "EVITAR" son los NO que se pedían: no valen los mismos para todas las
    prendas, salen de mirar ESTA. Duplicar todo esto en el módulo de videos era
    condenarlo a quedar viejo cada vez que se mejorara el de fotos."""
    fotos: List[str] = req.get("fotos") or []
    vistas: List[str] = req.get("foto_vista") or []
    frente, espalda = [], []
    for i, f in enumerate(fotos[:8]):
        (espalda if (vistas[i] if i < len(vistas) else "frente") == "espalda"
         else frente).append(f)
    if not frente:                      # todas marcadas como espalda: van igual
        frente = espalda[:1]
    try:
        ficha = await gemini_analyze(frente[:5], back_b64s=espalda[:3] or None)
        return ficha_to_text(ficha)[:1400]
    except Exception as e:
        print(f"[videos_luma] ficha error: {e}")
        return ""


async def _traducir_libres(libres: Dict[str, str]) -> Dict[str, str]:
    """Pasa al inglés las tomas que escribió la usuaria, todas en UNA llamada.

    El prompt del clip va en inglés porque los modelos de video rinden bastante
    mejor así, y el cuadro llave va en castellano: son dos textos distintos para
    la misma toma. Si esto falla no se cae nada — `_toma_def` usa el castellano
    original."""
    if not libres:
        return {}
    key = await _current_api_key()
    if not key:
        return {}
    pedido = (
        "Traducí al inglés estas descripciones de tomas de un video de moda de "
        "catálogo. Son instrucciones de encuadre para un modelo de video: "
        "traducilas literales y técnicas, sin adornar, sin agregar nada que no "
        "esté, y usando vocabulario de cine (close-up, waistband, hem, strap, "
        "seam…).\n"
        "Devolvé SOLO un JSON, sin markdown, con las MISMAS claves:\n"
        + json.dumps(libres, ensure_ascii=False)
    )
    url = f"{GEMINI_BASE}/models/{TEXT_MODEL}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=60) as cli:
            r = await cli.post(url, headers={"x-goog-api-key": key,
                                             "Content-Type": "application/json"},
                               json={"contents": [{"parts": [{"text": pedido}]}]})
        if r.status_code != 200:
            print(f"[videos_luma] traducción HTTP {r.status_code}: {r.text[:200]}")
            return {}
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        data = json.loads(raw)
        return {k: str(v).strip() for k, v in data.items()
                if k in libres and str(v or "").strip()}
    except Exception as e:
        print(f"[videos_luma] traducción error: {e}")
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


async def _frenado(jid: str) -> bool:
    """¿Pidió frenar? Se pregunta al KV y no a una variable en memoria: el
    pedido de frenar llega por OTRA request, y con un solo proceso una variable
    alcanzaría, pero con dos workers de Railway el trabajo se queda corriendo.

    Se frena entre toma y toma, no en el medio: cortar un clip a la mitad no
    devuelve la plata —ya se pidió y ya se paga— y encima deja el archivo roto.
    Lo que ahorra es todo lo que venía DESPUÉS."""
    job = await _job_get(jid)
    return bool(job and job.get("frenar"))


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
    # Con las fotos de ella como cuadros no se dibuja nada: no se paga imagen.
    imgs = 0.0 if req.get("cuadros_propios") else round(p_img * n, 4)
    segundos = int(req.get("segundos", 6))
    p_seg = PRECIO_SEG.get(req.get("motor", MOTOR_DEFAULT), PRECIO_SEG[MOTOR_DEFAULT])
    # Sólo se pagan los segundos que mueve la IA: los de cámara los hace ffmpeg.
    con_ia = sum(1 for t in tomas if _motor_toma(req, t) == "ia")
    video = 0.0 if req.get("solo_cuadros") else round(p_seg * segundos * con_ia, 4)
    extras = 0.0
    if not req.get("solo_cuadros") and req.get("audio") == "voz":
        extras = COSTO_GUION + COSTO_TTS
    return {
        "cuadros": n, "usd_cuadros": imgs,
        # Los segundos que se PAGAN, que ya no son todos: los de cámara son
        # gratis. El largo del video sigue siendo segundos * n.
        "segundos_video": 0 if req.get("solo_cuadros") else segundos * con_ia,
        "segundos_total": 0 if req.get("solo_cuadros") else segundos * n,
        "tomas_ia": con_ia, "tomas_camara": n - con_ia,
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
    nombres = ", ".join(_toma_def(t, req)["label"] for t in reversed(sacadas))
    return (f"Para no pasar el tope de US${tope:.2f} dejé el video en "
            f"{len(tomas)} tomas (saqué: {nombres}).")


# ─────────────────────────────────────────────────────────────────────────────
# WORKER
# ─────────────────────────────────────────────────────────────────────────────

async def _cuadro_llave(req: Dict[str, Any], toma: str, refs: List[str],
                        con_ancla: bool, settings: Dict[str, Any],
                        correcciones: str = "", look: int = 0) -> bytes:
    """Genera UNA toma como foto. `refs` = imágenes b64 en orden (la 1 manda)."""
    prompt = _prompt_cuadro(toma, req, con_ancla, len(refs), correcciones, look)
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
    por_look, look_base = _fotos_por_look(req)
    toma_look: Dict[str, int] = req.get("toma_look") or {}
    varios_looks = len(por_look) > 1
    gastado_img, gastado_video = 0.0, 0.0
    try:
        # ── 1) Cuadros llave ────────────────────────────────────────────────
        cuadros: Dict[int, Path] = {}
        # Un ancla POR LOOK, no una sola para el video. La segunda toma de una
        # modelo mira la primera de ELLA y le queda igual la cara; la modelo
        # siguiente arranca limpia de sus propias fotos, que es justamente lo
        # que hace que sea otra modelo con otro color.
        anclas: Dict[int, str] = {}
        p_img = _precio_cuadro(settings, req.get("calidad_cuadro", "2K"))

        def _etiqueta(t: str) -> str:
            base = _toma_def(t, req)["label"]
            L = toma_look.get(t, look_base)
            return f"{base} · {_etiqueta_look(req, L)}" if varios_looks else base

        estados = [{"n": i + 1, "toma": t, "label": _etiqueta(t),
                    "look": toma_look.get(t, look_base), "estado": "pendiente"}
                   for i, t in enumerate(tomas)]
        await _job_set(jid, {"estado": "cuadros", "tomas": estados,
                             "detalle": "Mirando bien la prenda antes de dibujar…"})
        # Una sola llamada de texto, antes de dibujar nada: describe la prenda
        # real y ese texto viaja en TODAS las tomas. Es lo que evita que la de
        # espalda salga de otro color.
        if not req.get("cuadros_propios"):
            req["ficha_prenda"] = await _ficha_prenda(req)
            if req["ficha_prenda"]:
                await _job_set(jid, {"ficha_prenda": req["ficha_prenda"]})
        await _job_set(jid, {"detalle": "Armando el primer cuadro en fondo blanco…"})

        # Sus fotos YA son las tomas: no hay nada que dibujar ni que revisar.
        # Van en orden —la foto 1 es la toma 1— y no se paga un centavo de
        # imagen. Si trajo menos fotos que tomas, el video sale con las que hay.
        if req.get("cuadros_propios"):
            for i, toma in enumerate(tomas):
                if i >= len(fotos_b64):
                    estados[i]["estado"] = "error"
                    estados[i]["error"] = "No subiste una foto para esta toma"
                    continue
                p = d / f"cuadro_{i + 1}.jpg"
                try:
                    p.write_bytes(base64.b64decode(fotos_b64[i]))
                    cuadros[i + 1] = p
                    estados[i]["estado"] = "listo"
                except Exception as e:
                    estados[i]["estado"] = "error"
                    estados[i]["error"] = str(e)[:200]
            await _job_set(jid, {"tomas": estados,
                                 "detalle": "Tus fotos entran como tomas…"})

        for i, toma in ([] if req.get("cuadros_propios")
                        else list(enumerate(tomas))):
            n = i + 1
            if await _frenado(jid):
                for e in estados[i:]:
                    if e["estado"] == "pendiente":
                        e["estado"] = "frenada"
                # Se guarda ANTES de salir: el `break` se saltea el _job_set
                # que está al final del bucle, y sin esto el panel se queda
                # mostrando el estado de antes de frenar.
                await _job_set(jid, {"tomas": estados})
                break
            L = toma_look.get(toma, look_base)
            suyas = por_look.get(L) or fotos_b64      # sin fotos propias, las de todos
            ancla_b64 = anclas.get(L)
            estados[i]["estado"] = "generando"
            await _job_set(jid, {"tomas": estados,
                                 "detalle": f"Cuadro {n}/{len(tomas)} — "
                                            f"{_etiqueta(toma)}…"})
            # El ancla se dibuja mirando las fotos DE ESE LOOK; el resto del
            # look mira el ancla primero, que es lo que mantiene la cara, la luz
            # y el blanco. Las fotos de los otros looks no entran nunca: son
            # otro color, y acá adentro cuentan como "la verdad del diseño".
            refs = ([ancla_b64] + suyas[:3]) if ancla_b64 else suyas[:4]
            try:
                img = await _cuadro_llave(req, toma, refs, ancla_b64 is not None,
                                          settings, look=L)
                gastado_img += p_img

                # Inspector de prenda: una vez por look, sobre su ancla. Es la
                # toma que define a las demás de ese look, así que si ahí se
                # coló un cambio de diseño se arrastra. Y se compara contra las
                # fotos DE ESE LOOK: contra las de otro color reportaría
                # diferencias siempre y pagaríamos un rehacer al pepe.
                # El inspector mira TODAS las tomas, no sólo el ancla. Antes
                # se revisaba la primera y las demás salían a ciegas: así se
                # coló una toma de espalda negra en un pijama bordó, que es
                # justo la más expuesta (si las fotos no muestran la espalda,
                # el modelo la inventa). Una revisión de texto sale mucho menos
                # que un cuadro mal, y muchísimo menos que animarlo.
                if (str(settings.get("qc_prenda", "si")) == "si"
                        and str(req.get("qc", "si")) == "si"):
                    await _job_set(jid, {"detalle": "Revisando que la prenda "
                                                    "sea igual a la real…"})
                    # Con la espalda rotulada aparte: sin esto, el inspector
                    # comparaba una toma de atrás contra fotos del frente y
                    # cantaba diferencias que no existían (o peor, dejaba pasar
                    # una espalda inventada por no tener contra qué).
                    qc = await verificar_prenda(img, _vista_de(req, L, "frente")[:3],
                                                back_b64s=_vista_de(req, L, "espalda")[:2] or None)
                    umbral = int(settings.get("qc_umbral", 9) or 9)
                    if qc and int(qc.get("puntaje", 10)) < umbral:
                        difs = "; ".join(qc.get("diferencias", []))[:500]
                        await _job_set(jid, {"detalle": "La prenda salió con "
                                                        "diferencias; rehago el cuadro…"})
                        # Se rehace CON el mismo ancla que tenía. Antes iba
                        # siempre sin ancla —daba igual, porque sólo corría en
                        # la primera toma—, pero ahora corre en todas: sin el
                        # ancla, la corrección arregla la prenda y de paso te
                        # cambia la cara y la luz.
                        img = await _cuadro_llave(req, toma, refs,
                                                  ancla_b64 is not None,
                                                  settings, correcciones=difs,
                                                  look=L)
                        gastado_img += p_img
                        estados[i]["qc"] = f"corregido ({qc.get('puntaje')}/10)"

                p = d / f"cuadro_{n}.jpg"
                p.write_bytes(img)
                cuadros[n] = p
                if L not in anclas:
                    anclas[L] = _compress_ref(img, max_dim=1280, q=92)
                estados[i]["estado"] = "listo"
            except Exception as e:
                estados[i]["estado"] = "error"
                estados[i]["error"] = str(e)[:300]
            await _job_set(jid, {"tomas": estados})

        if not cuadros:
            raise RuntimeError("No salió ningún cuadro llave. Revisá que la "
                               "foto muestre bien la prenda y probá de nuevo.")

        # Frenó durante los cuadros: NO se sigue a la etapa cara. Los cuadros
        # que ya salieron se guardan igual — están pagos, y sirven para generar
        # el video después sin volver a dibujarlos.
        freno_temprano = await _frenado(jid)
        if req.get("solo_cuadros") or freno_temprano:
            await budget_record("video_cuadros", req.get("calidad_cuadro", "2K"),
                                gastado_img, len(cuadros),
                                note=f"cuadros llave · {req.get('producto', '')[:40]}")
            await _job_set(jid, {"detalle": "Guardando los cuadros en tu Drive…"})
            drive = await _guardar_en_drive(jid, req, None, cuadros)
            await _job_set(jid, {"drive": drive})
            await _job_set(jid, {
                "estado": "listo", "tomas": estados, "frenado": freno_temprano,
                "detalle": (f"Frenado ✋ Alcancé a hacer {len(cuadros)} "
                            "cuadro(s) y no gasté nada de video."
                            if freno_temprano else
                            "Cuadros listos ✅ Mirálos y, si te gustan, "
                            "generá el video."),
                "solo_cuadros": True, "terminado": time.time(),
                "costo": {"usd_cuadros": round(gastado_img, 4),
                          "usd_video": 0.0,
                          "usd_total": round(gastado_img, 4)}})
            return

        # ── 2) Movimiento ───────────────────────────────────────────────────
        # Las tomas escritas por la usuaria se traducen recién acá: el cuadro
        # llave las usa en castellano y el clip en inglés, así que si el trabajo
        # era "sólo cuadros" ya volvimos arriba sin gastar la llamada. Y las
        # traduce sólo si alguna toma libre va con IA: para las de cámara, el
        # texto en inglés no lo lee nadie.
        if req.get("libres") and any(_motor_toma(req, t) == "ia"
                                     for t in req["libres"] if t in tomas):
            req["libres_en"] = await _traducir_libres(req["libres"])
        dur = int(req.get("segundos", 6))
        if dur not in DURACIONES_OK:
            dur = 6
        p_seg = PRECIO_SEG.get(req.get("motor", MOTOR_DEFAULT), PRECIO_SEG[MOTOR_DEFAULT])
        clips: List[Path] = []
        for i, toma in enumerate(tomas):
            n = i + 1
            if n not in cuadros:
                continue
            if await _frenado(jid):
                # De acá en adelante NINGUNA se animó, aunque su cuadro diga
                # "listo" de la etapa anterior: el estado de la lista es el del
                # VIDEO. Sin esto, el panel mostraba tildes en tomas que no
                # existen en el video que se entrega.
                for e in estados[i:]:
                    if e["estado"] != "error":
                        e["estado"] = "frenada"
                await _job_set(jid, {"tomas": estados})   # ídem: el break saltea el de abajo
                break
            con_ia = _motor_toma(req, toma) == "ia"
            estados[i]["estado"] = "animando"
            await _job_set(jid, {"estado": "animando", "tomas": estados,
                                 "detalle": f"Moviendo la toma {n}/{len(tomas)} "
                                            + (f"con {MOTOR_LABEL.get(req.get('motor'), '')}…"
                                               if con_ia else "con la cámara (gratis)…")})
            destino = d / f"clip_{n}.mp4"
            try:
                if not con_ia:
                    # A un hilo aparte: son unos segundos de ffmpeg, pero
                    # bloqueando el loop el panel se queda sin actualizar.
                    ok = await asyncio.to_thread(
                        _clip_camara, cuadros[n], destino,
                        req.get("formato", "9:16"), dur, toma)
                    if not ok:
                        raise RuntimeError("No pude armar el movimiento de "
                                           "cámara (revisá que haya ffmpeg).")
                else:
                    frame = _compress_ref(cuadros[n].read_bytes(), max_dim=1536, q=94)
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
        modo_tr = req.get("transicion", "corte")
        unido = False
        if modo_tr in ("blanco", "fundido") and len(clips) > 1:
            unido = _concatenar_con_transicion(clips, crudo, modo_tr,
                                               req.get("formato", "9:16"))
            if not unido:
                # Si el fundido falla, el video sale igual con cortes secos:
                # entregar el video es más importante que la transición.
                aviso_tr = "No pude aplicar la transición; salió con cortes secos."
                await _job_set(jid, {"aviso": aviso_tr})
        if not unido and not _concatenar(clips, crudo):
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
        # A Drive ANTES de dar el trabajo por terminado: el video vale varios
        # dólares y el disco del server se borra en cada deploy.
        await _job_set(jid, {"detalle": "Guardando el video en tu Drive…"})
        drive = await _guardar_en_drive(jid, req, final, cuadros)
        if drive["estado"] not in ("ok", "sin_drive"):
            aviso = (aviso + " " if aviso else "") + drive["detalle"]
        frenado = await _frenado(jid)
        await _job_set(jid, {
            "estado": "listo", "frenado": frenado, "drive": drive,
            "detalle": (f"Frenado ✋ Te armé el video con las {len(clips)} "
                        "tomas que ya estaban pagas." if frenado
                        else "Video listo ✅"),
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
    # Si sus fotos SON las tomas, se guardan más grandes: de ahí sale el recorte
    # del movimiento de cámara, y al zoom máximo (1,6) el recorte de una foto de
    # 1600px tiene 1000 y la salida pide 1080 — o sea, saldría blanda.
    tope_px = 2200 if payload.get("cuadros_propios") else 1600
    livianas = []
    for f in fotos:
        try:
            livianas.append(_compress_ref(base64.b64decode(f), max_dim=tope_px, q=92))
        except Exception:
            livianas.append(f)   # si no la puedo abrir, va tal cual
    # El look de cada foto viaja en paralelo, así que se reordena CON ella: si
    # se reordenaran por separado, cada foto terminaría con el color de otra.
    crudos = payload.get("foto_look") or []
    vistas_in = payload.get("foto_vista") or []
    pares: List[Tuple[str, int, str]] = []
    for i, f in enumerate(livianas):
        try:
            L = int(crudos[i])
        except (IndexError, TypeError, ValueError):
            L = 1
        v = str(vistas_in[i]) if i < len(vistas_in) else "frente"
        pares.append((f, L if 1 <= L <= MAX_LOOKS else 1,
                      v if v in VISTAS else "frente"))
    # La foto marcada con la modelo va primera: es el ancla de su look.
    principal = int(payload.get("foto_principal") or 1)
    if 1 <= principal <= len(pares):
        pares = [pares[principal - 1]] + [p for i, p in enumerate(pares)
                                          if i != principal - 1]
    pares = pares[:MAX_FOTOS]
    req["fotos"] = [p[0] for p in pares]
    req["foto_look"] = [p[1] for p in pares]
    req["foto_vista"] = [p[2] for p in pares]

    nombres: Dict[str, str] = {}
    for k, v in (payload.get("looks_nombre") or {}).items():
        try:
            L = int(k)
        except (TypeError, ValueError):
            continue
        if 1 <= L <= MAX_LOOKS and str(v or "").strip():
            nombres[str(L)] = str(v).strip()[:40]
    req["looks_nombre"] = nombres

    # Las tomas libres sin texto no existen: sin descripción no hay encuadre que
    # pedirle al motor, y una toma vacía saldría inventada.
    libres: Dict[str, str] = {}
    for k, v in (payload.get("libres") or {}).items():
        if LIBRE_RE.match(str(k)) and str(v or "").strip():
            libres[str(k)] = str(v).strip()[:300]
        if len(libres) >= MAX_LIBRES:
            break
    req["libres"] = libres

    tomas = [t for t in (payload.get("tomas") or TOMAS_DEFAULT)
             if t in TOMAS or t in libres]
    if not tomas:
        tomas = list(TOMAS_DEFAULT)
    req["tomas"] = tomas[:MAX_TOMAS]

    # A qué look va cada toma. Va por NOMBRE de toma y no por posición: la lista
    # se filtra y se recorta acá arriba, y una lista paralela quedaría corrida.
    # Una toma apuntada a un look sin fotos cae al look base en vez de fallar.
    disponibles = set(req["foto_look"])
    base = req["foto_look"][0] if req["foto_look"] else 1
    pedidos = payload.get("toma_look") or {}
    toma_look: Dict[str, int] = {}
    for t in req["tomas"]:
        try:
            L = int(pedidos.get(t, base))
        except (TypeError, ValueError):
            L = base
        toma_look[t] = L if L in disponibles else base
    req["toma_look"] = toma_look

    # El motor de cada toma, también por nombre. Por defecto "ia", que es como
    # venía funcionando: nadie se encuentra con un video distinto sin pedirlo.
    motores = payload.get("toma_motor") or {}
    req["toma_motor"] = {t: (motores.get(t) if motores.get(t) in MOTORES_TOMA
                             else "ia") for t in req["tomas"]}
    req["cuadros_propios"] = bool(payload.get("cuadros_propios"))

    if req.get("formato") not in ("9:16", "16:9"):
        req["formato"] = "9:16"
    # Un motor que no existe NO se cambia calladito. Antes caía a Veo sin decir
    # nada, y el síntoma era "elegí otro motor y me siguió usando Gemini": el
    # panel viejo que quedó en la caché del navegador manda un nombre que este
    # archivo ya no conoce, y el video salía —y se pagaba— con otro motor.
    pedido_motor = str(req.get("motor") or "").strip()
    if pedido_motor not in PRECIO_SEG:
        req["motor"] = MOTOR_DEFAULT
        req["motor_aviso"] = (
            f"Pediste el motor «{pedido_motor}», que no existe acá, así que usé "
            f"{MOTOR_LABEL[MOTOR_DEFAULT]}. Si acabás de cambiarlo en el panel, "
            "refrescá la página con Ctrl+F5 (o cerrá y abrí la pestaña)."
            if pedido_motor else "")
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
    if req.get("transicion") not in TRANSICIONES:
        req["transicion"] = "corte"
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
async def api_generar(request: Request,
                      payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    # La cuenta se anota ACÁ, con el pedido en la mano. El worker corre después
    # y de fondo: leer ahí a quién pertenece el trabajo es pedirle al contexto
    # que sobreviva a un salto de tarea.
    req["user_sub"] = session_sub_from_request(request)
    if not await _current_api_key():
        raise HTTPException(500, "Falta la API key de Google (cargala en "
                                 "Ajustes o en las variables del servidor).")
    settings = await get_settings()
    # La key de fal se chequea ACÁ y no cuando toca animar: si falta, el trabajo
    # dibujaba los cuadros —que se pagan— y recién ahí fallaba clip por clip.
    if (req["motor"] in FAL_MODELS and not req.get("solo_cuadros")
            and not (FAL_KEY or str(settings.get("fal_api_key") or "").strip())):
        raise HTTPException(400, f"{MOTOR_LABEL[req['motor']]} corre en fal.ai y "
                                 "no encuentro la key: cargala en Ajustes (API "
                                 "key de fal) o como FAL_KEY en Railway. Elegí "
                                 "un motor Veo mientras tanto.")
    aviso = _recortar_por_tope(req, settings)
    if req.get("motor_aviso"):
        aviso = ((aviso + " ") if aviso else "") + req["motor_aviso"]
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
        "looks": sorted(set(req["foto_look"])),
        "tomas": [{"n": i + 1, "toma": t,
                   "label": _toma_def(t, req)["label"],
                   "look": req["toma_look"].get(t),
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


@router.post(ROUTE_PREFIX + "/api/frenar/{jid}")
async def api_frenar(jid: str) -> Dict[str, Any]:
    """Frena un trabajo en curso. Se corta entre toma y toma: lo que ya se pidió
    a ML—perdón, al motor— ya se paga, pero todo lo que venía después no."""
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "No encontré ese trabajo")
    if job.get("estado") in ("listo", "error"):
        return {"ok": False, "detalle": "Ese trabajo ya había terminado."}
    await _job_set(jid, {"frenar": True,
                         "detalle": "Frenando… termino la toma que está en "
                                    "curso y corto (esa ya está paga)."})
    return {"ok": True}


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
        "max_tomas": MAX_TOMAS, "max_looks": MAX_LOOKS, "max_fotos": MAX_FOTOS,
        "drive": await _drive_connected_for(CURRENT_SUB.get()),
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
  .foto .vista{position:absolute;bottom:0;left:0;right:0;background:rgba(0,0,0,.62);
    color:#fff;font-size:10.5px;text-align:center;padding:2px;letter-spacing:.06em;
    text-transform:uppercase;cursor:pointer}
  .foto.principal .vista{bottom:18px}
  .foto .look{position:absolute;top:4px;left:4px;min-width:24px;height:24px;padding:0 6px;
    border-radius:99px;background:rgba(201,168,107,.94);color:#17140d;font-size:12px;
    font-weight:600;display:flex;align-items:center;justify-content:center}
  .planfila{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 12px;
    border:1px solid var(--line);border-radius:11px;background:var(--card-2);margin-top:8px}
  .planfila .plann{font-weight:600;color:var(--rose-deep);min-width:18px}
  .planfila .plant{font-size:14.5px}
  .planfila .chips{margin:0 0 0 auto}
  .planfila .chip{padding:6px 12px;font-size:13px}
  .libre{display:flex;align-items:center;gap:8px;margin-top:8px}
  .libre .looknum.off{background:var(--card-2);color:var(--ink-soft);border:1px solid var(--line)}
  .libre .looknum{width:30px;height:30px;flex:none;border-radius:99px;background:var(--rose);
    color:#17140d;font-size:13px;font-weight:600;display:flex;align-items:center;
    justify-content:center}
  .libre input{flex:1}
  .libre .x{width:34px;height:34px;flex:none;border-radius:50%;border:1px solid var(--line);
    background:var(--card-2);color:var(--ink-soft);display:flex;align-items:center;
    justify-content:center;cursor:pointer;font-size:16px}
  .btn{display:block;width:100%;padding:15px;border-radius:12px;border:none;
    background:var(--rose);color:#17140d;font-family:Jost,sans-serif;font-size:17px;
    font-weight:500;cursor:pointer;margin-top:16px}
  .btn:disabled{opacity:.5;cursor:not-allowed}
  .btn.sec{background:var(--card-2);color:var(--ink);border:1px solid var(--line)}
  .btn.mini{width:auto;padding:9px 15px;font-size:14.5px;margin-top:10px}
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
  <div class="note" id="notaFotos">La <b>principal</b> es la que manda: de ahí salen
  la cara, el cuerpo y el color de la prenda. Las otras se usan para copiar bien
  los detalles (costuras, espalda, terminaciones).</div>

  <div class="chips" style="margin-top:10px">
    <div class="chip" id="chipMulti">Varias modelos / varios colores</div>
    <div class="chip" id="chipPropios">Mis fotos YA son las tomas</div>
  </div>
  <div id="propiosBox" class="oculto">
    <div class="note">No dibujo ningún cuadro: <b>la foto 1 es la toma 1, la foto
    2 es la toma 2</b>, y así. No pagás imágenes y la prenda sale pixel por pixel
    de tus fotos. Subilas en el orden en que las querés ver, y elegí abajo la
    misma cantidad de tomas que de fotos.</div>
  </div>
  <div id="looksBox" class="oculto">
    <div class="note">Cada <b>look</b> es una modelo con su color. Tocá el
    numerito de cada foto para mandarla a otro look, y tocá la foto para que sea
    la que manda ahí (cara, cuerpo y color salen de ella). Después, abajo, elegís
    de qué look sale cada toma.</div>
    <div id="looksNombres"></div>
  </div>

  <label>Producto (opcional)</label>
  <input id="producto" placeholder="Ej: Conjunto seamless línea Aura, negro">

  <label>Indicaciones para la marca (opcional)</label>
  <textarea id="notas" placeholder="Ej: que se vea el detalle del encaje del escote, sin calzado"></textarea>

  <label>Qué se ve</label>
  <div class="chips" id="sujeto">
    <div class="chip on" data-v="modelo">Mi modelo con la prenda</div>
    <div class="chip" data-v="prenda">La prenda sola (maniquí fantasma)</div>
  </div>

  <label>Tomas del video <span style="color:var(--rose-deep)">· tocá para sumarla o sacarla; el número es el orden</span></label>
  <div class="chips" id="tomas"></div>
  <div class="hint" id="tomasAyuda" style="margin-top:8px"></div>
  <div id="libres"></div>
  <button class="btn sec mini" id="btnLibre">+ Una toma mía</button>
  <div class="note">Las tomas las elegís vos: tocás las que querés y en el orden
  que las tocás. Y si el detalle que te importa no está en la lista, escribilo
  vos en <b>una toma mía</b> (ej. "primer plano del ruedo del short, de
  costado").</div>
  <div id="avisoEspalda"></div>
  <div id="plan"></div>

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
    <option value="seedance" selected>Seedance Lite — el que mejor salió en la prueba, y el más barato (US$0,036/s)</option>
    <option value="ltx23_fast">LTX 2.3 Fast — el más barato de los nuevos (US$0,04/s)</option>
    <option value="wan">Wan 2.6 — económico (US$0,05/s)</option>
    <option value="veo_lite">Veo 3.1 Lite — el Veo barato (US$0,08/s)</option>
    <option value="ltx25_fast">LTX 2.5 Fast — nuevo (US$0,13/s)</option>
    <option value="seedance_pro">Seedance Pro — el mismo Seedance pero a 1080p (US$0,148/s)</option>
    <option value="veo_fast">Veo 3.1 Fast — el que venía puesto antes (US$0,15/s)</option>
    <option value="ltx25_pro">LTX 2.5 Pro — nuevo, máxima calidad de LTX (US$0,17/s)</option>
    <option value="minimax_h3">MiniMax H3 — nuevo, hasta 2K (US$0,26/s)</option>
    <option value="veo_standard">Veo 3.1 — máxima calidad, el más caro (US$0,40/s)</option>
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

  <label>Cómo se pega una toma con la otra</label>
  <div class="chips" id="transicion">
    <div class="chip on" data-v="corte">Cortes secos</div>
    <div class="chip" data-v="blanco">Fundido a blanco</div>
    <div class="chip" data-v="fundido">Fundido cruzado</div>
  </div>
  <div class="hint" style="margin-top:6px">El corte seco es lo que hacen las
  marcas. Los fundidos son cortitos (0,35s) y sirven cuando una toma entera va
  pegada a un macro: el salto de tamaño pega feo.</div>

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
  <div id="driveAviso"></div>
  <div class="err oculto" id="errorBox"></div>
</div>

<div class="card oculto" id="jobCard">
  <div class="estado"><div class="punto" id="punto"></div><div id="detalle">…</div></div>
  <div class="lista" id="listaTomas"></div>
  <div id="avisoBox"></div>
  <button class="btn sec" id="btnFrenar">✋ Frenar</button>
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
const MAX_TOMAS = %%MAX_TOMAS%%, MAX_LIBRES = %%MAX_LIBRES%%;
const MAX_LOOKS = %%MAX_LOOKS%%, MAX_FOTOS = %%MAX_FOTOS%%;
let FOTOS = [], PRINCIPAL = 1, ORDEN = %%DEFAULT_JSON%%, JOB = null, TIMER = null;
let LIBRES = {}, LIBRE_N = 0;   // libre_1 -> "primer plano del ruedo del short"
// Un look es una modelo con su color. FOTO_LOOK va en paralelo a FOTOS.
let MULTI = false, FOTO_LOOK = [], LOOK_NOMBRE = {}, TOMA_LOOK = {};
const VISTAS = ["frente", "perfil", "espalda"];
let FOTO_VISTA = [];   // de qué lado está sacada cada foto
// Quién mueve cada toma: "ia" (paga) o "camara" (ffmpeg, gratis).
let TOMA_MOTOR = {}, PROPIOS = false;
// El texto que escribe ella nunca entra como HTML: un "<" le rompería el chip.
const txt = (nodo, s) => { nodo.appendChild(document.createTextNode(s)); return nodo; };
const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g,
  c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

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
const etiqueta = k => TOMAS[k] ? TOMAS[k].label : etiquetaLibre(LIBRES[k]);
function etiquetaLibre(t){
  t = (t||"").trim();
  if(!t) return "Toma mía (escribila)";
  return "Toma mía: " + (t.length > 28 ? t.slice(0,28) + "…" : t);
}

function pintarTomas(){
  const c = $("#tomas"); c.innerHTML = "";
  Object.keys(TOMAS).concat(Object.keys(LIBRES)).forEach(k => {
    const i = ORDEN.indexOf(k);
    const d = document.createElement('div');
    d.className = 'chip' + (i >= 0 ? ' on' : '');
    d.innerHTML = (i >= 0 ? '<span class="num">'+(i+1)+'.</span> ' : '');
    txt(d, etiqueta(k));
    d.onclick = () => {
      const j = ORDEN.indexOf(k);
      if(j >= 0) ORDEN.splice(j, 1);
      else if(ORDEN.length >= MAX_TOMAS){
        error("Hasta " + MAX_TOMAS + " tomas por video. Sacá una para sumar otra.");
        return;
      } else ORDEN.push(k);
      if(!ORDEN.length) ORDEN = [k];
      // pintarLibres también: el renglón de una toma mía muestra su número de
      // orden, y si se apaga desde el chip tiene que apagarse ahí también.
      error(""); pintarTomas(); pintarLibres(); pintarPlan(); estimar();
    };
    d.onmouseenter = () => {
      $("#tomasAyuda").textContent = TOMAS[k] ? TOMAS[k].ayuda : (LIBRES[k] || "");
    };
    c.appendChild(d);
  });
  $("#tomasAyuda").textContent = ORDEN.map(etiqueta).join(" → ");
  // Si pide una toma de atrás sin una foto de atrás, el modelo INVENTA la
  // espalda entera — así salió un pijama bordó con la espalda negra satinada.
  const deAtras = ORDEN.some(k => k === 'espalda' || k === 'detalle_espalda');
  $("#avisoEspalda").innerHTML = deAtras
    ? '<div class="note">Pediste una toma de <b>espalda</b>: subí también una '
      + 'foto de la prenda DE ATRÁS. Si no la ve, la inventa — y suele salir de '
      + 'otro color y con otras terminaciones.</div>'
    : "";
}

/* ---------- tomas mías (las que escribe ella) ---------- */
function pintarLibres(){
  const c = $("#libres"); c.innerHTML = "";
  Object.keys(LIBRES).forEach(k => {
    const d = document.createElement('div');
    d.className = 'libre';
    const dentro = ORDEN.indexOf(k) >= 0;
    d.innerHTML = '<span class="looknum' + (dentro ? '' : ' off') + '">'
      + (dentro ? (ORDEN.indexOf(k) + 1) : '–') + '</span>'
      + '<input placeholder="Qué se ve en esta toma. Ej: primer plano '
      + 'del ruedo del short, de costado"><div class="x">×</div>';
    const inp = d.querySelector('input');
    inp.value = LIBRES[k];
    inp.oninput = () => {
      LIBRES[k] = inp.value;
      // Escribir ES querer la toma. Si estaba afuera —porque tocó su chip y lo
      // apagó sin querer— vuelve sola: el texto quedaba en pantalla y la toma
      // no se generaba, y no había forma de darse cuenta.
      if(inp.value.trim() && ORDEN.indexOf(k) < 0 && ORDEN.length < MAX_TOMAS){
        ORDEN.push(k); pintarTomas(); pintarPlan(); estimar();
      }
    };
    // Se repinta al salir del campo, no en cada tecla: si no, el chip cambia de
    // ancho letra por letra y el teclado del celular se cierra.
    inp.onblur = () => { pintarTomas(); pintarPlan(); pintarLibres(); };
    d.querySelector('.x').onclick = () => {
      delete LIBRES[k]; delete TOMA_LOOK[k];
      const j = ORDEN.indexOf(k); if(j >= 0) ORDEN.splice(j, 1);
      pintarLibres(); pintarTomas(); pintarPlan(); estimar();
    };
    c.appendChild(d);
  });
}

/* ---------- looks (una modelo con su color) ---------- */
const looksUsados = () =>
  [...new Set(FOTOS.map((_, i) => FOTO_LOOK[i] || 1))].sort((a, b) => a - b);
const nombreLook = L => (LOOK_NOMBRE[L] || "").trim() || ("Look " + L);

function pintarLooks(){
  const c = $("#looksNombres"); c.innerHTML = "";
  if(!MULTI) return;
  looksUsados().forEach(L => {
    const d = document.createElement('div');
    d.className = 'libre';
    d.innerHTML = '<span class="looknum">'+L+'</span>'
      + '<input placeholder="Cómo se llama este look. Ej: Coral. Opcional">';
    const inp = d.querySelector('input');
    inp.value = LOOK_NOMBRE[L] || "";
    inp.oninput = () => { LOOK_NOMBRE[L] = inp.value; };
    inp.onblur = () => pintarPlan();
    c.appendChild(d);
  });
}

/* El plan del video: una fila por toma, en orden, con lo que se decide toma por
   toma — quién la mueve y, si hay varias modelos, de qué look sale. */
function pintarPlan(){
  const c = $("#plan"); c.innerHTML = "";
  const usados = looksUsados();
  const conLooks = MULTI && usados.length > 1;
  if(!ORDEN.length) return;
  c.innerHTML = '<label>El video, toma por toma</label>';
  ORDEN.forEach((k, i) => {
    const fila = document.createElement('div');
    fila.className = 'planfila';
    fila.innerHTML = '<span class="plann">'+(i+1)+'.</span>';
    fila.appendChild(txt(Object.assign(document.createElement('span'),
                                       {className:'plant'}), etiqueta(k)));
    const chips = document.createElement('div');
    chips.className = 'chips';

    // Quién mueve esta toma. Es lo que más mueve el precio, así que va primero.
    [["ia","Con IA"],["camara","Sólo cámara · gratis"]].forEach(([v, texto]) => {
      const b = document.createElement('div');
      b.className = 'chip' + ((TOMA_MOTOR[k] || "ia") === v ? ' on' : '');
      b.textContent = texto;
      b.onclick = () => { TOMA_MOTOR[k] = v; pintarPlan(); estimar(); };
      chips.appendChild(b);
    });

    if(conLooks){
      // Si el look que tenía se quedó sin fotos, la toma vuelve al primero: sin
      // esto la fila queda sin ninguna opción marcada y el server decide solo.
      if(usados.indexOf(TOMA_LOOK[k]) < 0) TOMA_LOOK[k] = usados[0];
      usados.forEach(L => {
        const b = document.createElement('div');
        b.className = 'chip' + (TOMA_LOOK[k] === L ? ' on' : '');
        b.textContent = nombreLook(L);
        b.onclick = () => { TOMA_LOOK[k] = L; pintarPlan(); };
        chips.appendChild(b);
      });
    }
    fila.appendChild(chips);
    // En las tomas donde el cuerpo se mueve, la cámara sola se nota: la modelo
    // queda congelada mientras el recorte entra. Se avisa, no se prohíbe.
    if(TOMA_MOTOR[k] === "camara" && TOMAS[k] && TOMAS[k].vivo){
      const av = document.createElement('div');
      av.className = 'hint'; av.style.cssText = "width:100%;margin:6px 0 0";
      av.textContent = "En esta toma se mueve el cuerpo: sólo con cámara, la "
        + "modelo va a quedar quieta y se nota.";
      fila.appendChild(av);
    }
    c.appendChild(fila);
  });
}

$("#chipPropios").onclick = () => {
  PROPIOS = !PROPIOS;
  $("#chipPropios").classList.toggle('on', PROPIOS);
  $("#propiosBox").classList.toggle('oculto', !PROPIOS);
  // Con las fotos como tomas, los looks no tienen sentido: cada foto es la
  // toma que es, no la referencia de una modelo.
  if(PROPIOS && MULTI) $("#chipMulti").onclick();
  $("#chipMulti").classList.toggle('oculto', PROPIOS);
  pintarFotos(); estimar();
};

$("#chipMulti").onclick = () => {
  MULTI = !MULTI;
  $("#chipMulti").classList.toggle('on', MULTI);
  $("#looksBox").classList.toggle('oculto', !MULTI);
  $("#notaFotos").classList.toggle('oculto', MULTI);
  pintarFotos(); pintarLooks(); pintarPlan(); estimar();
};

$("#btnLibre").onclick = () => {
  if(Object.keys(LIBRES).length >= MAX_LIBRES)
    return error("Hasta " + MAX_LIBRES + " tomas tuyas por video.");
  if(ORDEN.length >= MAX_TOMAS)
    return error("Hasta " + MAX_TOMAS + " tomas por video. Sacá una para sumar otra.");
  const k = "libre_" + (++LIBRE_N);
  LIBRES[k] = "";
  ORDEN.push(k);
  error(""); pintarLibres(); pintarTomas();
  const ult = $("#libres").lastElementChild;
  if(ult) ult.querySelector('input').focus();
};

/* ---------- fotos ---------- */
/* La que MANDA en un look es la primera de ese look en la lista — el server
   agrupa por look sin cambiar el orden—. Por eso tocar una foto la manda al
   frente de SU grupo, en vez de marcarla y nada más. */
function mandaEnSuLook(i){
  const L = FOTO_LOOK[i] || 1;
  const j = FOTOS.findIndex((_, k) => (FOTO_LOOK[k] || 1) === L);
  if(j < 0 || j === i) return;
  const f = FOTOS.splice(i, 1)[0], l = FOTO_LOOK.splice(i, 1)[0];
  FOTOS.splice(j, 0, f); FOTO_LOOK.splice(j, 0, l);
}

function pintarFotos(){
  const c = $("#fotos");
  c.querySelectorAll('.foto').forEach(n => n.remove());
  const vistos = {};
  FOTOS.forEach((f, i) => {
    const L = FOTO_LOOK[i] || 1;
    const manda = MULTI ? !vistos[L] : (i + 1 === PRINCIPAL);
    vistos[L] = true;
    const d = document.createElement('div');
    d.className = 'foto' + (manda ? ' principal' : '');
    d.innerHTML = '<img src="'+f+'">'
      + (manda ? '<div class="tag">PRINCIPAL</div>' : '')
      + (MULTI ? '<div class="look">'+L+'</div>' : '')
      + '<div class="vista">' + (FOTO_VISTA[i] || 'frente') + '</div>'
      + '<div class="x">×</div>';
    d.onclick = e => {
      if(e.target.classList.contains('x')){
        FOTOS.splice(i, 1); FOTO_LOOK.splice(i, 1); FOTO_VISTA.splice(i, 1);
        if(PRINCIPAL > FOTOS.length) PRINCIPAL = 1;
      } else if(e.target.classList.contains('vista')){
        // De qué lado está sacada: el análisis de la prenda y el inspector la
        // necesitan para no inventar la espalda ni comparar frente con espalda.
        FOTO_VISTA[i] = VISTAS[(VISTAS.indexOf(FOTO_VISTA[i] || 'frente') + 1) % VISTAS.length];
      } else if(e.target.classList.contains('look')){
        FOTO_LOOK[i] = (L % MAX_LOOKS) + 1;
      } else if(MULTI){
        mandaEnSuLook(i);
      } else { PRINCIPAL = i + 1; }
      pintarFotos(); pintarLooks(); pintarPlan();
    };
    c.insertBefore(d, $("#btnFoto"));
  });
}
$("#btnFoto").onclick = () => $("#inputFoto").click();
$("#inputFoto").onchange = e => {
  [...e.target.files].slice(0, MAX_FOTOS).forEach(file => {
    const r = new FileReader();
    r.onload = ev => {
      if(FOTOS.length >= MAX_FOTOS)
        return error("Hasta " + MAX_FOTOS + " fotos por video.");
      FOTOS.push(ev.target.result);
      FOTO_LOOK.push(1);   // toda foto nueva entra al look 1; de ahí se mueve
      FOTO_VISTA.push("frente");
      pintarFotos(); pintarLooks(); pintarPlan();
    };
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
    fotos: FOTOS,
    // Con varios looks el orden de la lista YA dice quién manda en cada uno
    // (la primera de su grupo), así que no hay nada que reordenar del lado del
    // server: mandar 1 es dejar la lista como está.
    foto_principal: MULTI ? 1 : PRINCIPAL,
    foto_look: MULTI ? FOTOS.map((_, i) => FOTO_LOOK[i] || 1) : [],
    foto_vista: FOTOS.map((_, i) => FOTO_VISTA[i] || "frente"),
    looks_nombre: MULTI ? LOOK_NOMBRE : {},
    toma_look: MULTI ? TOMA_LOOK : {},
    toma_motor: TOMA_MOTOR, cuadros_propios: PROPIOS,
    producto: $("#producto").value, notas: $("#notas").value,
    sujeto: valor('sujeto'), formato: valor('formato'),
    // Una toma mía vacía no viaja: el server la descartaría igual, pero acá
    // además evita que el precio de arriba cuente una toma que no se va a hacer.
    tomas: ORDEN.filter(k => TOMAS[k] || (LIBRES[k]||"").trim()),
    libres: LIBRES,
    segundos: parseInt(valor('segundos')), motor: $("#motor").value,
    calidad_cuadro: $("#calidad").value,
    tope_usd: parseFloat($("#tope").value || 0),
    audio: valor('audio'), transicion: valor('transicion'),
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
    $("#precioDet").textContent =
      (e.usd_cuadros > 0 ? e.cuadros + " cuadros (US$" + e.usd_cuadros.toFixed(2) + ")"
                         : e.cuadros + " cuadros tuyos (US$0)")
      + " + " + e.segundos_video + "s de IA (US$" + e.usd_video.toFixed(2) + ")"
      + (e.tomas_camara ? " + " + e.tomas_camara + " de cámara (US$0)" : "")
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
  // Freno acá: una toma mía sin texto no tiene encuadre que pedir, así que el
  // server la descarta. Si la dejáramos pasar en silencio, el video saldría sin
  // la toma que ella justamente quería y sin saber por qué.
  if(Object.keys(LIBRES).some(k => ORDEN.indexOf(k) >= 0 && !(LIBRES[k]||"").trim())){
    error("Te quedó una toma tuya sin escribir: poné qué querés ver, o sacala con la ×.");
    return;
  }
  // La escribió pero quedó afuera de la lista: se avisa en vez de generar el
  // video sin ella y dejarla mirando su texto en pantalla.
  const afuera = Object.keys(LIBRES).filter(k => (LIBRES[k]||"").trim() && ORDEN.indexOf(k) < 0);
  if(afuera.length){
    error("Escribiste " + afuera.length + " toma tuya que NO está en la lista: tocá su "
      + "chip arriba para incluirla, o borrala con la ×. Así como está, el video sale sin ella.");
    return;
  }
  error("");
  $("#btnGenerar").disabled = $("#btnCuadros").disabled = true;
  try{
    const r = await fetch(API + "/generar", {method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify(pedido(solo))});
    const j = await r.json();
    if(!r.ok){ error(j.detail || "No pude arrancar el trabajo."); return; }
    JOB = j.job_id;
    $("#btnFrenar").disabled = false;
    $("#btnFrenar").textContent = "✋ Frenar";
    $("#btnFrenar").classList.remove('oculto');
    $("#formCard").classList.add('oculto');
    $("#jobCard").classList.remove('oculto');
    if(j.aviso) $("#avisoBox").innerHTML = '<div class="note">' + esc(j.aviso) + '</div>';
    seguir();
  }catch(e){ error(String(e)); }
  finally{ $("#btnGenerar").disabled = $("#btnCuadros").disabled = false; }
}
$("#btnGenerar").onclick = () => lanzar(false);
$("#btnCuadros").onclick = () => lanzar(true);
$("#btnFrenar").onclick = async () => {
  if(!JOB) return;
  $("#btnFrenar").disabled = true;
  $("#btnFrenar").textContent = "Frenando…";
  try{
    const r = await (await fetch(API + "/frenar/" + JOB, {method:"POST"})).json();
    if(!r.ok) $("#btnFrenar").textContent = r.detalle || "Ya había terminado";
  }catch(e){ $("#btnFrenar").disabled = false; $("#btnFrenar").textContent = "✋ Frenar"; }
};
$("#btnVolver").onclick = () => {
  clearInterval(TIMER);
  $("#jobCard").classList.add('oculto');
  $("#formCard").classList.remove('oculto');
  $("#gridCuadros").innerHTML = ""; $("#resultado").innerHTML = "";
  $("#avisoBox").innerHTML = ""; historial();
};

const ICONO = {pendiente:"·", generando:"⏳", animando:"🎬", listo:"✓", error:"✕", frenada:"✋"};

async function seguir(){
  clearInterval(TIMER);
  const tick = async () => {
    const r = await fetch(API + "/estado/" + JOB);
    if(!r.ok) return;
    const j = await r.json();
    $("#detalle").textContent = j.detalle || j.estado;
    $("#listaTomas").innerHTML = (j.tomas||[]).map(t =>
      '<div class="item ' + (t.estado==='listo'?'listo':(t.estado==='error'?'error':'')) + '">'
      + '<span>' + (ICONO[t.estado]||"·") + '</span><span>' + t.n + '. ' + esc(t.label) + '</span>'
      + '<span class="est">' + esc(t.error ? t.error : (t.qc || t.estado)) + '</span></div>').join("");
    $("#gridCuadros").innerHTML = (j.cuadros_disponibles||[]).map(n =>
      '<a href="' + API + '/cuadro/' + JOB + '/' + n + '" target="_blank">'
      + '<img src="' + API + '/cuadro/' + JOB + '/' + n + '"></a>').join("");
    if(j.aviso) $("#avisoBox").innerHTML = '<div class="note">' + esc(j.aviso) + '</div>';

    $("#btnFrenar").classList.toggle('oculto',
      j.estado === 'listo' || j.estado === 'error');
    if(j.estado === 'listo' || j.estado === 'error'){
      clearInterval(TIMER);
      $("#punto").style.animation = "none";
      $("#btnVolver").classList.remove('oculto');
      let html = "";
      if(j.estado === 'error') html += '<div class="err">' + esc(j.detalle||"") + '</div>';
      if(j.final_disponible){
        html += '<video controls playsinline src="' + API + '/final/' + JOB + '"></video>'
             + '<a class="btn" style="text-align:center;text-decoration:none" download '
             + 'href="' + API + '/final/' + JOB + '">Descargar el video</a>';
      }
      // Drive: lo que PASÓ, no lo que se intentó. Si no se guardó, el aviso
      // tiene que gritar, porque el archivo se borra en el próximo deploy.
      if(j.drive){
        const d = j.drive, ok = d.estado === 'ok';
        const clase = ok ? 'note' : 'err';
        html += '<div class="' + clase + '">' + (ok ? '✅ ' : '⚠️ ') + esc(d.detalle||'')
          + (d.link ? ' <a href="' + esc(d.link) + '" target="_blank">Abrir en Drive</a>' : '')
          + (ok ? '' : ' <b>Bajate el video ahora</b>: el disco del server se borra en cada deploy.')
          + '</div>';
      }
      if(j.costo) html += '<div class="note">Gastaste US$' + (j.costo.usd_total||0).toFixed(2)
        + ' (cuadros US$' + (j.costo.usd_cuadros||0).toFixed(2)
        + ' · video US$' + (j.costo.usd_video||0).toFixed(2) + ')</div>';
      if(j.guion) html += '<div class="note">Locución: “' + esc(j.guion) + '”</div>';
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
      + '<span>' + esc(x.producto || 'Sin nombre') + (x.solo_cuadros ? ' · sólo cuadros' : '') + '</span>'
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
grupo('transicion');
$("#motor").onchange = estimar; $("#calidad").onchange = estimar;
pintarTomas(); pintarLooks(); pintarPlan(); estimar(); historial();
fetch(API + "/health").then(r => r.json()).then(h => {
  if(h.musica) pintarMusica(true);
  if(!h.ffmpeg) error("Ojo: el servidor no tiene ffmpeg, así que no voy a poder "
    + "unir las tomas. Los clips sueltos sí se generan.");
  // Se avisa ANTES de generar: enterarse de que no hay Drive cuando el video ya
  // salió —y ya se pagó— no sirve de nada.
  if(!h.drive) $("#driveAviso").innerHTML = '<div class="note">⚠️ <b>Google Drive '
    + 'no está conectado.</b> Los videos van a quedar sólo en el disco del server, '
    + 'que se borra en cada deploy. Conectalo en Ajustes → Google Drive y se '
    + 'guardan solos.</div>';
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
             .replace("%%DEFAULT_JSON%%", json.dumps(TOMAS_DEFAULT))
             .replace("%%MAX_TOMAS%%", str(MAX_TOMAS))
             .replace("%%MAX_LIBRES%%", str(MAX_LIBRES))
             .replace("%%MAX_LOOKS%%", str(MAX_LOOKS))
             .replace("%%MAX_FOTOS%%", str(MAX_FOTOS)))
