# -*- coding: utf-8 -*-
"""
comerciales.py — Videos comerciales (estilo campaña de marca, tipo Rip Curl)
============================================================================

Módulo STANDALONE de Studio Luma (mismo molde que videos_luma.py).

Qué hace
--------
De un puñado de fotos de campaña —la modelo con la prenda, en locación— arma un
VIDEO COMERCIAL: cámara lenta, luz natural, grade de película, música y una
placa de cierre con la marca. Dos maneras, y se eligen en la pantalla:

1. "KLING ARMA EL COMERCIAL": las fotos van como REFERENCIA (hasta 7) a Kling
   3.0 Omni (fal), que inventa las tomas manteniendo a la misma modelo y la
   misma prenda. Sale una pieza con continuidad, como filmada, de hasta 15 s
   por pedido (6 tomas). Un comercial de 30 s son dos tandas pegadas.
   Las tomas salen de una PLANTILLA (surf, playa, urbano) o las escribe la
   usuaria; cada una con su duración.
2. "FOTO POR FOTO": cada foto ES una toma, en su orden. El movimiento lo hace
   la IA (image-to-video de fal, con un prompt de LOCACIÓN: conserva el fondo
   real, cámara lenta, viento, olas) o la cámara de edición (una deriva lenta
   sobre la foto, gratis). Fidelidad exacta a cada foto.

Después, para las dos: corte o fundidos (cortos o largos), GRADE de película
(teal y naranja, cálido, frío, blanco y negro), grano fino, viñeta, franjas de
cine opcionales, la placa final con la marca y la cortina musical (la misma
que se sube en Videos).

Variables de entorno
--------------------
  FAL_KEY               (obligatoria)  -> Kling y los motores image-to-video
  FAL_KLING_STD_MODEL   (opcional)     -> ruta del modelo Standard en fal
  FAL_KLING_PRO_MODEL   (opcional)     -> ruta del modelo Pro en fal
  COMERCIALES_PRECIO_STD / _PRO (opcional) -> USD por segundo, para estimar
  COMERCIALES_PREFIX    (opcional)     -> default "/comerciales"
"""

import asyncio
import base64
import json
import math
import os
import subprocess
import time
import uuid as _uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from imagenes_ia import (
    ANALYZE_ENDPOINT,
    _compress_ref,
    _current_api_key,
    _img_part,
    _pfx,
    _strip_data_url,
    budget_check,
    budget_record,
    get_settings,
    kv,
    session_sub_from_request,
    set_current_sub,
)
from videos_luma import (
    FAL_MODELS,
    MOTOR_LABEL,
    PRECIO_SEG,
    RESOLUCION_FAL,
    _dims,
    _duracion_video,
    _ffmpeg_bin,
    _font_path,
    _generar_fal,
    _musica_path,
    _spawn,
    _traducir_libres,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

ROUTE_PREFIX = os.environ.get("COMERCIALES_PREFIX", "/comerciales").rstrip("/")
VERSION = "1.6.1"   # subí este número cada vez que cambiamos el archivo

FAL_KEY = os.getenv("FAL_KEY", "") or os.getenv("FAL_API_KEY", "")
FAL_BASE = "https://queue.fal.run"

# Kling 3.0 Omni en fal ("O3"): referencia a video, con multi-shot. Si fal le
# cambia la ruta, se corrige con la variable de entorno sin tocar el código.
KLING = {
    "kling_std": {
        "modelo": os.getenv("FAL_KLING_STD_MODEL",
                            "fal-ai/kling-video/o3/standard/reference-to-video"),
        "label": "Kling 3.0 Omni · Standard",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_STD", "0.084")),
    },
    "kling_pro": {
        "modelo": os.getenv("FAL_KLING_PRO_MODEL",
                            "fal-ai/kling-video/o3/pro/reference-to-video"),
        "label": "Kling 3.0 Omni · Pro (más calidad)",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112")),
    },
}
KLING_DEFAULT = "kling_std"
# Kling también hace IMAGEN A VIDEO: la foto de ella es el PRIMER CUADRO literal y
# Kling la continúa (3 a 15 s por clip, cámara lenta si se la pide). Es la manera de
# usar SUS tomas tal cual, en el modo foto por foto.
KLING_I2V = {
    "kling_i2v_std": {
        "modelo": os.getenv("FAL_KLING_I2V_STD_MODEL",
                            "fal-ai/kling-video/o3/standard/image-to-video"),
        "label": "Kling 3.0 Omni · Standard (tu foto es el primer cuadro)",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_STD", "0.084")),
    },
    "kling_i2v_pro": {
        "modelo": os.getenv("FAL_KLING_I2V_PRO_MODEL",
                            "fal-ai/kling-video/o3/pro/image-to-video"),
        "label": "Kling 3.0 Omni · Pro (tu foto es el primer cuadro)",
        "precio_seg": float(os.getenv("COMERCIALES_PRECIO_PRO", "0.112")),
    },
}
# Ningún motor filma 1 segundo (Kling baja hasta 3; Seedance y Wan hasta 5). Para los
# FLASHES de 1, 1,5 o 2 s se le pide el mínimo del motor y se corta en edición al largo
# pedido; se paga el mínimo. Es lo que hace cualquier editor: filmar de más y cortar.
KLING_MIN_SEG = 3
KLING_I2V_SEG = (1, 1.5, 2, 3, 4, 5, 6, 8)
TANDA_SEG = 15                  # tope de Kling por pedido
TOMAS_POR_TANDA = 6             # tope de tomas por pedido (multi-shot)
DURACIONES_TOTAL = (15, 30, 45)
MAX_REFS_KLING = 7              # 1 frontal + 3 de la modelo + 3 del lugar
MAX_FOTOS = 16
MAX_TOMAS_LIBRES = 18
TOMA_SEG_MIN, TOMA_SEG_MAX = 1, 8
KLING_TIMEOUT = 20 * 60
IA_TIMEOUT = 12 * 60

MODOS = {
    "kling": "Kling arma el comercial (tus fotos son la referencia)",
    "fotos": "Foto por foto (cada foto es una toma, en su orden)",
}
MOTORES_FOTO = ("ia", "camara")
# El RITMO de cada toma del modo foto por foto: cámara lenta (el sello de campaña),
# velocidad real, o rápida (un golpe de energía: acción corta y decidida).
RITMOS = {"lenta": "Cámara lenta", "normal": "Velocidad real", "rapida": "Rápida, con energía"}
RITMO_DEFAULT = "normal"
# La MEZCLA de tomas. Una foto fija va nítida (2400 px) y un clip de IA sale a 720p, más
# blando: pegados uno al lado del otro, la diferencia canta. "ia" manda todas las fotos al
# motor (misma textura en todo el video); "camara" ninguna (todo deriva, gratis); "libre"
# deja decidir al director, y las fijas que queden se igualan a la textura de los clips.
MEZCLAS = {"ia": "Todas con IA (misma textura, cobra vida todo)",
           "libre": "Que el director elija (IA en algunas, cámara en otras)",
           "camara": "Todas con cámara (foto con deriva, gratis)"}
MEZCLA_DEFAULT = "ia"


def _res_motor(motor_ia: str) -> int:
    """Alto en píxeles con que entrega el motor de IA (720 o 1080)."""
    if motor_ia in KLING_I2V:
        return 1080 if "pro" in motor_ia else 720
    return 1080 if str(RESOLUCION_FAL.get(motor_ia, "720p")).lower().startswith("1080") else 720
# Los motores del modo foto por foto: los image-to-video de Videos más Kling.
MOTORES_IA_FOTO: Dict[str, str] = {**{k: MOTOR_LABEL.get(k, k) for k in FAL_MODELS},
                                   **{k: v["label"] for k, v in KLING_I2V.items()}}
MOTOR_IA_DEFAULT = "kling_i2v_std"
SEG_CAMARA_OK = (2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
SEG_IA_OK = (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0)

GRADES = {
    "luminoso": "Luminoso · claro y aireado (los colores de las fotos, como en Canva)",
    "pelicula": "Película · teal y naranja (surf/deporte, más dramático)",
    "calido": "Cálido · atardecer dorado",
    "frio": "Frío · azulado, de invierno",
    "bn": "Blanco y negro",
    "ninguno": "Sin grade (colores de la foto)",
}
GRADE_DEFAULT = "luminoso"
TRANSICIONES = {
    "corte": "Cortes secos (como las marcas)",
    "fundido": "Fundido cruzado corto (0,5 s)",
    "fundido_largo": "Fundido cruzado largo (1 s, más lento)",
    "negro": "Pasa por negro (0,6 s)",
}
TRANSICION_SEG = {"corte": 0.0, "fundido": 0.5, "fundido_largo": 1.0, "negro": 0.6}
FORMATOS = ("9:16", "16:9")
PLACA_SEG = 2.5
PLACA_DEFAULT = "LUMA Íntima"
# APERTURA Y CIERRE. Un comercial arranca con un título que te hace entrar, como el
# prólogo de una película, y termina con otro. Cada uno puede ir sobre negro (una placa)
# o escrito sobre la toma (la primera o la última), con fundido.
TITULOS = {"placa": "Sobre negro, como prólogo de película",
           "sobre_toma": "Escrito sobre la toma, con fundido",
           "no": "Sin título"}
TITULO_SOBRE_SEG = 3.2          # cuánto se lee un título sobre la toma
# El ESTILO del título. "campana" es el de su edición en Canva: una línea chica arriba
# ("NEW SEASON"), el título grande en sans negrita ("SUMMER 2027") y otra chica abajo
# ("SWIMWEAR"), en negro sobre una toma clara (o en blanco si la toma es oscura).
# "pelicula" es el de créditos: serif clara con versalitas espaciadas.
ESTILOS_TITULO = {"campana": "Campaña · sans negrita, negro sobre la toma (como en Canva)",
                  "pelicula": "Película · serif clara, como créditos"}
ESTILO_TITULO_DEFAULT = "campana"

JOB_TTL = 7 * 24 * 3600
JOBS_INDICE = 40
WORK_DIR = (Path("/data/comerciales_luma") if Path("/data").exists()
            else Path("/tmp/comerciales_luma"))
WORK_DIR.mkdir(parents=True, exist_ok=True)
# Las fotos se suben DE A UNA apenas se eligen y quedan en el server con su nombre; el
# director y el armado mandan sólo los ids. Mandar 10 fotos del celular (30-40 MB) en un
# solo JSON era lo que cortaba la conexión ("Failed to fetch"). Las fotos NO se achican al
# subirlas: se guardan tal cual. Para el video se llevan a 2400 px como mucho (la salida
# es 1080×1920: más píxeles no suman) y para que el director las MIRE, a 900 px.
FOTO_TTL = 7 * 24 * 3600
FOTO_MAX_MB = 30
FOTO_MAX_PX = 2400
# Videos como material: los propios de ella o los clips que Kling ya hizo en otro
# comercial y le gustaron (se traen del historial). Van tal cual; se recortan al armar.
VIDEO_MAX_MB = 200
VIDEO_MAX_SEG = 120
SEG_VIDEO_OK = (1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0)
DURACIONES_OBJETIVO = (15, 30, 45, 60)

# ─────────────────────────────────────────────────────────────────────────────
# PLANTILLAS: la lista de tomas de un comercial. "es" es lo que se lee y edita
# en la pantalla; "en" es lo que viaja al motor (rinden mejor en inglés).
# @Element1 es la modelo (con SU prenda), tal como Kling nombra a las referencias.
# ─────────────────────────────────────────────────────────────────────────────

_CINE = ("Cinematic slow-motion brand film, natural light, shallow depth of field, "
         "steady gimbal glide, subtle wind in the hair. ")
# Kling rechaza cada prompt del multi-shot que pase de 512 caracteres (lo devolvió fal
# en el primer comercial real: "Prompt must not exceed 512 characters"). El texto de la
# toma se arma corto y, si igual se pasa, se recorta la acción en una palabra entera.
KLING_PROMPT_MAX = 512

PLANTILLAS: Dict[str, Dict[str, Any]] = {
    "surf": {
        "nombre": "Surf · estilo Rip Curl",
        # El orden que ella armó a mano en Canva con sus clips, y que funciona.
        "guia": ("COMIENZO: llega en la camioneta por la arena (el título va sobre esa toma), el "
                 "mar de fondo. ACCIÓN: se prepara — se cierra el traje, se ata el leash en el "
                 "shack, baja la tabla de la camioneta, se ata el leash en la arena mojada — y va "
                 "al agua: camina con la tabla, rema. FIN: retratos — en la puerta del shack, la "
                 "mano en el pelo, la tabla bajo el brazo — y un primer plano para cerrar."),
        "desc": "Camioneta en la arena, shack de tablas, leash, olas. Cámara lenta y luz de mañana.",
        "lugar_es": "una playa de surf: un shack de madera con tablas, una camioneta en la arena, olas",
        "lugar_en": "a surf beach: a wooden surf shack with boards, a pickup truck on the sand, breaking waves",
        "tomas": [
            {"es": "Desde arriba: las manos apoyadas en la caja de la camioneta, el mar detrás", "seg": 3,
             "en": "Top-down shot: @Element1 rests both hands on the open tailgate of a pickup parked on the sand, the ocean behind, hair moving in the wind."},
            {"es": "Saca la tabla de la camioneta, de costado", "seg": 2,
             "en": "Side angle: @Element1 slides a surfboard out of the truck bed in slow motion, dunes and sea behind her."},
            {"es": "Sentada en el banco del shack atándose el leash al tobillo", "seg": 3,
             "en": "Inside the wooden surf shack: @Element1 sits on a bench and fastens the leash around her ankle, boards and wetsuits behind, the sea through the window."},
            {"es": "Se acomoda el pelo y mira a cámara, tablas en la pared", "seg": 2,
             "en": "Three-quarter back view: @Element1 runs a hand through her hair and turns her gaze to the lens, surfboards against the wall, warm side light."},
            {"es": "Cierra el cierre del traje en la orilla", "seg": 3,
             "en": "Medium shot at the shoreline: @Element1 zips up the front zipper of her suit, looking down, waves foaming behind."},
            {"es": "Camina hacia el agua con la tabla bajo el brazo", "seg": 2,
             "en": "Wide shot: @Element1 walks toward the water with the board under her arm, slow motion, wet sand reflecting the sky."},
            {"es": "De rodillas en la arena mojada, ajusta el leash", "seg": 3,
             "en": "Low angle: @Element1 kneels on the wet sand next to her board and tightens the leash, foam sliding around her feet."},
            {"es": "Mirada firme a cámara, olas detrás", "seg": 2,
             "en": "Close-up portrait: @Element1 looks straight into the lens with a calm, confident expression, waves breaking out of focus behind her."},
            {"es": "Sonrisa sobre el hombro", "seg": 3,
             "en": "Back three-quarter shot: @Element1 looks over her shoulder and smiles, hand on her neck, the beach hut and flags far behind."},
            {"es": "Entra al agua, la espuma en los pies", "seg": 2,
             "en": "Tracking shot from behind: @Element1 walks into the shallow water, foam rushing around her ankles, slow motion."},
            {"es": "Detalle del estampado del lateral de la prenda", "seg": 3,
             "en": "Slow push-in detail: the printed side panel and the zipper of @Element1's suit, fabric texture visible, sunlight glancing off it."},
            {"es": "Se aleja hacia el mar, plano abierto", "seg": 2,
             "en": "Wide cinematic shot: @Element1 walks away toward the sea with the board, small in the frame, the horizon wide and bright."},
        ],
    },
    "playa": {
        "nombre": "Playa · verano",
        "guia": ("COMIENZO: llega a la playa, camina por la orilla, el título sobre esa toma. "
                 "ACCIÓN: se acomoda la prenda, se sienta, corre al agua, juega con el agua. FIN: "
                 "retratos al atardecer, la mirada sobre el hombro, se aleja por la orilla."),
        "desc": "Orilla, arena, reposera, atardecer. Sin equipo de surf.",
        "lugar_es": "una playa al atardecer: arena clara, la orilla, una reposera de madera",
        "lugar_en": "a beach at golden hour: pale sand, the shoreline, a wooden lounger",
        "tomas": [
            {"es": "Camina por la orilla, plano entero", "seg": 3,
             "en": "Wide shot: @Element1 walks along the shoreline in slow motion, the water touching her feet."},
            {"es": "Gira hacia cámara y sonríe", "seg": 2,
             "en": "Medium shot: @Element1 turns toward the lens and smiles softly, hair in the wind."},
            {"es": "Sentada en la reposera, mirada al mar", "seg": 3,
             "en": "Side view: @Element1 sits on a wooden lounger looking at the sea, warm backlight."},
            {"es": "Detalle de la prenda: tela y tiras", "seg": 2,
             "en": "Slow push-in detail: the fabric, straps and trims of @Element1's garment in the sun."},
            {"es": "Se acomoda el pelo, primer plano", "seg": 3,
             "en": "Close-up: @Element1 tucks her hair behind her ear and looks down, then up to the lens."},
            {"es": "Corre hacia el agua, de espaldas", "seg": 2,
             "en": "Tracking shot from behind: @Element1 runs into the shallow water in slow motion, splashes catching the light."},
            {"es": "De espaldas, mirando el horizonte", "seg": 3,
             "en": "Back view: @Element1 stands still facing the horizon, the wind moving her hair and the fabric."},
            {"es": "Se da vuelta sobre el hombro", "seg": 2,
             "en": "Over-the-shoulder look: @Element1 glances back at the camera with a half smile."},
            {"es": "Acostada en la arena, desde arriba", "seg": 3,
             "en": "Top-down shot: @Element1 lies on the sand, eyes closed, sunlight on her skin."},
            {"es": "Camina de frente, plano medio", "seg": 2,
             "en": "Medium shot: @Element1 walks toward the camera with a relaxed stride."},
            {"es": "Detalle de la espalda de la prenda", "seg": 3,
             "en": "Detail from behind: the back of @Element1's garment, straps and closure, slow drift."},
            {"es": "Se aleja por la orilla, atardecer", "seg": 2,
             "en": "Wide shot at sunset: @Element1 walks away along the shore, silhouette against the golden water."},
        ],
    },
    "urbano": {
        "nombre": "Urbano · calle",
        "guia": ("COMIENZO: llega caminando por la vereda, el título sobre esa toma. ACCIÓN: cruza, "
                 "se apoya en la pared, se acomoda la prenda, el detalle. FIN: retratos con la luz "
                 "dorada, la media sonrisa, se aleja."),
        "desc": "Vereda, pared de ladrillo, luz de tarde, paso firme.",
        "lugar_es": "una calle de ciudad: vereda ancha, una pared de ladrillo, luz de tarde",
        "lugar_en": "a city street: a wide sidewalk, a brick wall, late-afternoon light",
        "tomas": [
            {"es": "Camina por la vereda hacia cámara", "seg": 3,
             "en": "Wide shot: @Element1 walks down the sidewalk toward the camera with a confident stride, slow motion."},
            {"es": "Apoyada en la pared de ladrillo, mira a cámara", "seg": 2,
             "en": "Medium shot: @Element1 leans against a brick wall and looks into the lens."},
            {"es": "Detalle de la prenda con la luz de costado", "seg": 3,
             "en": "Slow push-in detail: the fabric and cut of @Element1's garment, side light raking across it."},
            {"es": "Cruza la calle, plano abierto", "seg": 2,
             "en": "Wide shot: @Element1 crosses the street, city blurred behind her."},
            {"es": "Gira y el pelo se mueve", "seg": 3,
             "en": "Close-up: @Element1 turns her head, hair swinging in slow motion."},
            {"es": "De espaldas, se aleja por la vereda", "seg": 2,
             "en": "Back view: @Element1 walks away down the sidewalk, long shadows on the ground."},
            {"es": "Sentada en un escalón, relajada", "seg": 3,
             "en": "Medium shot: @Element1 sits on a doorstep, relaxed, looking off to the side."},
            {"es": "Primer plano, media sonrisa", "seg": 2,
             "en": "Close-up portrait: @Element1 gives a half smile to the lens."},
            {"es": "Camina en diagonal, la cámara la sigue", "seg": 3,
             "en": "Tracking shot: the camera follows @Element1 walking diagonally across the frame."},
            {"es": "Detalle de la espalda de la prenda", "seg": 2,
             "en": "Detail from behind: the back of @Element1's garment, slow drift."},
            {"es": "Se acomoda la prenda, plano medio", "seg": 3,
             "en": "Medium shot: @Element1 adjusts her garment with both hands, looking down, then up."},
            {"es": "Plano abierto final, luz dorada", "seg": 2,
             "en": "Wide final shot: @Element1 stands still in golden light, the street empty around her."},
        ],
    },
    "libre": {
        "nombre": "Libre · escribo yo las tomas",
        "desc": "Vos escribís cada toma y cuánto dura.",
        "lugar_es": "",
        "lugar_en": "",
        "tomas": [],
    },
}
PLANTILLA_DEFAULT = "surf"

# Movimientos para el modo FOTO POR FOTO con IA: rotan con la toma, y la usuaria
# puede escribir el suyo. Todos conservan el fondo real de la foto.
MOVIMIENTOS_FOTO = [
    {"es": "Gira despacio la cabeza hacia cámara", "en": "she slowly turns her head toward the lens"},
    {"es": "Camina dos pasos lentos", "en": "she takes two slow steps forward"},
    {"es": "La cámara flota de costado, ella respira quieta", "en": "the camera glides slowly sideways while she holds the pose, breathing"},
    {"es": "Levanta la mirada de la prenda al horizonte", "en": "she lifts her gaze from the garment to the horizon"},
    {"es": "El viento le mueve el pelo, sonríe apenas", "en": "the wind moves her hair and she smiles slightly"},
    {"es": "La cámara se acerca lento a la prenda", "en": "the camera pushes in slowly toward the garment"},
]

# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────────────────────

_NEGATIVO = ("text, captions, subtitles, logos, watermark, extra people, morphing, "
             "warping, face change, different person, different garment, redesigned "
             "product, extra fingers, distorted hands, cgi, cartoon")


def _recortar(txt: str, tope: int) -> str:
    """Corta en una palabra entera, sin pasarse de `tope`."""
    txt = " ".join(str(txt or "").split())
    if len(txt) <= tope:
        return txt
    tope = max(tope, 0)
    corte = txt[:tope]
    if tope < len(txt) and txt[tope] != " ":      # quedó una palabra por la mitad
        corte = corte.rsplit(" ", 1)[0]
    return corte.rstrip(" ,;:") or txt[:tope]


def _prompt_kling_toma(req: Dict[str, Any], toma: Dict[str, Any], guia: str = "") -> str:
    """Una toma del multi-shot de Kling: estilo + lugar + la acción, en ≤ 512 letras."""
    lugar = _recortar(req.get("lugar_en") or "", 110)
    fijo = ("Slow-motion cinematic brand film, natural light, shallow depth of field, "
            "gimbal glide. " + (f"Location: {lugar}. " if lugar else "")
            + "@Element1 wears EXACTLY the garment from the references, unchanged. ")
    cola = " No text, no logos." + (" " + guia if guia else "")
    accion = _recortar(toma.get("en") or toma.get("es") or "",
                       KLING_PROMPT_MAX - len(fijo) - len(cola))
    return (fijo + accion + cola)[:KLING_PROMPT_MAX]


_RITMO_EN = {
    "lenta": "SLOW MOTION at half speed, steady gimbal glide",
    "normal": "REAL-TIME pace, natural and unhurried, steady gimbal glide",
    "rapida": "ENERGETIC pace: one quick, decisive movement, punchy like a campaign cut",
}


def _prompt_foto_ia(req: Dict[str, Any], i: int, toma: Dict[str, Any]) -> str:
    """Image-to-video sobre UNA foto en locación: el fondo real se conserva."""
    mov_txt = (toma.get("en") or "").strip()
    if not mov_txt:
        mov_txt = MOVIMIENTOS_FOTO[i % len(MOVIMIENTOS_FOTO)]["en"]
    ritmo = _RITMO_EN.get(toma.get("ritmo") or RITMO_DEFAULT, _RITMO_EN["lenta"])
    return (
        "Cinematic fashion campaign footage. The first frame is the reference image: "
        "keep EXACTLY this person, this garment, this location and this framing. "
        f"Action: {mov_txt}. "
        f"{ritmo}, shallow depth of field, natural "
        "light. Gentle life in the background: waves, leaves, light, wind in the hair. "
        "Keep the real background of the photo — do not change the location, the time of "
        "day or the weather. IDENTITY LOCK: same face, same body, same hair, same garment "
        "with the same colors and print from the first frame to the last. No text, no "
        "logos, no morphing, no extra people, no new objects."
    )


# ─────────────────────────────────────────────────────────────────────────────
# EL DIRECTOR: una IA con oficio de comercial mira cada foto y decide qué hacer con
# ella (moverla con IA, una deriva de cámara o dejarla casi quieta), a qué ritmo, cuánto
# dura y qué pasa en la toma. Devuelve una propuesta por foto; ella la corrige.
# ─────────────────────────────────────────────────────────────────────────────

DIRECTOR_PROMPT = """Sos DIRECTOR/A DE COMERCIALES de moda y lifestyle (surf, playa, urbano), con el oficio de
las campañas de Rip Curl, Billabong, Roxy: cámara lenta que respira, cortes secos al ritmo
de la música, un golpe de energía cada tanto, y siempre la prenda bien vista.

Te paso el MATERIAL de una campaña: fotos (F1, F2, …) y, a veces, videos ya filmados (V1,
V2, …; de cada video ves tres cuadros: inicio, medio y final, y cuánto dura). El orden en
que te lo paso NO importa: vos decidís el orden.

PRIMERO LA HISTORIA. Un comercial no es una seguidilla de movimientos: cuenta algo. Mirá
todo el material y escribí una historia corta en TRES PARTES con lo que HAY (no inventes
escenas que no estén): (1) COMIENZO — llega, se ubica: dónde estamos, quién es ella; (2)
ACCIÓN — se prepara y hace: se cierra el traje, agarra la tabla, camina, entra al agua, la
prenda en uso; (3) FIN — el cierre emocional: los retratos, la mirada, la sonrisa, un primer
plano. Con varias modelos, la historia es UNA y el hilo es la acción, no la persona: las
modelos se alternan dentro de cada parte y funciona igual.

DESPUÉS LA SECUENCIA. Elegí QUÉ material va y EN QUÉ ORDEN para contar esa historia, dentro
de la duración objetivo. Descartá lo que no aporte o repita (decí por qué): vale usar
menos material que el que hay. OJO CON EL ORDEN: la secuencia NO es la lista del material
tal como te la pasé; es el orden de la HISTORIA. Primero todo lo del comienzo, después todo
lo de la acción, al final lo del fin. Si te queda en el mismo orden en que llegó el material,
casi seguro está mal: revisalo. Cada toma de la secuencia lleva:
- "material": "F3" o "V1".
- "acto": 1 (comienzo), 2 (acción) o 3 (fin).
- "motor" (sólo fotos): "ia" si vale la pena que cobre vida (viento en el pelo, olas, ella
  gira, camina, se acomoda algo, la cámara flota) o "camara" si conviene dejarla como foto
  con una deriva suave (retratos muy quietos, detalles de la prenda). Los videos van tal
  cual, recortados.
- "ritmo": "normal" es la base (velocidad real: así se ven las campañas de ropa, la acción
  se lee), "lenta" sólo donde suma (el agua, el pelo al viento, el retrato del fin) y
  "rapida" un golpe corto de energía (un giro, entrar al agua; una o dos por video).
- "seg": cuánto dura la toma. Lenta 3 a 5; normal 2 a 4; rápida 1 a 3 (1 o 1,5 es un FLASH:
  un golpe de corte de campaña, ideal en tandas de dos o tres seguidos). Las de cámara 2 a
  4. Un video: la parte que sirva, nunca más que su duración.
- "desde" (sólo videos): en qué segundo del video arranca la parte que elegiste.
- "accion": en castellano rioplatense y corto, qué pasa en la toma (para "camara": cómo se
  mueve la cámara: "entra despacio a la cara", "se corre de costado").
- "accion_en": lo mismo en inglés, técnico y literal, para el motor de video. Sin cambiar
  la locación, la prenda ni la persona. Si es "camara", describí el movimiento de cámara.
- "por_que": una línea, como se lo dirías a la clienta: qué aporta a la historia.

Reglas: la prenda tiene que verse; tomas seguidas no repiten la misma idea ni el mismo
encuadre; la primera ubica, la última emociona. No inventes elementos que no estén.

EL ESTILO. Definí el LOOK del comercial mirando el material (la luz, los colores, el lugar,
la actitud) y lo que pida la clienta en ESTILO/REFERENCIA si lo escribió:
- "grade": "luminoso" (claro y aireado, los colores tal cual con un toque de contraste: el
  look de las campañas de ropa de verano y playa; es el que va casi siempre), "pelicula"
  (teal y naranja, dramático, para surf de invierno o deporte), "calido" (atardecer,
  romántico), "frio" (invierno, urbano, editorial), "bn" (blanco y negro, dramático) o
  "ninguno" (los colores tal cual, para producto muy colorido).
- "transicion": "corte" (marcas, energía), "fundido" (suave), "fundido_largo" (lento,
  onírico) o "negro" (capítulos).
- "cine": true sólo si el estilo es cinematográfico; para una campaña de ropa clara, false.
- "grano": true casi siempre; false si el estilo es limpio y digital.
- "musica": qué música le iría (género, tempo, ánimo), una línea.
- "estilo_resumen": dos líneas, como se lo contarías a la clienta.

LOS TÍTULOS. Un comercial ARRANCA con un título que te hace entrar, como el prólogo de una
película, y TERMINA con otro. Escribilos en "titulos":
- "apertura": 2 a 4 palabras, el gancho grande: la temporada o la idea ("SUMMER 2027",
  "Nueva temporada", "Antes que el sol"), no el nombre de la marca.
- "apertura_arriba": una línea chica ARRIBA del título, opcional ("New season", "Colección").
- "apertura_sub": una línea chica ABAJO, opcional: qué es ("Swimwear", "Surf", un lugar).
- "apertura_modo": "sobre_toma" (escrito sobre la primera toma mientras ya pasa algo; es lo
  habitual en una campaña de ropa) o "placa" (sobre negro, como prólogo de película).
- "cierre": la marca o una frase de cierre corta ("LUMA Íntima", "Volvé al mar").
- "cierre_sub": opcional: el @ de Instagram, la colección, "nueva temporada".
- "cierre_modo": "placa" o "sobre_toma".

Devolvé SOLO un JSON, sin markdown, con esta forma exacta:
{"historia": {"titulo": "…", "sinopsis": "tres líneas", "actos": ["acto 1: …", "acto 2: …", "acto 3: …"]},
 "secuencia": [{"material": "F3", "acto": 1, "motor": "ia", "ritmo": "lenta", "seg": 4, "desde": 0,
"accion": "…", "accion_en": "…", "por_que": "…"}, …],
 "descartes": [{"material": "F5", "por_que": "…"}],
 "look": {"grade": "pelicula", "transicion": "corte", "cine": true, "grano": true,
"musica": "…", "estilo_resumen": "…"},
 "titulos": {"apertura": "…", "apertura_arriba": "…", "apertura_sub": "…", "apertura_modo": "sobre_toma",
"cierre": "…", "cierre_sub": "…", "cierre_modo": "placa"},
 "nota": "una línea sobre el ritmo general"}
Cada material aparece a lo sumo UNA vez, en la secuencia o en los descartes."""


def _etiqueta_material(m: Dict[str, Any], k_foto: int, k_video: int) -> str:
    return f"V{k_video}" if m.get("tipo") == "video" else f"F{k_foto}"


def _etiquetas(materiales: List[Dict[str, Any]]) -> List[str]:
    """F1, F2, V1, F3… en el orden del material."""
    out, kf, kv = [], 0, 0
    for m in materiales:
        if m.get("tipo") == "video":
            kv += 1
        else:
            kf += 1
        out.append(_etiqueta_material(m, kf, kv))
    return out


def _director_limpiar(data: Dict[str, Any], materiales: List[Dict[str, Any]],
                      mezcla: str = "libre") -> Dict[str, Any]:
    """Acota lo que devolvió el modelo: cada material a lo sumo una vez, en la secuencia
    (con motor, ritmo, segundos y 'desde' válidos) o en los descartes; lo que el modelo
    olvidó nombrar queda como descarte, para que ella lo vea y lo pueda volver a meter."""
    etiquetas = _etiquetas(materiales)
    idx_por_etq = {e: k for k, e in enumerate(etiquetas)}
    data = data if isinstance(data, dict) else {}
    usados: set = set()
    secuencia: List[Dict[str, Any]] = []
    for t in (data.get("secuencia") or data.get("tomas") or []):
        if not isinstance(t, dict):
            continue
        etq = str(t.get("material") or "").strip().upper()
        if not etq and t.get("foto"):
            etq = f"F{t.get('foto')}"          # forma vieja: {"foto": 3}
        k = idx_por_etq.get(etq)
        if k is None or k in usados:
            continue
        usados.add(k)
        m = materiales[k]
        es_video = m.get("tipo") == "video"
        motor = t.get("motor") if t.get("motor") in MOTORES_FOTO else "camara"
        if mezcla in ("ia", "camara"):
            motor = mezcla            # la mezcla elegida manda sobre el director
        if es_video:
            motor = "video"
        ritmo = t.get("ritmo") if t.get("ritmo") in RITMOS else RITMO_DEFAULT
        try:
            seg = float(t.get("seg") or (4 if motor == "ia" else 3))
        except (TypeError, ValueError):
            seg = 4.0 if motor == "ia" else 3.0
        ok = SEG_VIDEO_OK if es_video else (SEG_IA_OK if motor == "ia" else SEG_CAMARA_OK)
        if es_video and m.get("dur"):
            ok = tuple(v for v in ok if v <= float(m["dur"]) + 0.01) or (min(ok),)
        seg = min(ok, key=lambda v: abs(v - seg))
        try:
            desde = max(0.0, float(t.get("desde") or 0))
        except (TypeError, ValueError):
            desde = 0.0
        if es_video and m.get("dur"):
            desde = min(desde, max(0.0, float(m["dur"]) - seg))
        try:
            acto = int(t.get("acto") or 0)
        except (TypeError, ValueError):
            acto = 0
        secuencia.append({"indice": k, "material": etq, "tipo": ("video" if es_video else "foto"),
                          "acto": acto if 1 <= acto <= 3 else 0,
                          "motor": motor, "ritmo": ritmo, "seg": seg, "desde": round(desde, 2),
                          "texto": str(t.get("accion") or "").strip()[:200],
                          "texto_en": str(t.get("accion_en") or "").strip()[:300],
                          "por_que": str(t.get("por_que") or "").strip()[:200]})
    # A PRUEBA DEL MODELO: aunque haya devuelto las tomas en el orden en que llegó el
    # material, la secuencia se reordena por parte (comienzo → acción → fin), conservando
    # el orden interno de cada parte. Las que no tienen parte quedan donde estaban, como
    # si fueran de la acción.
    if any(t["acto"] for t in secuencia):
        secuencia = sorted(secuencia, key=lambda t: (t["acto"] or 2))
    descartes: List[Dict[str, Any]] = []
    motivos = {}
    for dsc in (data.get("descartes") or []):
        if isinstance(dsc, dict):
            motivos[str(dsc.get("material") or "").strip().upper()] = str(dsc.get("por_que") or "").strip()[:200]
    for k, etq in enumerate(etiquetas):
        if k not in usados:
            descartes.append({"indice": k, "material": etq, "tipo": materiales[k].get("tipo", "foto"),
                              "por_que": motivos.get(etq) or "el director no la usó en la historia"})
    h = data.get("historia") if isinstance(data.get("historia"), dict) else {}
    actos = [str(a).strip()[:300] for a in (h.get("actos") or []) if str(a).strip()][:3]
    historia = {"titulo": str(h.get("titulo") or "").strip()[:80],
                "sinopsis": str(h.get("sinopsis") or "").strip()[:600], "actos": actos}
    nota = str(data.get("nota") or "").strip()[:300]
    lk = data.get("look") if isinstance(data.get("look"), dict) else {}
    look = {
        "grade": lk.get("grade") if lk.get("grade") in GRADES else GRADE_DEFAULT,
        "transicion": lk.get("transicion") if lk.get("transicion") in TRANSICIONES else "corte",
        "cine": bool(lk.get("cine", True)),
        "grano": bool(lk.get("grano", True)),
        "musica": str(lk.get("musica") or "").strip()[:200],
        "estilo_resumen": str(lk.get("estilo_resumen") or "").strip()[:400],
    }
    tt = data.get("titulos") if isinstance(data.get("titulos"), dict) else {}
    titulos = {
        "apertura": str(tt.get("apertura") or "").strip()[:40],
        "apertura_arriba": str(tt.get("apertura_arriba") or "").strip()[:40],
        "apertura_sub": str(tt.get("apertura_sub") or "").strip()[:60],
        "apertura_modo": tt.get("apertura_modo") if tt.get("apertura_modo") in TITULOS else "sobre_toma",
        "cierre": str(tt.get("cierre") or "").strip()[:40] or PLACA_DEFAULT,
        "cierre_sub": str(tt.get("cierre_sub") or "").strip()[:60],
        "cierre_modo": tt.get("cierre_modo") if tt.get("cierre_modo") in TITULOS else "placa",
    }
    # Compatibilidad con la pantalla vieja: "tomas" en el orden de la secuencia.
    tomas = [{"foto": t["indice"] + 1, **{k2: v for k2, v in t.items() if k2 != "indice"}} for t in secuencia]
    return {"historia": historia, "secuencia": secuencia, "descartes": descartes,
            "tomas": tomas, "nota": nota, "look": look, "titulos": titulos}


def _cuadros_video(path: Path, max_dim: int = 900) -> List[str]:
    """Tres cuadros (inicio, medio, final) de un video, en base64, para que el director
    lo mire sin mandarle el archivo entero."""
    dur = _duracion_video(path) or 1.0
    out: List[str] = []
    for frac in (0.1, 0.5, 0.9):
        png = path.with_name(path.stem + f"_c{int(frac * 10)}.jpg")
        ok, _ = _ff(["-ss", f"{max(dur * frac, 0):.2f}", "-i", str(path), "-frames:v", "1",
                     "-vf", f"scale={max_dim}:-2:force_original_aspect_ratio=decrease", "-q:v", "5",
                     str(png)], 120)
        if ok and png.exists():
            out.append(base64.b64encode(png.read_bytes()).decode())
    return out


async def _director(materiales: List[Dict[str, Any]], estilo: str, lugar: str,
                    estilo_txt: str = "", mezcla: str = "libre", objetivo: int = 30,
                    historia_txt: str = "") -> Dict[str, Any]:
    """Le muestra el material al modelo de visión con el brief de director y devuelve
    la propuesta (historia, secuencia, descartes, look) ya acotada."""
    api_key = await _current_api_key()
    if not api_key:
        raise HTTPException(500, "Falta la API key de Google (Fotos → Ajustes).")
    pl = PLANTILLAS.get(estilo) or {}
    brief = DIRECTOR_PROMPT
    if pl.get("nombre") and estilo != "libre":
        brief += f"\n\nESTILO DEL COMERCIAL: {pl['nombre']} — {pl['desc']}"
        if pl.get("guia"):
            brief += (f"\nGUÍA DE ESTA CLASE DE COMERCIAL (el orden que funciona; adaptalo al "
                      f"material que hay): {pl['guia']}")
    if lugar.strip():
        brief += f"\nLUGAR: {lugar.strip()[:300]}"
    if estilo_txt.strip():
        # Lo que ella escribió del estilo manda sobre la plantilla: es su referencia.
        brief += (f"\nESTILO/REFERENCIA (lo pidió la clienta, mandá sobre todo lo demás): "
                  f"{estilo_txt.strip()[:500]}")
    if historia_txt.strip():
        brief += (f"\nLA HISTORIA QUE QUIERE CONTAR LA CLIENTA (respetala; vos la ordenás en "
                  f"actos con el material que hay): {historia_txt.strip()[:600]}")
    brief += (f"\nDURACIÓN OBJETIVO: unos {int(objetivo)} segundos en total (sumando los "
              "\"seg\" de la secuencia; podés quedar un poco abajo, nunca muy arriba).")
    if mezcla == "ia":
        brief += ("\nMEZCLA: la clienta quiere TODAS las fotos con \"ia\" (misma textura en "
                  "todo el video). Poné \"motor\": \"ia\" en todas las fotos y elegí para cada "
                  "una la acción y el ritmo.")
    elif mezcla == "camara":
        brief += ("\nMEZCLA: TODAS las fotos con \"camara\" (foto con deriva). Poné "
                  "\"motor\": \"camara\" en todas y describí sólo el movimiento de cámara.")
    else:
        brief += ("\nMEZCLA: podés combinar, pero tené en cuenta que un clip de IA sale más "
                  "blando que una foto nítida: si mezclás, que las de cámara sean pocas y "
                  "cortas (flashes) o detalles de la prenda.")
    parts: List[Dict[str, Any]] = [{"text": brief}]
    etiquetas = _etiquetas(materiales)
    n_f = sum(1 for m in materiales if m.get("tipo") != "video")
    n_v = len(materiales) - n_f
    parts.append({"text": f"MATERIAL: {n_f} foto(s) y {n_v} video(s)."})
    for m, etq in zip(materiales, etiquetas):
        if m.get("tipo") == "video":
            cuadros = m.get("cuadros") or []
            parts.append({"text": f"{etq} — VIDEO de {float(m.get('dur') or 0):.1f} s "
                                  f"({len(cuadros)} cuadros: inicio, medio, final):"})
            for b in cuadros:
                parts.append(_img_part(b))
        else:
            parts.append({"text": f"{etq} — FOTO:"})
            parts.append(_img_part(m["b64"]))
    headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}
    cfg_fast = {"temperature": 0.4, "responseMimeType": "application/json",
                "thinkingConfig": {"thinkingLevel": "low"}}
    cfg_plain = {"temperature": 0.4, "responseMimeType": "application/json"}

    async def _call(cfg):
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": cfg}
        async with httpx.AsyncClient(timeout=240) as cli:
            return await cli.post(ANALYZE_ENDPOINT, json=body, headers=headers)

    r = await _call(cfg_fast)
    if r.status_code == 400:
        r = await _call(cfg_plain)
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"El director devolvió error: {r.text[:300]}")
    try:
        raw = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[4:] if raw.lower().startswith("json") else raw
        data = json.loads(raw)
    except Exception as e:
        raise HTTPException(502, f"El director no devolvió un JSON legible: {e}")
    return _director_limpiar(data, materiales, mezcla)


# ─────────────────────────────────────────────────────────────────────────────
# FAL: Kling 3.0 Omni (referencia a video, multi-shot)
# ─────────────────────────────────────────────────────────────────────────────

def _uri(b64: str) -> str:
    return f"data:image/jpeg;base64,{b64}"


def _payload_kling(req: Dict[str, Any], tomas: List[Dict[str, Any]]) -> Dict[str, Any]:
    """El pedido a Kling para UNA tanda (hasta 6 tomas, hasta 15 s).

    Las fotos van como referencia: la primera es la cara/frente de la modelo
    (`frontal_image_url`), las tres siguientes son más vistas de ella
    (`reference_image_urls`) y hasta tres más van como referencia de estilo y
    lugar (`image_urls`). En el prompt ella es @Element1."""
    fotos = req["fotos"]
    elemento = {"frontal_image_url": _uri(fotos[0]),
                "reference_image_urls": [_uri(f) for f in fotos[1:4]]}
    # FOTOS DE GUÍA: si una toma dice "como mi foto N", esa foto viaja en `image_urls`
    # y la toma la nombra como @ImageK (Kling copia el encuadre, el lugar y la luz de
    # esa foto). Entran hasta 3 distintas por tanda; sin guías, van las fotos 5 a 7.
    guias: List[int] = []
    for t in tomas:
        fi = t.get("foto")
        if isinstance(fi, int) and 0 <= fi < len(fotos) and fi not in guias and len(guias) < 3:
            guias.append(fi)
    prompts = []
    for t in tomas:
        fi = t.get("foto")
        guia = (f"Match the framing, location, light and pose of @Image{guias.index(fi) + 1}: "
                "that photo brought to life."
                if isinstance(fi, int) and fi in guias else "")
        prompts.append({"prompt": _prompt_kling_toma(req, t, guia), "duration": int(t["seg"])})
    payload: Dict[str, Any] = {
        "multi_prompt": prompts,
        "elements": [elemento],
        "duration": int(sum(int(t["seg"]) for t in tomas)),
        "aspect_ratio": req["formato"],
        "generate_audio": False,
        "cfg_scale": 0.5,
        "negative_prompt": _NEGATIVO,
    }
    extras = ([_uri(fotos[i]) for i in guias] if guias
              else [_uri(f) for f in fotos[4:MAX_REFS_KLING]])
    if extras:
        payload["image_urls"] = extras
    return payload


def _fallbacks_kling(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Variantes del pedido, de la más completa a la más simple. fal rechaza con
    422 lo que un modelo no acepta; se prueba la siguiente en vez de fallar."""
    out = [payload]
    p = dict(payload)
    for campo in ("negative_prompt", "image_urls", "cfg_scale"):
        if campo in p:
            p = {k: v for k, v in p.items() if k != campo}
            out.append(p)
    if "multi_prompt" in p:
        # Sin multi-shot: un solo prompt con las tomas encadenadas.
        q = {k: v for k, v in p.items() if k != "multi_prompt"}
        q["prompt"] = " Then: ".join(x["prompt"] for x in p["multi_prompt"])
        out.append(q)
        p = q
    if "aspect_ratio" in p:
        out.append({k: v for k, v in p.items() if k != "aspect_ratio"})
    return out


async def _fal_key() -> str:
    settings = await get_settings()
    return FAL_KEY or str(settings.get("fal_api_key") or "").strip()


async def _fal_cola(jid: str, modelo: str, variantes: List[Dict[str, Any]], destino: Path,
                    pref: str, label: str, seg: int, var_env: str) -> None:
    """Un pedido a la cola de fal: manda (probando variantes si rechaza con 422),
    espera mostrando el paso, baja el video a `destino`."""
    key = await _fal_key()
    if not key:
        raise RuntimeError("Falta la API key de fal (Fotos → Ajustes, o FAL_KEY en Railway).")
    url = f"{FAL_BASE}/{modelo}"
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=240) as cli:
        # fal puede rechazar los datos al ENVIAR (422 en el POST) o recién al pedir el
        # RESULTADO (el POST entra en cola y el GET del resultado devuelve el 422): en
        # el primer comercial real pasó lo segundo. En los dos casos se prueba la
        # variante siguiente del pedido.
        res: Optional[Dict[str, Any]] = None
        ultimo_rechazo = ""
        for intento, p in enumerate(variantes):
            r = await cli.post(url, headers=headers, json=p)
            if r.status_code == 404:
                raise RuntimeError(
                    f"fal HTTP 404: el modelo '{modelo}' no existe con esa ruta. Si fal se la "
                    f"cambió, cargá la nueva en la variable {var_env}.")
            if r.status_code in (400, 422):
                ultimo_rechazo = r.text[:300]
                print(f"[comerciales] {label} rechazó la variante {intento} al enviar: {ultimo_rechazo[:200]}")
                continue
            if r.status_code not in (200, 201):
                raise RuntimeError(f"{label} no aceptó el pedido (HTTP {r.status_code}): {r.text[:300]}")
            if intento:
                print(f"[comerciales] {label} aceptó la variante {intento} del pedido")
            data = r.json()
            rid = data.get("request_id") or data.get("requestId")
            status_url = data.get("status_url") or (f"{url}/requests/{rid}/status" if rid else None)
            result_url = data.get("response_url") or (f"{url}/requests/{rid}" if rid else None)
            if not status_url or not result_url:
                raise RuntimeError(f"fal no devolvió status_url: {json.dumps(data)[:200]}")
            inicio = time.time()
            ultimo = ""
            while True:
                if time.time() - inicio > KLING_TIMEOUT:
                    raise RuntimeError(f"{label} no terminó ({pref}) en {KLING_TIMEOUT // 60} minutos.")
                if await _frenado(jid):
                    raise RuntimeError("Frenado por la usuaria.")
                rs = await cli.get(status_url, headers=headers)
                d = rs.json() if rs.status_code == 200 else {}
                st = str(d.get("status", ""))
                if st in ("COMPLETED", "Completed", "succeeded", "OK"):
                    break
                if st in ("FAILED", "Error", "CANCELLED"):
                    raise RuntimeError(f"{label} falló ({pref}): {rs.text[:300]}")
                if st == "IN_QUEUE":
                    pos = d.get("queue_position")
                    paso = f"{pref}: en la cola de fal" + (f", puesto {pos}" if pos is not None else "") + "…"
                else:
                    paso = f"{pref}: {label} está filmando ({seg} s)…"
                if paso != ultimo:
                    await _job_set(jid, {"paso": paso})
                    ultimo = paso
                await asyncio.sleep(8)
            rr = await cli.get(result_url, headers=headers)
            if rr.status_code in (400, 422):
                ultimo_rechazo = rr.text[:300]
                print(f"[comerciales] {label} rechazó la variante {intento} en el resultado: {ultimo_rechazo[:200]}")
                await _job_set(jid, {"paso": f"{pref}: fal rechazó un dato del pedido; probando una "
                                             "variante más simple…"})
                continue
            if rr.status_code != 200:
                raise RuntimeError(f"fal result HTTP {rr.status_code}: {rr.text[:200]}")
            res = rr.json()
            break
        if res is None:
            raise RuntimeError(f"{label} rechazó todas las variantes del pedido. Último motivo: "
                               f"{ultimo_rechazo}")
        vurl = (res.get("video") or {}).get("url") if isinstance(res.get("video"), dict) else None
        if not vurl and res.get("videos"):
            vurl = (res["videos"][0] or {}).get("url")
        vurl = vurl or res.get("video_url") or res.get("url")
        if not vurl:
            raise RuntimeError(f"{label} no devolvió video: {json.dumps(res)[:300]}")
        dl = await cli.get(vurl, follow_redirects=True)
        if dl.status_code != 200:
            raise RuntimeError(f"fal descarga HTTP {dl.status_code}")
        destino.write_bytes(dl.content)


async def _kling_tanda(jid: str, req: Dict[str, Any], k: int,
                       tomas: List[Dict[str, Any]], destino: Path) -> float:
    """Manda una tanda a Kling (referencias, multi-shot), espera y baja el video.
    Devuelve el costo."""
    motor = KLING[req["motor"]]
    payload = _payload_kling(req, tomas)
    seg = int(payload["duration"])
    await _job_set(jid, {"paso": f"Tanda {k + 1}: mandando {len(tomas)} tomas ({seg} s) a "
                                 f"{motor['label']}…"})
    await _fal_cola(jid, motor["modelo"], _fallbacks_kling(payload), destino, f"Tanda {k + 1}",
                    motor["label"], seg, "FAL_KLING_STD_MODEL / FAL_KLING_PRO_MODEL")
    costo = round(motor["precio_seg"] * seg, 3)
    await budget_record("comercial_kling", motor["modelo"], costo, 1,
                        note=f"comercial tanda {k + 1} ({seg} s, {len(tomas)} tomas)")
    return costo


def _payload_kling_i2v(prompt: str, foto_b64: str, seg: int) -> Dict[str, Any]:
    """Kling imagen a video: la foto es el primer cuadro y el clip dura `seg`."""
    return {"prompt": prompt, "image_url": _uri(foto_b64), "duration": int(seg),
            "generate_audio": False, "cfg_scale": 0.5, "negative_prompt": _NEGATIVO}


def _fallbacks_kling_i2v(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out = [payload]
    p = dict(payload)
    for campo in ("negative_prompt", "cfg_scale", "generate_audio", "duration"):
        if campo in p:
            p = {k: v for k, v in p.items() if k != campo}
            out.append(p)
    return out


async def _kling_i2v(jid: str, req: Dict[str, Any], i: int, foto_b64: str, prompt: str,
                     seg: int, destino: Path) -> float:
    """Una toma del modo foto por foto con Kling: SU foto continúa en video."""
    motor = KLING_I2V[req["motor_ia"]]
    await _job_set(jid, {"paso": f"Toma {i + 1}: mandando tu foto a {motor['label']} ({seg} s)…"})
    await _fal_cola(jid, motor["modelo"], _fallbacks_kling_i2v(_payload_kling_i2v(prompt, foto_b64, seg)),
                    destino, f"Toma {i + 1}", motor["label"], seg,
                    "FAL_KLING_I2V_STD_MODEL / FAL_KLING_I2V_PRO_MODEL")
    costo = round(motor["precio_seg"] * seg, 3)
    await budget_record("comercial_kling", motor["modelo"], costo, 1,
                        note=f"comercial toma {i + 1} imagen a video ({seg} s)")
    return costo


# ─────────────────────────────────────────────────────────────────────────────
# FFMPEG: deriva lenta sobre una foto, normalizado, transiciones, grade, placa, música
# ─────────────────────────────────────────────────────────────────────────────

def _ff(cmd: List[str], timeout: int = 600) -> Tuple[bool, str]:
    binario = _ffmpeg_bin()
    if not binario:
        return False, "no hay ffmpeg"
    try:
        res = subprocess.run([binario, "-y"] + cmd, capture_output=True, timeout=timeout)
        if res.returncode == 0:
            return True, ""
        err = (res.stderr or b"").decode(errors="replace")[-400:]
        print(f"[comerciales] ffmpeg falló: {err}")
        return False, err
    except Exception as e:
        return False, str(e)[:200]


def _clip_deriva(foto: Path, salida: Path, formato: str, seg: float, idx: int,
                 ritmo: str = "lenta", igualar: int = 0) -> bool:
    """La cámara de edición: una deriva sobre la foto quieta, distinta en cada toma
    (entra, sale, se corre de costado, baja, va a la cara). Gratis. Con ritmo
    "lenta" apenas se mueve; "normal" recorre más; "rápida" entra de golpe y frena
    (el corte de campaña)."""
    w, h = _dims(formato)
    cuadros = max(int(round(seg * 24)), 24)
    ult = max(cuadros - 1, 1)
    amp = {"lenta": 0.12, "normal": 0.18, "rapida": 0.30}.get(ritmo, 0.12)
    z = 1.0 + amp * 0.8
    zmax = 1.0 + amp
    w2 = min(int(w * (1.04 + amp)) // 2 * 2, 4096)
    h2 = min(int(h * (1.04 + amp)) // 2 * 2, 4096)
    # rápida: avance = 1-(1-t)^2, sale disparado y llega frenando
    t = f"(1-pow(1-on/{ult},2))" if ritmo == "rapida" else f"(on/{ult})"
    modos = [
        # z, x, y como expresiones de zoompan
        (f"min(1.0+{zmax - 1:.4f}*{t},{zmax:.4f})", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.40"),   # entra
        (f"max({zmax:.4f}-{zmax - 1:.4f}*{t},1.0)", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.45"),   # sale
        (f"{z:.4f}", f"(iw-iw/zoom)*{t}", "(ih-ih/zoom)*0.45"),                               # derecha
        (f"{z:.4f}", "(iw-iw/zoom)*0.5", f"(ih-ih/zoom)*{t}"),                                # baja
        (f"min(1.0+{amp + 0.03:.4f}*{t},{zmax + 0.03:.4f})", "(iw-iw/zoom)*0.5", "(ih-ih/zoom)*0.30"),   # a la cara
        (f"{z:.4f}", f"(iw-iw/zoom)*(1-{t})", "(ih-ih/zoom)*0.55"),                           # izquierda
    ]
    zexp, xexp, yexp = modos[idx % len(modos)]
    # IGUALAR TEXTURA: la toma de cámara sale del original (2400 px, nítida) y el clip de
    # IA del motor (720p, más blando); pegados, se ve armado con dos cosas distintas. Si
    # en el video hay clips de IA, la de cámara se baja a la resolución REAL del motor y
    # se vuelve a subir: pierde el detalle que la otra no tiene y quedan del mismo palo.
    igual = ""
    if igualar and igualar < min(w, h):
        f_ = igualar / float(min(w, h))
        wi, hi = int(w * f_) // 2 * 2, int(h * f_) // 2 * 2
        # Además del sube-y-baja, un desenfoque leve: el clip del motor pasa por su
        # códec y llega más blando que un simple reescalado (medido sobre la foto 04:
        # bordes 5,95 nítida, 4,79 sólo reescalada; el clip real quedaba bastante
        # más abajo).
        igual = f"scale={wi}:{hi}:flags=bicubic,scale={w}:{h}:flags=bicubic,gblur=sigma=0.7,"
    vf = (f"scale={w2}:{h2}:force_original_aspect_ratio=increase,crop={w2}:{h2},"
          f"zoompan=z='{zexp}':d={cuadros}:x='{xexp}':y='{yexp}':s={w}x{h}:fps=24,"
          + igual + "format=yuv420p")
    ok, _ = _ff(["-loop", "1", "-i", str(foto), "-vf", vf, "-frames:v", str(cuadros), "-an",
                 "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(salida)], 300)
    return ok and salida.exists()


def _normalizar_clip(src: Path, dst: Path, formato: str, seg: Optional[float] = None,
                     ralenti: float = 1.0, desde: float = 0.0) -> bool:
    """Tamaño exacto (crop-to-fill), 24 fps, mudo; opcionalmente cámara lenta
    (ralenti > 1 estira el tiempo) y recorte a `seg` segundos."""
    w, h = _dims(formato)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    if ralenti and abs(ralenti - 1.0) > 0.01:
        vf = f"setpts={ralenti:.3f}*PTS," + vf
    vf += ",fps=24,format=yuv420p"
    cmd = (["-ss", f"{desde:.2f}"] if desde and desde > 0 else []) + \
          ["-i", str(src), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19"]
    if seg:
        cmd += ["-t", f"{seg:.2f}"]
    cmd += [str(dst)]
    ok, _ = _ff(cmd, 600)
    return ok and dst.exists()


def _concatenar(clips: List[Path], salida: Path, formato: str, modo: str) -> bool:
    """Une las tomas. Corte seco con el demuxer; con fundido, xfade (cruzado o
    por negro), con un largo que nunca se come más de un tercio de la toma corta."""
    if not clips:
        return False
    if len(clips) == 1:
        import shutil
        shutil.copy(clips[0], salida)
        return True
    d = TRANSICION_SEG.get(modo, 0.0)
    if modo == "corte" or d <= 0:
        lista = salida.parent / (salida.stem + "_lista.txt")
        lista.write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
        binario = _ffmpeg_bin()
        if not binario:
            return False
        res = subprocess.run([binario, "-y", "-f", "concat", "-safe", "0", "-i", lista.name,
                              "-c", "copy", salida.name], capture_output=True,
                             cwd=salida.parent, timeout=600)
        if res.returncode == 0 and salida.exists():
            return True
        res = subprocess.run([binario, "-y", "-f", "concat", "-safe", "0", "-i", lista.name,
                              "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                              "-pix_fmt", "yuv420p", salida.name],
                             capture_output=True, cwd=salida.parent, timeout=600)
        return res.returncode == 0 and salida.exists()
    w, h = _dims(formato)
    durs = [max(_duracion_video(c), 0.1) for c in clips]
    cmd: List[str] = []
    for c in clips:
        cmd += ["-i", str(c)]
    filtros = [f"[{i}:v]scale={w}:{h},setsar=1,fps=24,format=yuv420p[v{i}]"
               for i in range(len(clips))]
    trans = "fadeblack" if modo == "negro" else "fade"
    prev, reloj = "[v0]", durs[0]
    for i in range(1, len(clips)):
        di = max(min(d, min(durs[i - 1], durs[i]) / 3.0), 0.05)
        offset = max(reloj - di, 0)
        etq = f"[x{i}]"
        filtros.append(f"{prev}[v{i}]xfade=transition={trans}:duration={di:.3f}:offset={offset:.3f}{etq}")
        prev = etq
        reloj = offset + durs[i]
    filtros.append(f"{prev}format=yuv420p[vout]")
    ok, _ = _ff(cmd + ["-filter_complex", ";".join(filtros), "-map", "[vout]", "-an",
                       "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                       "-movflags", "+faststart", str(salida)], 900)
    return ok and salida.exists()


def _vf_grade(req: Dict[str, Any], w: int, h: int) -> str:
    """El look de campaña: color, grano, viñeta y (opcional) franjas de cine."""
    partes: List[str] = []
    g = req.get("grade", GRADE_DEFAULT)
    if g == "luminoso":
        # Medido sobre su edición en Canva: brillo medio 135, igual que las fotos. Nada
        # de teñir: apenas más contraste y color, y las luces un toque más abiertas.
        partes.append("eq=contrast=1.03:saturation=1.04:brightness=0.01")
    elif g == "pelicula":
        # Sombras hacia el teal, luces hacia el naranja, un poco menos saturado y
        # con la curva apenas en S: el grade que usan las campañas de surf.
        # Medido sobre las fotos reales: con más naranja en las luces el cielo y la
        # espuma salían amarillos, sepia. Así queda cálido en la piel y frío en las
        # sombras sin teñir el mar.
        partes.append("curves=r='0/0 0.25/0.23 0.5/0.5 0.75/0.77 1/1':b='0/0.03 0.5/0.5 1/0.97'")
        partes.append("colorbalance=rs=-0.06:gs=-0.01:bs=0.07:rh=0.03:gh=0.01:bh=-0.03")
        partes.append("eq=contrast=1.04:saturation=0.95")
    elif g == "calido":
        partes.append("colorbalance=rs=0.08:gs=0.03:bs=-0.08:rh=0.06:bh=-0.06")
        partes.append("eq=contrast=1.03:saturation=1.05")
    elif g == "frio":
        partes.append("colorbalance=rs=-0.06:bs=0.09:rh=-0.03:bh=0.05")
        partes.append("eq=contrast=1.04:saturation=0.9")
    elif g == "bn":
        partes.append("hue=s=0")
        partes.append("eq=contrast=1.12:brightness=-0.01")
    if req.get("vineta", True):
        # Medido sobre el primer comercial real: con el ángulo por defecto de ffmpeg
        # (PI/5) el video salía un 28 % más oscuro que las fotos (brillo medio 104 contra
        # 145) y el grade no tenía la culpa. Con PI/12 oscurece un 5 % y las esquinas
        # apenas se cierran, que es lo que se busca.
        partes.append("vignette=angle=PI/12")
    if req.get("grano", True):
        partes.append("noise=alls=7:allf=t+u")
    if req.get("cine"):
        # Franjas arriba y abajo (11 % cada una): el cuadro de cine dentro del vertical.
        hb = int(h * 0.11)
        partes.append(f"drawbox=x=0:y=0:w={w}:h={hb}:color=black:t=fill")
        partes.append(f"drawbox=x=0:y={h - hb}:w={w}:h={hb}:color=black:t=fill")
    partes.append("format=yuv420p")
    return ",".join(partes)


def _aplicar_grade(src: Path, dst: Path, req: Dict[str, Any]) -> bool:
    w, h = _dims(req["formato"])
    ok, _ = _ff(["-i", str(src), "-vf", _vf_grade(req, w, h), "-an", "-c:v", "libx264",
                 "-preset", "fast", "-crf", "18", str(dst)], 900)
    return ok and dst.exists()


def _font_serif() -> Optional[str]:
    """Una serif para los títulos, como en los créditos de una película."""
    import glob as _glob
    for pat in ("/usr/share/fonts/**/LiberationSerif-Regular.ttf",
                "/usr/share/fonts/**/DejaVuSerif.ttf",
                "/usr/share/fonts/**/FreeSerif.ttf",
                "/usr/share/fonts/**/*Serif*.ttf"):
        hits = _glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return _font_path()


def _font_sans_bold() -> Optional[str]:
    import glob as _glob
    for pat in ("/usr/share/fonts/**/LiberationSans-Bold.ttf",
                "/usr/share/fonts/**/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/**/*Sans*Bold*.ttf"):
        hits = _glob.glob(pat, recursive=True)
        if hits:
            return hits[0]
    return _font_path()


def _cuadro_claro(video: Path, t: float, w: int, h: int) -> bool:
    """¿La toma es clara donde va el título (el centro)? Decide negro o blanco."""
    try:
        from PIL import Image, ImageStat
        png = video.with_name(video.stem + "_luz.jpg")
        ok, _ = _ff(["-ss", f"{max(t, 0):.2f}", "-i", str(video), "-frames:v", "1",
                     "-vf", "scale=180:-2", "-q:v", "6", str(png)], 60)
        if not ok or not png.exists():
            return True
        im = Image.open(png).convert("L")
        cw, ch = im.size
        centro = im.crop((int(cw * 0.15), int(ch * 0.3), int(cw * 0.85), int(ch * 0.7)))
        return ImageStat.Stat(centro).mean[0] >= 115
    except Exception:
        return True


def _titulo_png(texto: str, sub: str, w: int, h: int, destino: Path,
                transparente: bool = False, estilo: str = "pelicula", arriba: str = "",
                claro: bool = True) -> bool:
    """Un título. Estilo "campana" (como su Canva): línea chica arriba, sans negrita grande,
    línea chica abajo, en negro sobre una toma clara o en blanco sobre una oscura. Estilo
    "pelicula": serif clara con versalitas espaciadas. Sobre negro (placa) o transparente
    con sombra (para escribir sobre la toma)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    if estilo == "campana":
        return _titulo_campana(texto, sub, arriba, w, h, destino, transparente, claro)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0) if transparente else (8, 8, 10, 255))
    dr = ImageDraw.Draw(img)
    fp = _font_serif()
    fs = _font_path()
    try:
        f1 = ImageFont.truetype(fp, int(w * 0.082)) if fp else ImageFont.load_default()
        f2 = ImageFont.truetype(fs or fp, int(w * 0.026)) if (fs or fp) else ImageFont.load_default()
    except Exception:
        f1 = f2 = ImageFont.load_default()
    texto = (texto or "").strip() or PLACA_DEFAULT
    sub = " ".join((sub or "").strip().upper())       # versalitas espaciadas: L U M A
    # Si el título es largo, va en dos renglones.
    lineas = [texto]
    if len(texto) > 18 and " " in texto:
        pal = texto.split()
        mitad = len(pal) // 2
        lineas = [" ".join(pal[:mitad]), " ".join(pal[mitad:])]
    alto_l = int(w * 0.10)
    y = (h - alto_l * len(lineas)) / 2 - int(h * 0.03)
    for k, ln in enumerate(lineas):
        b = dr.textbbox((0, 0), ln, font=f1)
        x = (w - (b[2] - b[0])) / 2 - b[0]
        yy = y + k * alto_l - b[1]
        if transparente:
            for dx, dy in ((2, 2), (0, 3), (3, 0)):
                dr.text((x + dx, yy + dy), ln, font=f1, fill=(0, 0, 0, 150))
        dr.text((x, yy), ln, font=f1, fill=(246, 240, 226, 255))
    if sub.strip():
        b2 = dr.textbbox((0, 0), sub, font=f2)
        x2 = (w - (b2[2] - b2[0])) / 2 - b2[0]
        y2 = y + alto_l * len(lineas) + int(h * 0.015) - b2[1]
        if transparente:
            dr.text((x2 + 2, y2 + 2), sub, font=f2, fill=(0, 0, 0, 150))
        dr.text((x2, y2), sub, font=f2, fill=(212, 200, 176, 255) if transparente else (150, 145, 160, 255))
    img.save(destino, "PNG")
    return True


def _titulo_campana(texto: str, sub: str, arriba: str, w: int, h: int, destino: Path,
                    transparente: bool, claro: bool) -> bool:
    from PIL import Image, ImageDraw, ImageFont
    fondo = (0, 0, 0, 0) if transparente else ((244, 240, 232, 255) if claro else (12, 12, 14, 255))
    img = Image.new("RGBA", (w, h), fondo)
    dr = ImageDraw.Draw(img)
    fb, fr, fsr = _font_sans_bold(), _font_path(), _font_serif()
    try:
        f1 = ImageFont.truetype(fb, int(w * 0.13)) if fb else ImageFont.load_default()
        f2 = ImageFont.truetype(fr or fb, int(w * 0.026)) if (fr or fb) else ImageFont.load_default()
        f3 = ImageFont.truetype(fsr or fb, int(w * 0.036)) if (fsr or fb) else ImageFont.load_default()
    except Exception:
        f1 = f2 = f3 = ImageFont.load_default()
    tinta = (22, 22, 24, 255) if claro else (246, 244, 238, 255)
    sombra = (255, 255, 255, 110) if claro else (0, 0, 0, 150)
    texto = (texto or "").strip().upper() or PLACA_DEFAULT.upper()
    arriba = " ".join((arriba or "").strip().upper())
    sub = (sub or "").strip().upper()
    lineas = [texto]
    if len(texto) > 14 and " " in texto:
        pal = texto.split()
        mitad = len(pal) // 2
        lineas = [" ".join(pal[:mitad]), " ".join(pal[mitad:])]
    alto_l = int(w * 0.135)
    bloque = alto_l * len(lineas) + (int(w * 0.05) if arriba else 0) + (int(w * 0.06) if sub else 0)
    y = (h - bloque) / 2
    def _pintar(txt, font, yy):
        b = dr.textbbox((0, 0), txt, font=font)
        x = (w - (b[2] - b[0])) / 2 - b[0]
        if transparente:
            dr.text((x + 2, yy - b[1] + 2), txt, font=font, fill=sombra)
        dr.text((x, yy - b[1]), txt, font=font, fill=tinta)
        return b[3] - b[1]
    if arriba:
        _pintar(arriba, f2, y)
        y += int(w * 0.05)
    for ln in lineas:
        _pintar(ln, f1, y)
        y += alto_l
    if sub:
        _pintar(sub, f3, y + int(w * 0.005))
    img.save(destino, "PNG")
    return True


def _placa_png(texto: str, sub: str, w: int, h: int, destino: Path, estilo: str = "pelicula",
               arriba: str = "") -> bool:
    return _titulo_png(texto, sub, w, h, destino, transparente=False, estilo=estilo, arriba=arriba,
                       claro=False)


def _clip_placa(req: Dict[str, Any], d: Path, texto: str = "", sub: str = "",
                nombre: str = "placa", arriba: str = "") -> Optional[Path]:
    """Una placa sobre negro de 2,5 s con fundido de entrada y de salida."""
    w, h = _dims(req["formato"])
    png = d / f"{nombre}.png"
    if not _placa_png(texto or req.get("placa_texto", ""), sub if texto else req.get("placa_sub", ""), w, h, png,
                      req.get("estilo_titulo", ESTILO_TITULO_DEFAULT), arriba):
        return None
    out = d / f"{nombre}.mp4"
    cuadros = int(PLACA_SEG * 24)
    ok, _ = _ff(["-loop", "1", "-framerate", "24", "-i", str(png), "-frames:v", str(cuadros),
                 "-vf", f"fade=t=in:st=0:d=0.7,fade=t=out:st={PLACA_SEG - 0.5:.2f}:d=0.5,"
                        "format=yuv420p",
                 "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(out)], 300)
    return out if ok and out.exists() else None


def _titulo_sobre_toma(src: Path, dst: Path, png: Path, ini: float, dur: float) -> bool:
    """Escribe el título sobre el video entre `ini` e `ini + dur`, entrando y saliendo
    con fundido (la transparencia del PNG se funde, no el video)."""
    fin = ini + dur
    ok, _ = _ff(["-i", str(src), "-loop", "1", "-framerate", "24", "-t", f"{fin + 0.5:.2f}", "-i", str(png),
                 "-filter_complex",
                 f"[1:v]format=rgba,fade=t=in:st={ini:.2f}:d=0.7:alpha=1,"
                 f"fade=t=out:st={fin - 0.7:.2f}:d=0.7:alpha=1[t];"
                 f"[0:v][t]overlay=(W-w)/2:(H-h)/2:enable='between(t,{ini:.2f},{fin:.2f})':shortest=1,format=yuv420p[v]",
                 "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "19", str(dst)], 600)
    return ok and dst.exists()


def _con_musica(src: Path, dst: Path, musica: Path, vol: float = 0.5) -> bool:
    dur = _duracion_video(src) or 1.0
    tope = f"{dur:.2f}"
    ok, _ = _ff(["-i", str(src), "-stream_loop", "-1", "-t", tope, "-i", str(musica),
                 "-filter_complex",
                 f"[1:a]volume={vol:.2f},afade=t=in:st=0:d=1.0,"
                 f"afade=t=out:st={max(dur - 1.5, 0):.2f}:d=1.5[a]",
                 "-map", "0:v", "-map", "[a]", "-t", tope, "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst)], 600)
    return ok and dst.exists()


# ─────────────────────────────────────────────────────────────────────────────
# JOBS
# ─────────────────────────────────────────────────────────────────────────────

def _k_job(jid: str) -> str:
    return _pfx() + "comjob:" + jid


def _k_indice() -> str:
    return _pfx() + "comjobs"


async def _job_set(jid: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    job = (await kv.get(_k_job(jid))) or {"job_id": jid}
    job.update(patch)
    job["latido"] = time.time()
    await kv.set(_k_job(jid), job, ttl=JOB_TTL)
    return job


async def _job_get(jid: str) -> Optional[Dict[str, Any]]:
    return await kv.get(_k_job(jid))


async def _frenado(jid: str) -> bool:
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
    import shutil
    limite = time.time() - JOB_TTL
    try:
        for d in WORK_DIR.iterdir():
            if d.is_dir() and d.stat().st_mtime < limite:
                shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# FOTOS SUBIDAS (de a una, sin achicar)
# ─────────────────────────────────────────────────────────────────────────────

def _fotos_dir() -> Path:
    import hashlib
    marca = hashlib.sha1(_pfx().encode()).hexdigest()[:12]
    d = WORK_DIR / "fotos" / marca
    d.mkdir(parents=True, exist_ok=True)
    return d


_ID_OK = set("0123456789abcdef")


def _foto_path(fid: str) -> Optional[Path]:
    fid = str(fid or "").strip().lower()
    if not fid or len(fid) > 32 or any(c not in _ID_OK for c in fid):
        return None
    p = _fotos_dir() / f"{fid}.jpg"
    return p if p.exists() else None


def _video_path(fid: str) -> Optional[Path]:
    fid = str(fid or "").strip().lower()
    if not fid or len(fid) > 32 or any(c not in _ID_OK for c in fid):
        return None
    p = _fotos_dir() / f"{fid}.mp4"
    return p if p.exists() else None


def _guardar_video(data_o_path, nombre: str = "") -> Dict[str, Any]:
    """Guarda un video como material (tal cual), con su miniatura y su duración."""
    fid = _uuid.uuid4().hex[:16]
    d = _fotos_dir()
    dst = d / f"{fid}.mp4"
    if isinstance(data_o_path, (bytes, bytearray)):
        dst.write_bytes(bytes(data_o_path))
    else:
        import shutil
        shutil.copy(str(data_o_path), str(dst))
    dur = _duracion_video(dst)
    if not dur:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"No pude leer ese video ({nombre or 'archivo'}): ¿es un mp4 o mov?")
    if dur > VIDEO_MAX_SEG:
        dst.unlink(missing_ok=True)
        raise HTTPException(400, f"El video dura {dur:.0f} s; el tope es {VIDEO_MAX_SEG}.")
    _ff(["-ss", f"{min(0.5, dur / 2):.2f}", "-i", str(dst), "-frames:v", "1",
         "-vf", "scale=420:-2:force_original_aspect_ratio=decrease", "-q:v", "6",
         str(d / f"{fid}_min.jpg")], 120)
    return {"id": fid, "tipo": "video", "dur": round(dur, 2), "kb": dst.stat().st_size // 1024}


def _a_jpeg(data: bytes) -> Tuple[bytes, int, int]:
    """Cualquier formato (HEIC no) a JPEG con la rotación del EXIF aplicada y SIN
    achicar: se guarda con toda su resolución."""
    from PIL import Image, ImageOps
    import io as _io
    im = Image.open(_io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")
    buf = _io.BytesIO()
    im.save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue(), im.width, im.height


def _miniatura(src: Path, dst: Path) -> None:
    from PIL import Image
    im = Image.open(src)
    im.thumbnail((420, 420))
    im.convert("RGB").save(dst, "JPEG", quality=82)


def _purgar_fotos() -> None:
    limite = time.time() - FOTO_TTL
    try:
        for cuenta in (WORK_DIR / "fotos").iterdir():
            for p in cuenta.iterdir():
                if p.stat().st_mtime < limite:
                    p.unlink(missing_ok=True)
    except Exception:
        pass


def _materiales_del_pedido(payload: Dict[str, Any], max_dim: int, q: int,
                           para_director: bool = False) -> List[Dict[str, Any]]:
    """El material del pedido, en orden: {"tipo": "foto", "b64"} o {"tipo": "video",
    "path", "dur" (+ "cuadros" para el director)}. Acepta `materiales` [{tipo, id}],
    `foto_ids` (sólo fotos) o `fotos` en base64 (compatibilidad)."""
    out: List[Dict[str, Any]] = []
    mats = payload.get("materiales")
    if not mats:
        mats = [{"tipo": "foto", "id": i} for i in (payload.get("foto_ids") or [])]
    if mats:
        for m in list(mats)[:MAX_FOTOS]:
            if not isinstance(m, dict):
                continue
            fid = str(m.get("id") or "")
            if m.get("tipo") == "video":
                p = _video_path(fid)
                if not p:
                    raise HTTPException(400, f"El video {fid} ya no está en el servidor (el material "
                                             "dura 7 días): volvé a subirlo.")
                item = {"tipo": "video", "path": p, "dur": _duracion_video(p), "id": fid}
                if para_director:
                    item["cuadros"] = _cuadros_video(p, max_dim)
                out.append(item)
            else:
                p = _foto_path(fid)
                if not p:
                    raise HTTPException(400, f"La foto {fid} ya no está en el servidor (el material "
                                             "dura 7 días): volvé a subirla.")
                out.append({"tipo": "foto", "b64": _compress_ref(p.read_bytes(), max_dim=max_dim, q=q),
                            "id": fid})
        return out
    for b in _fotos_del_pedido(payload, max_dim, q):
        out.append({"tipo": "foto", "b64": b, "id": ""})
    return out


def _fotos_del_pedido(payload: Dict[str, Any], max_dim: int, q: int) -> List[str]:
    """Las fotos del pedido, en base64: por id (subidas antes) o en el JSON (compatibilidad).
    Se llevan a `max_dim` como mucho para el motor de video o para el director."""
    out: List[str] = []
    ids = payload.get("foto_ids") or []
    if ids:
        for fid in ids[:MAX_FOTOS]:
            p = _foto_path(fid)
            if not p:
                raise HTTPException(400, f"La foto {fid} ya no está en el servidor (las subidas "
                                         "duran 7 días): volvé a subirla.")
            out.append(_compress_ref(p.read_bytes(), max_dim=max_dim, q=q))
        return out
    for f in (payload.get("fotos") or [])[:MAX_FOTOS]:
        if not f:
            continue
        b = _strip_data_url(str(f))
        try:
            out.append(_compress_ref(base64.b64decode(b), max_dim=max_dim, q=q))
        except Exception:
            out.append(b)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# PEDIDO, ESTIMACIÓN, PROCESO
# ─────────────────────────────────────────────────────────────────────────────

def _tandas(tomas: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Parte la lista de tomas en tandas de hasta 6 tomas y 15 s para Kling."""
    out: List[List[Dict[str, Any]]] = []
    actual: List[Dict[str, Any]] = []
    seg = 0
    for t in tomas:
        s = int(t["seg"])
        if actual and (len(actual) >= TOMAS_POR_TANDA or seg + s > TANDA_SEG):
            out.append(actual)
            actual, seg = [], 0
        actual.append(t)
        seg += s
    if actual:
        out.append(actual)
    return out


def _tomas_plantilla(plantilla: str, total: int) -> List[Dict[str, Any]]:
    """Las tomas de la plantilla que entran en `total` segundos, en orden."""
    lista = list(PLANTILLAS.get(plantilla, PLANTILLAS[PLANTILLA_DEFAULT])["tomas"])
    out: List[Dict[str, Any]] = []
    seg = 0
    for t in lista:
        if seg + int(t["seg"]) > total:
            break
        out.append(dict(t))
        seg += int(t["seg"])
    return out


def _ia_seg(seg: float) -> int:
    return 5 if seg <= 5.5 else 10


def _seg_kling_i2v(t: Dict[str, Any], req: Dict[str, Any]) -> Tuple[int, float]:
    """Cuántos segundos se le piden a Kling para una toma y cuánto se estira después.
    Medido en el primer comercial real: la "cámara lenta" de Kling es apenas más lenta
    que la vida (la cabeza giraba 90° en 1,2 s). Así que en las tomas LENTAS, con el
    estirado activado, se le pide un clip más corto y se estira hasta 1,5× en la mesa de
    edición: cámara lenta de verdad, y encima más barato."""
    seg = float(t["seg"])
    if (t.get("ritmo") or RITMO_DEFAULT) == "lenta" and req.get("ralenti", True):
        pedido = max(KLING_MIN_SEG, int(math.ceil(seg / 1.5)))
        # Si el clip pedido ya es más largo que la toma (un flash de 1 s), se estira
        # 1,5× igual y después se corta: cámara lenta también en el flash.
        return pedido, (seg / pedido if seg / pedido >= 1.0 else 1.5)
    return max(KLING_MIN_SEG, int(math.ceil(seg))), 1.0


def _estimar(req: Dict[str, Any]) -> Dict[str, Any]:
    if req["modo"] == "kling":
        tandas = _tandas(req["tomas"])
        seg = sum(int(t["seg"]) for t in req["tomas"])
        usd = round(KLING[req["motor"]]["precio_seg"] * seg, 3)
        return {"usd_total": usd, "segundos": seg, "tandas": len(tandas),
                "detalle": f"{len(tandas)} tanda(s) de Kling · {seg} s de video · "
                           f"USD {usd:.2f} (+ placa y música gratis)"}
    usd = 0.0
    seg_total = 0.0
    n_ia = 0
    for t in req["tomas"]:
        seg_total += float(t["seg"])
        if t["motor"] == "video":
            continue
        if t["motor"] == "ia":
            n_ia += 1
            if req["motor_ia"] in KLING_I2V:
                usd += KLING_I2V[req["motor_ia"]]["precio_seg"] * _seg_kling_i2v(t, req)[0]
            else:
                usd += PRECIO_SEG.get(req["motor_ia"], 0.05) * _ia_seg(float(t["seg"]))
    usd = round(usd, 3)
    n_v = sum(1 for t in req["tomas"] if t["motor"] == "video")
    return {"usd_total": usd, "segundos": round(seg_total, 1), "tandas": 0,
            "detalle": f"{len(req['tomas'])} tomas ({n_ia} con IA, "
                       f"{len(req['tomas']) - n_ia - n_v} de cámara"
                       + (f", {n_v} video(s)" if n_v else "") + f") · {seg_total:.0f} s · USD {usd:.2f}"}


def _normalizar_pedido(payload: Dict[str, Any]) -> Dict[str, Any]:
    req: Dict[str, Any] = {}
    materiales = _materiales_del_pedido(payload, FOTO_MAX_PX, 92)
    if not materiales:
        raise HTTPException(400, "Subí al menos una foto de la modelo con la prenda.")
    req["materiales"] = materiales
    # Kling con referencias usa SÓLO fotos; el modo foto por foto usa todo el material.
    req["fotos"] = [m["b64"] for m in materiales if m["tipo"] == "foto"]
    req["modo"] = payload.get("modo") if payload.get("modo") in MODOS else "kling"
    if req["modo"] == "kling" and not req["fotos"]:
        raise HTTPException(400, "Kling con referencias necesita fotos, no sólo videos.")
    req["formato"] = payload.get("formato") if payload.get("formato") in FORMATOS else "9:16"
    req["motor"] = payload.get("motor") if payload.get("motor") in KLING else KLING_DEFAULT
    req["motor_ia"] = (payload.get("motor_ia") if payload.get("motor_ia") in MOTORES_IA_FOTO
                       else MOTOR_IA_DEFAULT)
    req["plantilla"] = (payload.get("plantilla") if payload.get("plantilla") in PLANTILLAS
                        else PLANTILLA_DEFAULT)
    try:
        total = int(payload.get("duracion_total") or 15)
    except (TypeError, ValueError):
        total = 15
    req["duracion_total"] = total if total in DURACIONES_TOTAL else 15
    req["grade"] = payload.get("grade") if payload.get("grade") in GRADES else GRADE_DEFAULT
    req["grano"] = payload.get("grano") is not False
    req["vineta"] = payload.get("vineta") is not False
    req["cine"] = bool(payload.get("cine"))
    req["transicion"] = (payload.get("transicion") if payload.get("transicion") in TRANSICIONES
                         else "corte")
    req["placa_texto"] = str(payload.get("placa_texto") or PLACA_DEFAULT).strip()[:40]
    req["placa_sub"] = str(payload.get("placa_sub") or "").strip()[:60]
    # Cierre: el selector nuevo; si no viene, el tilde viejo "placa" decide.
    if payload.get("cierre") in TITULOS:
        req["cierre"] = payload["cierre"]
    else:
        req["cierre"] = "placa" if payload.get("placa") is not False else "no"
    req["placa"] = req["cierre"] == "placa"
    # Apertura: si hay texto y no dicen cómo, va sobre la primera toma (como en su Canva).
    req["apertura_texto"] = str(payload.get("apertura_texto") or "").strip()[:40]
    req["apertura_sub"] = str(payload.get("apertura_sub") or "").strip()[:60]
    req["apertura_arriba"] = str(payload.get("apertura_arriba") or "").strip()[:40]
    req["apertura"] = (payload.get("apertura") if payload.get("apertura") in TITULOS
                       else ("sobre_toma" if req["apertura_texto"] else "no"))
    if req["apertura"] != "no" and not req["apertura_texto"]:
        req["apertura"] = "no"
    req["estilo_titulo"] = (payload.get("estilo_titulo") if payload.get("estilo_titulo") in ESTILOS_TITULO
                            else ESTILO_TITULO_DEFAULT)
    req["musica"] = bool(payload.get("musica"))
    req["ralenti"] = bool(payload.get("ralenti", True))
    req["mezcla"] = payload.get("mezcla") if payload.get("mezcla") in MEZCLAS else "libre"
    req["igualar"] = payload.get("igualar") is not False
    pl = PLANTILLAS[req["plantilla"]]
    req["lugar_es"] = str(payload.get("lugar") or pl["lugar_es"]).strip()[:300]
    req["lugar_en"] = pl["lugar_en"] if req["lugar_es"] == pl["lugar_es"] else ""

    tomas_in = payload.get("tomas") or []
    tomas: List[Dict[str, Any]] = []
    if req["modo"] == "kling":
        base = _tomas_plantilla(req["plantilla"], req["duracion_total"])
        if tomas_in:
            por_es = {t["es"]: t for t in pl["tomas"]}
            for t in tomas_in[:MAX_TOMAS_LIBRES]:
                txt = str((t or {}).get("texto") or "").strip()[:300]
                if not txt:
                    continue
                try:
                    seg = int(round(float((t or {}).get("seg") or 3)))
                except (TypeError, ValueError):
                    seg = 3
                seg = max(TOMA_SEG_MIN, min(TOMA_SEG_MAX, seg))
                orig = por_es.get(txt)
                toma = {"es": txt, "en": (orig["en"] if orig else ""), "seg": seg}
                # "Como mi foto N": esa foto guía el encuadre y el lugar de la toma.
                try:
                    fi = int((t or {}).get("foto") or 0) - 1
                except (TypeError, ValueError):
                    fi = -1
                if 0 <= fi < len(req["fotos"]):
                    toma["foto"] = fi
                tomas.append(toma)
        else:
            tomas = base
        if not tomas:
            raise HTTPException(400, "Escribí al menos una toma (o elegí una plantilla).")
        # No más de lo que entra en las tandas de la duración pedida.
        tope = req["duracion_total"]
        rec: List[Dict[str, Any]] = []
        seg = 0
        for t in tomas:
            if seg + t["seg"] > tope:
                break
            rec.append(t)
            seg += t["seg"]
        tomas = rec or tomas[:1]
    else:
        for i, m in enumerate(materiales):
            t = tomas_in[i] if i < len(tomas_in) and isinstance(tomas_in[i], dict) else {}
            es_video = m["tipo"] == "video"
            motor = t.get("motor") if t.get("motor") in MOTORES_FOTO else "camara"
            if req["mezcla"] in ("ia", "camara"):
                motor = req["mezcla"]
            if es_video:
                motor = "video"
            try:
                seg = float(t.get("seg") or 3.0)
            except (TypeError, ValueError):
                seg = 3.0
            if es_video:
                ok = tuple(v for v in SEG_VIDEO_OK if v <= float(m.get("dur") or 0) + 0.01) or (SEG_VIDEO_OK[0],)
            else:
                ok = ((KLING_I2V_SEG if req["motor_ia"] in KLING_I2V else SEG_IA_OK)
                      if motor == "ia" else SEG_CAMARA_OK)
            seg = min(ok, key=lambda v: abs(v - seg))
            try:
                desde = max(0.0, float(t.get("desde") or 0))
            except (TypeError, ValueError):
                desde = 0.0
            if es_video:
                desde = min(desde, max(0.0, float(m.get("dur") or 0) - seg))
            ritmo = t.get("ritmo") if t.get("ritmo") in RITMOS else RITMO_DEFAULT
            tomas.append({"motor": motor, "seg": seg, "ritmo": ritmo, "desde": round(desde, 2),
                          "tipo": m["tipo"],
                          "es": str(t.get("texto") or "").strip()[:200],
                          # Si la toma viene del director ya trae su inglés: se respeta.
                          "en": str(t.get("texto_en") or "").strip()[:300]})
    req["tomas"] = tomas
    return req


async def _traducir_tomas(req: Dict[str, Any]) -> None:
    """Las tomas escritas por ella (sin versión en inglés) se traducen en UNA
    llamada; si falla, viajan en castellano, que los motores también entienden."""
    faltan = {str(i): t["es"] for i, t in enumerate(req["tomas"]) if t.get("es") and not t.get("en")}
    if req["modo"] == "kling" and req["lugar_es"] and not req["lugar_en"]:
        faltan["lugar"] = req["lugar_es"]
    if not faltan:
        return
    try:
        tr = await _traducir_libres(faltan)
    except Exception:
        tr = {}
    for k, v in tr.items():
        if k == "lugar":
            req["lugar_en"] = v
        else:
            req["tomas"][int(k)]["en"] = v
    if req["modo"] == "kling" and req["lugar_es"] and not req["lugar_en"]:
        req["lugar_en"] = req["lugar_es"]
    for t in req["tomas"]:
        if t.get("es") and not t.get("en"):
            t["en"] = t["es"]


async def _procesar(jid: str, req: Dict[str, Any]) -> None:
    d = _dir(jid)
    costo = 0.0
    try:
        set_current_sub(req.get("user_sub"))
        await _job_set(jid, {"estado": "trabajando", "paso": "Preparando el material…"})
        # Un archivo por material, en el orden del pedido: foto_i.jpg o el video tal cual.
        fotos_disco: List[Path] = []
        for i, m in enumerate(req.get("materiales") or [{"tipo": "foto", "b64": b} for b in req["fotos"]]):
            if m["tipo"] == "video":
                fotos_disco.append(Path(m["path"]))
            else:
                p = d / f"foto_{i}.jpg"
                p.write_bytes(base64.b64decode(m["b64"]))
                fotos_disco.append(p)
        await _traducir_tomas(req)
        clips: List[Path] = []
        if req["modo"] == "kling":
            tandas = _tandas(req["tomas"])
            for k, tomas in enumerate(tandas):
                if await _frenado(jid):
                    raise RuntimeError("Frenado por la usuaria.")
                crudo = d / f"tanda_{k}.mp4"
                costo += await _kling_tanda(jid, req, k, tomas, crudo)
                await _job_set(jid, {"paso": f"Tanda {k + 1}: acomodando el clip…", "costo": costo,
                                     "tandas_listas": k + 1})
                norm = d / f"clip_{k}.mp4"
                ral = 1.0   # Kling ya filma en cámara lenta cuando el prompt lo pide
                if not _normalizar_clip(crudo, norm, req["formato"], None, ral):
                    raise RuntimeError(f"No pude acomodar el clip de la tanda {k + 1}.")
                clips.append(norm)
        else:
            hay_ia = any(t["motor"] == "ia" for t in req["tomas"])
            igualar = _res_motor(req["motor_ia"]) if (hay_ia and req.get("igualar", True)) else 0
            for i, t in enumerate(req["tomas"]):
                if await _frenado(jid):
                    raise RuntimeError("Frenado por la usuaria.")
                foto = fotos_disco[i]
                norm = d / f"clip_{i}.mp4"
                if t["motor"] == "video":
                    # Un video de ella (o un clip de otro comercial): la parte elegida,
                    # al ritmo pedido, gratis.
                    await _job_set(jid, {"paso": f"Toma {i + 1}: recortando tu video ({RITMOS.get(t.get('ritmo'), 'lenta').lower()})…"})
                    ral = {"lenta": 1.5, "normal": 1.0, "rapida": 0.85}.get(t.get("ritmo"), 1.5)
                    if not req.get("ralenti", True) and ral > 1.0:
                        ral = 1.0
                    if not await asyncio.to_thread(_normalizar_clip, foto, norm, req["formato"],
                                                   float(t["seg"]), ral, float(t.get("desde") or 0)):
                        raise RuntimeError(f"No pude acomodar tu video de la toma {i + 1}.")
                elif t["motor"] == "ia" and req["motor_ia"] in KLING_I2V:
                    # Kling: su foto es el primer cuadro; el clip dura lo pedido y la
                    # cámara lenta la filma él (no se estira después).
                    crudo = d / f"ia_{i}.mp4"
                    frame = base64.b64encode(foto.read_bytes()).decode()
                    seg_k, ral = _seg_kling_i2v(t, req)
                    costo += await _kling_i2v(jid, req, i, frame, _prompt_foto_ia(req, i, t),
                                              seg_k, crudo)
                    if not _normalizar_clip(crudo, norm, req["formato"], float(t["seg"]), ral):
                        raise RuntimeError(f"No pude acomodar el clip de la toma {i + 1}.")
                elif t["motor"] == "ia":
                    await _job_set(jid, {"paso": f"Toma {i + 1}: {MOTOR_LABEL.get(req['motor_ia'], req['motor_ia'])} "
                                                 f"está filmando la foto ({_ia_seg(t['seg'])} s)…"})
                    crudo = d / f"ia_{i}.mp4"
                    frame = base64.b64encode(foto.read_bytes()).decode()
                    await _generar_fal(_prompt_foto_ia(req, i, t), frame, req["motor_ia"],
                                       crudo, _ia_seg(t["seg"]))
                    c = round(PRECIO_SEG.get(req["motor_ia"], 0.05) * _ia_seg(t["seg"]), 3)
                    costo += c
                    await budget_record("comercial_ia", FAL_MODELS[req["motor_ia"]], c, 1,
                                        note=f"comercial toma {i + 1} ({_ia_seg(t['seg'])} s)")
                    # lenta: se estira 1,5× (los motores de Videos no filman en cámara
                    # lenta de verdad); normal: tal cual; rápida: apenas acelerada.
                    ral = {"lenta": 1.5, "normal": 1.0, "rapida": 0.85}.get(t.get("ritmo"), 1.5)
                    if not req.get("ralenti", True) and ral > 1.0:
                        ral = 1.0
                    if not _normalizar_clip(crudo, norm, req["formato"], float(t["seg"]), ral):
                        raise RuntimeError(f"No pude acomodar el clip de la toma {i + 1}.")
                else:
                    await _job_set(jid, {"paso": f"Toma {i + 1}: cámara sobre la foto ({RITMOS.get(t.get('ritmo'), 'lenta').lower()})…"})
                    if not await asyncio.to_thread(_clip_deriva, foto, norm, req["formato"],
                                                   float(t["seg"]), i, t.get("ritmo") or RITMO_DEFAULT,
                                                   igualar):
                        raise RuntimeError(f"No pude armar la toma {i + 1} con la cámara.")
                clips.append(norm)
                await _job_set(jid, {"costo": costo, "tomas_listas": i + 1})

        await _job_set(jid, {"paso": "Pegando las tomas…"})
        unido = d / "unido.mp4"
        if not await asyncio.to_thread(_concatenar, clips, unido, req["formato"], req["transicion"]):
            raise RuntimeError("No pude pegar las tomas.")
        await _job_set(jid, {"paso": "Aplicando el grade de película…"})
        con_grade = d / "grade.mp4"
        if not await asyncio.to_thread(_aplicar_grade, unido, con_grade, req):
            con_grade = unido
        # APERTURA y CIERRE: escritos sobre la primera/última toma (con fundido) o como
        # placa sobre negro antes/después. El texto va DESPUÉS del grade, así queda limpio.
        w_, h_ = _dims(req["formato"])
        cuerpo = con_grade
        dur_cuerpo = _duracion_video(cuerpo) or 0.0
        est_t = req.get("estilo_titulo", ESTILO_TITULO_DEFAULT)
        if req.get("apertura") == "sobre_toma" and req.get("apertura_texto"):
            png = d / "apertura_t.png"
            claro = await asyncio.to_thread(_cuadro_claro, cuerpo, 1.0, w_, h_)
            if await asyncio.to_thread(_titulo_png, req["apertura_texto"], req.get("apertura_sub", ""), w_, h_, png, True,
                                       est_t, req.get("apertura_arriba", ""), claro):
                out = d / "con_apertura.mp4"
                if await asyncio.to_thread(_titulo_sobre_toma, cuerpo, out, png, 0.4, min(TITULO_SOBRE_SEG, max(dur_cuerpo - 0.8, 1.0))):
                    cuerpo = out
        if req.get("cierre") == "sobre_toma" and req.get("placa_texto"):
            png = d / "cierre_t.png"
            claro = await asyncio.to_thread(_cuadro_claro, cuerpo, max(dur_cuerpo - 1.5, 0.0), w_, h_)
            if await asyncio.to_thread(_titulo_png, req["placa_texto"], req.get("placa_sub", ""), w_, h_, png, True,
                                       est_t, "", claro):
                out = d / "con_cierre.mp4"
                dur_t = min(TITULO_SOBRE_SEG, max(dur_cuerpo - 0.8, 1.0))
                if await asyncio.to_thread(_titulo_sobre_toma, cuerpo, out, png, max(dur_cuerpo - dur_t - 0.2, 0.0), dur_t):
                    cuerpo = out
        partes = []
        if req.get("apertura") == "placa" and req.get("apertura_texto"):
            pa = await asyncio.to_thread(_clip_placa, req, d, req["apertura_texto"], req.get("apertura_sub", ""), "apertura",
                                         req.get("apertura_arriba", ""))
            if pa:
                partes.append(pa)
        partes.append(cuerpo)
        if req.get("cierre") == "placa":
            placa = await asyncio.to_thread(_clip_placa, req, d, req.get("placa_texto", ""), req.get("placa_sub", ""), "placa")
            if placa:
                partes.append(placa)
        con_grade = cuerpo
        mudo = d / "mudo.mp4"
        if len(partes) > 1:
            modo_placa = "negro" if req["transicion"] == "corte" else "fundido"
            if not await asyncio.to_thread(_concatenar, partes, mudo, req["formato"], modo_placa):
                mudo = con_grade
        else:
            mudo = con_grade
        final = d / "final.mp4"
        mus = _musica_path()
        if req.get("musica") and mus.exists():
            await _job_set(jid, {"paso": "Sumando la música…"})
            if not await asyncio.to_thread(_con_musica, mudo, final, mus):
                import shutil
                shutil.copy(mudo, final)
        else:
            import shutil
            shutil.copy(mudo, final)
        dur = _duracion_video(final)
        await _job_set(jid, {"estado": "listo", "paso": "Listo.", "costo": round(costo, 3),
                             "duracion": round(dur, 1), "final": True,
                             "terminado": time.time()})
    except Exception as e:
        print(f"[comerciales] job {jid} falló: {e}")
        await _job_set(jid, {"estado": "error", "detalle": str(e)[:400], "costo": round(costo, 3)})


# ─────────────────────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────────────────────

async def _bind(request: Request) -> None:
    set_current_sub(session_sub_from_request(request))


router = APIRouter(dependencies=[Depends(_bind)])


def _publico(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if k not in ("fotos",)}


@router.get(ROUTE_PREFIX + "/api/config")
async def api_config() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "modos": MODOS,
        "motores_kling": {k: {"label": v["label"], "precio_seg": v["precio_seg"]} for k, v in KLING.items()},
        "motores_ia": MOTORES_IA_FOTO,
        "seg_kling": KLING_I2V_SEG,
        "plantillas": {k: {"nombre": v["nombre"], "desc": v["desc"], "lugar": v["lugar_es"],
                           "tomas": [{"texto": t["es"], "seg": t["seg"]} for t in v["tomas"]]}
                       for k, v in PLANTILLAS.items()},
        "grades": GRADES, "transiciones": TRANSICIONES, "duraciones": DURACIONES_TOTAL,
        "movimientos_foto": [m["es"] for m in MOVIMIENTOS_FOTO],
        "ritmos": RITMOS,
        "mezclas": MEZCLAS,
        "seg_video": SEG_VIDEO_OK, "objetivos": DURACIONES_OBJETIVO,
        "titulos": TITULOS, "estilos_titulo": ESTILOS_TITULO,
        "actos": {1: "comienzo", 2: "acción", 3: "fin"},
        "seg_camara": SEG_CAMARA_OK, "seg_ia": SEG_IA_OK,
        "musica": _musica_path().exists(), "max_fotos": MAX_FOTOS, "max_refs": MAX_REFS_KLING,
        "fal_key": bool(await _fal_key()),
    }


@router.post(ROUTE_PREFIX + "/api/foto")
async def api_foto(archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube UNA foto, tal cual (sin achicar), y devuelve su id."""
    data = await archivo.read()
    if len(data) > FOTO_MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"La foto pesa más de {FOTO_MAX_MB} MB.")
    try:
        jpg, w, h = await asyncio.to_thread(_a_jpeg, data)
    except Exception as e:
        raise HTTPException(400, f"No pude abrir esa imagen ({type(e).__name__}). Si es HEIC, "
                                 "exportala como JPG.")
    fid = _uuid.uuid4().hex[:16]
    d = _fotos_dir()
    (d / f"{fid}.jpg").write_bytes(jpg)
    try:
        await asyncio.to_thread(_miniatura, d / f"{fid}.jpg", d / f"{fid}_min.jpg")
    except Exception:
        pass
    _purgar_fotos()
    return {"id": fid, "w": w, "h": h, "kb": len(jpg) // 1024}


@router.post(ROUTE_PREFIX + "/api/video")
async def api_video(archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """Sube UN video como material, tal cual."""
    data = await archivo.read()
    if len(data) > VIDEO_MAX_MB * 1024 * 1024:
        raise HTTPException(400, f"El video pesa más de {VIDEO_MAX_MB} MB.")
    res = await asyncio.to_thread(_guardar_video, data, archivo.filename or "")
    _purgar_fotos()
    return res


@router.get(ROUTE_PREFIX + "/api/foto/{fid}")
async def api_foto_ver(fid: str):
    """La miniatura para la pantalla (foto o video; el grande queda en el server)."""
    p = _foto_path(fid) or _video_path(fid)
    if not p:
        raise HTTPException(404, "Ese material no está.")
    m = p.with_name(p.stem + "_min.jpg")
    if m.exists():
        return FileResponse(str(m), media_type="image/jpeg")
    if p.suffix == ".mp4":
        raise HTTPException(404, "Ese video no tiene miniatura.")
    return FileResponse(str(p), media_type="image/jpeg")


@router.get(ROUTE_PREFIX + "/api/material/{fid}/ver")
async def api_material_ver(fid: str):
    """El video de material entero, para mirarlo en la pantalla."""
    p = _video_path(fid)
    if not p:
        raise HTTPException(404, "Ese video no está.")
    return FileResponse(str(p), media_type="video/mp4")


@router.delete(ROUTE_PREFIX + "/api/foto/{fid}")
async def api_foto_borrar(fid: str) -> Dict[str, Any]:
    for p in (_foto_path(fid), _video_path(fid)):
        if p:
            p.unlink(missing_ok=True)
            p.with_name(p.stem + "_min.jpg").unlink(missing_ok=True)
            for k in (1, 5, 9):
                p.with_name(p.stem + f"_c{k}.jpg").unlink(missing_ok=True)
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/jobs/{jid}/clips")
async def api_job_clips(jid: str) -> Dict[str, Any]:
    """Los clips (una toma cada uno) de un comercial anterior, para reusarlos."""
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe.")
    d = _dir(jid)
    out = []
    for p in sorted(d.glob("clip_*.mp4"), key=lambda x: int(x.stem.split("_")[1])):
        n = int(p.stem.split("_")[1])
        mini = d / f"clipmin_{n}.jpg"
        if not mini.exists():
            _ff(["-ss", "0.3", "-i", str(p), "-frames:v", "1",
                 "-vf", "scale=300:-2:force_original_aspect_ratio=decrease", "-q:v", "6", str(mini)], 60)
        toma = (job.get("tomas") or [{}] * (n + 1))[n] if n < len(job.get("tomas") or []) else {}
        out.append({"n": n, "dur": round(_duracion_video(p), 2), "texto": (toma or {}).get("texto", ""),
                    "mini": mini.exists()})
    return {"clips": out}


@router.get(ROUTE_PREFIX + "/api/clipmin/{jid}/{n}")
async def api_clipmin(jid: str, n: int):
    p = _dir(jid) / f"clipmin_{n}.jpg"
    if not p.exists():
        raise HTTPException(404, "Sin miniatura.")
    return FileResponse(str(p), media_type="image/jpeg")


@router.post(ROUTE_PREFIX + "/api/importar_clip")
async def api_importar_clip(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Trae un clip de un comercial anterior como material (video) de este."""
    jid = str(payload.get("jid") or "")
    try:
        n = int(payload.get("n"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Falta el número del clip.")
    if not await _job_get(jid):
        raise HTTPException(404, "Ese trabajo no existe.")
    p = _dir(jid) / f"clip_{n}.mp4"
    if not p.exists():
        raise HTTPException(404, "Ese clip ya no está (los trabajos duran 7 días).")
    return await asyncio.to_thread(_guardar_video, p, f"clip {n + 1}")


@router.post(ROUTE_PREFIX + "/api/director")
async def api_director(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """El director mira las fotos y propone motor, ritmo, duración y acción por foto."""
    materiales = await asyncio.to_thread(_materiales_del_pedido, payload, 900, 80, True)
    if not materiales:
        raise HTTPException(400, "Subí el material primero: el director necesita verlo.")
    estilo = payload.get("estilo") if payload.get("estilo") in PLANTILLAS else PLANTILLA_DEFAULT
    mezcla = payload.get("mezcla") if payload.get("mezcla") in MEZCLAS else "libre"
    try:
        objetivo = int(payload.get("objetivo") or 30)
    except (TypeError, ValueError):
        objetivo = 30
    objetivo = objetivo if objetivo in DURACIONES_OBJETIVO else 30
    res = await _director(materiales, estilo, str(payload.get("lugar") or ""),
                          str(payload.get("estilo_txt") or ""), mezcla, objetivo,
                          str(payload.get("historia") or ""))
    # La pantalla necesita saber a qué id corresponde cada índice de la secuencia.
    ids = [{"tipo": m["tipo"], "id": m.get("id", ""), "dur": m.get("dur")} for m in materiales]
    res["materiales"] = ids
    return res


@router.post(ROUTE_PREFIX + "/api/estimar")
async def api_estimar(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    est = _estimar(req)
    est["tomas"] = [{"texto": t.get("es", ""), "seg": t["seg"], "motor": t.get("motor", "kling"),
                     "foto": (t["foto"] + 1) if isinstance(t.get("foto"), int) else None}
                    for t in req["tomas"]]
    return est


@router.post(ROUTE_PREFIX + "/api/generar")
async def api_generar(request: Request, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    req = _normalizar_pedido(payload)
    req["user_sub"] = session_sub_from_request(request)
    necesita_fal = req["modo"] == "kling" or any(t["motor"] == "ia" for t in req["tomas"])
    if necesita_fal and not await _fal_key():
        raise HTTPException(400, "Falta la API key de fal: cargala en Fotos → Ajustes (API key de "
                                 "fal) o como FAL_KEY en Railway.")
    if req["modo"] == "kling" and len(req["fotos"]) < 1:
        raise HTTPException(400, "Kling necesita al menos una foto de referencia.")
    est = _estimar(req)
    permitido, motivo, _t, _c = await budget_check(est["usd_total"])
    if not permitido:
        raise HTTPException(402, motivo)
    _purgar_viejos()
    jid = _uuid.uuid4().hex[:12]
    await _job_set(jid, {
        "job_id": jid, "estado": "encolado", "paso": "En cola…", "creado": time.time(),
        "modo": req["modo"], "motor": (KLING[req["motor"]]["label"] if req["modo"] == "kling"
                                       else MOTORES_IA_FOTO.get(req["motor_ia"], req["motor_ia"])),
        "plantilla": PLANTILLAS[req["plantilla"]]["nombre"] if req["modo"] == "kling" else "Foto por foto",
        "formato": req["formato"], "estimado": est, "n_fotos": len(req["fotos"]),
        "tomas": [{"texto": t.get("es", ""), "seg": t["seg"], "motor": t.get("motor", "kling")}
                  for t in req["tomas"]],
        "tandas": len(_tandas(req["tomas"])) if req["modo"] == "kling" else 0,
        "costo": 0.0,
    })
    await _indice_agregar(jid)
    _spawn(_procesar(jid, req))
    return {"job_id": jid, "estimado": est}


@router.get(ROUTE_PREFIX + "/api/estado/{jid}")
async def api_estado(jid: str) -> Dict[str, Any]:
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe (o ya venció).")
    return _publico(job)


@router.post(ROUTE_PREFIX + "/api/frenar/{jid}")
async def api_frenar(jid: str) -> Dict[str, Any]:
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe.")
    await _job_set(jid, {"frenar": True})
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/jobs")
async def api_jobs() -> Dict[str, Any]:
    ids = (await kv.get(_k_indice())) or []
    out = []
    for jid in ids[:JOBS_INDICE]:
        j = await _job_get(jid)
        if j:
            out.append({k: j.get(k) for k in ("job_id", "estado", "paso", "modo", "motor", "plantilla",
                                              "costo", "duracion", "creado", "final", "detalle")})
    return {"jobs": out}


@router.get(ROUTE_PREFIX + "/api/final/{jid}")
async def api_final(jid: str):
    job = await _job_get(jid)
    if not job:
        raise HTTPException(404, "Ese trabajo no existe.")
    p = _dir(jid) / "final.mp4"
    if not p.exists():
        raise HTTPException(404, "Todavía no hay video final.")
    return FileResponse(str(p), media_type="video/mp4", filename=f"comercial_{jid}.mp4")


@router.get(ROUTE_PREFIX + "/api/clip/{jid}/{n}")
async def api_clip(jid: str, n: int):
    p = _dir(jid) / f"clip_{n}.mp4"
    if not p.exists():
        raise HTTPException(404, "Ese clip no existe todavía.")
    return FileResponse(str(p), media_type="video/mp4")


@router.post(ROUTE_PREFIX + "/api/musica")
async def api_musica(archivo: UploadFile = File(...)) -> Dict[str, Any]:
    """La misma cortina musical que usa Videos (una por cuenta)."""
    data = await archivo.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(400, "La música pesa más de 25 MB.")
    _musica_path().write_bytes(data)
    return {"ok": True}


@router.get(ROUTE_PREFIX + "/api/health")
async def api_health() -> Dict[str, Any]:
    return {"ok": True, "version": VERSION, "ffmpeg": bool(_ffmpeg_bin()),
            "kling": {k: v["modelo"] for k, v in KLING.items()}}


@router.get(ROUTE_PREFIX, response_class=HTMLResponse)
async def ui() -> HTMLResponse:
    html = (HTML_PAGE
            .replace("%%API%%", ROUTE_PREFIX + "/api")
            .replace("%%HOME%%", os.environ.get("IMAGENES_PREFIX", "") or "/")
            .replace("%%VIDEOS%%", os.environ.get("VIDEOS_PREFIX", "/videos"))
            .replace("%%VERSION%%", VERSION))
    return HTMLResponse(html)


# ─────────────────────────────────────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es-AR">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Studio Luma · Comerciales</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%23161419'/%3E%3Crect x='2.5' y='2.5' width='59' height='59' rx='12' fill='none' stroke='%23c9a86b' stroke-width='2'/%3E%3Ctext x='32' y='44' font-family='Georgia,serif' font-size='34' font-weight='600' fill='%23d8b878' text-anchor='middle'%3ESL%3C/text%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Bodoni+Moda:opsz,wght@6..96,400;6..96,500;6..96,600&family=Jost:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
  :root{--ink:#ecebf1;--ink-soft:#96919f;--line:#2c2a34;--ivory:#131218;--card:#1b1a21;--card-2:#232128;
    --rose:#c9a86b;--rose-deep:#d8b878;--ok:#5fae86;--bad:#e0736f;
    --shadow:0 1px 2px rgba(0,0,0,.5),0 12px 34px rgba(0,0,0,.4)}
  *{box-sizing:border-box}
  body{margin:0;background:var(--ivory);color:var(--ink);font-family:Jost,system-ui,sans-serif;font-size:17px;line-height:1.55;-webkit-font-smoothing:antialiased}
  a{color:var(--rose-deep);text-decoration:none}
  header{padding:16px 16px 12px;border-bottom:1px solid var(--line);background:rgba(19,18,24,.9);backdrop-filter:blur(8px);position:sticky;top:0;z-index:20}
  .brandrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  .mono{width:40px;height:40px;border-radius:11px;border:1px solid var(--rose);display:flex;align-items:center;justify-content:center;flex:none;background:linear-gradient(150deg,#221f27,#161419);font-family:'Bodoni Moda',serif;font-weight:600;font-size:19px;color:var(--rose-deep)}
  .brand{font-family:'Bodoni Moda',serif;font-size:23px;font-weight:600;line-height:1}
  .brand small{font-family:Jost;font-weight:400;font-size:11px;color:var(--ink-soft);letter-spacing:.22em;text-transform:uppercase;display:block;margin-top:5px}
  .volver{font-size:13px;border:1px solid var(--line);border-radius:99px;padding:6px 12px;background:var(--card)}
  .links{margin-left:auto;display:flex;gap:6px}
  main{max-width:900px;margin:0 auto;padding:16px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;margin-bottom:16px;box-shadow:var(--shadow)}
  h2{font-family:'Bodoni Moda',serif;font-weight:600;font-size:23px;margin:0 0 6px}
  h3{font-family:'Bodoni Moda',serif;font-weight:500;font-size:18px;margin:0 0 4px}
  .hint{color:var(--ink-soft);font-size:14.5px;margin:0 0 12px}
  label{display:block;font-size:14px;font-weight:500;color:var(--ink-soft);margin:12px 0 6px}
  input,select,textarea{width:100%;padding:12px 13px;border:1px solid var(--line);border-radius:11px;background:var(--card-2);color:var(--ink);font-family:Jost,sans-serif;font-size:16px}
  textarea{min-height:70px;resize:vertical}
  .row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  .row3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}
  @media(max-width:640px){.row,.row3{grid-template-columns:1fr}}
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
  .chip{padding:9px 14px;border-radius:999px;border:1px solid var(--line);background:var(--card-2);cursor:pointer;font-size:14.5px;color:var(--ink-soft);user-select:none}
  .chip.on{border-color:var(--rose);color:var(--rose-deep);background:rgba(201,168,107,.1)}
  .btn{display:inline-flex;align-items:center;gap:8px;padding:13px 20px;border-radius:12px;border:1px solid var(--rose);background:linear-gradient(150deg,#2a2431,#1b1a21);color:var(--rose-deep);font-family:Jost;font-size:16px;font-weight:500;cursor:pointer}
  .btn.sec{border-color:var(--line);color:var(--ink-soft)}
  .btn:disabled{opacity:.5;cursor:default}
  .fotos{display:grid;grid-template-columns:repeat(auto-fill,minmax(96px,1fr));gap:8px;margin-top:10px}
  .foto{position:relative;aspect-ratio:4/5;border-radius:10px;overflow:hidden;border:1px solid var(--line);background:#000}
  .foto img{width:100%;height:100%;object-fit:cover;display:block}
  .foto .n{position:absolute;top:5px;left:5px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;border-radius:99px;padding:1px 8px}
  .foto .x{position:absolute;top:4px;right:4px;background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:99px;width:22px;height:22px;cursor:pointer;font-size:13px}
  .foto .mv{position:absolute;bottom:4px;left:4px;right:4px;display:flex;justify-content:space-between}
  .foto .mv button{background:rgba(0,0,0,.65);color:#fff;border:0;border-radius:6px;padding:1px 7px;cursor:pointer;font-size:12px}
  .foto .v{position:absolute;bottom:26px;left:5px;background:rgba(201,168,107,.85);color:#131218;font-size:11px;font-weight:600;border-radius:99px;padding:1px 7px}
  .foto.desc{opacity:.45}
  .historia{background:var(--card-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:10px 0;font-size:15px}
  .historia b{font-family:'Bodoni Moda',serif;font-size:18px;font-weight:500}
  .historia ol{margin:6px 0 0 18px;padding:0;color:var(--ink-soft);font-size:14px}
  .toma .acto{color:var(--rose-deep);font-size:12px;margin-left:4px}
  .toma .usar{width:auto;margin:0}
  .toma.foto-toma{grid-template-columns:28px 24px 1fr 110px 96px 76px}
  .clips{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 0;width:100%}
  .clips .cl{width:84px;text-align:center;font-size:12px;color:var(--ink-soft)}
  .clips .cl img{width:84px;aspect-ratio:9/16;object-fit:cover;border-radius:8px;border:1px solid var(--line);display:block}
  .clips .cl button{margin-top:4px;font-size:12px;padding:3px 8px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--rose-deep);cursor:pointer}
  .hist .it{flex-wrap:wrap}
  .tomas{display:flex;flex-direction:column;gap:8px;margin-top:8px}
  .toma{display:grid;grid-template-columns:28px 1fr 84px 96px 30px;gap:8px;align-items:center}
  .toma.foto-toma{grid-template-columns:28px 1fr 110px 96px 76px}
  .porque{grid-column:2/-1;color:var(--ink-soft);font-size:13px;margin:-2px 0 4px}
  .toma-wrap{display:flex;flex-direction:column}
  @media(max-width:640px){.toma.foto-toma{grid-template-columns:24px 1fr 1fr;grid-auto-rows:auto}.toma.foto-toma input{grid-column:2/-1}}
  .toma .idx{color:var(--ink-soft);font-size:13px;text-align:right}
  .toma input,.toma select{padding:9px 10px;font-size:14.5px}
  .toma button{background:none;border:0;color:var(--bad);cursor:pointer;font-size:16px}
  .sw{display:flex;align-items:center;gap:10px;margin:10px 0;font-size:15px;cursor:pointer}
  .sw input{width:auto}
  .est{font-size:15px;color:var(--rose-deep);margin-top:10px}
  .prog{background:var(--card-2);border:1px solid var(--line);border-radius:12px;padding:14px;margin-top:12px;font-size:15px}
  .bar{height:6px;background:var(--line);border-radius:99px;overflow:hidden;margin-top:8px}
  .bar i{display:block;height:100%;background:var(--rose);width:0;transition:.4s}
  video{width:100%;max-height:70vh;border-radius:12px;background:#000;margin-top:12px}
  .err{color:var(--bad);font-size:14.5px;margin-top:8px}
  .hist{display:flex;flex-direction:column;gap:8px}
  .hist .it{display:flex;justify-content:space-between;gap:10px;padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:var(--card-2);font-size:14.5px;align-items:center}
  .hist .it span{color:var(--ink-soft)}
  .hidden{display:none}
  .q{display:inline-block;width:18px;height:18px;border-radius:99px;border:1px solid var(--line);color:var(--ink-soft);font-size:12px;text-align:center;line-height:16px;margin-left:5px;cursor:help}
</style>
</head>
<body>
<header>
  <div class="brandrow">
    <div class="mono">SL</div>
    <div class="brand">Comerciales<small>Studio Luma · v%%VERSION%%</small></div>
    <div class="links">
      <a class="volver" href="%%HOME%%">← Fotos</a>
      <a class="volver" href="%%VIDEOS%%">🎬 Videos</a>
    </div>
  </div>
</header>
<main>
  <div class="card">
    <h2>Un comercial con tus fotos</h2>
    <p class="hint">Estilo campaña: cámara lenta, luz natural, grade de película, música y la placa de la marca al final. Subí las fotos de la modelo con la prenda, en locación. También videos: los tuyos, o clips de comerciales anteriores (abajo, en el historial, "Clips").</p>
    <input type="file" id="f-fotos" accept="image/*,video/mp4,video/quicktime,video/*" multiple>
    <div class="fotos" id="fotos"></div>
    <p class="hint" id="fotos-ayuda" style="margin-top:8px"></p>
  </div>

  <div class="card">
    <h3>Cómo se arma</h3>
    <div class="chips" id="modos"></div>
    <p class="hint" id="modo-ayuda" style="margin-top:8px"></p>

    <div id="panel-kling">
      <div class="row">
        <div><label>Plantilla</label><select id="plantilla"></select></div>
        <div><label>Duración total</label><select id="duracion"></select></div>
      </div>
      <div class="row">
        <div><label>Motor</label><select id="motor"></select></div>
        <div><label>Formato</label><select id="formato"><option value="9:16">Vertical 9:16 (reel)</option><option value="16:9">Horizontal 16:9</option></select></div>
      </div>
      <label>Lugar <span class="q" title="Dónde transcurre el comercial. Se llena con la plantilla; podés cambiarlo.">?</span></label>
      <input id="lugar" placeholder="una playa de surf: un shack de madera con tablas, una camioneta en la arena, olas">
      <label>Las tomas, en orden <span class="q" title="Cada toma tiene su texto, sus segundos y, si querés, una de tus fotos como guía: Kling copia el encuadre, el lugar y la luz de esa foto para esa toma (hasta 3 fotos de guía distintas por tanda). Kling filma hasta 6 tomas y 15 segundos por tanda; un comercial de 30 son dos tandas pegadas.">?</span></label>
      <div class="tomas" id="tomas-kling"></div>
      <div style="margin-top:8px"><button class="btn sec" id="add-toma">+ toma</button></div>
    </div>

    <div id="panel-fotos" class="hidden">
      <div class="row">
        <div><label>Motor para las tomas con IA</label><select id="motor-ia"></select></div>
        <div><label>Formato</label><select id="formato2"><option value="9:16">Vertical 9:16 (reel)</option><option value="16:9">Horizontal 16:9</option></select></div>
      </div>
      <label>Mezcla de tomas <span class="q" title="Una foto fija va nítida y un clip de IA sale más blando: pegados, se nota. 'Todas con IA' deja todo con la misma textura. Si mezclás, las fijas se igualan a la textura de los clips.">?</span></label>
      <select id="mezcla"></select>
      <div class="row">
        <div><label>Estilo para el director</label><select id="estilo-dir"></select></div>
        <div><label>Lugar (opcional)</label><input id="lugar-dir" placeholder="una playa de surf al amanecer"></div>
      </div>
      <label>Contale el estilo con tus palabras (opcional) <span class="q" title="Una referencia, un ánimo, una marca que te guste: 'estilo Rip Curl, crudo y con energía', 'romántico y dorado, cortes suaves', 'editorial frío, blanco y negro'. El director la usa para elegir las tomas y el look (grade, cortes, franjas, grano).">?</span></label>
      <input id="estilo-txt" placeholder="estilo Rip Curl: cámara lenta, cortes secos al ritmo de la música, un golpe de energía">
      <div class="row">
        <div><label>La historia (opcional) <span class="q" title="Qué querés contar. Si no escribís nada, el director la arma mirando el material: llegada, preparación y acción, cierre emocional.">?</span></label><input id="historia-txt" placeholder="llega a la playa al amanecer, se prepara en el shack y entra al agua"></div>
        <div><label>Duración objetivo</label><select id="objetivo"></select></div>
      </div>
      <div style="margin-top:12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
        <button class="btn" id="dirigir">🎬 Que el director decida</button>
        <span class="hint" style="margin:0">Una IA con oficio de comercial mira cada foto y propone: IA o cámara, ritmo, segundos y qué pasa. Después corregís lo que quieras.</span>
      </div>
      <div class="historia hidden" id="historia"></div>
      <p class="hint" id="dir-nota" style="margin-top:8px"></p>
      <label class="sw" style="margin-top:6px"><input type="checkbox" id="igualar" checked> Igualar la textura de las tomas fijas a la de los clips de IA <span style="color:var(--ink-soft)">(si hay mezcla)</span></label>
      <label class="sw" style="margin-top:6px"><input type="checkbox" id="ralenti" checked> Cámara lenta de edición en las tomas lentas <span style="color:var(--ink-soft)">(se estiran hasta 1,5×; la de los motores es apenas más lenta que la vida)</span></label>
      <label>Las tomas, en el orden del video <span class="q" title="Cada material es una toma. Foto: cámara (deriva, gratis) o IA (tu foto es el primer cuadro y el motor la continúa). Video: va tal cual, recortado. Ritmo: lenta, real o rápida. El tilde 'usar' saca una toma sin borrarla; las que el director descartó vienen sin tilde y con su motivo.">?</span></label>
      <div class="tomas" id="tomas-fotos"></div>
    </div>
  </div>

  <div class="card">
    <h3>Terminación</h3>
    <div class="row">
      <div><label>Grade de película</label><select id="grade"></select></div>
      <div><label>Entre tomas</label><select id="transicion"></select></div>
    </div>
    <h3 style="margin-top:16px">Apertura <span class="q" title="El título que te hace entrar, como el prólogo de una película. Sobre negro antes de la primera toma, o escrito sobre ella mientras ya pasa algo. El director lo escribe con la historia; cambialo si querés.">?</span></h3>
    <div class="row3">
      <div><label>Cómo</label><select id="apertura"></select></div>
      <div><label>Título grande</label><input id="apertura-texto" placeholder="SUMMER 2027" maxlength="40"></div>
      <div><label>Estilo de letra</label><select id="estilo-titulo"></select></div>
    </div>
    <div class="row">
      <div><label>Línea chica arriba</label><input id="apertura-arriba" placeholder="New season" maxlength="40"></div>
      <div><label>Línea chica abajo</label><input id="apertura-sub" placeholder="Swimwear" maxlength="60"></div>
    </div>
    <h3 style="margin-top:16px">Cierre</h3>
    <div class="row3">
      <div><label>Cómo</label><select id="cierre"></select></div>
      <div><label>Texto</label><input id="placa-texto" value="LUMA Íntima" maxlength="40"></div>
      <div><label>Línea chica</label><input id="placa-sub" placeholder="@lumaintima · nueva colección" maxlength="60"></div>
    </div>
    <label>Música <span class="q" title="La misma cortina que usás en Videos. Subila acá o allá; es una por cuenta.">?</span></label><input type="file" id="f-musica" accept="audio/*">
    <label class="sw"><input type="checkbox" id="musica"> Sumar la música <span id="musica-estado" style="color:var(--ink-soft)"></span></label>
    <label class="sw"><input type="checkbox" id="grano" checked> Grano fino de película</label>
    <label class="sw"><input type="checkbox" id="vineta" checked> Viñeta suave</label>
    <label class="sw"><input type="checkbox" id="cine"> Franjas de cine (arriba y abajo)</label>
  </div>

  <div class="card">
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
      <button class="btn sec" id="estimar">Calcular costo</button>
      <button class="btn" id="generar">🎬 Armar el comercial</button>
      <button class="btn sec hidden" id="frenar">Frenar</button>
    </div>
    <div class="est" id="est"></div>
    <div class="err" id="err"></div>
    <div class="prog hidden" id="prog"><div id="paso"></div><div class="bar"><i id="bar"></i></div></div>
    <video id="video" class="hidden" controls playsinline></video>
    <div id="descarga" class="hidden" style="margin-top:8px"><a class="btn sec" id="dl" download>⬇ Descargar</a></div>
  </div>

  <div class="card">
    <h3>Comerciales anteriores</h3>
    <div class="hist" id="hist"></div>
  </div>
</main>
<script>
const API = "%%API%%";
const $ = s => document.querySelector(s);
let CFG = null, FOTOS = [], MODO = "kling", JOB = null, TIMER = null;
const esc = s => String(s || "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

async function api(path, opts) {
  let r;
  try { r = await fetch(API + path, opts); }
  catch (e) { throw new Error("Se cortó la conexión con el servidor (" + e.message + "). Revisá la señal y probá de nuevo; las fotos ya subidas quedan."); }
  let d = null; try { d = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error((d && d.detail) || ("HTTP " + r.status));
  return d;
}
function leer(file) {
  return new Promise((ok, no) => { const fr = new FileReader(); fr.onload = () => ok(fr.result); fr.onerror = no; fr.readAsDataURL(file); });
}
// FOTOS = [{id, tipo, src, dur, usar, desc}] — cada material (foto o video) se sube al
// server apenas se elige (uno por pedido, sin achicar) y de ahí en más viaja sólo su id.
function pintarFotos() {
  const c = $("#fotos"); c.innerHTML = "";
  FOTOS.forEach((f, i) => {
    const d = document.createElement("div"); d.className = "foto" + (f.usar === false ? " desc" : "");
    d.innerHTML = `<img src="${f.src}"><span class="n">${i + 1}${f.id ? "" : " ⏳"}</span>` + (f.tipo === "video" ? `<span class="v">▶ ${f.dur ? f.dur.toFixed(0) + " s" : "video"}</span>` : "") + `<button class="x" title="Sacar">×</button>
      <div class="mv"><button title="Antes">‹</button><button title="Después">›</button></div>`;
    d.querySelector(".x").onclick = () => { const q = FOTOS.splice(i, 1)[0]; if (q && q.id) fetch(API + "/foto/" + q.id, {method: "DELETE"}).catch(() => {}); pintarFotos(); };
    const [a, b] = d.querySelectorAll(".mv button");
    a.onclick = () => { if (i > 0) { [FOTOS[i - 1], FOTOS[i]] = [FOTOS[i], FOTOS[i - 1]]; pintarFotos(); } };
    b.onclick = () => { if (i < FOTOS.length - 1) { [FOTOS[i + 1], FOTOS[i]] = [FOTOS[i], FOTOS[i + 1]]; pintarFotos(); } };
    c.appendChild(d);
  });
  const n = FOTOS.length, nv = FOTOS.filter(f => f.tipo === "video").length;
  $("#fotos-ayuda").textContent = !n ? "Todavía no hay material." :
    (MODO === "kling" ? `${n - nv} foto(s). Kling usa hasta ${CFG.max_refs} como referencia: la 1 es la cara de la modelo, la 2 a la 4 más vistas de ella, la 5 a la 7 el lugar. Las demás no viajan.` + (nv ? ` Los ${nv} video(s) no se usan en este modo.` : "")
                      : `${n} material(es): ${n - nv} foto(s) y ${nv} video(s). El director elige el orden y qué va; también podés ordenar a mano con ‹ ›.`);
  if (MODO === "fotos") pintarTomasFotos(); else pintarTomasKling();
}
$("#f-fotos").onchange = async e => {
  const archivos = Array.from(e.target.files || []); e.target.value = "";
  for (const f of archivos) {
    if (FOTOS.length >= CFG.max_fotos) break;
    const esVideo = (f.type || "").startsWith("video/") || /\.(mp4|mov|m4v|webm)$/i.test(f.name);
    const item = {id: null, tipo: esVideo ? "video" : "foto", src: esVideo ? "" : URL.createObjectURL(f), nombre: f.name, usar: true};
    FOTOS.push(item); pintarFotos();
    const fd = new FormData(); fd.append("archivo", f);
    let ok = false;
    for (let intento = 0; intento < 3 && !ok; intento++) {
      try { const r = await api(esVideo ? "/video" : "/foto", {method: "POST", body: fd}); item.id = r.id; item.dur = r.dur || 0; item.src = API + "/foto/" + r.id; ok = true; }
      catch (err) { if (intento === 2) { $("#err").textContent = `No pude subir ${f.name}: ${err.message}`; const k = FOTOS.indexOf(item); if (k >= 0) FOTOS.splice(k, 1); } else { await new Promise(r => setTimeout(r, 1500 * (intento + 1))); } }
    }
    pintarFotos();
  }
};
function idsListos() {
  if (FOTOS.some(f => !f.id)) throw new Error("Esperá a que termine de subir el material (lo que tiene ⏳).");
  return FOTOS.filter(f => f.tipo !== "video").map(f => f.id);
}
function materialesListos(soloUsados) {
  if (FOTOS.some(f => !f.id)) throw new Error("Esperá a que termine de subir el material (lo que tiene ⏳).");
  return FOTOS.filter(f => !soloUsados || f.usar !== false).map(f => ({tipo: f.tipo || "foto", id: f.id}));
}
async function importarClip(jid, n, btn) {
  btn.disabled = true; btn.textContent = "…";
  try {
    const r = await api("/importar_clip", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({jid, n})});
    FOTOS.push({id: r.id, tipo: "video", dur: r.dur || 0, src: API + "/foto/" + r.id, usar: true});
    if (MODO !== "fotos") setModo("fotos"); else pintarFotos();
    btn.textContent = "✓ agregado";
    window.scrollTo({top: 0, behavior: "smooth"});
  } catch (e) { $("#err").textContent = e.message; btn.disabled = false; btn.textContent = "+ usar"; }
}
function opciones(sel, obj, val) {
  sel.innerHTML = "";
  for (const [k, v] of Object.entries(obj)) { const o = document.createElement("option"); o.value = k; o.textContent = typeof v === "string" ? v : (v.label || v.nombre || k); sel.appendChild(o); }
  if (val !== undefined) sel.value = val;
}
function setModo(m) {
  MODO = m;
  document.querySelectorAll("#modos .chip").forEach(c => c.classList.toggle("on", c.dataset.m === m));
  $("#panel-kling").classList.toggle("hidden", m !== "kling");
  $("#panel-fotos").classList.toggle("hidden", m !== "fotos");
  $("#modo-ayuda").textContent = m === "kling"
    ? "Kling 3.0 Omni inventa las tomas con tus fotos de guía: sale una sola pieza con continuidad, como filmada. Hasta 15 s por tanda; 30 s son dos tandas."
    : "Cada foto es una toma, en su orden. Fidelidad exacta a cada foto: la cámara de edición es gratis, la IA la hace vivir (se paga por toma).";
  pintarFotos();
}
function tomasPlantilla() {
  const p = CFG.plantillas[$("#plantilla").value]; const total = parseInt($("#duracion").value || "15", 10);
  const out = []; let seg = 0;
  for (const t of p.tomas) { if (seg + t.seg > total) break; out.push({texto: t.texto, seg: t.seg}); seg += t.seg; }
  $("#lugar").value = p.lugar || "";
  pintarTomasKling(out);
}
function pintarTomasKling(lista) {
  const c = $("#tomas-kling"); c.innerHTML = "";
  (lista || leerTomasKling()).forEach((t, i) => {
    const d = document.createElement("div"); d.className = "toma";
    const fotosOpts = ['<option value="">sin guía</option>'].concat(FOTOS.map((_, k) => `<option value="${k + 1}" ${t.foto == k + 1 ? "selected" : ""}>foto ${k + 1}</option>`)).join("");
    d.innerHTML = `<span class="idx">${i + 1}</span><input value="${esc(t.texto)}" placeholder="Qué pasa en esta toma">
      <select class="s">${[1,2,3,4,5,6,8].map(s => `<option value="${s}" ${s == t.seg ? "selected" : ""}>${s} s</option>`).join("")}</select>
      <select class="f" title="Tu foto como guía de esta toma">${fotosOpts}</select><button title="Sacar">×</button>`;
    d.querySelector("button").onclick = () => { const l = leerTomasKling(); l.splice(i, 1); pintarTomasKling(l); };
    c.appendChild(d);
  });
  tandasAyuda();
}
function leerTomasKling() {
  return Array.from(document.querySelectorAll("#tomas-kling .toma")).map(d => ({texto: d.querySelector("input").value.trim(), seg: parseInt(d.querySelector(".s").value, 10), foto: parseInt(d.querySelector(".f").value || "0", 10) || null})).filter(t => t.texto);
}
function tandasAyuda() {
  const l = leerTomasKling(); const seg = l.reduce((a, t) => a + t.seg, 0); const total = parseInt($("#duracion").value || "15", 10);
  $("#est").textContent = l.length ? `${l.length} tomas · ${seg} s de ${total}` + (seg > total ? " — te pasás: se cortan las últimas" : "") : "";
}
$("#add-toma").onclick = () => { const l = leerTomasKling(); l.push({texto: "", seg: 3}); pintarTomasKling(l); };
let DIR = {};   // propuesta del director por foto (índice → {texto_en, por_que})
function pintarTomasFotos(lista) {
  const c = $("#tomas-fotos"); const prev = lista || leerTomasFotos(); c.innerHTML = "";
  FOTOS.forEach((f, i) => {
    const mz = ($("#mezcla") && $("#mezcla").value) || "ia";
    const esVideo = f.tipo === "video";
    const t = prev[i] || {motor: esVideo ? "video" : (mz === "camara" ? "camara" : "ia"), seg: esVideo ? Math.min(3, f.dur || 3) : (mz === "camara" ? 3 : 4), texto: "", ritmo: "normal", desde: 0};
    const d = document.createElement("div"); d.className = "toma foto-toma";
    const ritmos = Object.entries(CFG.ritmos).map(([k, v]) => `<option value="${k}" ${(t.ritmo || "normal") === k ? "selected" : ""}>${v}</option>`).join("");
    const actoTxt = (DIR[i] && DIR[i].acto) ? `<span class="acto">${(CFG.actos || {})[DIR[i].acto] || ("acto " + DIR[i].acto)}</span>` : "";
    const motorSel = esVideo ? `<select class="m"><option value="video" selected>Video (tal cual)</option></select>`
      : `<select class="m"><option value="camara" ${t.motor === "camara" ? "selected" : ""}>Cámara (gratis)</option><option value="ia" ${t.motor === "ia" ? "selected" : ""}>IA (cobra vida)</option></select>`;
    d.innerHTML = `<span class="idx">${i + 1}${actoTxt}</span><input type="checkbox" class="usar" title="Usar esta toma" ${f.usar === false ? "" : "checked"}>
      <input value="${esc(t.texto)}" placeholder="${esVideo ? "qué parte del video (opcional)" : esc(CFG.movimientos_foto[i % CFG.movimientos_foto.length]) + " (opcional)"}">
      ${motorSel}
      <select class="r">${ritmos}</select>
      <select class="s"></select>` + (DIR[i] && DIR[i].por_que ? `<div class="porque">🎬 ${esc(DIR[i].por_que)}</div>` : (f.usar === false && f.desc ? `<div class="porque">🎬 descartada: ${esc(f.desc)}</div>` : ""));
    d.dataset.desde = String(t.desde || 0);
    const s = d.querySelector(".s"); const llenar = () => {
      let ok = esVideo ? CFG.seg_video.filter(v => v <= (f.dur || 99) + 0.01) : (d.querySelector(".m").value === "ia" ? (($("#motor-ia").value || "").startsWith("kling") ? CFG.seg_kling : CFG.seg_ia) : CFG.seg_camara);
      if (!ok.length) ok = [CFG.seg_video[0]];
      s.innerHTML = ok.map(v => `<option value="${v}" ${Math.abs(v - t.seg) < 0.01 ? "selected" : ""}>${v} s</option>`).join(""); if (!ok.some(v => Math.abs(v - t.seg) < 0.01)) s.value = String(ok[Math.min(1, ok.length - 1)]); };
    llenar(); d.querySelector(".m").onchange = llenar;
    d.querySelector(".usar").onchange = ev => { f.usar = ev.target.checked; pintarFotos(); };
    c.appendChild(d);
  });
}
function leerTomasFotos() {
  return Array.from(document.querySelectorAll("#tomas-fotos .toma")).map((d, i) => ({texto: d.querySelector("input:not(.usar)").value.trim(), motor: d.querySelector(".m").value,
    ritmo: d.querySelector(".r").value, seg: parseFloat(d.querySelector(".s").value), desde: parseFloat(d.dataset.desde || "0") || 0,
    texto_en: (DIR[i] && DIR[i].texto === d.querySelector("input:not(.usar)").value.trim()) ? DIR[i].texto_en : ""}));
}
$("#dirigir").onclick = async () => {
  $("#err").textContent = "";
  if (!FOTOS.length) { $("#err").textContent = "Subí el material primero."; return; }
  $("#dirigir").disabled = true; $("#dir-nota").textContent = "El director está mirando tu material y armando la historia…";
  try {
    const mats = materialesListos(false);
    const d = await api("/director", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({materiales: mats, estilo: $("#estilo-dir").value, lugar: $("#lugar-dir").value, estilo_txt: $("#estilo-txt").value, mezcla: $("#mezcla").value, objetivo: parseInt($("#objetivo").value, 10), historia: $("#historia-txt").value})});
    // El director eligió el ORDEN: el material se reordena como la secuencia, y lo que
    // descartó queda al final, sin tilde, con su motivo (se puede volver a tildar).
    const viejos = FOTOS.slice();
    const nuevos = [], filas = []; DIR = {};
    d.secuencia.forEach((t, k) => { const f = viejos[t.indice]; if (!f) return; f.usar = true; f.desc = ""; nuevos.push(f); DIR[k] = t; filas.push({texto: t.texto, motor: t.motor, ritmo: t.ritmo, seg: t.seg, desde: t.desde || 0}); });
    d.descartes.forEach(x => { const f = viejos[x.indice]; if (!f) return; f.usar = false; f.desc = x.por_que; nuevos.push(f); filas.push({texto: "", motor: f.tipo === "video" ? "video" : "camara", ritmo: "normal", seg: 3, desde: 0}); });
    FOTOS = nuevos; pintarFotos(); pintarTomasFotos(filas);
    const h = d.historia || {};
    if (h.titulo || h.sinopsis || (h.actos && h.actos.length)) {
      $("#historia").innerHTML = `<b>${esc(h.titulo || "La historia")}</b><div>${esc(h.sinopsis || "")}</div>` + (h.actos && h.actos.length ? `<ol>${h.actos.map(a => `<li>${esc(a)}</li>`).join("")}</ol>` : "");
      $("#historia").classList.remove("hidden");
    }
    if (d.look) {   // el look elegido se aplica a la terminación; ella lo puede cambiar
      $("#grade").value = d.look.grade; $("#transicion").value = d.look.transicion;
      $("#cine").checked = !!d.look.cine; $("#grano").checked = !!d.look.grano;
    }
    if (d.titulos) {   // el prólogo y el cierre que escribió el director
      if (d.titulos.apertura) { $("#apertura").value = d.titulos.apertura_modo || "sobre_toma"; $("#apertura-texto").value = d.titulos.apertura; $("#apertura-sub").value = d.titulos.apertura_sub || ""; $("#apertura-arriba").value = d.titulos.apertura_arriba || ""; }
      if (d.titulos.cierre) { $("#cierre").value = d.titulos.cierre_modo || "placa"; $("#placa-texto").value = d.titulos.cierre; $("#placa-sub").value = d.titulos.cierre_sub || ""; }
    }
    const partes = [];
    if (d.look && d.look.estilo_resumen) partes.push(d.look.estilo_resumen);
    if (d.look && d.look.musica) partes.push("Música: " + d.look.musica);
    if (d.nota) partes.push(d.nota);
    $("#dir-nota").textContent = partes.length ? "🎬 " + partes.join(" · ") + " — Ya apliqué el look en Terminación; corregí lo que quieras." : "Listo: corregí lo que quieras y calculá el costo.";
  } catch (e) { $("#err").textContent = e.message; $("#dir-nota").textContent = ""; }
  $("#dirigir").disabled = false;
};
function pedido() {
  const p = {foto_ids: idsListos(), modo: MODO, grade: $("#grade").value, transicion: $("#transicion").value,
    cierre: $("#cierre").value, placa_texto: $("#placa-texto").value, placa_sub: $("#placa-sub").value,
    apertura: $("#apertura").value, apertura_texto: $("#apertura-texto").value, apertura_sub: $("#apertura-sub").value,
    apertura_arriba: $("#apertura-arriba").value, estilo_titulo: $("#estilo-titulo").value,
    musica: $("#musica").checked, grano: $("#grano").checked, vineta: $("#vineta").checked, cine: $("#cine").checked};
  if (MODO === "kling") {
    Object.assign(p, {plantilla: $("#plantilla").value, duracion_total: parseInt($("#duracion").value, 10), motor: $("#motor").value,
      formato: $("#formato").value, lugar: $("#lugar").value, tomas: leerTomasKling()});
  } else {
    // Sólo el material tildado, con su toma alineada.
    const todas = leerTomasFotos();
    const usados = FOTOS.map((f, i) => f.usar === false ? null : i).filter(i => i !== null);
    if (!usados.length) throw new Error("No quedó ninguna toma tildada para usar.");
    p.materiales = usados.map(i => ({tipo: FOTOS[i].tipo || "foto", id: FOTOS[i].id}));
    delete p.foto_ids;
    Object.assign(p, {motor_ia: $("#motor-ia").value, formato: $("#formato2").value, ralenti: $("#ralenti").checked, mezcla: $("#mezcla").value, igualar: $("#igualar").checked, tomas: usados.map(i => todas[i])});
  }
  return p;
}
$("#estimar").onclick = async () => {
  $("#err").textContent = "";
  try { const e = await api("/estimar", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(pedido())}); $("#est").textContent = e.detalle; }
  catch (e) { $("#err").textContent = e.message; }
};
$("#f-fotos").setAttribute("title", "Las fotos se suben tal cual, sin achicar");
$("#generar").onclick = async () => {
  $("#err").textContent = "";
  if (!FOTOS.length) { $("#err").textContent = "Subí al menos una foto."; return; }
  $("#generar").disabled = true;
  try {
    let cuerpo; try { cuerpo = JSON.stringify(pedido()); } catch (e) { $("#err").textContent = e.message; $("#generar").disabled = false; return; }
    const r = await api("/generar", {method: "POST", headers: {"Content-Type": "application/json"}, body: cuerpo});
    JOB = r.job_id; $("#est").textContent = r.estimado.detalle; $("#prog").classList.remove("hidden"); $("#frenar").classList.remove("hidden");
    $("#video").classList.add("hidden"); $("#descarga").classList.add("hidden");
    seguir();
  } catch (e) { $("#err").textContent = e.message; $("#generar").disabled = false; }
};
$("#frenar").onclick = async () => { if (JOB) { try { await api("/frenar/" + JOB, {method: "POST"}); $("#paso").textContent = "Frenando al terminar la toma en curso…"; } catch (e) {} } };
function seguir() {
  clearTimeout(TIMER);
  TIMER = setTimeout(async () => {
    try {
      const j = await api("/estado/" + JOB);
      const hechas = j.tandas ? (j.tandas_listas || 0) / j.tandas : (j.tomas ? (j.tomas_listas || 0) / j.tomas.length : 0);
      $("#paso").textContent = (j.paso || j.estado) + (j.costo ? ` · USD ${Number(j.costo).toFixed(2)}` : "");
      $("#bar").style.width = (j.estado === "listo" ? 100 : Math.round(hechas * 85)) + "%";
      if (j.estado === "listo") { mostrar(JOB, j); return; }
      if (j.estado === "error") { $("#err").textContent = j.detalle || "Falló."; $("#generar").disabled = false; $("#frenar").classList.add("hidden"); cargarHist(); return; }
      seguir();
    } catch (e) { $("#err").textContent = e.message; seguir(); }
  }, 4000);
}
function mostrar(jid, j) {
  $("#generar").disabled = false; $("#frenar").classList.add("hidden");
  const v = $("#video"); v.src = API + "/final/" + jid + "?t=" + Date.now(); v.classList.remove("hidden");
  $("#dl").href = API + "/final/" + jid; $("#descarga").classList.remove("hidden");
  $("#paso").textContent = `Listo · ${j.duracion || ""} s · USD ${Number(j.costo || 0).toFixed(2)}`;
  cargarHist();
}
async function cargarHist() {
  try {
    const d = await api("/jobs"); const c = $("#hist"); c.innerHTML = "";
    if (!d.jobs.length) { c.innerHTML = '<p class="hint">Todavía ninguno.</p>'; return; }
    for (const j of d.jobs) {
      const it = document.createElement("div"); it.className = "it";
      const f = j.creado ? new Date(j.creado * 1000).toLocaleString("es-AR", {day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"}) : "";
      it.innerHTML = `<div>${esc(j.plantilla || j.modo)} · ${esc(j.motor || "")}<br><span>${f} · ${esc(j.estado)}${j.duracion ? " · " + j.duracion + " s" : ""}${j.costo ? " · USD " + Number(j.costo).toFixed(2) : ""}${j.detalle ? " · " + esc(j.detalle) : ""}</span></div>` +
        (j.estado === "listo" ? `<div style="display:flex;gap:6px"><button class="btn sec" data-j="${j.job_id}">Ver</button><button class="btn sec" data-c="${j.job_id}" title="Reusar las tomas que te gustaron como material">Clips</button></div>` : (j.estado === "trabajando" || j.estado === "encolado" ? `<button class="btn sec" data-s="${j.job_id}">Seguir</button>` : ""));
      it.querySelectorAll("button").forEach(b => {
        if (b.dataset.j) b.onclick = () => mostrar(b.dataset.j, j);
        if (b.dataset.s) b.onclick = () => { JOB = b.dataset.s; $("#prog").classList.remove("hidden"); seguir(); };
        if (b.dataset.c) b.onclick = async () => {
          let box = it.querySelector(".clips");
          if (box) { box.remove(); return; }
          box = document.createElement("div"); box.className = "clips"; box.innerHTML = '<span class="hint">Cargando los clips…</span>'; it.appendChild(box);
          try {
            const cl = await api("/jobs/" + b.dataset.c + "/clips");
            box.innerHTML = cl.clips.length ? "" : '<span class="hint">Este comercial ya no tiene clips guardados (duran 7 días).</span>';
            cl.clips.forEach(x => { const e = document.createElement("div"); e.className = "cl";
              e.innerHTML = (x.mini ? `<img src="${API}/clipmin/${b.dataset.c}/${x.n}">` : "") + `<div title="${esc(x.texto || "")}">toma ${x.n + 1} · ${x.dur} s</div><button>+ usar</button>`;
              e.querySelector("button").onclick = ev => importarClip(b.dataset.c, x.n, ev.target); box.appendChild(e); });
          } catch (e) { box.innerHTML = `<span class="err">${esc(e.message)}</span>`; }
        };
      });
      c.appendChild(it);
    }
  } catch (e) {}
}
$("#f-musica").onchange = async e => {
  const f = e.target.files && e.target.files[0]; if (!f) return;
  const fd = new FormData(); fd.append("archivo", f);
  try { await api("/musica", {method: "POST", body: fd}); $("#musica-estado").textContent = "(subida)"; $("#musica").checked = true; }
  catch (err) { $("#err").textContent = err.message; }
};
(async () => {
  CFG = await api("/config");
  const modos = $("#modos");
  for (const [k, v] of Object.entries(CFG.modos)) { const c = document.createElement("div"); c.className = "chip"; c.dataset.m = k; c.textContent = v; c.onclick = () => setModo(k); modos.appendChild(c); }
  opciones($("#plantilla"), CFG.plantillas, "surf");
  const dur = $("#duracion"); CFG.duraciones.forEach(d => { const o = document.createElement("option"); o.value = d; o.textContent = d + " segundos" + (d > 15 ? ` (${Math.ceil(d / 15)} tandas)` : ""); dur.appendChild(o); });
  opciones($("#motor"), CFG.motores_kling, "kling_std");
  opciones($("#motor-ia"), CFG.motores_ia, "kling_i2v_std");
  opciones($("#estilo-dir"), Object.fromEntries(Object.entries(CFG.plantillas).filter(([k]) => k !== "libre")), "surf");
  opciones($("#mezcla"), CFG.mezclas, "ia");
  const obj = $("#objetivo"); CFG.objetivos.forEach(v => { const o = document.createElement("option"); o.value = v; o.textContent = v + " segundos"; obj.appendChild(o); }); obj.value = "30";
  // Con "todas con IA" o "todas con cámara", el selector por toma sigue la mezcla.
  $("#mezcla").onchange = () => { const m = $("#mezcla").value; if (m === "libre") return; pintarTomasFotos(leerTomasFotos().map(t => Object.assign(t, {motor: m}))); };
  $("#motor-ia").onchange = pintarTomasFotos;
  opciones($("#grade"), CFG.grades, "luminoso");
  opciones($("#apertura"), CFG.titulos, "sobre_toma"); opciones($("#cierre"), CFG.titulos, "placa");
  opciones($("#estilo-titulo"), CFG.estilos_titulo, "campana");
  opciones($("#transicion"), CFG.transiciones, "corte");
  $("#plantilla").onchange = tomasPlantilla; $("#duracion").onchange = tomasPlantilla;
  $("#tomas-kling").addEventListener("change", tandasAyuda); $("#tomas-kling").addEventListener("input", tandasAyuda);
  $("#musica-estado").textContent = CFG.musica ? "(hay una subida)" : "(no hay música subida)";
  if (CFG.musica) $("#musica").checked = true;
  if (!CFG.fal_key) $("#err").textContent = "No hay API key de fal cargada: Kling y las tomas con IA no van a andar hasta que la cargues en Fotos → Ajustes.";
  tomasPlantilla(); setModo("kling"); cargarHist();
})();
</script>
</body>
</html>
"""
